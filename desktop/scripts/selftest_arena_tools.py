# -*- coding: utf-8 -*-
"""2b-ii 自检：插件那侧的 7 个 Arena 工具 + 核心的服务通道（link）。

三段：
  A) 起真核心，验 core-link.json 的生命周期与「独立服务 token 只开 6 条 arena 路由」；
  B) 用桩核心跑 plugin/arena_tools.py 的每个工具与权限层；
  C) 装到一份仿制插件文件上跑全流程（含回滚），若真插件在生产机上就只做一致性读检。
本文件不写生产配置，也不重启 AstrBot。

Run: python scripts/selftest_arena_tools.py
"""
import ast
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(DESK, "core")
PY = sys.executable

PASS = [0]
FAIL = []


def check(name, ok, extra=""):
    if ok:
        PASS[0] += 1
        print("PASS | %s %s" % (name, ("| " + str(extra)[:130]) if extra else ""))
    else:
        FAIL.append(name)
        print("FAIL | %s | %s" % (name, str(extra)[:260]))
    return ok


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(method, port, path, token=None, service=None, body=None, timeout=8):
    import urllib.error
    import urllib.request
    headers = {"Host": "127.0.0.1:%d" % port}
    if token:
        headers["X-Dfy-Token"] = token
    if service:
        headers["X-Dfy-Service-Token"] = service
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "{}")
        except ValueError:
            return e.code, {"raw": raw[:120]}
    except Exception as e:
        return 0, {"error": type(e).__name__}


# ============================================================ A) the core-side link
print("=== A) core 起的 .runtime/core-link.json 与其权限 ===")
RT = tempfile.mkdtemp(prefix="dfy-link-")
env = dict(os.environ, PYTHONPATH=CORE, DFY_CORE_TOKEN="gui-token-for-test",
           DFY_CORE_PORT=str(free_port()), DFY_RUNTIME_DIR=RT, PYTHONIOENCODING="utf-8",
           DFY_CORE_WATCH_STDIN="1",
           DFY_DESKTOP_CONFIG=os.environ.get("DFY_DESKTOP_CONFIG")
           or os.path.join(DESK, "config", "desktop.json"))
core = subprocess.Popen([PY, "-m", "dfy_core.app"], cwd=CORE, env=env,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                        errors="replace")
port = None
for _ in range(80):
    ln = core.stdout.readline()
    if not ln:
        break
    if ln.startswith("READY"):
        port = int(ln.split()[1])
        break
check("A0 核心起来了并报了端口", bool(port), port)

link_file = os.path.join(RT, "core-link.json")
doc = None
for _ in range(30):
    if os.path.isfile(link_file):
        try:
            doc = json.loads(open(link_file, encoding="utf-8").read())
        except (OSError, ValueError):
            doc = None
        if doc:
            break
    time.sleep(0.2)
check("A1 core-link.json 写出来了", isinstance(doc, dict), link_file)
check("A2 里面的端口/pid 与真进程一致",
      bool(doc) and doc.get("port") == port and doc.get("pid") == core.pid, doc)
check("A3 只登记那 6 条 arena 路由",
      bool(doc) and len(doc.get("routes") or ()) == 6
      and "/v1/arena/grant" not in (doc.get("routes") or []), (doc or {}).get("routes"))
stoken = (doc or {}).get("token") or ""
check("A4 服务 token 是 64 位十六进制且不同于 GUI token",
      len(stoken) == 64 and all(c in "0123456789abcdef" for c in stoken)
      and stoken != "gui-token-for-test", len(stoken))

code, body = http("GET", port, "/v1/arena/tasks", service=stoken)
check("A5 服务 token 能用 /v1/arena/tasks", code == 200, (code, body))
code, body = http("GET", port, "/v1/status", service=stoken)
check("A6 服务 token 读不到 /v1/status", code == 403 and body.get("reason") == "plugin_route",
      (code, body))
code, body = http("GET", port, "/v1/credentials", service=stoken)
check("A7 服务 token 读不到凭据", code == 403 and body.get("reason") == "plugin_route", (code, body))
code, body = http("POST", port, "/v1/arena/grant", service=stoken,
                  body={"minutes": 30, "confirm": "yes"})
check("A8 服务 token 不能给自己开执行授权", code == 403 and body.get("reason") == "plugin_route",
      (code, body))
code, body = http("GET", port, "/v1/arena/tasks", service="0" * 64)
check("A9 错的服务 token 一律拒", code == 403 and body.get("reason") == "service-token", (code, body))
code, body = http("GET", port, "/v1/status", token="gui-token-for-test")
check("A10 GUI token 照旧全开（没被这次改动影响）", code == 200, (code, list(body)[:4]))
code, body = http("GET", port, "/v1/status")
check("A11 两个 token 都不给仍然 403", code == 403 and body.get("reason") == "token", (code, body))

audit_txt = ""
for f in sorted(os.listdir(RT)):
    if f.startswith("audit-"):
        audit_txt += open(os.path.join(RT, f), encoding="utf-8", errors="replace").read()
check("A12 插件调用进了审计", '"plugin_call"' in audit_txt and '"via":"plugin"' in audit_txt,
      [l for l in audit_txt.splitlines() if "plugin_call" in l][:1])
check("A13 审计里没有服务 token，也没有被拒的请求内容",
      stoken not in audit_txt and "gui-token-for-test" not in audit_txt, len(audit_txt))

code, body = http("POST", port, "/v1/diag/bundle", token="gui-token-for-test",
                  body={"note": "selftest", "confirm": "yes"})
bundle = body.get("path") or body.get("file") or ""
if not os.path.isabs(str(bundle)):
    cand = os.path.join(RT, "diagnostics", os.path.basename(str(bundle or "")))
    bundle = cand if os.path.isfile(cand) else ""
check("A14 能生成诊断包", bool(bundle) and os.path.isfile(bundle), (code, body))
if bundle:
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
        leak = [n for n in names if "core-link" in n]
        hit = any(stoken in z.read(n).decode("utf-8", "replace") for n in names
                  if n.endswith(".json") or n.endswith(".txt") or n.endswith(".jsonl"))
    check("A15 诊断包里没有 core-link.json", not leak, names[:6])
    check("A16 诊断包里也没有服务 token", not hit, "")

print("--- A17 关 stdin（模拟强杀窗口）：link 文件必须跟着走 ---")
try:
    core.stdin.close()
except Exception:
    pass
gone = False
for _ in range(60):
    if not os.path.isfile(link_file):
        gone = True
        break
    time.sleep(0.2)
check("A17 核心一退出就删掉了指针文件", gone, link_file)
try:
    core.wait(timeout=10)
except Exception:
    core.kill()
shutil.rmtree(RT, ignore_errors=True)

# ============================================================ B) plugin-side module
print("=== B) plugin/arena_tools.py 对桩核心的行为 ===")
spec = importlib.util.spec_from_file_location("dfy_arena_tools",
                                              os.path.join(DESK, "plugin", "arena_tools.py"))
at = importlib.util.module_from_spec(spec)
spec.loader.exec_module(at)

HITS = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        HITS.append({"path": self.path, "method": self.command,
                     "svc": self.headers.get("X-Dfy-Service-Token"),
                     "gui": self.headers.get("X-Dfy-Token"),
                     "body": raw.decode("utf-8", "replace")})
        if self.path.startswith("/v1/arena/tasks"):
            out = {"items": [{"id": "A103", "status": "running", "title": "写周报"},
                             {"id": "A104", "status": "queued", "title": "整理截图"}]}
        elif self.path.startswith("/v1/arena/models"):
            out = {"items": [{"id": "deepseek-v4.1-flash", "note": "日常优先用这个"}]}
        elif self.path == "/v1/arena/dispatch":
            out = {"id": "A105", "queued": True}
        elif self.path == "/v1/arena/note":
            out = {"ok": True}
        elif self.path == "/v1/arena/exec":
            out = {"error": "arena_exec 未授权（默认关闭），请先在窗口里临时授权"}
        elif self.path == "/v1/arena/workbench":
            out = {"confirm_required": True, "will": "关闭 profile_3",
                   "note": "关实例会中断正在跑的浏览器任务"}
        else:
            out = {"error": "stub: unknown"}
        data = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _handle
    do_POST = _handle


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
stub_port = stub.server_address[1]
threading.Thread(target=stub.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()

SB = tempfile.mkdtemp(prefix="dfy-stub-")
os.makedirs(SB, exist_ok=True)
with open(os.path.join(SB, "core-link.json"), "w", encoding="utf-8") as fh:
    json.dump({"port": stub_port, "pid": os.getpid(), "token": "svc-token",
               "routes": list(at.ROUTES)}, fh)
os.environ["DFY_RUNTIME_DIR"] = SB
OWNER = {"owner_private": True, "sender_qq": "<OWNER_QQ>", "umo": "u"}
NOT_OWNER = {"owner_private": False, "sender_qq": "111", "umo": "g"}

HITS.clear()
denials = [at.run(t, NOT_OWNER) for t in ("tasks", "models", "dispatch", "note",
                                          "workbench_status", "workbench_action", "exec")]
check("B1 非主人/群聊：7 个工具全部拒绝",
      all(at.DENY_TEXT in d for d in denials), denials[:2])
check("B2 拒绝时一个请求都没发出去（权限层在传输之前）", HITS == [], HITS)

check("B3 tasks 渲染出条数与状态", "A103" in at.run("tasks", OWNER) and "2 条" in at.run("tasks", OWNER), "")
check("B4 调用带的是服务 token，不带 GUI token",
      HITS and HITS[-1]["svc"] == "svc-token" and HITS[-1]["gui"] is None, HITS[-1])
check("B5 models 把主人备注一起给出", "日常优先用这个" in at.run("models", OWNER), "")
check("B6 dispatch 走 POST 并把 mode 带上",
      at.run("dispatch", OWNER, task="写周报", mode="queue") and
      HITS[-1]["method"] == "POST" and json.loads(HITS[-1]["body"]).get("mode") == "queue", HITS[-1])
check("B7 dispatch 空任务不发请求", "task" in at.run("dispatch", OWNER, task="  ")
      and HITS[-1]["method"] == "POST", "")
n_before = len(HITS)
check("B8 mode 乱写会被挡住", "queue" in at.run("dispatch", OWNER, task="x", mode="teleport")
      and len(HITS) == n_before, "")
check("B9 exec 未授权时如实说失败（不假装成功）",
      "未成功" in at.run("exec", OWNER, cmd="dir"), "")
check("B10 插件侧从不调 /v1/arena/grant",
      all(h["path"].split("?")[0] != "/v1/arena/grant" for h in HITS), [h["path"] for h in HITS])
w = at.run("workbench_action", OWNER, action="close", instance="profile_3")
check("B11 需要确认的操作把「先确认」原话转达", "确认" in w, w[:80])
check("B12 非法 action 被本地挡掉", "open" in at.run("workbench_action", OWNER, action="nuke"), "")
check("B13 note 缺 model 时不发请求", "model" in at.run("note", OWNER, model="", note="x"), "")

long_payload = {"ok": True, "text": "长" * 5000}
check("B14 超长结果被截断并指回窗口", "已截断" in at.render(long_payload, cap=at.MAX_TEXT), "")
check("B15 未知工具不抛异常", "未知工具" in at.run("nope", OWNER), "")

bad = tempfile.mkdtemp(prefix="dfy-dead-")
with open(os.path.join(bad, "core-link.json"), "w", encoding="utf-8") as fh:
    json.dump({"port": 1, "pid": 999999, "token": "x" * 64}, fh)
out = at.post("/v1/arena/tasks", None, base=bad)
check("B16 核心没在跑时给的是人话，不是堆栈", out.get("ok") is False
      and "连不上" in str(out.get("error")) + str(out.get("ok")), out)
check("B17 指针文件缺失时明确说没找到", "core-link.json" in str(at.post("/v1/arena/tasks", None,
                                                                      base=tempfile.mkdtemp()).get("error")), "")
evil = tempfile.mkdtemp(prefix="dfy-evil-")
with open(os.path.join(evil, "core-link.json"), "w", encoding="utf-8") as fh:
    json.dump({"port": stub_port, "host": "attacker.example", "token": "svc-token"}, fh)
HITS.clear()
oe = at.post("/v1/arena/tasks", None, base=evil)
check("B18 就算指针文件写了别的 host，也只打 127.0.0.1",
      oe.get("ok") is True and len(HITS) == 1, (oe.get("error"), len(HITS)))
src = open(os.path.join(DESK, "plugin", "arena_tools.py"), encoding="utf-8").read()
imps = sorted({l.strip().split()[1].split(".")[0].rstrip(",") for l in src.splitlines()
               if l.strip().startswith(("import ", "from "))})
check("B19 插件组件只用标准库（不引 astrbot/requests）",
      set(imps) <= {"json", "os", "urllib", "time"}, imps)

for d in (SB, bad, evil):
    shutil.rmtree(d, ignore_errors=True)
stub.shutdown()

# ============================================================ C) installer
print("=== C) install_arena_tools 在仿制插件上的全流程 ===")
sys.path.insert(0, os.path.join(DESK, "scripts"))
import install_arena_tools as IAI  # noqa: E402

FIXTURE = '''from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star


class DafeiyuSocial(Star):
    def _is_owner_private(self, event):
        return True

    def _truncate(self, text, limit=None):
        return text

    @filter.llm_tool(name="dfy_context")
    async def dfy_context(self, event: AstrMessageEvent) -> str:
        """看看状态。"""
        return "ok"

    @filter.llm_tool(name="dfy_send")
    async def dfy_send(self, event: AstrMessageEvent, text: str) -> str:
        """发出去。"""
        return "sent"
'''

box = tempfile.mkdtemp(prefix="dfy-plugin-")
pkg = os.path.join(box, "astrbot_plugin_dafeiyu_social")
os.makedirs(pkg)
RT2 = os.path.join(box, ".runtime")
os.makedirs(os.path.join(RT2, "backups"))
os.environ["DFY_RUNTIME_DIR"] = RT2
orig = FIXTURE
main_py = os.path.join(pkg, "main.py")
open(main_py, "w", encoding="utf-8", newline="\n").write(orig)
open(os.path.join(box, "orig.py"), "w", encoding="utf-8", newline="\n").write(orig)

new_text, state = IAI.build_installed(orig)
check("C1 计划里的插入方式", state == "inserted", state)
check("C2 七个工具名都在", all(('name="%s"' % t) in new_text for t in IAI.TOOL_NAMES), "")
check("C3 除托管块外一个字没动",
      [l for l in IAI.split_block(new_text)[0].splitlines() if l.strip()]
      == [l for l in orig.splitlines() if l.strip()], "")
check("C3b 少传参数也先过权限关，不该报 TypeError",
      "不能为空" in at.run("dispatch", OWNER) and "不能为空" in at.run("note", OWNER), 
      (at.run("dispatch", OWNER)[:40], at.run("note", OWNER)[:40]))
tree = ast.parse(new_text)
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
names = {n.name for n in cls.body if isinstance(n, ast.AsyncFunctionDef)}
check("C4 七个 async 工具方法确实落在类里", set(IAI.TOOL_NAMES) <= names, sorted(names)[:8])
tmp_py = os.path.join(box, "syntax-check.py")
open(tmp_py, "w", encoding="utf-8").write(new_text)
check("C5 拼出来的文件语法过得了", not IAI.src_broken(tmp_py), tmp_py)

plan = IAI.install(pkg, dry=True)
check("C6 --dry-run 不落盘", plan.get("dry_run") and os.path.isfile(main_py)
      and open(main_py, encoding="utf-8").read() == orig, plan)
res = IAI.install(pkg)
check("C7 安装成功并给出重启提示", res.get("ok") and res.get("restart_required"), res)
check("C8 装完能 check 通过", IAI.check(pkg, quiet=True)["complete"], IAI.check(pkg, quiet=True))
check("C9 组件文件同步过去且哈希一致", IAI.check(pkg, quiet=True)["module_up_to_date"], "")
_snaps = sorted(os.listdir(os.path.join(RT2, "backups")))
check("C10 写之前留了护栏快照，内容就是被改前的原文",
      len(_snaps) == 1 and IAI.sha256(os.path.join(RT2, "backups", _snaps[0]))
      == IAI.sha256(os.path.join(box, "orig.py")) if os.path.isfile(os.path.join(box, "orig.py"))
      else False, _snaps)
res2 = IAI.install(pkg)
after = open(main_py, encoding="utf-8").read()
check("C11 重复安装是幂等的（块只有一份）", after.count(IAI.BEGIN) == 1 and res2.get("ok"), after.count(IAI.BEGIN))
check("C12 没东西可改就一个字都不写、快照也不多发",
      res2.get("changed") is False and len(os.listdir(os.path.join(RT2, "backups"))) == 1,
      (res2.get("changed"), os.listdir(os.path.join(RT2, "backups"))))

# 写坏一次：语法检查失败 → 自动回滚
pre = IAI.sha256(main_py)
real_compile = IAI.src_broken


def fake_broken(path):
    return True


IAI.src_broken = fake_broken
glue_old = IAI.GLUE
IAI.GLUE = glue_old.replace(IAI.END, "    # 一次有实质变化的重装\n" + IAI.END)
res3 = IAI.install(pkg)
IAI.GLUE = glue_old
IAI.src_broken = real_compile
check("C13 自检不过时报错而不是硬塞进生产", res3.get("ok") is False, res3)
check("C14 失败后 main.py 自己回到了原样", IAI.sha256(main_py) == pre, (IAI.sha256(main_py), pre))

u = IAI.uninstall(pkg)
txt = open(main_py, encoding="utf-8").read()
check("C15 卸载把托管块摘干净", u.get("had_block") and IAI.BEGIN not in txt
      and all(t not in txt for t in IAI.TOOL_NAMES), "")
check("C16 卸载后原有工具还在", "dfy_context" in txt and "dfy_send" in txt, "")

prod = os.path.join(os.path.expanduser("~"), "astrbot", "data", "plugins",
                    "astrbot_plugin_dafeiyu_social", "main.py")
if os.path.isfile(prod):
    psrc = open(prod, encoding="utf-8").read()
    pnew, pstate = IAI.build_installed(psrc)
    ptmp = os.path.join(box, "prod-syntax-check.py")
    open(ptmp, "w", encoding="utf-8").write(pnew)
    # 装过之后真文件本身已含托管块：两边都先剥掉块再比，才比的是"我们没碰的部分"
    base = IAI.split_block(psrc)[0] if IAI.BEGIN in psrc else psrc
    check("C17 对真插件文件（只读）：只多一块，其他非空行一字不差",
          [l for l in IAI.split_block(pnew)[0].splitlines() if l.strip()]
          == [l for l in base.splitlines() if l.strip()]
          and pstate in ("inserted", "replaced"),
          (pstate, len(psrc)))
    check("C20 没装过就该有一处改动，装过就该逐字节无事可做",
          (pstate != "inserted") == (pnew == psrc), (pstate, pnew == psrc))
    check("C18 真插件文件拼上托管块之后语法仍然过",
          not IAI.src_broken(ptmp), ptmp)
    check("C19 七个工具与生产文件里的 self 助手都在（_is_owner_private/_truncate）",
          all(('name="%s"' % t) in pnew for t in IAI.TOOL_NAMES)
          and "_is_owner_private" in psrc and "_truncate" in psrc, "")
else:
    check("C17 本机没有生产插件文件，跳过真文件读检", True, prod)

shutil.rmtree(box, ignore_errors=True)
print("\nARENA TOOLS SELFTEST (pass=%d fail=%d)" % (PASS[0], len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
