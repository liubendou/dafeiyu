"""Send-path self-test. Runs against a SCRATCH outbox, so nothing reaches QQ.

Covers: core validation, idempotency, receipt transitions, HTTP guards, and the
invariant that the real plugin inbox file count does not change.
"""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(DESK, "core"))
SCRATCH = os.path.join(DESK, ".runtime", "selftest-outbox")
REAL_INBOX = None
fails, notes = [], []


def check(name, cond, extra=""):
    (notes if cond else fails).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" | " + str(extra)) if extra else ""), flush=True)


def reset_scratch():
    if os.path.isdir(SCRATCH):
        shutil.rmtree(SCRATCH, ignore_errors=True)
    for d in ("inbox", "sent", "failed"):
        os.makedirs(os.path.join(SCRATCH, d), exist_ok=True)


reset_scratch()
os.environ["DFY_OUTBOX_ROOT"] = SCRATCH
from dfy_core import config, outbox  # noqa: E402

cfg = config.load()
REAL_INBOX = os.path.join(cfg["plugin_data_dir"], "desktop_outbox", "inbox")
real_before = len(os.listdir(REAL_INBOX)) if os.path.isdir(REAL_INBOX) else 0
print("scratch=%s" % SCRATCH)
print("real inbox=%s files=%d (must not change)" % (REAL_INBOX, real_before))

convs = None
os.environ.pop("DFY_OUTBOX_ROOT")  # only submit() may not know the real dirs; conversations needs real data
convs = __import__("dfy_core.threads", fromlist=["threads"]).conversations(cfg)
os.environ["DFY_OUTBOX_ROOT"] = SCRATCH
items = convs.get("items") or []
owner = next((c for c in items if c.get("kind") == "private"), None)
check("found a private conversation to target", owner is not None, str([c.get("umo") for c in items]))
umo = owner["umo"] if owner else ""

s = outbox.submit(cfg, umo, "")
check("empty text refused", "error" in s, str(s)[:60])
s = outbox.submit(cfg, umo, "x" * 1201)
check("over-long text refused", "error" in s, str(s)[:60])
s = outbox.submit(cfg, "bogus:FriendMessage:123", "hi")
check("unknown conversation refused", "error" in s and "未知会话" in s["error"], str(s)[:60])
s = outbox.submit(cfg, umo, "自检消息，不该发出去")
check("valid request queued", s.get("state") == "pending" and s.get("id"), str(s)[:80])
rid = s.get("id")
p = os.path.join(SCRATCH, "inbox", rid + ".json")
check("inbox file created", os.path.isfile(p))
if os.path.isfile(p):
    rec = json.load(open(p, encoding="utf8"))
    check("request carries the plugin's expected keys",
          all(k in rec for k in ("id", "umo", "text", "created_at", "message_type", "session_id")),
          str(sorted(rec)))
    check("request marked via=desktop", rec.get("via") == "desktop")
    check("receipt id is not guessable-as-path", ".." not in rid and "/" not in rid, rid)

r2 = outbox.submit(cfg, umo, "同一 idem 不该重复入队", "IDEM-1")
r3 = outbox.submit(cfg, umo, "同一 idem 不该重复入队", "IDEM-1")
check("idempotency: second call deduplicated", r3.get("deduplicated") is True and r3.get("id") == r2.get("id"),
      "%s vs %s" % (r2.get("id"), r3.get("id")))

check("receipt: pending before serve", outbox.receipt(cfg, rid)["state"] == "pending")
shutil.move(p, os.path.join(SCRATCH, "sent", rid + ".json"))
json.dump({"id": rid, "ok": True, "chars": 9, "at": int(time.time() * 1000), "umo": umo},
          open(os.path.join(SCRATCH, "sent", rid + ".json"), "w", encoding="utf8"))
check("receipt: sent after plugin served it", outbox.receipt(cfg, rid)["state"] == "sent")
bad = os.path.join(SCRATCH, "failed", "zzz-1.json")
json.dump({"id": "zzz-1", "ok": False, "error": "目标不在白名单", "at": 1}, open(bad, "w", encoding="utf8"))
rec = outbox.receipt(cfg, "zzz-1")
check("receipt: failure surfaces the plugin's reason",
      rec["state"] == "failed" and "白名单" in str(rec.get("detail", {}).get("error")), str(rec)[:100])
check("receipt: path traversal id rejected", outbox.receipt(cfg, "../../etc/passwd")["state"] == "unknown")

qs = outbox.queue_state(cfg)
check("queue_state counts match", qs.get("inbox") == 1 and qs.get("sent") == 1 and qs.get("failed") == 1, str(qs))
off = dict(cfg); off["send_enabled"] = False
check("send_enabled=false blocks at the core", "error" in outbox.submit(off, umo, "不该发"))

# ---- HTTP guards, with a private core instance ----
TOKEN = "sendtest-%d" % int(time.time() * 1000)
env = dict(os.environ)
env.update({"DFY_CORE_TOKEN": TOKEN, "DFY_CORE_PORT": "0",
            "PYTHONPATH": os.path.join(DESK, "core"),
            "DFY_OUTBOX_ROOT": SCRATCH,
            # 同上：测试起的核心把 runtime 挪到临时目录，别往生产 .runtime 里落东西
            "DFY_RUNTIME_DIR": tempfile.mkdtemp(prefix="dfy-selftest-send-")})
proc = subprocess.Popen([sys.executable, "-m", "dfy_core.app"], cwd=os.path.join(DESK, "core"),
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
port = 0
t0 = time.time()
while time.time() - t0 < 20:
    ln = proc.stdout.readline()
    if not ln:
        break
    if ln.startswith("READY"):
        port = int(ln.split()[1])
        break
check("core up for http guards", port > 0, "port=%d" % port)


def post(path, obj, tok=TOKEN):
    data = json.dumps(obj).encode()
    h = {"Content-Type": "application/json"}
    if tok:
        h["X-Dfy-Token"] = tok
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf8")
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b


try:
    st, body = post("/v1/status", {})
    check("POST to a read route -> 405", st == 405, "%s %s" % (st, str(body)[:60]))
    st, body = post("/v1/send", {"umo": umo, "text": "hi"}, tok=None)
    check("POST without token -> 403", st == 403, str(st))
    st, body = post("/v1/send", {"umo": "nope:FriendMessage:1", "text": "hi"})
    check("POST unknown conversation -> 400 refused", st == 400 and "error" in body, "%s %s" % (st, str(body)[:60]))
    st, body = post("/v1/send", {"umo": umo, "text": ""})
    check("POST empty text -> 400", st == 400, str(st))
    st, body = post("/v1/send", {"umo": umo, "text": "http 层自检"})
    check("POST valid -> queued (into scratch only)", st == 200 and body.get("state") == "pending", str(body)[:80])
    st, body = post("/v1/send", {"umo": umo, "text": "x" * 5000})
    check("POST over-long -> 400 or 413", st in (400, 413), str(st))

    def get(path, tok=TOKEN):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                     headers={"X-Dfy-Token": tok} if tok else {})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, json.loads(r.read().decode("utf8"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf8")[:80]

    st, body = get("/v1/outbox")
    check("GET /v1/outbox shows the queue", st == 200 and body.get("inbox") is not None, str(body)[:110])
    st, body = get("/v1/receipt?id=" + (json.dumps({}) and "scratch-none"))
    check("GET /v1/receipt unknown -> state unknown", st == 200 and body.get("state") == "unknown", str(body)[:80])
finally:
    proc.terminate()
    try:
        proc.wait(timeout=6)
    except Exception:
        proc.kill()

real_after = len(os.listdir(REAL_INBOX)) if REAL_INBOX and os.path.isdir(REAL_INBOX) else 0
check("REAL plugin inbox untouched by the whole test", real_before == real_after,
      "%d -> %d" % (real_before, real_after))
reset_scratch()
print("\nSENDTEST %s (pass=%d fail=%d)" % ("OK" if not fails else "FAILED", len(notes), len(fails)))
if fails:
    print("failed:\n  " + "\n  ".join(fails))
sys.exit(1 if fails else 0)
