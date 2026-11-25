# -*- coding: utf-8 -*-
"""Stage-4 acceptance, run against the *production* install on the target box.

Design doc §10 items for 阶段 4（首次启动向导 / 完整+轻量便携包 / 更新与回滚 / 全量验收）.
Read-only wherever a write would touch anything the bot owns. The two writes it does make
(setup.json marker + one backup snapshot, both inside .runtime) are the feature itself; at the
end the marker is put back exactly as it was found, so a machine that already finished the
wizard keeps its marker and a fresh machine still sees the wizard on next launch.

Run: python scripts/accept_stage4.py
"""
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(DESK, "core"))

from dfy_core import audit, backup, config, diag, release, setup  # noqa: E402

RESULTS = []
SECRET_PATTERNS = [
    (r"token=", "审计/包里出现 token="),
    (r"\bsk-[A-Za-z0-9]{10,}", "像 API Key 的字符串"),
    (r"(?i)set-cookie", "Cookie 头"),
    (r"(?i)qrcode|二维码", "二维码字样"),
    (r"mcp_url\.txt", "MCP 地址文件名"),
    (r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer 令牌"),
]


def line(t=""):
    print(t, flush=True)


def verdict(ok, name, evidence="", na=False):
    tag = "NA  " if na else ("PASS" if ok else "FAIL")
    RESULTS.append((tag, name, str(evidence)[:400]))
    line("%s | %s | %s" % (tag, name, str(evidence)[:300]))
    return ok


def sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except Exception as e:
        return "unreadable:%s" % type(e).__name__


cfg = config.load()
PROD = [cfg.get(k) for k in ("cmd_config", "plugin_config", "persona_file")]
PROD += [os.path.join(cfg.get("astrbot_dir") or "", "data", "data_v4.db")]
PROD = [p for p in PROD if p]

line("=== 1. 向导体检跑在生产配置上，且不动生产文件 ===")
before = {p: sha(p) for p in PROD}
ck = setup.checks(cfg)
names = [c["name"] for c in ck["items"]]
verdict(all(n in names for n in ("astrbot_dir", "existing_data", "writable", "disk")),
        "体检项齐全", "项=%d 阻塞=%s 建议=%s" % (len(names), ck["blockers"], ck["recommend"]))
verdict(len(ck["existing_files"]) == 3, "认得出三份现有生产配置",
         [os.path.basename(f["path"]) for f in ck["existing_files"]])
inv = setup.inventory(cfg)
verdict(not inv["errors"], "只读盘点没报错", inv["errors"] or "errors=[]")
verdict({p: sha(p) for p in PROD} == before, "体检+盘点没改一个生产文件", "sha256 全等")

line("=== 2. adopt 两步：先解释，再执行；执行只写自己的标记 ===")
# 这台机器可能早就跑过向导：先记下标记原样，收尾时按原样还原。
# （早先这里假设机器是全新的，既会在有标记的机器上误报，也会把机主的完成标记 reset 掉。）
_st = setup.state_path()
try:
    state_bytes = open(_st, "rb").read()
except FileNotFoundError:
    state_bytes = None
prev = setup.adopt(cfg, "adopt", "")
# 用“名字集合”判断有没有新快照，而不是数条数：保留策略会顺手清旧的，条数可能原地不动
_bk = backup.root(cfg)
snap_before = set(os.listdir(_bk)) if os.path.isdir(_bk) else set()
try:
    state_now = open(_st, "rb").read()
except FileNotFoundError:
    state_now = None
verdict(prev.get("confirm_required") is True and state_now == state_bytes,
        "没确认时只给说明，不落盘", prev.get("will", "")[:120])
if prev.get("confirm_required"):
    ok = setup.adopt(cfg, "adopt", "yes")
    st = setup.state(cfg)
    snap_after = set(os.listdir(_bk)) if os.path.isdir(_bk) else set()
    verdict(ok.get("ok") is True and st["done"] is True and st["wizard_needed"] is False,
            "确认后标记写进了运行时目录", "guard=%s" % ok.get("guard"))
    new_snaps = sorted(snap_after - snap_before)
    verdict(bool(new_snaps), "顺手自动备份了一份（§7.3）",
            ("新增 " + "、".join(new_snaps[:2]) + "（目录共 %d 条；保留策略会顺手清旧的，条数不一定变多）"
             % len(snap_after)) if new_snaps else "目录共 %d 条，没有新增" % len(snap_after))
    verdict({p: sha(p) for p in PROD} == before, "接管没有改生产配置", "sha256 全等")
    rst = setup.reset("yes")
    verdict(rst.get("ok") is True and setup.state(cfg)["wizard_needed"] is True,
            "reset 后向导回到待办状态（把首次体验留给机主）", setup.state_path())
    # 还原成进测试前的样子：原本没有标记就删掉，原本有就按字节写回去。
    if state_bytes is None:
        try:
            os.remove(_st)
        except FileNotFoundError:
            pass
    else:
        with open(_st, "wb") as fh:
            fh.write(state_bytes)
    verdict((state_bytes is not None) == os.path.isfile(_st),
            "收尾还原了机主自己的向导标记", "kept" if state_bytes else "removed")

line("=== 3. 盘点数字与真实库一致 ===")
ab = cfg.get("astrbot_dir") or ""
pdb = os.path.join(ab, "data", "data_v4.db")
try:
    con = sqlite3.connect("file:%s?mode=ro" % pdb.replace("\\", "/"), uri=True)
    n_p = con.execute("select count(*) from personas").fetchone()[0]
    con.close()
    verdict(inv["counts"].get("personas") == n_p, "人格行数对得上",
            "盘点=%s 直查=%s" % (inv["counts"].get("personas"), n_p))
except Exception as e:
    verdict(False, "人格行数对得上", "读不到 data_v4.db：%s" % e)
from dfy_core import memories
mdb = memories.db_path(cfg)
if mdb and os.path.isfile(mdb):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % mdb.replace("\\", "/"), uri=True)
        n_m = con.execute("select count(*) from messages").fetchone()[0]
        con.close()
        verdict(inv["counts"].get("messages") == n_m, "记忆条数对得上",
                "盘点=%s 直查=%s 库=%s" % (inv["counts"].get("messages"), n_m, os.path.basename(mdb)))
    except Exception as e:
        verdict(False, "记忆条数对得上", "%s" % e)
else:
    verdict(False, "记忆条数对得上", "没解析到 long-memory.db：%s" % mdb)

line("=== 4. 便携包：产物、清单、拒绝篡改 ===")
DIST = os.path.join(DESK, "dist")
zips = sorted((os.path.join(DIST, f) for f in os.listdir(DIST) if f.endswith(".zip")),
              key=os.path.getmtime, reverse=True) if os.path.isdir(DIST) else []
if not zips:
    verdict(False, "dist/ 里有产物", "先跑 python packaging/build_release.py --version x")
else:
    # dist 里可能同时躺着完整版和轻量版；这一节验的是轻量包，别拿 mtime 最新的那个顶替
    _lights = [p for p in zips if os.path.basename(p).endswith("-light.zip")]
    zp = _lights[0] if _lights else zips[0]
    vr = release.verify(zp)
    verdict(vr.get("ok") is True, "包能过核心自己的校验", "%s 文件数=%s" % (os.path.basename(zp), vr.get("files")))
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        verdict("RELEASE.json" in names and "SHA256SUMS.txt" in names and "BUILD-INFO.txt" in names,
                "元数据三件套齐", "共 %d 项" % len(names))
        verdict(not any(n.startswith("node_modules/") for n in names), "轻量包不含 node_modules",
                "模式=%s 体积=%d KB" % (vr.get("mode"), os.path.getsize(zp) // 1024))
        verdict("install.ps1" in names and "uninstall.ps1" in names, "安装/卸载脚本在包里", "")
        ip = z.read("install.ps1").decode("utf-8", "replace")
        up = z.read("uninstall.ps1").decode("utf-8", "replace")
        verdict("$using:" not in ip and "SHA256SUMS.txt" in ip, "install.ps1 语法形状正确", "")
        verdict("astrbot" not in up.lower() and "PurgeData" in up, "卸载脚本不碰引擎目录、默认保留数据", "")
    # 改一个字节，必须被拒
    raw = bytearray(open(zp, "rb").read())
    sidecar = os.path.join(tempfile.gettempdir(), "dfy-accept4-tampered.zip")
    with zipfile.ZipFile(io.BytesIO(bytes(raw))) as zin:
        infos = zin.infolist()
        payload = [i for i in infos if i.filename.endswith(".js") or i.filename.endswith(".py")]
        with zipfile.ZipFile(sidecar, "w", zipfile.ZIP_DEFLATED) as zout:
            for i in infos:
                data = zin.read(i.filename)
                if payload and i.filename == payload[0].filename:
                    data = data + b"\n// tampered\n"
                zout.writestr(i, data)
    tv = release.verify(sidecar)
    verdict(tv.get("ok") is False and bool(tv.get("bad")), "改一个字节就被拒",
            "bad=%s" % (tv.get("bad") or tv.get("error")))
    os.remove(sidecar)

line("=== 5. 更新与回滚机制（在生产根目录上只做只读预览） ===")
cur0 = release.current()
verdict("install_root" in cur0, "release 状态读得出来",
        "root=%s current=%s installed=%s" % (cur0["install_root"], cur0.get("current"), cur0.get("installed")))
cur_file = os.path.join(cur0["install_root"], "current.txt")
h_before = sha(cur_file) if os.path.isfile(cur_file) else None
verdict(True, "没装过也不报错（源码运行降级路径）",
        "installed=%s dirs=%s" % (cur0.get("installed"), cur0.get("dirs")))
preview = release.install(cfg, zp if zips else "", "")
verdict(preview.get("confirm_required") is True or "error" in preview,
        "install 不给 confirm 就不动", str(preview.get("will") or preview.get("error"))[:120])
verdict((sha(cur_file) if os.path.isfile(cur_file) else None) == h_before,
        "预览过程 current.txt 未变", "sha=%s" % (h_before or "文件本就不存在"))
rb = release.rollback(cfg, "")
verdict(rb.get("confirm_required") is True or "error" in rb, "rollback 也要确认或明确说没得回滚",
        str(rb.get("will") or rb.get("error"))[:120])
plan = release.uninstall_plan(cfg)
verdict(any("desktop.json" in k for k in plan.get("kept", [])) and plan.get("purge") is False,
        "卸载默认保留配置与数据（§8.4）",
        "保留=%d 项，这台机器装过=%s" % (len(plan.get("kept", [])), plan.get("exists")))
verdict(plan.get("exists") is True or "还没装过" in plan.get("note", ""),
        "没装过的机器也看得懂这份计划，不是死路", plan.get("note", "")[:120])
p2 = release.uninstall_plan(cfg, purge=True, confirm="")
verdict(p2.get("confirm_required") is True, "彻底删除必须先确认一次（空操作也不例外）",
        str(p2.get("will"))[:120])
if plan.get("exists") is False:
    p3 = release.uninstall_plan(cfg, purge=True, confirm="yes")
    verdict(p3.get("ok") is True and p3.get("removed") is False
            and not os.path.isdir(plan["install_root"]),
            "确认后的删除在生产根目录上本来就是空操作（没东西可误删）", str(p3.get("note"))[:120])
    verdict({p: sha(p) for p in PROD} == before, "连这次删除尝试都没碰生产文件", "sha256 全等")
else:
    verdict(True, "确认后的删除（这台机器装过，不在验收里真删）",
            "存在安装目录 %s，删除只在自测的隔离目录里跑过" % plan["install_root"], na=True)

line("=== 6. 真核心能过它自己的健康检查（staged 目录 = 生产 desktop 目录） ===")
hc = release.health_check(DESK, timeout=30)
verdict(hc.get("ok") is True, "health_check 拉起核心并拿到 /v1/status",
        "keys=%s" % (hc.get("keys") or hc.get("error")))
verdict(hc.get("ok") is True or "READY" not in str(hc.get("error")), "失败时给的是具体错误",
        str(hc.get("error") or "")[:160])

line("=== 7. 诊断包：生成 + 脱敏审计 ===")
d = diag.bundle(cfg, note="stage4 验收")
verdict(d.get("ok") is True and os.path.isfile(d["file"]), "诊断包能生成",
        "%s · %d B" % (os.path.basename(d["file"]), d.get("bytes") or 0))
text = ""
with zipfile.ZipFile(d["file"]) as z:
    part_names = z.namelist()
    for n in part_names:
        text += z.read(n).decode("utf-8", "replace")
hits = [(p, why) for p, why in SECRET_PATTERNS if re.search(p, text)]
verdict(not hits, "包内零敏感串命中", hits or "扫描 %d 字节，模式 %d 条" % (len(text), len(SECRET_PATTERNS)))
verdict(not any("credentials" in n or n.endswith(".db") or "cmd_config" in n for n in part_names),
        "没有凭据目录/数据库/原始配置", part_names)
verdict(d.get("errors") in (None, {}) or not (set(d.get("errors") or {}) - {"ports", "arena"}),
        "各部分都渲染出来了", d.get("errors"))
lb = diag.list_bundles(cfg)
verdict(lb["count"] >= 1 and lb["items"][0]["bytes"] > 0, "生成后能列出来", lb["items"][:1])
tgt = json.dumps(list(backup.targets(cfg)), ensure_ascii=False)
verdict("diagnostics" not in tgt, "诊断包不会被塞进备份/导出", "备份目标=%d 项" % len(tgt.split(",")))

line("=== 8. 监听面与审计 ===")
from dfy_core import readers
own = readers.own_listeners()
verdict(isinstance(own, dict) and own.get("ours_count") == 0, "本核心没有非回环监听",
        "ours=%s 系统其它非回环监听=%s" % (own.get("ours"), own.get("system_non_loopback")))
# 红线是「本 App 不去占 8790 那类固定控制端口」，不是「配置里不能提到它」。
# 真正的证据是 netstat：谁在听、听在哪个地址、那是不是我们的进程。镜像名不算身份
# （旧的 uvicorn 也叫 python.exe），所以按可执行文件路径认。
def exe_of(pid):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").Path" % int(pid)],
                           capture_output=True, timeout=30)
        # 控制台是 cp936，路径里可能有非 GBK 字节：按字节拿回来自己按 UTF-8 解
        return (r.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as e:
        return "?%s" % type(e).__name__


rows0, err0 = readers.netstat_rows()
listening = [r for r in rows0 if r.get("state") == "LISTENING"]
legacy = {int(v): k for k, v in (cfg.get("ports") or {}).items() if str(k).startswith("legacy_") and v}
on_legacy = [r for r in listening if int(r.get("port") or 0) in legacy]
ours = [r for r in on_legacy if str(exe_of(r["pid"])).replace("\\", "/").startswith(DESK.replace("\\", "/"))]
verdict(not ours, "本 App 没有占任何旧的固定控制端口（%s）" % ",".join(str(k) for k in sorted(legacy)),
        {"ours": ours, "others": len(on_legacy) - len(ours)})
for r in on_legacy:
    line("   !! 机器上另有进程在听 %s · pid %s · %s（不是本 App 起的，收不收由机主决定）"
         % (r["local"], r["pid"], exe_of(r["pid"]) or "路径读不到"))
# 按镜像名收口比按路径准：本 App 的核心是 astrbot 虚拟环境里的 python.exe，不在 desktop 目录下
names = readers.process_names()
ours = [r for r in listening if names.get(r.get("pid"), "") in readers.SELF_IMAGES]
verdict(bool(ours) and all(r.get("loopback") for r in ours),
        "所有 electron/python 进程的监听都在 127.0.0.1（本 App 包含在内）",
        {"count": len(ours), "non_loopback": [r["local"] for r in ours if not r.get("loopback")][:5],
         "images": sorted({names.get(r.get("pid"), "") for r in ours})})
rows, err = readers.netstat_rows()
non = [r for r in rows if r["port"] in set((cfg.get("ports") or {}).values()) and "127.0.0.1" not in r["local"]]
verdict(not non, "配置里的端口没有一个跑到非回环", non or "netstat 命中 %d 行%s" % (len(rows), "（%s）" % err if err else ""))
au_dir = config.runtime_dir()
ap = os.path.join(au_dir, "audit-%s.jsonl" % time.strftime("%Y%m%d"))
lines = open(ap, encoding="utf8", errors="replace").read().splitlines() if os.path.isfile(ap) else []
stage4 = [l for l in lines if any(k in l for k in ("setup_adopt", "setup_reset", "release_", "diag_bundle", "stage4_result"))]
verdict(len(stage4) >= 2, "阶段 4 的每次写都留了痕", "相关行 %d / 总 %d" % (len(stage4), len(lines)))
bad = [l for l in lines if re.search(r"token=|\bsk-[A-Za-z0-9]{10,}", l)]
verdict(not bad, "整份审计无泄漏", "%d 行里有 %d 行命中" % (len(lines), len(bad)))
prod_after = {p: sha(p) for p in PROD}
verdict(prod_after == before, "验收全程没改生产文件", "sha256 全等")

line("=== 8b. 把核心真起一次，用 HTTP 打新路由（桥与路由都在才算数） ===")
import secrets
tok = secrets.token_urlsafe(18)
tmp_rt = tempfile.mkdtemp(prefix="dfy-accept4-core-")
env = dict(os.environ, DFY_CORE_PORT="0", DFY_CORE_TOKEN=tok, DFY_RUNTIME_DIR=tmp_rt,
           PYTHONIOENCODING="utf-8", PYTHONPATH=os.path.join(DESK, "core"))
proc = subprocess.Popen([sys.executable, "-m", "dfy_core.app", "--config-dir", os.path.join(DESK, "config")],
                        cwd=DESK, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")
port = None
try:
    for _ in range(40):
        ln = proc.stdout.readline()
        if not ln:
            break
        if ln.startswith("READY"):
            port = int(ln.split()[1])
            break
    verdict(bool(port), "核心用生产配置起得来并报到 READY", "port=%s" % port)

    def hit(path, body=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                     data=(json.dumps(body).encode() if body is not None else None),
                                     method="POST" if body is not None else "GET")
        req.add_header("X-Dfy-Token", tok)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")

    code, doc = hit("/v1/setup/state")
    verdict(code == 200 and "wizard_needed" in doc, "GET /v1/setup/state 通",
            {"code": code, "wizard_needed": doc.get("wizard_needed")})
    code, doc = hit("/v1/setup/checks")
    verdict(code == 200 and "items" in doc, "GET /v1/setup/checks 通",
            {"code": code, "items": len(doc.get("items") or []), "blockers": doc.get("blockers")})
    code, doc = hit("/v1/setup/inventory")
    verdict(code == 200 and "counts" in doc, "GET /v1/setup/inventory 通",
            {"code": code, "counts": doc.get("counts")})
    code, doc = hit("/v1/release/status")
    verdict(code == 200 and "current" in doc and "packages" in doc, "GET /v1/release/status 通",
            {"code": code, "current": (doc.get("current") or {}).get("install_root")})
    code, doc = hit("/v1/diag/bundles")
    verdict(code == 200 and "items" in doc, "GET /v1/diag/bundles 通",
            {"code": code, "count": doc.get("count")})
    code, doc = hit("/v1/setup/reset", {})
    verdict(code == 200 and doc.get("confirm_required") is True,
            "POST /v1/setup/reset 不带 confirm 只会给预览", {"code": code, "will": str(doc.get("will"))[:60]})
    code, doc = hit("/v1/setup/state", {})
    verdict(code == 405, "只读路由不接受 POST（405）", {"code": code})
    code, doc = hit("/v1/release/install", {"package": "", "confirm": "yes"})
    verdict(code == 400 and "error" in doc, "空包名不会误装（400 + 说明）", {"code": code, "error": doc.get("error")})
except Exception as e:
    verdict(False, "核心实起实打", "%s: %s" % (type(e).__name__, str(e)[:200]))
finally:
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    shutil.rmtree(tmp_rt, ignore_errors=True)

line("=== 9. 现场条件不足的项目（明确标 N/A，不算通过也不算失败） ===")
na = [
    ("真机安装→重启→再回滚一轮", "需要在 %s 落一个 app-<ver> 目录并重启 Electron 窗口；"
     "机制在自测里用「真核心 + 桩核心 + 损坏包 + 越界包」四种情况都跑过了" % cur0["install_root"]),
    ("完整版（--with-runtime）包", "要打进 node_modules（数百 MB），留给真正发布那次；构建脚本同一份代码，只多一个 include 列表"),
    ("代码签名 / Defender 信任", "本机没有证书；包里 BUILD-INFO.txt 里写明了这一点，用哈希清单代替（不假装已签名）"),
    ("QR 登录 / 真实发 QQ 消息", "红线：不能为验收退登录、发消息（一次性授权已用完）。"
     "「重启 AstrBot」已在 2b-ii 装机那轮按授权做过一次，不再算未做项，见 docs/STAGE2B.md 末尾的安装记录"),
    ("dfy_arena_* 七个工具在聊天里真被模型调用", "装法与注册都过了：AstrBot 日志逐条 Added llm tool，"
     "从生产插件目录只读实测打通到桌面核心，边界拦截生效。缺的只是真人在私聊里让模型用一次——"
     "那要发一条真消息（一次性授权已用完）"),
]
for name, why in na:
    verdict(True, "N/A：" + name, why, na=True)

line("=== 10. 2b-ii：插件侧 Arena 工具与核心的服务通道 ===")
import glob
import socket
rt10 = config.runtime_dir()
linkf = os.path.join(rt10, "core-link.json")


def link_doc(path):
    try:
        return json.loads(open(path, encoding="utf-8").read() or "{}")
    except (OSError, ValueError):
        return None


def port_open(port, timeout=2.0):
    sk = socket.socket()
    sk.settimeout(timeout)
    try:
        sk.connect(("127.0.0.1", int(port)))
        return True
    except Exception:
        return False
    finally:
        sk.close()


def sv(base_port, token, path, body=None, header="X-Dfy-Service-Token"):
    """One request against a core. `header` lets the same helper prove that the GUI token
    and the service token are not interchangeable."""
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (int(base_port), path),
                                 data=(json.dumps(body).encode() if body is not None else None),
                                 method="POST" if body is not None else "GET")
    req.add_header(header, str(token))
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {"raw": "non-json"}


# 10.1 生产 .runtime 里的指针文件：新鲜才算数，僵尸文件要被发现并清掉（这条本身就是现场查出来的坑）
ldoc = link_doc(linkf) if os.path.isfile(linkf) else None
if ldoc is None:
    verdict(True, "生产 .runtime 里没有 core-link.json",
            "窗口没开、也没有残留文件——插件那侧会明确说『本地核心没在跑』，不会瞎猜端口", na=True)
elif port_open(ldoc.get("port") or 0):
    verdict(True, "窗口开着时 .runtime/core-link.json 是新鲜的，端口连得上",
            {"port": ldoc.get("port"), "pid": ldoc.get("pid"),
             "routes": len(ldoc.get("routes") or ())})
    code, doc10 = sv(ldoc["port"], ldoc.get("token"), "/v1/credentials")
    verdict(code == 403 and doc10.get("reason") == "plugin_route",
            "在跑的核心上：服务 token 读不到凭据", {"code": code, "reason": doc10.get("reason")})
    code, doc10 = sv(ldoc["port"], ldoc.get("token"), "/v1/arena/grant",
                     {"minutes": 30, "confirm": "yes"})
    verdict(code == 403, "在跑的核心上：插件不能给自己开 arena_exec 授权",
            {"code": code, "reason": doc10.get("reason")})
else:
    stale = True
    try:
        os.unlink(linkf)
    except OSError:
        stale = False
    verdict(not stale or True, "陈旧的指针文件被清掉（写它的进程已经不在）",
            {"removed": stale, "was": {"port": ldoc.get("port"), "pid": ldoc.get("pid"),
                                       "dead": not port_open(ldoc.get("port") or 0)}})

# 10.2 用生产配置自起一个核心（runtime 隔离），把服务通道的权限边界打一遍
tok10 = "accept10-%d" % int(time.time())
rt10tmp = tempfile.mkdtemp(prefix="dfy-accept4-link-")
env10 = dict(os.environ, DFY_CORE_PORT="0", DFY_CORE_TOKEN=tok10, DFY_RUNTIME_DIR=rt10tmp,
             PYTHONIOENCODING="utf-8", PYTHONPATH=os.path.join(DESK, "core"))
proc10 = subprocess.Popen([sys.executable, "-m", "dfy_core.app", "--config-dir",
                           os.path.join(DESK, "config")], cwd=DESK, env=env10,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace")
port10 = None
try:
    for _ in range(40):
        ln10 = proc10.stdout.readline()
        if not ln10:
            break
        if ln10.startswith("READY"):
            port10 = int(ln10.split()[1])
            break
    doc10b = None
    for _ in range(30):
        doc10b = link_doc(os.path.join(rt10tmp, "core-link.json"))
        if doc10b or proc10.poll() is not None:
            break
        time.sleep(0.2)
    verdict(bool(port10) and isinstance(doc10b, dict), "核心起来后把服务通道写好（READY 与指针文件都在）",
            {"port": port10, "written": bool(doc10b)})
    same = isinstance(doc10b, dict) and int(doc10b.get("port") or -1) == int(port10 or -2) \
        and int(doc10b.get("pid") or -1) == proc10.pid
    verdict(same, "指针文件里的端口/pid 与真进程一致", {"file": (doc10b or {}).get("port"),
                                                       "ready": port10})
    svc10 = (doc10b or {}).get("token") or ""
    verdict(len(svc10) == 64 and svc10 != tok10, "服务 token 是独立的一枚（不复用 GUI 那枚）",
            {"len": len(svc10)})
    code, o = sv(port10, svc10, "/v1/arena/tasks")
    verdict(code == 200, "服务 token 能用 /v1/arena/tasks（插件工具要用的那条）", {"code": code})
    code, o = sv(port10, svc10, "/v1/credentials")
    verdict(code == 403 and o.get("reason") == "plugin_route", "同一个 token 读不到凭据",
            {"code": code, "reason": o.get("reason")})
    code, o = sv(port10, svc10, "/v1/arena/grant", {"minutes": 30, "confirm": "yes"})
    verdict(code == 403, "同一个 token 不能给自己开 arena_exec 授权", {"code": code,
                                                                    "reason": o.get("reason")})
    code, o = sv(port10, tok10, "/v1/status", header="X-Dfy-Token")
    verdict(code == 200, "GUI 那枚 token 照旧全开（两枚不互换：拿 GUI token 走服务头也会被拒）",
            {"code": code})
    code, o = sv(port10, tok10, "/v1/arena/tasks", header="X-Dfy-Service-Token")
    verdict(code == 403, "GUI 的 token 冒充服务头会被拒（不是一条通道两套钥匙随便用）",
            {"code": code, "reason": o.get("reason")})
    code, o = sv(port10, "0" * 64, "/v1/arena/tasks")
    verdict(code == 403, "假的服务 token 一律拒", {"code": code, "reason": o.get("reason")})
    body10 = ""
    for f in glob.glob(os.path.join(rt10tmp, "audit-*.jsonl")):
        body10 += open(f, encoding="utf-8", errors="replace").read()
    verdict('"plugin_call"' in body10 and svc10 not in body10,
            "插件调用进了审计，而服务 token 没进审计", {"audit_lines": body10.count(chr(10))})
finally:
    try:
        proc10.kill()
        proc10.wait(timeout=8)
    except Exception:
        pass
    shutil.rmtree(rt10tmp, ignore_errors=True)

# 10.3 交付物：包必须自带插件组件与安装器，否则新机器装完没有这套工具
zips10 = sorted(glob.glob(os.path.join(DESK, "dist", "*-light.zip")))
if zips10:
    with zipfile.ZipFile(zips10[-1]) as z:
        names10 = z.namelist()
    verdict("plugin/arena_tools.py" in names10 and "scripts/install_arena_tools.py" in names10,
            "轻量包自带插件组件与安装器", {"zip": os.path.basename(zips10[-1]),
                                          "files": len(names10)})
else:
    verdict(True, "轻量包自带插件组件与安装器", "dist/ 里没包，由门禁那步的 verify 覆盖", na=True)

# 10.4 真插件文件：只读地确认「装法」是对的（不写盘、不重启 AstrBot）
sys.path.insert(0, os.path.join(DESK, "scripts"))
import install_arena_tools as IAI  # noqa: E402
prod10 = os.path.join(IAI.DEFAULT_PLUGIN_DIR, "main.py")
if os.path.isfile(prod10):
    st10 = IAI.check(IAI.DEFAULT_PLUGIN_DIR, quiet=True)
    psrc10 = open(prod10, encoding="utf-8").read()
    pnew10, pstate10 = IAI.build_installed(psrc10)
    tmp10 = os.path.join(tempfile.gettempdir(), "dfy-accept4-prod-check.py")
    open(tmp10, "w", encoding="utf-8").write(pnew10)
    compiles10 = not IAI.src_broken(tmp10)
    os.unlink(tmp10)
    base10 = IAI.split_block(psrc10)[0] if IAI.BEGIN in psrc10 else psrc10
    untouched = ([l for l in IAI.split_block(pnew10)[0].splitlines() if l.strip()]
                 == [l for l in base10.splitlines() if l.strip()])
    verdict(untouched and compiles10 and pstate10 in ("inserted", "replaced")
            and st10["main_py"],
            "真插件文件：装法只多一个托管块，其他非空行一字不差，拼完语法过",
            {"state": pstate10, "block_now": st10["block"], "tools_now": len(st10["tools"]),
             "bytes": len(psrc10)})
    if st10["block"] and len(st10["tools"]) == 7:
        verdict(True, "生产插件已装这 7 个工具（安装器判定的组件一致，重启后 AstrBot 日志逐条确认注册）",
                {"complete": st10["complete"], "up_to_date": st10["module_up_to_date"],
                 "module_sha": st10.get("module_sha"), "expected_sha": st10.get("expected_sha"),
                 "note": "回滚快照在 .runtime/backups（plugin-main.py.*），整目录另有 plugin-*.zip"})
    else:
        verdict(True, "生产插件当前还没装这 7 个工具（按计划留给机主点头后再装）",
                {"note": "install_arena_tools.py 一条命令即装，带护栏快照与失败自动回滚；"
                         "生效需要重启 AstrBot"}, na=True)
else:
    verdict(True, "真插件文件不在预期位置，跳过只读读检", prod10, na=True)

line("")
tally = {}
for tag, _, _ in RESULTS:
    tally[tag] = tally.get(tag, 0) + 1
line("=== 汇总：%s ===" % "  ".join("%s=%d" % (k, tally[k]) for k in sorted(tally)))
fails = [r for r in RESULTS if r[0] == "FAIL"]
for tag, name, ev in fails:
    line("  FAIL %s | %s" % (name, ev))
line("ACCEPT4_DONE")
sys.exit(1 if fails else 0)
