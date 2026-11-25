# -*- coding: utf-8 -*-
"""Service-to-service link: how the AstrBot plugin reaches this core (§6.4 / §6.5).

The GUI's token never touches disk -- the Electron main process keeps it in memory and
the renderer can't see it at all. The plugin lives in a different process tree (AstrBot),
so it can't be handed that token, and §6.4 says explicitly: 服务间调用使用独立 token，不共用.
So the core mints a *second*, per-boot token for service callers and publishes it in
``.runtime/core-link.json``:

    {"version": 1, "host": "127.0.0.1", "port": 50683, "pid": 22144,
     "token": "<64 hex>", "started_at": "...", "routes": [ ... ]}

What that buys, and what it costs:

* the file only unlocks ``PLUGIN_ROUTES`` below -- six arena routes. It cannot read chat,
  memory, personas, credentials, backups, settings or audit, and it cannot grant itself
  arena_exec (``/v1/arena/grant`` is deliberately absent);
* it is rewritten on every boot and deleted when the core exits, including the
  stdin-watchdog path. A leftover from a dead boot points at a port nobody owns;
* it sits in the user's own profile, inside a directory that is already user-private and
  is never packaged into backups or diagnostic bundles (asserted by the selftest).

The alternative -- letting the plugin talk to the Arena gateway directly -- would put the
MCP address and the permission layer outside the core, which is exactly what §7 wants to
avoid: 权限与审计只在一处。
"""

import json
import os
import time

from . import audit, config

FILE_NAME = "core-link.json"

#: the only routes a service caller may use. Read routes for the three "看看" tools,
#: write routes for the four that act. Everything else stays GUI-only.
PLUGIN_ROUTES = ("/v1/arena/tasks", "/v1/arena/models", "/v1/arena/workbench",
                 "/v1/arena/dispatch", "/v1/arena/note", "/v1/arena/exec")

#: 生产插件（AstrBot 侧）按老约定去 ~/大肥鱼/desktop/.runtime 找指针文件，而核心的
#: runtime 目录随安装版本走（%LOCALAPPDATA%\Dafeiyu\app-<ver>\.runtime）。两处对不上，
#: 插件就会读到上一次的端口和令牌，表现为「连不上 / 被拒」。所以发布时往这里再镜像一份。
MIRROR_DIRS = (
    os.path.join(os.path.expanduser("~"), u"\u5927\u80a5\u9c7c", "desktop", ".runtime"),
)

_state = {"token": None, "path": None, "port": None}


def link_path():
    return os.path.join(config.runtime_dir(), FILE_NAME)


def publish(port):
    """Mint the per-boot service token and write the pointer file. Returns the document
    (token included) or None when publishing failed -- failure here must not stop the
    core: the GUI works fine without the plugin link."""
    import secrets
    token = secrets.token_hex(32)
    doc = {"version": 1, "host": "127.0.0.1", "port": int(port), "pid": os.getpid(),
           "token": token, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "routes": list(PLUGIN_ROUTES)}
    path = link_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
        try:
            os.chmod(tmp, 0o600)  # a no-op on Windows ACLs, but right on anything POSIX
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError as e:
        audit.write("link_publish_failed", error=type(e).__name__)
        return None
    _mirror(doc)
    _state.update(token=token, path=path, port=int(port))
    # the audit line says a link was published and on which port -- never the token
    audit.write("link_published", port=int(port), routes=len(PLUGIN_ROUTES))
    return doc


def remove():
    path = _state.get("path") or link_path()
    _state.update(token=None, port=None)
    if not path or not os.path.isfile(path):
        return False
    try:
        doc = json.loads(open(path, encoding="utf-8").read() or "{}")
    except (OSError, ValueError):
        doc = {}
    if doc and doc.get("pid") not in (None, os.getpid()):
        return False  # someone else republished it; leave it alone
    try:
        os.unlink(path)
        audit.write("link_removed", path=FILE_NAME)
        return True
    except OSError:
        return False


def _mirror(doc):
    """Write the same pointer document into the plugin's known runtime dirs.

    Best effort by design: a missing/unwritable mirror directory must never stop the
    core, and never raises. The token is the same service token the file already holds.
    """
    for d in MIRROR_DIRS:
        try:
            if not os.path.isdir(d):
                continue
            p = os.path.join(d, FILE_NAME)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except (OSError, ValueError):
            pass


def token():
    return _state.get("token")


def active():
    return bool(_state.get("token"))


def allowed_route(path):
    return path in PLUGIN_ROUTES


def read(path=None):
    """Convenience for tests and for the plugin-side reader: the published document."""
    p = path or link_path()
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.loads(fh.read() or "{}")
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def usable(doc):
    """Shape check for a pointer file. Liveness is *not* probed here on purpose: the only
    honest answer on this box comes from actually making the request (a connect to a dead
    port fails in milliseconds). ``os.kill(pid, 0)`` would be the cute way to check, and on
    Windows it terminates the process instead of probing it -- so we don't go near it."""
    if not isinstance(doc, dict):
        return False, "no-link"
    if not doc.get("port") or not doc.get("token"):
        return False, "incomplete"
    routes = doc.get("routes")
    if routes is not None and not set(routes) <= set(PLUGIN_ROUTES):
        return False, "unknown-routes"   # a file from a different/newer core build
    try:
        int(doc["port"])
    except (TypeError, ValueError):
        return False, "bad-port"
    return True, "ok"
