# -*- coding: utf-8 -*-
"""Stage gate: mirror-vs-remote hash check, compile, every selftest suite, typecheck,
build, release packaging, and the live acceptance run. ASCII-only source so the job
script that invokes it never has to carry Chinese text; child output is written to
stdout as raw UTF-8 bytes so a cp936 console cannot raise on it.

Run: python scripts/gate.py [--skip build]   (flags: compile suites typecheck build package accept)
"""
import hashlib
import json
import os
import subprocess
import sys
import time

DESK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(DESK, "core")
CHANGED = [
    "core/dfy_core/setup.py", "core/dfy_core/diag.py", "core/dfy_core/release.py",
    "core/dfy_core/app.py", "core/dfy_core/security.py", "packaging/build_release.py",
    "scripts/selftest_stage4.py", "scripts/selftest_stage2.py", "scripts/selftest_arena.py",
    "src/main/index.mjs", "src/main/supervisor.mjs", "src/renderer/App.tsx",
    "src/renderer/styles.css", "src/renderer/pages/FirstRunPage.tsx",
    "src/renderer/pages/SettingsPage.tsx",
    # 2b-ii：插件侧 Arena 工具与核心服务通道
    "core/dfy_core/link.py", "plugin/arena_tools.py", "scripts/install_arena_tools.py",
    "scripts/selftest_arena_tools.py", "core/dfy_core/release.py", "scripts/accept_stage4.py",
]
SUITES = ["stage1", "send", "stage2", "arena", "stage3", "stage4", "watchdog", "arena_tools"]


def say(text=""):
    sys.stdout.buffer.write((str(text) + "\n").encode("utf-8", "replace"))
    sys.stdout.flush()


def run(cmd, timeout=900, env_extra=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.update(env_extra or {})
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=DESK, capture_output=True, timeout=timeout, env=env)
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode, out, int((time.time() - t0) * 1000)
    except Exception as e:
        return 900, "%s: %s" % (type(e).__name__, e), int((time.time() - t0) * 1000)


def tail(text, n=4):
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return "\n".join("    " + l[:220] for l in lines[-n:])


def main():
    want = set(sys.argv[1:])
    want.discard("--skip")
    all_ok = True
    do = (lambda name: True) if not want or "all" in want else (lambda name: name in want)

    say("=== 0. hashes (compare against the local mirror) ===")
    for rel in CHANGED:
        p = os.path.join(DESK, *rel.split("/"))
        if not os.path.isfile(p):
            say("    MISSING %s" % rel)
            all_ok = False
            continue
        say("    %s %s %d" % (hashlib.sha256(open(p, "rb").read()).hexdigest()[:16], rel,
                              os.path.getsize(p)))

    if do("compile"):
        say("=== 1. py_compile ===")
        bad = []
        for base in (os.path.join(DESK, "core"), os.path.join(DESK, "plugin")):
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if f.endswith(".py"):
                        p = os.path.join(root, f)
                        rc, out, _ = run([sys.executable, "-m", "py_compile", p], timeout=60)
                        if rc:
                            bad.append("%s: %s" % (p, out[:200]))
        for f in os.listdir(os.path.join(DESK, "scripts")):
            if f.endswith(".py"):
                rc, out, _ = run([sys.executable, "-m", "py_compile", os.path.join(DESK, "scripts", f)],
                                 timeout=60)
                if rc:
                    bad.append("scripts/%s: %s" % (f, out[:200]))
        say("    %s" % ("ok" if not bad else "FAILURES:\n" + "\n".join(bad)))
        all_ok &= not bad

    if do("suites"):
        say("=== 2. selftest suites ===")
        for s in SUITES:
            script = os.path.join(DESK, "scripts", "selftest_%s.py" % s)
            if not os.path.isfile(script):
                say("    %s: no script" % s)
                all_ok = False
                continue
            rc, out, ms = run([sys.executable, script], timeout=900)
            summary = [l for l in out.splitlines() if "pass=" in l and "fail=" in l]
            say("    %-8s rc=%d %dms  %s" % (s, rc, ms, (summary[-1] if summary else out[-120:].replace("\n", " / "))))
            if rc:
                say(tail(out, 12))
                all_ok = False

    if do("typecheck"):
        say("=== 3. tsc --noEmit + vite build ===")
        rc, out, ms = run("cmd /c npx --no-install tsc --noEmit -p tsconfig.json", timeout=900)
        say("    tsc rc=%d %dms%s" % (rc, ms, "" if not rc else "\n" + tail(out, 20)))
        all_ok &= rc == 0
        rc, out, ms = run("cmd /c npx --no-install vite build", timeout=1200)
        ok = rc == 0 and ("built in" in out or "modules transformed" in out)
        say("    vite rc=%d %dms %s" % (rc, ms, "ok" if ok else tail(out, 12)))
        all_ok &= ok

    if do("package"):
        say("=== 4. build_release (light) ===")
        rc, out, ms = run([sys.executable, os.path.join(DESK, "packaging", "build_release.py"),
                           "--version", "0.4.0", "--out", os.path.join(DESK, "dist")], timeout=1200)
        say("    rc=%d %dms" % (rc, ms))
        say(tail(out, 3))
        all_ok &= rc == 0
        if rc == 0:
            sys.path.insert(0, CORE)
            try:
                from dfy_core import release
                zp = os.path.join(DESK, "dist", "dafeiyu-app-0.4.0-light.zip")
                v = release.verify(zp)
                say("    verify -> ok=%s files=%s bytes=%s" % (v.get("ok"), v.get("files"),
                                                                os.path.getsize(zp) if os.path.isfile(zp) else "?"))
                all_ok &= bool(v.get("ok"))
            except Exception as e:
                say("    verify failed: %s: %s" % (type(e).__name__, e))
                all_ok = False

    if do("accept"):
        say("=== 5. accept_stage4 (production config, read-only) ===")
        rc, out, ms = run([sys.executable, os.path.join(DESK, "scripts", "accept_stage4.py")], timeout=1500)
        say(out.rstrip()[-6000:] if rc else tail(out, 2))
        say("    accept rc=%d %dms done=%s" % (rc, ms, "ACCEPT4_DONE" in out))
        all_ok &= rc == 0

    say("GATE %s" % ("OK" if all_ok else "PROBLEMS"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
