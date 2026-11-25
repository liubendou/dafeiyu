"""Stage-1 self-test: spawn the core on its own and assert the three new routes.

Runs against real production data, read-only. Exits non-zero on any failed
assertion, so verify.ps1 can gate on it.
"""
import json, os, subprocess, sys, tempfile, time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(DESK, "core"))
TOKEN = "selftest-%d" % int(time.time() * 1000)

env = dict(os.environ)
# 读的是生产数据，但 runtime 目录必须另开一个：审计、备份、core-link.json 都写在
# runtime 里，测试起的核心不该往生产 .runtime 里落任何东西（这条是 2b-ii 现场查出来的）
RT = tempfile.mkdtemp(prefix="dfy-selftest-stage1-")
env.update({"DFY_CORE_TOKEN": TOKEN, "DFY_CORE_PORT": "0", "DFY_RUNTIME_DIR": RT,
            "PYTHONPATH": os.path.join(DESK, "core")})
proc = subprocess.Popen([sys.executable, "-m", "dfy_core.app"], cwd=os.path.join(DESK, "core"),
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
fails, notes = [], []


def check(name, cond, extra=""):
    (notes if cond else fails).append("%s%s" % (name, (" — " + extra) if extra else ""))
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" | " + str(extra)) if extra else ""))


def req(path, tok=TOKEN, origin=None, raw=False):
    h = {}
    if tok:
        h["X-Dfy-Token"] = tok
    if origin:
        h["Origin"] = origin
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=8) as resp:
            b = resp.read().decode("utf8")
            return resp.status, (b if raw else json.loads(b))
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf8")
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b


try:
    port = 0
    t0 = time.time()
    while time.time() - t0 < 20:
        ln = proc.stdout.readline()
        if not ln:
            break
        if ln.startswith("READY"):
            port = int(ln.split()[1])
            break
    check("core started on a loopback port", port > 0, "port=%s" % port)

    s, conv = req("/v1/conversations")
    check("GET /v1/conversations -> 200", s == 200, str(s))
    items = conv.get("items") or []
    check("conversations non-empty", len(items) > 0, "n=%d" % len(items))
    if items:
        c0 = items[0]
        for f in ("umo", "kind", "label", "message_count", "in_whitelist", "excluded"):
            check("conversation field %r present" % f, f in c0)
        keys = {c["kind"] for c in items}
        check("kinds are group/private only", keys <= {"group", "private"}, str(keys))

    umo = (items[0]["umo"] if items else "")
    s, th = req("/v1/messages?umo=" + urllib.parse.quote(umo))
    check("GET /v1/messages -> 200", s == 200, str(s))
    msgs = th.get("items") or []
    check("messages returned", len(msgs) > 0, "n=%d" % len(msgs))
    if msgs:
        m = msgs[-1]
        check("message carries text (stage-1 change)", isinstance(m.get("text"), str))
        check("text length capped at 500", all(len(x.get("text") or "") <= 500 for x in msgs))
        check("from_me flag is bool", isinstance(m.get("from_me"), bool))
        check("iso timestamp shaped", (m.get("iso") or "")[:4].isdigit() or m.get("iso") is None, str(m.get("iso")))
        joined = json.dumps(th, ensure_ascii=False)
        for bad in ("token", "password", "secret", "Cookie", "Authorization"):
            check("no %r anywhere in payload" % bad, bad.lower() not in joined.lower())
    s, bad = req("/v1/messages?umo=" + urllib.parse.quote("nope:FriendMessage:999999999"))
    check("unknown conversation -> graceful error, no path leak",
          s in (200, 404) and isinstance(bad, dict) and "error" in bad and "Traceback" not in str(bad),
          "%s %r" % (s, str(bad)[:70]))
    s, nolid = req("/v1/messages")
    check("missing umo -> error not crash", s == 200 and "error" in nolid, str(nolid)[:70])

    s, g = req("/v1/groups")
    check("GET /v1/groups -> 200", s == 200, str(s))
    gid = {str(x.get("session_id")) for x in (g.get("items") or [])}
    from dfy_core import config as _cfg
    fg = str((( _cfg.load().get("expected_whitelist") or {}).get("forbidden_group")) or "<FORBIDDEN_GROUP>")

    check("excluded legacy group absent from list", fg not in gid, "forbidden=%s seen=%s" % (fg, sorted(gid)))
    check("group whitelist checks present", isinstance(g.get("checks"), list) and len(g["checks"]) >= 3,
          str(len(g.get("checks") or [])))
    check("groups all_ok is True on live data", g.get("all_ok") is True, str(g.get("all_ok")))

    s, au = req("/v1/audit")
    check("GET /v1/audit -> 200 object shape", s == 200 and isinstance(au, dict) and isinstance(au.get("lines"), list),
          "%s keys=%s" % (s, sorted(au)[:5] if isinstance(au, dict) else type(au).__name__))
    lines = (au.get("lines") if isinstance(au, dict) else []) or []
    check("audit recorded the stage-1 reads", any('"/v1/messages"' in l or "read" in l for l in lines),
          "n=%d" % len(lines))
    joined_au = json.dumps(lines, ensure_ascii=False)
    check("audit carries no core token", TOKEN not in joined_au)
    check("audit carries no credential file contents", "b4ae10" not in joined_au and "Credential" not in joined_au)
    check("audit never logs message bodies", '"text"' not in joined_au)

    s, _ = req("/v1/messages?umo=x", tok=None)
    check("no token -> 403", s == 403, str(s))
    s, _ = req("/v1/messages?umo=x", tok="wrong-token")
    check("wrong token -> 403", s == 403, str(s))
    s, _ = req("/v1/messages?umo=x", origin="http://evil.example")
    check("hostile Origin -> 403", s == 403, str(s))
    s, _ = req("/v1/../../astrbot/data/cmd_config.json")
    check("path traversal refused", s in (403, 404), str(s))
    s, _ = req("/api/Debug/schemas")
    check("non-/v1 route refused by core", s == 404, str(s))
    check("core still alive after all probes", proc.poll() is None)
finally:
    try:
        proc.terminate()
        proc.wait(timeout=6)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

print("\nSELFTEST %s  (pass=%d fail=%d)" % ("OK" if not fails else "FAILED", len(notes), len(fails)))
if fails:
    print("failed checks:\n  " + "\n  ".join(fails))
sys.exit(1 if fails else 0)
