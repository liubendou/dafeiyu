"""Throwaway helper: zip a remote subtree and print it base64, so the local mirror can
catch up on files its snapshot lost. Read-only remotely; delete after use."""
import base64, io, os, sys, zipfile

BASE = r"C:\Users\<user>\大肥鱼\desktop"
# subtree name via an ASCII-only env var (Chinese inside a job script can get mangled)
ROOT = os.path.join(BASE, os.environ.get("DFY_SYNC_SUB") or "src")
SKIP = {"node_modules", "__pycache__", "out", "dist", ".git"}
MAX = 500000

buf = io.BytesIO()
n = 0
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP)
        for f in sorted(filenames):
            p = os.path.join(dirpath, f)
            try:
                if os.path.getsize(p) > MAX:
                    continue
            except Exception:
                continue
            z.write(p, os.path.relpath(p, ROOT).replace(os.sep, "/"))
            n += 1
blob = buf.getvalue()
out = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "dfy-mirror-sync.zip")
with open(out, "wb") as fh:
    fh.write(blob)
print("FILES=%d BYTES=%d" % (n, len(blob)))
print("BEGIN")
print(base64.b64encode(blob).decode())
print("END")
