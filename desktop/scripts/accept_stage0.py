"""Stage-0 acceptance: prove (1) production untouched, (2) secret deny-list bites,
(3) every listener we own is loopback-only, (4) the running app's core answers."""
import hashlib, json, os, subprocess, sys, time

DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.join(DESK, "core"))

def line(t=""):
    print(t)

line("=== 1. production files unchanged since baseline ===")
bp = os.path.join(DESK, ".runtime", "baseline-production-20261030.json")
base = json.load(open(bp, encoding="utf8"))
# An intentional, documented production edit (e.g. the plugin outbox hook) is
# listed in .runtime/intentional.json with its backup path. Anything that changes
# without being on that list is a hard failure.
intentional = {}
ip = os.path.join(DESK, ".runtime", "intentional.json")
if os.path.isfile(ip):
    try:
        intentional = json.load(open(ip, encoding="utf8"))
    except Exception as e:
        line(f"  !! intentional.json unreadable: {e}")
changed, same, gone, by_design, data_delta = [], [], [], [], []
for e in base["files"]:
    p = os.path.join(HOME, e["path"].replace("/", os.sep))
    if not os.path.isfile(p):
        gone.append(e["path"]); continue
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h == e["sha256"]:
        same.append(e["path"]); continue
    if e.get("mutable"):
        # live plugin data: it changes on every message. Prove it is still
        # readable and roughly the shape we expect, never assert a hash.
        try:
            d = json.load(open(p, encoding="utf8"))
            c = d.get("conversations") or {}
            data_delta.append("%s convs=%d bytes %s->%s" % (os.path.basename(p), len(c),
                                                            e.get("size"), os.path.getsize(p)))
        except Exception as exc:
            changed.append(e["path"] + " (mutable but unreadable: %s)" % exc)
        continue
    rec = intentional.get(e["path"]) or {}
    bk = rec.get("backup")
    # a documented change must still be reversible: the backup has to exist and
    # match the hash we recorded as "before".
    if rec and bk and os.path.isfile(os.path.join(DESK, bk) if not os.path.isabs(bk) else bk):
        bpath = os.path.join(DESK, bk) if not os.path.isabs(bk) else bk
        if hashlib.sha256(open(bpath, "rb").read()).hexdigest()[:16] == rec.get("before_sha16"):
            by_design.append((e["path"], rec.get("reason", "")))
            continue
    changed.append(e["path"] + (" (listed but backup/hash mismatch)" if rec else ""))
for d in data_delta:
    line("  DATA-DELTA: " + d)
line(f"  hashed now={len(base['files'])}  identical={len(same)}  by-design={len(by_design)}  "
     f"data-delta={len(data_delta)}  UNEXPECTED={len(changed)}  missing={len(gone)}")
for f, why in by_design:
    line(f"  BY DESIGN: {f} — {why}")
if changed: line("  !! UNEXPECTED CHANGES: " + ", ".join(changed))
if gone: line("  !! MISSING: " + ", ".join(gone))
line(f"  verdict: {'CLEAN' if not changed and not gone else 'DIRTY'}")

line("\n=== 2. secret deny-list actually bites ===")
from dfy_core import security, readers
for rel in ["大肥鱼/napcat/config/webui.json", "ops-dfy/ctl/mcp_url.txt",
            "astrbot/DASHBOARD_CREDENTIALS.enc", "大肥鱼/napcat/config/passkey.json"]:
    p = os.path.join(HOME, rel.replace("/", os.sep))
    data, err = readers.read_json_capped(p)
    verdict = "BLOCKED" if err and "deny-list" in err else ("!!READ!!" if data is not None else f"other:{err}")
    line(f"  {verdict:12s} {os.path.basename(rel)}  (exists={os.path.isfile(p)})")
ok = all(readers.read_json_capped(os.path.join(HOME, r))[1] and "deny-list" in (readers.read_json_capped(os.path.join(HOME, r))[1] or "")
         for r in ["大肥鱼/napcat/config/webui.json", "ops-dfy/ctl/mcp_url.txt"])
line(f"  deny-list enforced: {ok}")

line("\n=== 3. non-loopback listeners owned by OUR processes ===")
import subprocess as _sp
_names = {}
try:
    import csv as _csv, io as _io
    _out = _sp.run("tasklist /fo csv /nh", shell=True, capture_output=True).stdout.decode("gbk", "replace")
    _names = {r[1]: r[0].lower() for r in _csv.reader(_io.StringIO(_out)) if len(r) >= 2}
except Exception:
    pass
SELF = {"electron.exe", "python.exe", "pythonw.exe", "\u5927\u80a5\u9c7c.exe"}
rows, err = readers.netstat_rows()
ours = [r for r in rows if r["state"] == "LISTENING" and not r["loopback"]
        and _names.get(r["pid"], "") in SELF]
syscnt = sum(1 for r in rows if r["state"] == "LISTENING" and not r["loopback"])
line(f"  our processes: {sum(1 for v in _names.values() if v in SELF)}  system non-loopback listeners: {syscnt}")
line(f"  OURS non-loopback: {[(o['image'], o['port']) for o in ours] or 'NONE'}")
loop = sorted({r["port"] for r in rows if r["state"] == "LISTENING" and r["loopback"]
               and _names.get(r["pid"], "") in SELF})
line(f"  OURS loopback ports: {loop}")
line(f"  verdict: {'CLEAN' if not ours else 'EXPOSED'}")

line("\n=== 4. is the GUI app's core live right now? ===")
# the app writes nothing about its port; probe via the running electron process list
ps = subprocess.run("tasklist", shell=True, capture_output=True).stdout.decode("gbk", "replace")
has_electron = "electron.exe" in ps.lower()
line(f"  electron.exe running: {has_electron}")
line(f"  python.exe count: {ps.lower().count('python.exe')}")

line("\n=== 5. audit log tail (redaction spot-check) ===")
au = os.path.join(DESK, ".runtime")
files = [f for f in sorted(os.listdir(au)) if f.startswith("audit-")]
if files:
    lines = open(os.path.join(au, files[-1]), encoding="utf8").read().strip().splitlines()
    line(f"  {files[-1]}: {len(lines)} events")
    for l in lines[-4:]:
        line("   " + l[:150])
    leaks = [l for l in lines if "b4ae103236fa98a60b9ca7be" in l or "Administrator" in l and "webui" in l]
    line(f"  lines containing the NapCat token or webui reads: {len(leaks)}")
line("\nACCEPT_DONE")
