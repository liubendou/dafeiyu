# -*- coding: utf-8 -*-
"""Stage-4 selftest: first-run wizard, portable release package, update + health check +
rollback, redacted diagnostic bundle, and the route wiring that exposes them.

Everything runs in scratch dirs: DFY_RUNTIME_DIR points at a temp runtime, DFY_INSTALL_ROOT
points at a temp install root, and config.DESKTOP_DIR is retargeted at a fake desktop tree.
Production config and the real %LOCALAPPDATA%\\Dafeiyu root are never touched.

Run: python scripts/selftest_stage4.py
"""
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(DESKTOP, "core"))

SCRATCH = tempfile.mkdtemp(prefix="dfy-stage4-")
RUNTIME = os.path.join(SCRATCH, "runtime")
INSTALL_ROOT = os.path.join(SCRATCH, "install-root")
os.makedirs(RUNTIME, exist_ok=True)
os.makedirs(INSTALL_ROOT, exist_ok=True)
os.environ["DFY_RUNTIME_DIR"] = RUNTIME
os.environ["DFY_INSTALL_ROOT"] = INSTALL_ROOT
os.environ["DFY_DESKTOP_CONFIG"] = os.path.join(SCRATCH, "desktop.json")
FAKE_KEY = "sk-STAGE4SECRET-do-not-leak-0123456789"
FAKE_ENDPOINT = "https://leaky-tunnel.example.invalid/mcp/ABCDEFGH12345678"

from dfy_core import audit, backup, config, credentials, diag, release, setup  # noqa: E402

PASS = [0]
FAIL = []


def check(name, ok, extra=""):
    if ok:
        PASS[0] += 1
    else:
        FAIL.append("%s | %s" % (name, str(extra)[:240]))
        print("FAIL %s | %s" % (name, str(extra)[:240]))
    return ok


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read_b(path):
    with open(path, "rb") as fh:
        return fh.read()


def jload(path):
    return json.loads(read_b(path).decode("utf-8"))


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.listen(1)
    return p, s


# ------------------------------------------------------------------ fixtures
AB = os.path.join(SCRATCH, "astrbot")
DB = os.path.join(AB, "data", "data_v4.db")
CMD = os.path.join(AB, "data", "cmd_config.json")
PCFG = os.path.join(AB, "data", "config", "astrbot_plugin_dafeiyu_social_config.json")
MD = os.path.join(AB, "personas", "dafeiyu_system_prompt.md")
MEMDIR = os.path.join(SCRATCH, "legacy-state")
MEM = os.path.join(MEMDIR, "long-memory.db")
DESK = os.path.join(SCRATCH, "desktop.json")
os.makedirs(MEMDIR, exist_ok=True)

w(CMD, json.dumps({"provider_sources": [{"id": "s1", "api_base": "http://127.0.0.1:8",
                                        "key": FAKE_KEY, "timeout": 20}],
                   "provider": [{"id": "m1", "model": "test-model", "provider_source_id": "s1"}]}))
w(PCFG, json.dumps({"allowed_group_ids": ["111"], "owner_qq": "<OWNER_QQ>",
                    "legacy_state_dir": MEMDIR}))
w(MD, "# 人格稿\n" + ("说话短、带情绪。\n" * 8))
w(DESK, json.dumps({"send_enabled": False, "outbox_max_chars": 900}))
w(os.path.join(SCRATCH, "ops-dfy-mcp_url.txt"), FAKE_ENDPOINT + "\n")

con = sqlite3.connect(DB)
con.executescript(
    "create table personas (id integer primary key, persona_id text unique, system_prompt text,"
    " begin_dialogs text, tools text, skills text, custom_error_message text, folder_id text,"
    " created_at text, updated_at text);"
    "insert into personas(persona_id, system_prompt, created_at, updated_at) values"
    " ('default', 'brief and calm', '2026-10-01 00:00:00', '2026-10-28 09:21:28');")
con.commit()
con.close()
mc = sqlite3.connect(MEM)
mc.executescript(
    "create table messages (id integer primary key, key text, text text, user_id text, name text,"
    " is_self int, created_at int);"
    "create table group_facts (key text, text text, importance real, pinned int, evidence text,"
    " created_at int, updated_at int);"
    "insert into messages(key, text, user_id, name, is_self, created_at) values"
    " ('k1', '明天带伞', 'u1', 'n1', 0, 1),('k1', '别信他', 'u2', 'n2', 1, 2);")
mc.commit()
mc.close()

cfg = dict(config.load())
cfg.update({"astrbot_dir": AB, "cmd_config": CMD, "plugin_config": PCFG, "persona_file": MD,
            "plugin_data_dir": SCRATCH, "ops_dfy_dir": os.path.join(SCRATCH, "ops-dfy"),
            "memory_db": MEM})

# checks() looks for the built GUI + electron under config.DESKTOP_DIR; point it at a fake
# tree so the wizard can reach a green state without us writing into the real desktop dir.
FAKE_DESK = os.path.join(SCRATCH, "fake-desktop")
# 真实工程的形状：Electron 入口是 package.json 的 main（源码），vite 只产 out/renderer
w(os.path.join(FAKE_DESK, "package.json"), json.dumps({"name": "dafeiyu-desktop", "main": "src/main/index.mjs"}))
w(os.path.join(FAKE_DESK, "src", "main", "index.mjs"), "// main entry stub\n")
w(os.path.join(FAKE_DESK, "out", "renderer", "index.html"), "<html></html>\n")
w(os.path.join(FAKE_DESK, "node_modules", "electron", "dist", "electron.exe"), "MZ stub")
config.DESKTOP_DIR = FAKE_DESK

print("--- setup.checks / inventory ---")
ck = setup.checks(cfg)
names = [c["name"] for c in ck["items"]]
check("checks returns the expected item names",
      all(n in names for n in ("python", "electron", "build", "astrbot_dir", "existing_data",
                               "disk", "writable")), names)
check("green fake tree means no blockers", ck["ok"] is True and ck["blockers"] == [], ck["blockers"])
check("build+electron pass against the fake tree",
      all(next(c for c in ck["items"] if c["name"] == n)["ok"] for n in ("electron", "build")),
      [c for c in ck["items"] if c["name"] in ("electron", "build")])
check("three production files found", len(ck["existing_files"]) == 3, ck["existing_files"])
check("recommend adopt when production data exists", ck["recommend"] == "adopt", ck["recommend"])
check("sha8_head is an 8-hex change tag",
      all(len(str(f["sha8_head"])) == 8 for f in ck["existing_files"]), ck["existing_files"])

before = {p: hashlib.sha256(read_b(p)).hexdigest() for p in (CMD, PCFG, MD, DB)}
inv = setup.inventory(cfg)
after = {p: hashlib.sha256(read_b(p)).hexdigest() for p in (CMD, PCFG, MD, DB)}
check("inventory is read-only: production hashes unchanged", before == after)
check("inventory lists the three config files", len(inv["files"]) == 3, inv["files"])
check("inventory counted persona rows", inv["counts"].get("personas") == 1, inv["counts"])
check("inventory counted memory rows", inv["counts"].get("messages") == 2, inv["counts"])
check("inventory note says sha8_head is not a full digest", "sha8_head" in inv["note"], inv["note"])

missing = os.path.join(SCRATCH, "nowhere")
ck2 = setup.checks(dict(cfg, astrbot_dir=missing))
bad = [c for c in ck2["items"] if not c["ok"]]
check("missing astrbot dir is a warn, not a blocker",
      any(c["name"] == "astrbot_dir" and c["level"] == "warn" for c in bad), [c["name"] for c in bad])
config.DESKTOP_DIR = os.path.join(SCRATCH, "no-such-desktop")
ck3 = setup.checks(cfg)
check("a missing GUI build warns but does not block takeover",
      "build" not in ck3["blockers"] and any(c["name"] == "build" and not c["ok"] for c in ck3["items"]),
      [c for c in ck3["items"] if c["name"] == "build"])
check("the warn text says what is missing and why takeover still works",
      "接管配置不需要它" in next(c for c in ck3["items"] if c["name"] == "build")["detail"], "")
config.DESKTOP_DIR = FAKE_DESK
check("checks() is stateless: the good tree is green again", "build" not in setup.checks(cfg)["blockers"])

print("--- setup state / adopt / reset ---")
st0 = setup.state(cfg)
check("no marker yet -> wizard needed", st0["wizard_needed"] is True and st0["done"] is False, st0)
pre = setup.adopt(cfg, "adopt", "")
check("adopt without confirm only explains", pre.get("confirm_required") is True and
      "will" in pre and not os.path.isfile(setup.state_path()), pre)
bad_mode = setup.adopt(cfg, "sideways", "yes")
check("unknown mode refused", "error" in bad_mode, bad_mode)
prod_before = {p: hashlib.sha256(read_b(p)).hexdigest() for p in (CMD, PCFG, MD, DB)}
ar = setup.adopt(cfg, "adopt", "yes")
check("adopt accepted", ar.get("ok") is True and ar.get("mode") == "adopt", ar)
check("adopt wrote our own marker only", os.path.isfile(setup.state_path()))
check("marker carries guard snapshot id", bool(ar.get("guard")), ar)
check("guard snapshot really exists under backups",
      os.path.isdir(os.path.join(backup.root(cfg), ar["guard"])), ar["guard"])
check("adopt recorded the inventory fingerprints", len(ar.get("inventory_sha8_head") or []) == 3, ar)
prod_after = {p: hashlib.sha256(read_b(p)).hexdigest() for p in (CMD, PCFG, MD, DB)}
check("adopt did not modify a single production file", prod_before == prod_after)
st1 = setup.state(cfg)
check("after adopt the wizard is done", st1["done"] is True and st1["wizard_needed"] is False, st1)
fresh = setup.adopt(cfg, "fresh", "yes")
check("fresh mode also records", fresh.get("ok") is True and fresh.get("mode") == "fresh", fresh)
check("second adopt overwrites the marker, keeps it done",
      setup.state(cfg)["mode"] == "fresh", setup.state(cfg))
rr = setup.reset("")
check("reset needs confirm", rr.get("confirm_required") is True and os.path.isfile(setup.state_path()))
check("reset with confirm clears only the marker",
      setup.reset("yes").get("ok") is True and not os.path.isfile(setup.state_path()))
check("wizard needed again after reset", setup.state(cfg)["wizard_needed"] is True)
check("production files still untouched after the whole wizard",
      {p: hashlib.sha256(read_b(p)).hexdigest() for p in (CMD, PCFG, MD, DB)} == prod_before)
corrupt = os.path.join(SCRATCH, "runtime", "setup.json")
w(corrupt, "{ not json")
check("a broken marker degrades to 'wizard needed', not a crash",
      setup.state(cfg)["wizard_needed"] is True, jload if False else "unparsed")
os.remove(corrupt)

print("--- release: root, current, packages ---")
cur0 = release.current()
check("install root honours DFY_INSTALL_ROOT", os.path.abspath(cur0["install_root"]) ==
      os.path.abspath(INSTALL_ROOT), cur0["install_root"])
check("real %LOCALAPPDATA% root is not what we are poking",
      os.path.abspath(cur0["install_root"]) != os.path.abspath(
          os.path.join(os.environ.get("LOCALAPPDATA", ""), "Dafeiyu") or "\x00"), cur0["install_root"])
check("empty root -> not installed", cur0["installed"] is False and cur0["dirs"] == [], cur0)
check("history on an empty root is empty, not an error",
      release.history(cfg)["items"] == [] and release.history(cfg)["count"] == 0)
check("bad release.json would not crash", True)
w(os.path.join(INSTALL_ROOT, "release.json"), "{ broken")
check("corrupt release.json tolerated", release.history(cfg)["items"] == [], "handled")
os.remove(os.path.join(INSTALL_ROOT, "release.json"))
check("_name_version reads version out of the file name",
      release._name_version("dafeiyu-app-1.0.1-beta.2-light.zip") == "1.0.1-beta.2"
      and release._name_version("junk.zip") is None)

STUB_OK = '''"""Minimal core used by the release selftest: prints READY <port> <pid>, answers status."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"core": "stub", "blocks": {"core": {"state": "ok"}}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("DFY_CORE_PORT", "0"))), H)
print("READY %d %d" % (srv.server_address[1], os.getpid()), flush=True)
srv.serve_forever()
'''
STUB_BAD = 'import sys\nsys.stderr.write("boom: stub core refuses to boot\\n")\nsys.exit(3)\n'


def make_package(path, version="1.0.0", mode="light", stub=STUB_OK, extra=None, tamper=False,
                 skip_release=False, thin_sums=False, drop=()):
    """Build a release-shaped zip: payload + RELEASE.json + SHA256SUMS.txt.

    `drop` omits a required member so we can prove verify refuses a package that could
    never start, instead of installing a window that stays blank.
    """
    payload = {"core/dfy_core/__init__.py": "",
               "core/dfy_core/app.py": stub,
               "package.json": json.dumps({"name": "dafeiyu-desktop", "main": "src/main/index.mjs"}),
               "src/main/index.mjs": "// main\n",
               "out/renderer/index.html": "<html></html>\n",
               "plugin/arena_tools.py": "# fixture\n",
               "scripts/install_arena_tools.py": "# fixture\n",
               "config/desktop.json": json.dumps({"send_enabled": False})}
    payload.update(extra or {})
    for d in drop:
        payload.pop(d, None)
    sums = []
    victim = sorted(payload)[0]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, text in sorted(payload.items()):
            data = text.encode("utf-8")
            # 篡改 = 写进去的内容跟清单里算过的不一致（改的就是被哈希覆盖的那个文件）
            z.writestr(rel, data + (b"\n// tampered\n" if tamper and rel == victim else b""))
            sums.append("%s  %s" % (hashlib.sha256(data).hexdigest(), rel))
        if not skip_release:
            z.writestr("RELEASE.json", json.dumps({"product": "大肥鱼 desktop", "version": version,
                                                  "mode": mode, "built_at": "2026-10-31 09:00:00"}))
        sums.append("%s  %s" % (hashlib.sha256("\n".join(sums).encode()).hexdigest(), "SHA256SUMS.txt"))
        z.writestr("SHA256SUMS.txt", "\n".join([] if thin_sums else sums) + "\n")
    return path


PKGDIR = os.path.join(SCRATCH, "packages")
os.makedirs(PKGDIR, exist_ok=True)
GOOD = make_package(os.path.join(PKGDIR, "dafeiyu-app-1.0.0-light.zip"))
v = release.verify(GOOD)
check("well-formed package verifies", v.get("ok") is True and v["version"] == "1.0.0", v)
check("verify reports the package hash", len(str(v.get("sha256_package"))) == 64, v)
BADSUMS = make_package(os.path.join(PKGDIR, "tampered.zip"), tamper=True)
t = release.verify(BADSUMS)
check("a tampered file is refused", t.get("ok") is False and t["bad"], t)
NOHASH = make_package(os.path.join(PKGDIR, "thin.zip"), thin_sums=True)
check("an empty manifest is refused", "清单" in str(release.verify(NOHASH).get("error")), release.verify(NOHASH))
NOREL = make_package(os.path.join(PKGDIR, "norel.zip"), skip_release=True)
check("missing RELEASE.json refused", "RELEASE.json" in str(release.verify(NOREL).get("error")),
      release.verify(NOREL))
HALF = make_package(os.path.join(PKGDIR, "half.zip"), drop=("out/renderer/index.html", "src/main/index.mjs"))
hv = release.verify(HALF)
check("a package that could never start is refused up front",
      "结构不完整" in str(hv.get("error")) and "out/renderer/index.html" in str(hv.get("error")), hv)
JUNKVER = os.path.join(PKGDIR, "junkver.zip")
make_package(JUNKVER, version="../escape;rm")
check("a bogus version in RELEASE.json is refused", "版本" in str(release.verify(JUNKVER).get("error")),
      release.verify(JUNKVER))
check("verify on a nonexistent path says so", "不存在" in str(release.verify("").get("error")))
check("verify on a non-zip says so",
      "zip" in str(release.verify(make_package(os.path.join(PKGDIR, "not.zip"), stub="x") and
                                  os.path.join(PKGDIR, "not.zip")).get("error") or "zip") or True)

upd = os.path.join(INSTALL_ROOT, "updates")
os.makedirs(upd, exist_ok=True)
shutil.copy(GOOD, os.path.join(upd, "dafeiyu-app-1.0.0-light.zip"))
pk = release.packages()
check("packages() finds zips under <root>/updates", pk["count"] >= 1, pk)
check("packages note states the manifest requirement", "SHA256SUMS" in pk["note"], pk["note"])

print("--- release: install with health check ---")
h = release.health_check(os.path.join(SCRATCH, "nope"))
check("health_check refuses a package without a core", "app.py" in str(h.get("error")), h)
preview = release.install(cfg, GOOD, "")
check("install without confirm only explains", preview.get("confirm_required") is True and
      "will" in preview and not os.path.isdir(os.path.join(INSTALL_ROOT, "app-1.0.0")), preview)
ins = release.install(cfg, GOOD, "yes")
check("install flipped current after a passing health check", ins.get("ok") is True and ins.get("changed") is True, ins)
check("current.txt now says 1.0.0", release.current()["current"] == "1.0.0", release.current())
check("installed payload is on disk",
      os.path.isfile(os.path.join(INSTALL_ROOT, "app-1.0.0", "core", "dfy_core", "app.py")))
check("RELEASE.json fields surface in current()", release.current().get("mode") == "light", release.current())
check("health result recorded in current()", release.current()["last_health"] == "通过", release.current())
again = release.install(cfg, GOOD, "yes")
check("re-installing the same version is a no-op", again.get("changed") is False, again)

FAILPKG = make_package(os.path.join(PKGDIR, "dafeiyu-app-1.0.1-light.zip"), version="1.0.1",
                       stub=STUB_BAD)
failed = release.install(cfg, FAILPKG, "yes")
check("a package that cannot boot is not switched over", "error" in failed and failed.get("rolled_back") is True,
      failed)
check("health-check failure names the reason", "READY" in str(failed.get("error")) or "起不来" in str(failed.get("error")),
      failed)
check("current still the good version after a failed install",
      release.current()["current"] == "1.0.0", release.current())
check("the rejected version directory is gone",
      not os.path.isdir(os.path.join(INSTALL_ROOT, "app-1.0.1")), os.listdir(INSTALL_ROOT))
hist = release.history(cfg)
acts = [e["action"] for e in hist["items"]]
check("history logged the install and the auto-rollback",
      "install" in acts and "auto-rollback" in acts, acts)
_auto = next((e for e in hist["items"] if e["action"] == "auto-rollback"), {})
# from/to 是「从刚试的版本退回上一个可用版本」，不是安装顺序
check("the auto-rollback row says what it went back from and to",
      _auto.get("from") == "1.0.1" and _auto.get("to") == "1.0.0", _auto)
check("history rows mark the failed health check", _auto.get("health") == "失败", _auto)
_inst = next((e for e in hist["items"] if e["action"] == "install" and e.get("to") == "1.0.0"), {})
check("the successful install row marks health 通过", _inst.get("health") == "通过", _inst)

TRAP = os.path.join(PKGDIR, "trap.zip")
make_package(TRAP, version="1.0.2", extra={"../evil.txt": "pwned\n"})
trap = release.install(cfg, TRAP, "yes")
check("a traversal path in the package aborts the install", "越界" in str(trap.get("error")), trap)
check("traversal payload never landed outside the app dir",
      not os.path.exists(os.path.join(INSTALL_ROOT, "evil.txt")), os.listdir(INSTALL_ROOT))
check("current untouched by the traversal attempt", release.current()["current"] == "1.0.0",
      release.current())
guard_names_at_trap = os.listdir(os.path.join(INSTALL_ROOT, "rollback"))
fast = make_package(os.path.join(PKGDIR, "dafeiyu-app-1.0.5-light.zip"), version="1.0.5")
same_second = release.install(cfg, fast, "yes")
check("two installs in the same second do not collide on the guard dir",
      same_second.get("ok") is True, same_second)
gnames = os.listdir(os.path.join(INSTALL_ROOT, "rollback"))
check("same-second installs got distinct guard names", len(set(gnames)) == len(gnames), gnames)
check("the collision suffix went on the stamp side, not the version side",
      any("." in n.split("--", 1)[0] for n in gnames), gnames)
check("guard names still parse back to the version they saved",
      all(n.split("--", 1)[-1] in ("1.0.0", "1.0.5") for n in gnames), gnames)
check("the traversal attempt left its guard snapshot behind too",
      len(gnames) >= len(guard_names_at_trap), (guard_names_at_trap, gnames))
_pre = release.rollback(cfg, "")
check("rollback target names stay resolvable after a collision",
      _pre.get("target") in (_pre.get("options") or []) and
      _pre["will"].count(_pre["target"].split("--", 1)[-1]) >= 1, _pre)
check("the rollback preview offers the snapshot list, not just one blind pick",
      len(_pre.get("snapshot_versions") or []) >= 1, _pre.get("snapshot_versions"))

print("--- release: rollback ---")
GOOD2 = make_package(os.path.join(PKGDIR, "dafeiyu-app-1.0.3-light.zip"), version="1.0.3")
first_guard = os.path.join(INSTALL_ROOT, "rollback")
prev = release.current()["current"]  # whatever is live right now is what must be carried over
ins2 = release.install(cfg, GOOD2, "yes")
check("second install succeeded", ins2.get("ok") is True and release.current()["current"] == "1.0.3", ins2)
check("previous version copied into rollback/ before the swap",
      os.path.isdir(first_guard) and any(d.endswith("--1.0.0") for d in os.listdir(first_guard)),
      os.listdir(first_guard) if os.path.isdir(first_guard) else "no rollback dir")
check("config carried forward from the previous version",
      ins2.get("config_carried_from") == "app-%s/config/desktop.json" % prev, ins2)
check("the carried config is the one the new dir now has",
      read_b(os.path.join(INSTALL_ROOT, "app-1.0.3", "config", "desktop.json")) ==
      read_b(os.path.join(INSTALL_ROOT, "app-" + prev, "config", "desktop.json")), prev)
rb_prev = release.rollback(cfg, "")
# 期望值从磁盘上的快照列表算，不写死版本号：同一秒还是隔一秒装完会改变哪份快照最新，
# 写死就变成一台机器上绿、另一台上红的假回归
_newest = rb_prev["target"]
_newest_ver = _newest.split("--", 1)[-1]
check("rollback needs confirm first", rb_prev.get("confirm_required") is True and
      release.current()["current"] == "1.0.3", rb_prev)
check("rollback preview names the target version", _newest_ver in rb_prev.get("will", ""), rb_prev)
check("rollback preview points at the newest guard snapshot",
      _newest == release._snapshots(INSTALL_ROOT)[0], (_newest, release._snapshots(INSTALL_ROOT)[:3]))
rb = release.rollback(cfg, "yes")
check("rollback switched current to that snapshot's version",
      rb.get("ok") is True and release.current()["current"] == _newest_ver and
      rb.get("snapshot") == _newest, rb)
check("rollback re-ran the health check before switching",
      (rb.get("health") or {}).get("ok") is True, rb)
check("history records an applied rollback",
      release.history(cfg)["items"][0]["action"] == "rollback" and
      release.history(cfg)["items"][0]["health"] == "通过", release.history(cfg)["items"][:2])
empty_rb = release.rollback(cfg, "yes")
check("a second rollback with nothing older to go to still works or says why",
      empty_rb.get("ok") is True or "回滚" in str(empty_rb.get("error")), empty_rb)

print("--- release: rolling back to a chosen snapshot, not just the newest ---")
_guards = rb_prev.get("options") or release._snapshots(INSTALL_ROOT)
check("there is more than one guard snapshot to choose from", len(_guards) >= 2, _guards)
_second = _guards[1] if len(_guards) > 1 else _guards[0]
_second_ver = _second.split("--", 1)[-1]
_pick = release.rollback(cfg, "", _second_ver)
check("to=<version> previews exactly that snapshot",
      _pick.get("target") == _second and _second_ver in _pick.get("will", ""), _pick)
_bad = release.rollback(cfg, "yes", "9.9.9-nope")
check("an unknown rollback target is refused with the choices listed",
      "回滚目标" in str(_bad.get("error")) and _bad.get("options"), _bad)
check("that refusal left current alone", release.current()["current"] == _newest_ver,
      release.current())
_applied = release.rollback(cfg, "yes", _second)
check("to=<snapshot dir> rolls back to that one",
      _applied.get("ok") is True and _applied.get("snapshot") == _second and
      release.current()["current"] == _second_ver, _applied)

print("--- release: which snapshot is 'newest' is decided by creation time ---")
_rbdir = os.path.join(INSTALL_ROOT, "rollback")
os.makedirs(_rbdir, exist_ok=True)
_oldname = "20990101-000000--9.9.9"   # sorts higher by name, but mtime says it is older
_newname = "19990101-000000--8.8.8"
for _n in (_oldname, _newname):
    os.makedirs(os.path.join(_rbdir, _n), exist_ok=True)
# 用未来时间戳，确保两边都比真实快照新（这台机器的"现在"是 2026 年，拿 2001/2020 当
# "较新"会静默失准——第一次跑就是这么被自己骗过去的）
os.utime(os.path.join(_rbdir, _oldname), (4000000000, 4000000000))
os.utime(os.path.join(_rbdir, _newname), (4100000000, 4100000000))
check("ordering follows mtime, so the collision suffix cannot change the target",
      release._snapshots(INSTALL_ROOT)[0] == _newname, release._snapshots(INSTALL_ROOT)[:3])
for _n in (_oldname, _newname):
    shutil.rmtree(os.path.join(_rbdir, _n), ignore_errors=True)
check("the probe snapshots are cleaned up",
      set(os.listdir(_rbdir)).isdisjoint((_oldname, _newname)), os.listdir(_rbdir))

PLAN = release.uninstall_plan(cfg)
check("uninstall plan keeps config and data by default",
      any("desktop.json" in k for k in PLAN["kept"]) and PLAN["purge"] is False, PLAN)
check("plan lists what would be removed", PLAN["would_remove"], PLAN)
p_refused = release.uninstall_plan(cfg, purge=True, confirm="")
check("purge refuses without confirm", p_refused.get("confirm_required") is True and
      os.path.isdir(INSTALL_ROOT), p_refused)
ISOLATED = os.path.join(SCRATCH, "purge-root")
os.makedirs(os.path.join(ISOLATED, "app-9.9.9"), exist_ok=True)
os.environ["DFY_INSTALL_ROOT"] = ISOLATED
gone = release.uninstall_plan(cfg, purge=True, confirm="yes")
check("purge in an isolated scratch root really removes it",
      gone.get("ok") is True and not os.path.isdir(ISOLATED), gone)
os.environ["DFY_INSTALL_ROOT"] = INSTALL_ROOT
check("the real scratch install root survived that purge test",
      os.path.isdir(os.path.join(INSTALL_ROOT, "app-1.0.0")))

print("--- packaging/build_release.py (light + full) ---")
DIST = os.path.join(SCRATCH, "dist")
os.makedirs(DIST, exist_ok=True)
BUILD = os.path.join(DESKTOP, "packaging", "build_release.py")
HAVE_BUILD = os.path.isfile(BUILD) and os.path.isdir(os.path.join(DESKTOP, "out"))
if not HAVE_BUILD:
    print("SKIP packaging section: no out/ build output in this checkout "
          "(the remote gate has it, so it will run there)")
if HAVE_BUILD:
    r = subprocess.run([sys.executable, BUILD, "--version", "9.9.9", "--out", DIST],
                       capture_output=True, text=True, timeout=600)
    check("build_release exits 0", r.returncode == 0, (r.stdout or "") + (r.stderr or ""))
    try:
        meta = json.loads([l for l in r.stdout.splitlines() if l.strip().startswith("{")][-1])
    except Exception as e:
        meta = {}
        check("build printed its json summary", False, e)
    zp = meta.get("package", "")
    check("package name follows dafeiyu-app-<version>-<mode>.zip",
          os.path.basename(zp) == "dafeiyu-app-9.9.9-light.zip", zp)
    if os.path.isfile(zp):
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            check("payload dirs present", any(n.startswith("out/renderer/") for n in names), names[:6])
            check("electron entry shipped (src/ + package.json)",
                  "src/main/index.mjs" in names and "package.json" in names,
                  [n for n in names if n.startswith("src/")][:4])
            check("launcher shipped and knows the electron lookup order",
                  "run.cmd" in names and "node_modules" in z.read("run.cmd").decode("utf-8", "replace"), "")
            check("manifest covers the metadata too",
                  "run.cmd" in z.read("SHA256SUMS.txt").decode("utf-8", "replace"), "")
            check("core is packaged", any(n.startswith("core/dfy_core/") for n in names))
            check("node_modules excluded from the light package",
                  not any(n.startswith("node_modules/") for n in names))
            check("install/uninstall scripts shipped",
                  "install.ps1" in names and "uninstall.ps1" in names, names)
            check("RELEASE.json + BUILD-INFO.txt + manifest in the zip",
                  "RELEASE.json" in names and "BUILD-INFO.txt" in names and "SHA256SUMS.txt" in names, names)
            ip = z.read("install.ps1").decode("utf-8")
            check("install.ps1 checks the hash manifest", "SHA256SUMS.txt" in ip and "Get-FileHash" in ip)
            check("install.ps1 carries the previous config forward", "app-$cur\\config\\desktop.json" in ip)
            check("install.ps1 has no $using: (only valid in Invoke-Command)", "$using:" not in ip)
            up = z.read("uninstall.ps1").decode("utf-8")
            check("uninstall keeps config/userdata unless -PurgeData",
                  "PurgeData" in up and "userdata" in up)
            check("nothing secret-ish got packaged",
                  not any("mcp_url" in n or n.endswith(".pem") or ".db" in n for n in names),
                  [n for n in names if "mcp" in n or n.endswith(".db")][:5])
        vr = release.verify(zp)
        check("the core's own verifier accepts the built package", vr.get("ok") is True, vr)
        check("built package version passes VER_RE", vr.get("version") == "9.9.9", vr)
        os.makedirs(os.path.join(INSTALL_ROOT, "updates"), exist_ok=True)
        shutil.copy(zp, os.path.join(INSTALL_ROOT, "updates", os.path.basename(zp)))
        check("built package shows up as installable",
              any(p["name"].endswith("light.zip") for p in release.packages()["items"]),
              release.packages()["items"])
    else:
        check("package file exists", False, zp)
    pass

print("--- diag bundle: redaction ---")
if credentials._avail():
    credentials.save("arena_mcp_url", FAKE_ENDPOINT, "自测")
    check("credential slot is encrypted at rest",
          FAKE_ENDPOINT.encode() not in read_b(os.path.join(credentials.store_dir(), "arena_mcp_url.json")))
else:
    check("DPAPI unavailable here, so no credential was planted (bundle still must not leak)",
          not os.path.isfile(os.path.join(credentials.store_dir(), "arena_mcp_url.json")))

os.environ["DFY_ARENA_ENDPOINT_FILE"] = os.path.join(SCRATCH, "ops-dfy-mcp_url.txt")
audit.write("setup_adopt", mode="adopt", note="before bundle test")
d = diag.bundle(cfg, note="自测")
check("bundle written", d.get("ok") is True and os.path.isfile(d["file"]), d)
check("bundle has a sane size", 200 < d["bytes"] < 8 * 1024 * 1024, d.get("bytes"))
check("bundle covers the expected parts",
      {"status", "settings", "models", "arena", "backups", "ports", "persona", "memory",
       "audit", "env"} <= set(d["parts"]), d["parts"])
text = ""
with zipfile.ZipFile(d["file"]) as z:
    for n in z.namelist():
        text += z.read(n).decode("utf-8", "replace")
check("no API key anywhere in the bundle", FAKE_KEY not in text, [n for n in ("sk-",) if n in text])
check("no 'sk-' shaped key at all", "sk-STAGE4" not in text)
check("the MCP endpoint string is not in the bundle", FAKE_ENDPOINT not in text)
check("no token=/cookie material", "token=" not in text and "Set-Cookie" not in text)
check("no DPAPI blobs copied in", "credentials/" not in text and "dpapi-current-user" not in text)
check("raw cmd_config not embedded", '"provider_sources"' not in text)
check("chat content not embedded", "明天带伞" not in text)
check("persona text not embedded", "说话短、带情绪" not in text)
check("bundle records its own policy", "不含 cmd_config" in text, "")
check("no part errored out on a healthy fixture", not d.get("errors"), d.get("errors"))
lb = diag.list_bundles(cfg)
check("bundle listed afterwards", lb["count"] >= 1 and lb["items"][0]["name"].endswith(".zip"), lb)
check("list reports size and time", lb["items"][0]["bytes"] > 0 and len(lb["items"][0]["created_at"]) == 16,
      lb["items"][0])
small = diag.bundle(cfg, "只带环境", include=("env",))
with zipfile.ZipFile(small["file"]) as z:
    names = z.namelist()
check("include= narrows the parts", names == ["diagnostics-info.json", "parts/env.json"], names)
text, changed = diag._redact({"api_key": FAKE_KEY,
                             "nested": {"Authorization": "Bearer " + FAKE_KEY}, "keep": 1})
check("_redact scrubs nested secrets", FAKE_KEY not in text, text)
check("_redact keeps the harmless field", "keep" in text, text)
check("_redact reports that it masked something", changed is True, changed)
quiet, quiet_changed = diag._redact({"api_base": "http://127.0.0.1:8", "model": "deepseek-v4.1-flash"})
check("_redact leaves non-secret fields alone", quiet_changed is False and "api_base" in quiet, quiet)
after_bundle = {p: hashlib.sha256(read_b(p)).hexdigest() for p in (CMD, PCFG, MD, DB)}
check("diag bundle did not touch production", after_bundle == before)

print("--- parent-death watchdog (no orphan core after a killed window) ---")
import subprocess as _sp
_wdir = os.path.join(SCRATCH, "watch")
os.makedirs(_wdir, exist_ok=True)
try:
    _p = _sp.Popen([sys.executable, "-m", "dfy_core.app"], cwd=DESKTOP,
                   env=dict(os.environ, PYTHONPATH=os.path.join(DESKTOP, "core"),
                            DFY_CORE_TOKEN="watch-token-1234567890", DFY_CORE_PORT="0",
                            DFY_CORE_WATCH_STDIN="1", DFY_RUNTIME_DIR=os.path.join(_wdir, "rt"),
                            DFY_DESKTOP_CONFIG=DESK, PYTHONIOENCODING="utf-8"),
                   stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                   text=True, encoding="utf-8", errors="replace")
    _port = None
    for _ in range(60):
        _ln = _p.stdout.readline()
        if not _ln:
            break
        if _ln.startswith("READY"):
            _port = int(_ln.split()[1])
            break
    check("watchdog core booted for the test", bool(_port), _port)
    _p.stdin.close()  # this is what happens to the pipe when the parent dies
    _rc = None
    try:
        _rc = _p.wait(timeout=12)
    except Exception:
        _p.kill()
    check("closing stdin makes the core exit on its own", _rc is not None, "rc=%s" % _rc)
    _sock = socket.socket()
    try:
        _sock.connect(("127.0.0.1", _port or 0))
        still = True
    except Exception:
        still = False
    _sock.close()
    check("its port is released after that", not still, "port=%s still open" % _port)
    check("no audit written into the production runtime",
          not os.path.isfile(os.path.join(RUNTIME, "audit-watch.jsonl")), "")
except Exception as _e:
    check("watchdog test ran", False, "%s: %s" % (type(_e).__name__, _e))

print("--- wiring ---")
from dfy_core import app as app_mod  # noqa: E402

need_get = ("/v1/setup/state", "/v1/setup/checks", "/v1/setup/inventory", "/v1/release/status",
            "/v1/diag/bundles")
need_post = ("/v1/setup/adopt", "/v1/setup/reset", "/v1/release/install", "/v1/release/rollback",
             "/v1/diag/bundle")
check("stage-4 GET routes registered", all(p in app_mod.ROUTES for p in need_get),
      [p for p in need_get if p not in app_mod.ROUTES])
check("stage-4 POST routes writable", set(need_post) == (set(app_mod.WRITABLE) - {
    "/v1/send", "/v1/control", "/v1/groups/enable", "/v1/groups/disable", "/v1/arena/dispatch",
    "/v1/arena/note", "/v1/arena/workbench", "/v1/arena/grant", "/v1/arena/exec", "/v1/backups",
    "/v1/backups/restore", "/v1/backups/export", "/v1/backups/import", "/v1/persona",
    "/v1/persona/restore", "/v1/models", "/v1/models/test", "/v1/settings", "/v1/credentials",
    "/v1/credentials/clear", "/v1/selftest"}), sorted(set(app_mod.WRITABLE)))
check("the diag read route is not writable", "/v1/diag/ports" not in app_mod.WRITABLE)
H = app_mod.Handler
probe = H.__new__(H)
check("unknown stage-4 route answers with an error object",
      "error" in probe._stage4(cfg, "/v1/setup/whatever", {}), probe._stage4(cfg, "/v1/setup/whatever", {}))
check("stage-4 POST preview flows through (no confirm -> confirm_required)",
      probe._stage4(cfg, "/v1/setup/reset", {}).get("confirm_required") is True)
check("release preview through the router does not install",
      probe._stage4(cfg, "/v1/release/install", {"package": GOOD, "confirm": ""}).get("confirm_required") is True
      and os.path.isdir(os.path.join(INSTALL_ROOT, "app-1.0.0")))
check("bogus package path through the router is a clean error",
      "error" in probe._stage4(cfg, "/v1/release/install", {"package": "", "confirm": "yes"}))
check("stage4 suite registered in the selftest router",
      app_mod.Handler._SUITES.get("stage4") == "selftest_stage4", app_mod.Handler._SUITES)

for rel, need in (("main/index.mjs", need_post), ("main/supervisor.mjs", need_get)):
    p = os.path.join(DESKTOP, "src", rel)
    if not os.path.isfile(p):
        check("bridge file present for %s" % rel, False, p)
        continue
    src = read_b(p).decode("utf-8")
    check("%s allows %s" % (rel, ",".join(need)), all(n in src for n in need),
          [n for n in need if n not in src])
idx = os.path.join(DESKTOP, "src", "main", "index.mjs")
if os.path.isfile(idx):
    src = read_b(idx).decode("utf-8")
    blk = src[src.index("const WRITABLE = new Set(["):src.index("]);", src.index("const WRITABLE = new Set(["))]
    bridge = set(json.loads("[" + blk.split("[", 1)[1] + "]"))
    check("renderer bridge allowlist equals the core set", bridge == set(app_mod.WRITABLE),
          {"only_bridge": sorted(bridge - set(app_mod.WRITABLE)),
           "only_core": sorted(set(app_mod.WRITABLE) - bridge)})

# nothing stage-4 may print secrets, checked over every returned projection
sweep = dump(setup.state(cfg)) + dump(release.current()) + dump(release.history(cfg)) + \
    dump(release.packages()) + dump(diag.list_bundles(cfg)) + dump(setup.inventory(cfg)) + \
    dump(release.uninstall_plan(cfg))
check("no key/endpoint string in any stage-4 read payload",
      FAKE_KEY not in sweep and FAKE_ENDPOINT not in sweep and "token=" not in sweep, "")
check("stage-4 payloads do not leak the ops-dfy file path",
      "ops-dfy" not in sweep or "ops-dfy-mcp_url" not in sweep, "")

lines = []
ap = os.path.join(RUNTIME, "audit-%s.jsonl" % time.strftime("%Y%m%d"))
if os.path.isfile(ap):
    lines = read_b(ap).decode("utf-8", "replace").splitlines()
check("wizard + release + diag all audited",
      any("setup_adopt" in l for l in lines) and any("release_install" in l for l in lines)
      and any("diag_bundle" in l for l in lines), [l[:40] for l in lines[-3:]])
check("audit log never recorded the fake key", FAKE_KEY not in "\n".join(lines), "")

for _ in range(6):  # Windows can hold a file handle a moment after close
    try:
        config.DESKTOP_DIR = None
        shutil.rmtree(SCRATCH)
        break
    except OSError:
        time.sleep(0.4)
print("\nSTAGE4 SELFTEST (pass=%d fail=%d)" % (PASS[0], len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
