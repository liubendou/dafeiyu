# -*- coding: utf-8 -*-
"""Prove the parent-death watchdog on *this* machine, without touching the running app.

The GUI can be killed outright (task manager, a forced restart); then `before-quit` never
runs and the core would keep holding a loopback port forever. The fix: the supervisor hands
the core a stdin pipe and sets DFY_CORE_WATCH_STDIN=1; the core exits when that pipe closes.

Two cases, so the result can't be explained by "the core just died":
  A) watchdog on  -> closing stdin must make it exit by itself
  B) watchdog off -> closing stdin must NOT kill it (we clean it up ourselves)

Run: python scripts/selftest_watchdog.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(DESK, "core")
PY = sys.executable

PASS = [0]
FAIL = []


def check(name, ok, extra=""):
    if ok:
        PASS[0] += 1
        print("PASS | %s %s" % (name, ("| " + str(extra)[:120]) if extra else ""))
    else:
        FAIL.append(name)
        print("FAIL | %s | %s" % (name, str(extra)[:240]))


def boot(rt, watch):
    env = dict(os.environ, PYTHONPATH=CORE, DFY_CORE_TOKEN="watchdog-proof-token",
               DFY_CORE_PORT="0", DFY_RUNTIME_DIR=rt, PYTHONIOENCODING="utf-8",
               DFY_DESKTOP_CONFIG=os.environ.get("DFY_DESKTOP_CONFIG")
               or os.path.join(DESK, "config", "desktop.json"))
    if watch:
        env["DFY_CORE_WATCH_STDIN"] = "1"
    p = subprocess.Popen([PY, "-m", "dfy_core.app"], cwd=CORE, env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace")
    port = None
    for _ in range(60):
        ln = p.stdout.readline()
        if not ln:
            break
        if ln.startswith("READY"):
            port = int(ln.split()[1])
            break
    return p, port


def alive(p):
    return p.poll() is None


def reachable(port):
    if not port:
        return False
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def cleanup(p):
    try:
        p.kill()
        p.wait(timeout=8)
    except Exception:
        pass


print("=== A) watchdog on: closing stdin must end the core ===")
rtA = tempfile.mkdtemp(prefix="dfy-wd-on-")
try:
    pa, porta = boot(rtA, True)
    check("A0 core booted and reported a port", bool(porta), porta)
    check("A1 it is listening before we close stdin", reachable(porta), porta)
    time.sleep(0.4)
    try:
        pa.stdin.close()
    except Exception as e:
        check("A2 stdin close call succeeded", False, e)
    rc = None
    try:
        rc = pa.wait(timeout=12)
    except Exception:
        pass
    check("A2 the core exited on its own after the pipe closed", rc is not None, "rc=%s" % rc)
    check("A3 its port is released", not reachable(porta), porta)
    cleanup(pa)
finally:
    for _ in range(4):
        try:
            shutil.rmtree(rtA, ignore_errors=True)
            break
        except OSError:
            time.sleep(0.4)

print("=== B) watchdog off: same pipe close must NOT be fatal ===")
rtB = tempfile.mkdtemp(prefix="dfy-wd-off-")
try:
    pb, portb = boot(rtB, False)
    check("B0 control core booted", bool(portb), portb)
    time.sleep(0.4)
    try:
        pb.stdin.close()
    except Exception:
        pass
    time.sleep(3.0)
    check("B1 without the flag the core survives a closed stdin (so A was the watchdog, not a crash)",
          alive(pb), "alive=%s port=%s" % (alive(pb), portb))
    check("B2 and still answers on its port", reachable(portb), portb)
    if not alive(pb):
        out = ""
        try:
            out = pb.stdout.read()[:400]
        except Exception:
            pass
        check("B-exit reason", False, out)
    cleanup(pb)
finally:
    for _ in range(4):
        try:
            shutil.rmtree(rtB, ignore_errors=True)
            break
        except OSError:
            time.sleep(0.4)

print("=== C) the shipped supervisor actually sets it up ===")
sup = os.path.join(DESK, "src", "main", "supervisor.mjs")
txt = open(sup, encoding="utf-8").read() if os.path.isfile(sup) else ""
check("C1 supervisor passes DFY_CORE_WATCH_STDIN", "DFY_CORE_WATCH_STDIN" in txt, sup)
check("C2 supervisor gives the core a stdin pipe (not ignore)",
      'stdio: ["pipe"' in txt and 'stdio: ["ignore"' not in txt, "")
check("C3 a broken pipe on that unused stdin cannot crash the main process",
      'child.stdin.on("error"' in txt, "")

print("\nWATCHDOG SELFTEST (pass=%d fail=%d)" % (PASS[0], len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
