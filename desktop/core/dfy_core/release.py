# -*- coding: utf-8 -*-
"""Update, health check, rollback (design §7.4、§8.3、§8.4、阶段4).

Layout under the install root (default `%LOCALAPPDATA%\\Dafeiyu`, overridable so the
lifecycle can be exercised in a temp dir without touching a real install):

    app-<version>/          one directory per unpacked package
    current.txt             which app-* the launcher uses
    release.json            history: what went in, whether the health check passed
    rollback/<ts>/          the previous app-* copied aside before a swap

Rules we keep: never install over an unverified package, always snapshot before a
swap, run a real health check against the *staged* copy before flipping `current`, and
flip it back automatically when the health check fails. No silent upgrades of the bot
engine: this only ever moves our own app directory.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

from . import audit, config, security

VER_RE = re.compile(r"^\d+\.\d+\.\d{1,4}(-[a-z0-9.]{1,20})?$")
SUMS = "SHA256SUMS.txt"
MIN_FILES = 3
# 少一个就没有一个能启动的窗口/核心：验包阶段直接拒，别等装完再发现
REQUIRED_MEMBERS = ("RELEASE.json", SUMS, "package.json", "core/dfy_core/app.py",
                    "src/main/index.mjs", "out/renderer/index.html",
                    # 2b-ii：包必须自带插件组件与它的安装器，否则新机器装完没有 Arena 工具
                    "plugin/arena_tools.py", "scripts/install_arena_tools.py")


def root():
    r = os.environ.get("DFY_INSTALL_ROOT")
    if r:
        return os.path.abspath(os.path.expanduser(r))
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "Dafeiyu")


def _log_path():
    return os.path.join(root(), "release.json")


def _read_json(path):
    """Read a json doc we own; return (None, reason) instead of raising into the UI."""
    if not os.path.isfile(path):
        return None, "missing"
    try:
        return json.loads(open(path, encoding="utf-8").read()), None
    except Exception as e:
        return None, type(e).__name__


def _read_log():
    doc, err = _read_json(_log_path())
    if err or not isinstance(doc, dict):
        return {"history": []}
    doc.setdefault("history", [])
    return doc



def _write_log(doc):
    os.makedirs(root(), exist_ok=True)
    tmp = _log_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, indent=1))
    os.replace(tmp, _log_path())


def current():
    r = root()
    cur = None
    p = os.path.join(r, "current.txt")
    if os.path.isfile(p):
        cur = open(p, encoding="utf-8").read().strip() or None
    info = {"install_root": r, "current": cur, "installed": bool(cur and os.path.isdir(os.path.join(r, "app-" + cur)))}
    if info["installed"]:
        rel = os.path.join(r, "app-" + cur, "RELEASE.json")
        doc, err = _read_json(rel)
        if not err:
            info["version"] = doc.get("version")
            info["built_at"] = doc.get("built_at")
            info["channel"] = doc.get("channel")
            info["mode"] = doc.get("mode")
    hist = _read_log().get("history") or []
    last = (hist[-1].get("health") or {}) if hist else {}
    # 界面要的是「通过/失败」，不是内部那坨诊断；细节去 history 里看
    info["last_health"] = (None if not hist else ("通过" if last.get("ok") else "失败"))
    if hist and not last.get("ok"):
        info["last_health_error"] = last.get("error")
    info["dirs"] = sorted(d[4:] for d in os.listdir(r) if d.startswith("app-")) if os.path.isdir(r) else []
    info["note"] = "只动本机应用目录，绝不动 AstrBot 或 QQ 接入层"
    return info


def packages():
    """Candidate zips: the ones we just built in dist/, and anything dropped into
    <install root>/updates/."""
    found = []
    desk = getattr(config, "DESKTOP_DIR", None) or os.getcwd()
    for d in (os.path.join(desk, "dist"), os.path.join(root(), "updates")):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d), reverse=True):
            if fn.lower().endswith(".zip"):
                p = os.path.join(d, fn)
                found.append({"file": p, "name": fn, "bytes": os.path.getsize(p),
                              "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    cur = current().get("current")
    for f in found:
        # zip name shape: dafeiyu-app-<version>-<mode>.zip -> compare the version inside the name
        f["not_current"] = bool(cur and _name_version(f["name"]) != cur)
        f["same_as_current"] = bool(cur and _name_version(f["name"]) == cur)
    return {"items": found[:20], "count": len(found),
            "note": "包必须自带 SHA256SUMS.txt 与 RELEASE.json，否则拒绝安装"}


def _name_version(fn):
    """Pull the version out of a release zip filename, or None."""
    stem = os.path.basename(str(fn or ""))[:-4]
    m = re.search(r"(\d+\.\d+\.\d{1,4}(?:-[a-z0-9.]{1,20})?)", stem)
    return m.group(1) if m else None


def verify(path):
    p = str(path or "").strip()
    if not p or not os.path.isfile(p):
        return {"error": "包文件不存在"}
    try:
        z = zipfile.ZipFile(p)
    except Exception as e:
        return {"error": "打不开这个 zip：%s" % type(e).__name__}
    with z:
        names = z.namelist()
        if SUMS not in names:
            return {"error": "包里缺 %s，不敢装" % SUMS}
        lack = [m for m in REQUIRED_MEMBERS if m not in names]
        if lack:
            return {"error": "包结构不完整，装上也起不来：缺 %s" % "、".join(lack)}
        try:
            rel = json.loads(z.read("RELEASE.json").decode("utf-8"))
        except Exception as e:
            return {"error": "RELEASE.json 读不了：%s" % type(e).__name__}
        ver = str(rel.get("version") or "")
        if not VER_RE.match(ver):
            return {"error": "包里的版本号不合法：%r" % ver}
        sums = {}
        for line in z.read(SUMS).decode("utf-8", "replace").splitlines():
            line = line.strip().lstrip("*")
            if not line:
                continue
            h, _, name = line.partition(" ")
            sums[name.strip().replace("\\", "/")] = h.lower()
        if len(sums) < MIN_FILES:
            return {"error": "校验清单只有 %d 行，太少了" % len(sums)}
        bad, missing = [], []
        for name, want in sums.items():
            if name == SUMS or name.startswith("node_modules/"):
                continue
            if name not in names:
                missing.append(name)
                continue
            h = hashlib.sha256()
            try:
                with z.open(name) as fh:
                    for chunk in iter(lambda: fh.read(1 << 16), b""):
                        h.update(chunk)
            except Exception as e:
                bad.append({"name": name, "why": type(e).__name__})
                continue
            if h.hexdigest() != want:
                bad.append({"name": name, "why": "哈希不一致"})
        corrupt = z.testzip()
        if corrupt:
            bad.append({"name": corrupt, "why": "zip 内部损坏"})
        return {"ok": not bad and not missing, "version": ver, "mode": rel.get("mode"),
                "built_at": rel.get("built_at"), "files": len(sums), "bad": bad or None,
                "missing": missing or None, "sha256_package": _sha_file(p),
                "note": None if not (bad or missing) else "有文件对不上校验清单，拒绝"}


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def health_check(target_dir, timeout=25):
    """Boot the staged core on its own port and a throw-away runtime dir, ask /v1/status.

    The engine itself is never restarted here — this starts our own python core, which
    is read-only by construction.
    """
    core = os.path.join(target_dir, "core")
    entry = os.path.join(core, "dfy_core", "app.py")
    if not os.path.isfile(entry):
        return {"ok": False, "error": "包里没有 core/dfy_core/app.py"}
    import tempfile
    tmp = tempfile.mkdtemp(prefix="dfy-health-")
    token = "health-%d" % int(time.time())
    env = dict(os.environ)
    env.update({"DFY_CORE_PORT": "0", "DFY_CORE_TOKEN": token, "DFY_RUNTIME_DIR": tmp,
                "PYTHONPATH": core, "PYTHONIOENCODING": "utf-8"})
    cwd = os.path.dirname(entry)
    try:
        proc = subprocess.Popen([sys.executable, "-m", "dfy_core.app", "--config-dir",
                                 os.path.join(target_dir, "config")],
                                env=env, cwd=target_dir, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace")
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "error": "起不来：%s" % type(e).__name__}
    port = None
    try:
        for _ in range(40):
            ln = proc.stdout.readline()
            if not ln:
                break
            if ln.startswith("READY"):
                port = int(ln.split()[1])
                break
        if not port:
            return {"ok": False, "error": "没有打印 READY 行（核心没起来）"}
        req = urllib.request.Request("http://127.0.0.1:%d/v1/status" % port)
        req.add_header("X-Dfy-Token", token)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.loads(r.read().decode("utf-8", "replace"))
        ok = isinstance(doc, dict) and ("core" in doc or "blocks" in doc)
        return {"ok": bool(ok), "http": 200, "keys": sorted(doc)[:6] if isinstance(doc, dict) else None}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:140])}
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=6)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def install(cfg, package="", confirm=""):
    ver_check = verify(package)
    if ver_check.get("error"):
        return ver_check
    if not ver_check.get("ok"):
        return {"error": "校验没过：%s" % json.dumps({"bad": ver_check.get("bad"),
                                                      "missing": ver_check.get("missing")}, ensure_ascii=False)[:220]}
    ver = ver_check["version"]
    r = root()
    cur = current().get("current")
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True,
                "will": "把 %s 解到 %s，先做健康检查再切 current；现在这版会先复制到 rollback/ 里存着%s"
                        % (os.path.basename(package), os.path.join(r, "app-" + ver),
                           "" if not cur else "（当前版本 %s）" % cur),
                "version": ver, "mode": ver_check.get("mode")}
    if cur and cur == ver and os.path.isdir(os.path.join(r, "app-" + ver)):
        return {"ok": True, "changed": False, "note": "这个版本已经装好了，不重复解"}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    guard = None
    if cur and os.path.isdir(os.path.join(r, "app-" + cur)):
        # "--" separator, because a version can itself contain a dash (1.0.1-beta.2)
        # suffix goes on the stamp side: rollback parses the version out of "--<ver>",
        # so mangling the tail would silently roll back to a version that does not exist
        guard_name, n = "%s--%s" % (stamp, cur), 1
        while os.path.exists(os.path.join(r, "rollback", guard_name)):
            guard_name = "%s.%d--%s" % (stamp, n, cur)
            n += 1
        guard = os.path.join(r, "rollback", guard_name)
        os.makedirs(os.path.dirname(guard), exist_ok=True)
        shutil.copytree(os.path.join(r, "app-" + cur), guard, symlinks=True)
    dst = os.path.join(r, "app-" + ver)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)
    try:
        with zipfile.ZipFile(package) as z:
            for name in z.namelist():
                if name.endswith("/"):
                    continue
                safe = os.path.normpath(name).replace("\\", "/")
                if safe.startswith("..") or os.path.isabs(safe):
                    shutil.rmtree(dst, ignore_errors=True)
                    return {"error": "包里有越界路径，已中止并且没切 current：%s" % safe}
                out = os.path.join(dst, *safe.split("/"))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "wb") as fh:
                    fh.write(z.read(name))
    except Exception as e:
        shutil.rmtree(dst, ignore_errors=True)
        return {"error": "解包失败：%s（current 没动）" % type(e).__name__}
    # 把用户当前生效的配置带到新版本目录：core 读的是 <app 目录>/config/desktop.json，
    # 不带的后果就是升级一次配置被打回出厂值。带了之后健康检查用的也正是这份。
    carried = None
    if cur and cur != ver:
        src_cfg = os.path.join(r, "app-" + cur, "config", "desktop.json")
        dst_cfg = os.path.join(dst, "config", "desktop.json")
        if os.path.isfile(src_cfg):
            try:
                os.makedirs(os.path.dirname(dst_cfg), exist_ok=True)
                shutil.copy2(src_cfg, dst_cfg)
                carried = "app-%s/config/desktop.json" % cur
            except Exception:
                carried = None
    hc = health_check(dst)
    log = _read_log()
    entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "install", "from": cur,
             "to": ver, "package": os.path.basename(package), "package_sha256": ver_check.get("sha256_package"),
             "guard": os.path.relpath(guard, r) if guard else None, "health": hc}
    log["history"].append(entry)
    if not hc.get("ok"):
        log["history"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "auto-rollback",
                              "from": ver, "to": cur, "why": hc.get("error") or "health check failed"})
        _write_log(log)
        shutil.rmtree(dst, ignore_errors=True)
        audit.write("release_install_failed", to=ver, error=str(hc.get("error"))[:120])
        return {"error": "新版健康检查没过，已经把它扔掉、current 保持 %s：%s" % (cur or "（无）", hc.get("error")),
                "rolled_back": True}
    open(os.path.join(r, "current.txt"), "w", encoding="utf-8", newline="\n").write(ver + "\n")
    _write_log(log)
    audit.write("release_install", to=ver, health=True, guard=entry["guard"])
    return {"ok": True, "changed": True, "version": ver, "health": hc, "guard": entry["guard"],
            "config_carried_from": carried, "restart_required": True,
            "note": "current.txt 已切到 %s；下次启动窗口就用它（我没替你重启 GUI）%s"
                    % (ver, "，配置从 %s 带过来了" % carried if carried else "")}


def _snapshots(r):
    """Rollback snapshots, newest first.

    Ordered by the directory's own mtime, not by its name: guard names carry a
    collision suffix (<stamp>.1--<ver>), and if that string decides which snapshot
    counts as "the newest", the rollback target silently depends on whether the
    machine happened to install two versions inside the same second. Creation time is
    the fact we mean; the name only breaks ties within one second."""
    rb = os.path.join(r, "rollback")
    if not os.path.isdir(rb):
        return []
    names = [d for d in os.listdir(rb) if os.path.isdir(os.path.join(rb, d))]

    def key(d):
        try:
            ts = os.path.getmtime(os.path.join(rb, d))
        except OSError:
            ts = 0.0
        return (ts, d)

    return sorted(names, key=key, reverse=True)


def rollback(cfg, confirm="", to=""):
    """Point current.txt back at a guard snapshot. Default target is the most recent
    one; `to` picks a specific snapshot (its directory name or a bare version) when
    the newest is not the one you want -- the preview lists what exists either way."""
    r = root()
    cur = current().get("current")
    rb = os.path.join(r, "rollback")
    opts = _snapshots(r)
    if not cur:
        return {"error": "现在没有已安装版本，回滚个啥", "options": opts[:6]}
    if not opts:
        return {"error": "没有可回滚的快照（rollback/ 是空的）"}
    pick = opts[0]
    if to:
        want = str(to).strip()
        if want in opts:
            pick = want
        else:
            hits = [d for d in opts if d.split("--", 1)[-1] == want.lstrip("v")]
            if not hits:
                return {"error": "没有这个回滚目标：%s（可选：%s）"
                        % (want, ", ".join(opts[:6]) or "无"), "options": opts[:6]}
            pick = hits[0]
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True, "will": "把 current 从 %s 换回 %s（快照在 rollback/%s）"
                % (cur, pick.split("--", 1)[-1], pick), "options": opts[:6], "target": pick,
                "snapshot_versions": [d.split("--", 1)[-1] for d in opts[:6]],
                "note": "只切 current.txt；切之前对目标版本再跑一次健康检查，不通过就不切。"
                        "想退到更早那版就带 to=<快照目录名或版本号>"}
    src = os.path.join(rb, pick)
    ver = pick.split("--", 1)[-1]
    dst = os.path.join(r, "app-" + ver)
    if not os.path.isdir(dst):
        shutil.copytree(src, dst, symlinks=True)
    hc = health_check(dst)
    log = _read_log()
    if not hc.get("ok"):
        log["history"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "rollback",
                              "from": cur, "to": ver, "health": hc, "applied": False})
        _write_log(log)
        return {"error": "回滚目标自己也健康检查失败，current 保持 %s：%s" % (cur, hc.get("error"))}
    open(os.path.join(r, "current.txt"), "w", encoding="utf-8", newline="\n").write(ver + "\n")
    log["history"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "rollback",
                          "from": cur, "to": ver, "health": hc, "applied": True})
    _write_log(log)
    audit.write("release_rollback", to=ver, health=True)
    return {"ok": True, "from": cur, "to": ver, "health": hc, "restart_required": True,
            "snapshot": pick, "note": "current 已换回 %s（快照 %s）" % (ver, pick)}


def history(cfg=None):
    log = _read_log()
    out = []
    for e in reversed((log.get("history") or [])[:40]):
        out.append({"at": e.get("at"), "action": e.get("action"), "from": e.get("from"),
                    "to": e.get("to"), "package": e.get("package"),
                    "health": "通过" if (e.get("health") or {}).get("ok") else "失败",
                    "health_error": (e.get("health") or {}).get("error"),
                    "guard": e.get("guard")})
    return {"items": out, "count": len(out), "install_root": root(),
            "current": current().get("current")}


def uninstall_plan(cfg, purge=False, confirm=""):
    """§8.4: deleting the program keeps the data unless you say otherwise, and purge
    has to name the directories it is about to remove. A machine that never installed
    the app still gets a readable plan: the settings page must not dead-end on it."""
    r = root()
    exists = os.path.isdir(r)
    # 破坏性动作先要确认，哪怕这一台机器上它其实是空操作：留一条规则比留两条好记
    if purge and str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True, "exists": exists, "path": r,
                "will": "彻底删除：%s。这会连配置和本地数据一起删掉，删之前请先导一份加密备份" % r}
    app_dirs = [d for d in os.listdir(r) if d.startswith("app-")] if exists else []
    keep = config.runtime_dir()
    plan = {"install_root": r, "exists": exists,
            "would_remove": ["app-* (%d)" % len(app_dirs), "current.txt",
                             "release.json", "rollback/*"],
            "kept": ["config/desktop.json（你的配置）", "运行时数据目录 %s" % keep],
            "purge": bool(purge)}
    if purge:
        plan["would_remove"].append("整个 %s（含配置与本地数据，不可恢复）" % r)
        plan["kept"] = []
        if not exists:
            return {"ok": True, "removed": False,
                    "note": "没有安装目录 %s，本来就没东西要删；配置与运行时数据在别处，没动" % r}
        shutil.rmtree(r, ignore_errors=True)
        audit.write("release_uninstall", purge=True, removed=r)
        return {"ok": True, "purged": r, "note": "目录已删；QQ 与 AstrBot 本来就不归这个 App 管，没动它们"}
    plan["note"] = ("卸载只删程序目录，配置和数据留着（§8.4）；要连数据一起删得勾「彻底删除」并再确认一次"
                    + ("" if exists else "（这台机器还没装过，没有可删的程序目录）"))
    return plan
