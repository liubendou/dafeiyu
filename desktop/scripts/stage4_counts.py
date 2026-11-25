# -*- coding: utf-8 -*-
"""Read-only roll-up of the machine's stage-4 footprints: snapshots, bundles, audit hygiene,
package size. Nothing here writes; safe to run any time, including while the app is open.

Run: python scripts/stage4_counts.py
"""
import glob
import hashlib
import os
import zipfile

DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RT = os.path.join(DESK, ".runtime")
LEAK_PATTERNS = ("token=", "sk-", "Set-Cookie", "mcp_url", "Bearer ", "password")


out = []


def line(s):
    out.append(s)
    print(s)


for sub in ("backups", "diagnostics"):
    p = os.path.join(RT, sub)
    entries = sorted(os.listdir(p)) if os.path.isdir(p) else []
    kind = "dirs" if sub == "backups" else "files"
    if sub == "diagnostics":
        entries = [e for e in entries if e.endswith(".zip")]
        kind = "bundles"
    line("%s: %d %s" % (sub, len(entries), kind))
    for e in entries[-3:]:
        line("   .. %s" % e)

line("setup_marker: %s" % os.path.isfile(os.path.join(RT, "setup.json")))

total = 0
hits = 0
worst = []
for f in sorted(glob.glob(os.path.join(RT, "audit-*.jsonl"))):
    n = 0
    h = 0
    for raw in open(f, "rb"):
        n += 1
        text = raw.decode("utf-8", "replace")
        found = [p for p in LEAK_PATTERNS if p in text]
        if found:
            h += 1
            if len(worst) < 3:
                worst.append("%s:%d %s" % (os.path.basename(f), n, ",".join(found)))
    total += n
    hits += h
    line("audit %s: %d lines, %d leak hits" % (os.path.basename(f), n, h))
line("audit_total: %d lines, leak_hits: %d" % (total, hits))
for w in worst:
    line("   HIT %s" % w)

zips = sorted(glob.glob(os.path.join(DESK, "dist", "*.zip")))
for f in zips:
    raw = open(f, "rb").read()
    names = zipfile.ZipFile(f).namelist()
    line("zip %s: %d B, %d files, sha256 %s" % (os.path.basename(f), len(raw), len(names),
                                                hashlib.sha256(raw).hexdigest()[:16]))
    for probe in ("package.json", "src/main/index.mjs", "out/renderer/index.html",
                  "core/dfy_core/app.py", "run.cmd", "install.ps1", "uninstall.ps1",
                  "SHA256SUMS.txt", "RELEASE.json", "BUILD-INFO.txt",
                  "scripts/selftest_watchdog.py"):
        if probe not in names:
            line("   MISSING FROM PACKAGE: %s" % probe)
    line("   node_modules inside: %s" % any("node_modules" in n for n in names))
    line("   .runtime inside: %s" % any(n.startswith(".runtime") for n in names))

cfg = os.path.join(DESK, "config", "desktop.json")
if os.path.isfile(cfg):
    line("config/desktop.json sha256 %s (%d B)" % (
        hashlib.sha256(open(cfg, "rb").read()).hexdigest()[:16], os.path.getsize(cfg)))

entries = sorted(os.listdir(RT)) if os.path.isdir(RT) else []
line("runtime entries: %s" % ", ".join(entries))
print("\nCOUNTS DONE")
