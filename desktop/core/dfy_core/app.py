"""HTTP entry point. 127.0.0.1 on an ephemeral port, token-guarded, read-only.

Startup contract with the Electron main process:
  env  DFY_CORE_TOKEN  required (no token -> refuse to start)
  env  DFY_CORE_PORT   optional; "0" (default) = let the OS pick a free port
  out  first stdout line: "READY <port> <pid>"   (the token is never printed)
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import json

from . import (arena, audit, backup, config, control, credentials, diag, groups, link, market, memories,
               modelcfg, outbox, persona, readers, release, rooms, security, settings, setup,
               threads, toolslog)


def threads_own_listeners():
    """Route helper: kept separate so the diag endpoint reads as one thing."""
    return readers.own_listeners()

ROUTES = ("/v1/status", "/v1/logs", "/v1/diag/ports", "/v1/audit", "/v1/healthz",
          "/v1/conversations", "/v1/messages", "/v1/groups", "/v1/outbox", "/v1/receipt",
          "/v1/tools", "/v1/control/receipt",
          "/v1/arena/overview", "/v1/arena/tasks", "/v1/arena/task", "/v1/arena/models",
          "/v1/arena/rooms", "/v1/arena/instances", "/v1/arena/route",
          "/v1/plugins",
          "/v1/arena/workbench", "/v1/arena/grant",
          "/v1/backups", "/v1/persona", "/v1/persona/history", "/v1/persona/sources",
          "/v1/memory", "/v1/memory/info", "/v1/models", "/v1/settings", "/v1/credentials",
          "/v1/setup/state", "/v1/setup/checks", "/v1/setup/inventory",
          "/v1/release/status", "/v1/diag/bundles")
# The writable routes. Everything else that is POSTed to gets a flat 405.
WRITABLE = {"/v1/send", "/v1/control", "/v1/groups/enable", "/v1/groups/disable",
            "/v1/arena/dispatch", "/v1/arena/note", "/v1/arena/workbench", "/v1/arena/grant",
            "/v1/arena/exec",
            "/v1/backups", "/v1/backups/restore", "/v1/backups/export", "/v1/backups/import",
            "/v1/persona", "/v1/persona/restore", "/v1/models", "/v1/models/test",
            "/v1/settings", "/v1/credentials", "/v1/credentials/clear", "/v1/selftest",
            "/v1/setup/adopt", "/v1/setup/reset", "/v1/release/install", "/v1/release/rollback",
            "/v1/diag/bundle"}


class Handler(BaseHTTPRequestHandler):
    server_version = "dfy-core/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # the audit log is the record; keep the console quiet

    def _send(self, code, payload):
        body = security.json_dumps_safe(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _auth(self, path):
        """(allowed, mode, reason).

        Two kinds of caller, two tokens, deliberately not interchangeable (§6.4):
        the GUI presents the launch token and may use every route; the plugin presents
        the service token from .runtime/core-link.json and may only use link.PLUGIN_ROUTES.
        A service token anywhere else is refused by name so a compromised plugin cannot
        read chat history, credentials or backups off the same port.
        """
        port = self.server.server_address[1]
        if not security.host_ok(self.headers.get("Host"), port):
            return False, None, "host"
        if not security.origin_ok(self.headers.get("Origin"), port):
            return False, None, "origin"
        svc = self.headers.get("X-Dfy-Service-Token") or ""
        if svc.startswith("Bearer "):
            svc = svc[7:].strip()
        if svc:
            if not (link.active() and security.token_ok(link.token(), svc)):
                return False, None, "service-token"
            if not link.allowed_route(path):
                return False, "plugin", "plugin_route"
            return True, "plugin", None
        given = self.headers.get("X-Dfy-Token") or ""
        if given.startswith("Bearer "):
            given = given[7:].strip()
        if not security.token_ok(self.server.token, given):
            return False, None, "token"
        return True, "gui", None

    def _authorized(self, path):
        ok, mode, reason = self._auth(path)
        if not ok:
            audit.write("denied", path=path, reason=reason)
            self._send(403, {"error": "forbidden", "reason": reason})
            return None
        if mode == "plugin":
            # what was called and who called it; never the payload, never the token
            audit.write("plugin_call", path=path, via="plugin", method=self.command)
        return mode

    def _qs(self):
        from urllib.parse import parse_qs
        return parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/v1/healthz":
            self._send(200, {"ok": True, "pid": os.getpid(), "mode": "read-only"})
            return
        if self._authorized(path) is None:
            return
        try:
            if path == "/v1/status":
                out = readers.status()
            elif path == "/v1/diag/ports":
                cfg = config.load()
                listeners, est, exposed, err = readers.port_report(cfg)
                out = {"listeners": listeners, "established": est,
                       "non_loopback_watch_ports": exposed, "netstat_error": err,
                       "own": threads_own_listeners()}
            elif path in ("/v1/conversations", "/v1/messages", "/v1/groups"):
                cfg = config.load()
                q = self._qs()
                if path == "/v1/conversations":
                    out = threads.conversations(cfg)
                elif path == "/v1/groups":
                    out = threads.groups(cfg)
                else:
                    out = threads.messages(cfg, umo=(q.get("umo") or [""])[0],
                                           limit=(q.get("limit") or ["50"])[0])
                    audit.write("read", path=path, target=(q.get("umo") or [""])[0][:64],
                                n=out.get("returned"))
                    self._send(200, out)
                    return
            elif path == "/v1/arena/overview":
                out = arena.overview(config.load())
            elif path == "/v1/arena/tasks":
                out = arena.tasks(config.load(), limit=20)
            elif path == "/v1/arena/task":
                out = arena.task_detail(config.load(), (self._qs().get("file") or [""])[0])
            elif path == "/v1/arena/models":
                out = arena.models(config.load())
            elif path == "/v1/arena/rooms":
                q = self._qs()
                refresh = str((q.get("refresh") or [""])[0]).lower() in ("1", "yes", "true")
                raw_wait = str((q.get("wait") or [""])[0])
                wait = int(raw_wait) if raw_wait.isdigit() else rooms.DEFAULT_WAIT
                out = rooms.snapshot(config.load(), refresh=refresh, timeout=min(max(wait, 2), 60))
            elif path == "/v1/arena/instances":
                out = rooms.instances(config.load())
            elif path == "/v1/plugins":
                q = self._qs()
                refresh = str((q.get("refresh") or [""])[0]).lower() in ("1", "yes", "true")
                out = market.view(config.load(), refresh=refresh, q=(q.get("q") or [""])[0])
            elif path == "/v1/arena/route":
                out = arena.route_preview(config.load(), (self._qs().get("model") or [""])[0])
            elif path == "/v1/arena/workbench":
                out = arena.workbench_status(config.load())
            elif path == "/v1/arena/grant":
                out = arena.grant_state(config.load())
            elif path == "/v1/backups":
                cfg = config.load()
                out = backup.listing(cfg)
                try:
                    res = backup.autobackup_if_due(cfg)
                    if res.get("ok"):
                        out["auto_created"] = res.get("id")
                except Exception:
                    pass
            elif path == "/v1/persona/sources":
                out = persona.sources(config.load())
            elif path == "/v1/persona/history":
                q = self._qs()
                out = persona.history(config.load(), (q.get("source") or ["db"])[0],
                                      (q.get("persona_id") or [""])[0])
            elif path == "/v1/persona":
                q = self._qs()
                out = persona.read(config.load(), (q.get("source") or ["db"])[0],
                                   (q.get("persona_id") or [""])[0])
            elif path == "/v1/memory/info":
                out = memories.info(config.load())
            elif path == "/v1/memory":
                q = self._qs()
                out = memories.search(config.load(), (q.get("q") or [""])[0],
                                      limit=(q.get("limit") or ["12"])[0],
                                      table=(q.get("table") or [""])[0])
            elif path == "/v1/models":
                out = modelcfg.read(config.load())
            elif path == "/v1/settings":
                out = settings.read(config.load())
            elif path == "/v1/credentials":
                out = credentials.list_slots()
            elif path == "/v1/setup/state":
                out = setup.state(config.load())
            elif path == "/v1/setup/checks":
                out = setup.checks(config.load())
            elif path == "/v1/setup/inventory":
                out = setup.inventory(config.load())
            elif path == "/v1/release/status":
                cfg = config.load()
                out = {"current": release.current(), "packages": release.packages(),
                       "history": release.history(cfg),
                       "uninstall_plan": release.uninstall_plan(cfg)}
            elif path == "/v1/diag/bundles":
                out = diag.list_bundles(config.load())
            elif path == "/v1/tools":
                q = self._qs()
                out = toolslog.tool_trace(config.load(), umo=(q.get("umo") or [""])[0],
                                          limit=(q.get("limit") or ["20"])[0])
            elif path == "/v1/control/receipt":
                out = control.receipt(config.load(), (self._qs().get("id") or [""])[0])
            elif path == "/v1/outbox":
                out = outbox.queue_state(config.load())
            elif path == "/v1/receipt":
                out = outbox.receipt(config.load(), (self._qs().get("id") or [""])[0])
            elif path == "/v1/audit":
                t = audit.tail(60)
                # the reader may hand back a bare list; the UI contract is an object
                out = t if isinstance(t, dict) else {"lines": list(t or []), "count": len(t or [])}
            elif path == "/v1/logs":
                out = {"lines": [], "stage": 0,
                       "note": "阶段 1 接入服务日志 tail（脱敏后输出）"}
            else:
                self._send(404, {"error": "not found", "routes": list(ROUTES)})
                return
            audit.write("read", path=path)
            self._send(200, out)
        except Exception as e:
            audit.write("error", path=path, error="%s: %s" % (type(e).__name__, e))
            self._send(500, {"error": "core_failure", "type": type(e).__name__,
                             "message": security.redact(str(e))[:300]})


    def do_POST(self):
        """One write route only: queue a send request for the plugin."""
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if self._authorized(path) is None:
            return
        # Always drain the body first, or HTTP/1.1 keep-alive desyncs the socket.
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        n = max(0, min(n, outbox.BODY_CAP + 4096))
        raw = self.rfile.read(n) if n else b""
        if path not in WRITABLE:
            self._send(405, {"error": "method not allowed", "writable_routes": sorted(WRITABLE)})
            return
        if not raw:
            self._send(400, {"error": "empty body"})
            return
        if len(raw) > outbox.BODY_CAP:
            self._send(413, {"error": "body too large", "cap": outbox.BODY_CAP})
            return
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"error": "body must be json"})
            return
        if not isinstance(req, dict):
            self._send(400, {"error": "body must be an object"})
            return
        cfg = config.load()
        if path == "/v1/send":
            out = outbox.submit(cfg, req.get("umo"), req.get("text"), str(req.get("idem") or ""))
            audit.write("send_result", ok=("error" not in out), rid=out.get("id"),
                        error=security.redact(str(out.get("error") or ""))[:120])
        elif path.startswith("/v1/arena/"):
            out = self._arena(cfg, path, req)
        elif path.startswith(("/v1/backups", "/v1/persona", "/v1/models", "/v1/settings",
                              "/v1/credentials", "/v1/selftest")):
            out = self._stage3(cfg, path, req)
            audit.write("stage3_result", path=path, ok=("error" not in out))
        elif path.startswith(("/v1/setup", "/v1/release", "/v1/diag/")):
            out = self._stage4(cfg, path, req)
            audit.write("stage4_result", path=path, ok=("error" not in out))
        elif path == "/v1/control":
            out = control.submit(cfg, req.get("action"), req.get("umo"), req.get("args"))
            audit.write("control_result", ok=("error" not in out), rid=out.get("id"),
                        action=str(req.get("action") or "")[:20])
        else:  # /v1/groups/enable | /v1/groups/disable
            out = groups.set_group(cfg, req.get("group_id"), path.endswith("enable"),
                                   str(req.get("confirm") or ""))
            audit.write("group_result", ok=("error" not in out), gid=str(req.get("group_id") or "")[:20],
                        route=path)
        self._send(200 if "error" not in out else 400, out)

    _SUITES = {"stage1": "selftest_stage1", "send": "selftest_send", "stage2": "selftest_stage2",
               "arena": "selftest_arena", "stage3": "selftest_stage3",
               "stage4": "selftest_stage4",
               "watchdog": "selftest_watchdog",
               "arena_tools": "selftest_arena_tools"}

    def _selftest(self, suite):
        """Run one bundled selftest and hand back the tail. Fixed allowlist of script
        names, never a shell string from the client, nothing else reachable from here."""
        key = str(suite or "").strip()
        if key not in self._SUITES:
            return {"error": "只认识这些套件：%s" % "、".join(sorted(self._SUITES))}
        import subprocess
        import sys
        import time
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script = os.path.join(root, "scripts", self._SUITES[key] + ".py")
        if not os.path.isfile(script):
            return {"error": "脚本不在：%s.py" % self._SUITES[key]}
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, script], cwd=root, capture_output=True,
                               encoding="utf-8", errors="replace", timeout=240)
        except Exception as e:
            audit.write("selftest", suite=key, error=type(e).__name__)
            return {"error": "跑不起来：%s" % type(e).__name__}
        ms = int((time.time() - t0) * 1000)
        blob = security.redact(((p.stdout or "") + "\n" + (p.stderr or "")).strip())
        lines = [l for l in blob.splitlines()
                 if any(t in l for t in ("OK", "FAIL", "SELFTEST", "SENDTEST", "STAGE2", "ARENA", "rc="))]
        audit.write("selftest", suite=key, rc=p.returncode, ms=ms)
        return {"ok": p.returncode == 0, "suite": key, "rc": p.returncode, "ms": ms,
                "lines": lines[-16:], "all_lines": len(blob.splitlines())}

    def _stage4(self, cfg, path, req):
        """阶段 4：首次启动向导、更新与回滚、诊断包。

        凡是会动配置的（接管/重置/装包/回滚）都要求 confirm=yes，并且先做一次自动备份；
        诊断包只写运行时目录，内容全部过 redact。
        """
        confirm = str(req.get("confirm") or "")
        if path == "/v1/setup/adopt":
            return setup.adopt(cfg, str(req.get("mode") or "adopt"), confirm)
        if path == "/v1/setup/reset":
            return setup.reset(confirm)
        if path == "/v1/release/install":
            return release.install(cfg, str(req.get("package") or ""), confirm)
        if path == "/v1/release/rollback":
            return release.rollback(cfg, confirm, str(req.get("to") or ""))
        if path == "/v1/diag/bundle":
            return diag.bundle(cfg, str(req.get("note") or ""))
        return {"error": "unknown stage4 route", "path": path}

    def _stage3(self, cfg, path, req):
        """Stage-3 writes. Each one snapshots first, verifies after, and rolls back
        when the readback disagrees, so no partial state is ever left behind."""
        if path == "/v1/selftest":
            return self._selftest(req.get("suite"))
        if path == "/v1/backups":
            return backup.create(cfg, str(req.get("label") or "manual"))
        if path == "/v1/backups/restore":
            return backup.restore(cfg, req.get("id"), str(req.get("name") or ""),
                                  str(req.get("confirm") or ""))
        if path == "/v1/backups/export":
            return backup.export(cfg, str(req.get("note") or ""))
        if path == "/v1/backups/import":
            return backup.import_archive(cfg, req.get("file"), str(req.get("confirm") or ""))
        if path == "/v1/persona":
            return persona.write(cfg, str(req.get("source") or "db"),
                                 str(req.get("persona_id") or ""), req.get("text"),
                                 str(req.get("base_sha8") or ""), str(req.get("confirm") or ""),
                                 str(req.get("note") or ""))
        if path == "/v1/persona/restore":
            return persona.restore(cfg, str(req.get("source") or "db"),
                                   str(req.get("persona_id") or ""), str(req.get("entry") or ""),
                                   str(req.get("confirm") or ""))
        if path == "/v1/models":
            return modelcfg.save(cfg, str(req.get("source_id") or ""), req.get("patch") or {},
                                 str(req.get("entry_id") or ""), str(req.get("base_sha8") or ""),
                                 str(req.get("confirm") or ""))
        if path == "/v1/models/test":
            return modelcfg.test_connection(cfg, str(req.get("source_id") or ""),
                                            req.get("timeout") or 8)
        if path == "/v1/settings":
            return settings.save(cfg, str(req.get("section") or "gui"), req.get("fields") or {})
        if path == "/v1/credentials":
            out = credentials.save(str(req.get("name") or ""), req.get("value"),
                                   str(req.get("label") or ""))
            if out.get("ok") and out.get("name") == "arena_mcp_url":
                arena.forget_endpoint()
            return out
        if path == "/v1/credentials/clear":
            out = credentials.clear(str(req.get("name") or ""), str(req.get("confirm") or ""))
            if out.get("ok") and out.get("name") == "arena_mcp_url":
                arena.forget_endpoint()
            return out
        return {"error": "不认识的路由"}

    def _arena(self, cfg, path, req):
        """Arena writes. exec stays unreachable until the owner grants it, and every
        result here goes through the same redaction as everything else."""
        if path == "/v1/arena/dispatch":
            return arena.dispatch(cfg, req.get("task"), str(req.get("model") or ""),
                                  str(req.get("note") or ""), str(req.get("mode") or "queue"))
        if path == "/v1/arena/note":
            return arena.set_note(cfg, req.get("model"), req.get("note"))
        if path == "/v1/arena/workbench":
            return arena.workbench_action(cfg, req.get("action"), req.get("instance"),
                                          str(req.get("confirm") or ""))
        if path == "/v1/arena/grant":
            return arena.set_grant(cfg, req.get("minutes"), str(req.get("confirm") or ""))
        if path == "/v1/arena/exec":
            audit.write("arena_exec_request", chars=len(str(req.get("cmd") or "")))
            return arena.exec_command(cfg, req.get("cmd"))
        return {"error": "unknown arena route"}


def main():
    token = security.token_from_env()
    if not token:
        sys.stderr.write("DFY_CORE_TOKEN missing; refusing to start an "
                         "unauthenticated listener\n")
        return 2
    port = int(os.environ.get("DFY_CORE_PORT") or "0")
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.token = token
    srv.daemon_threads = True
    sys.stdout.write("READY %d %d\n" % (srv.server_address[1], os.getpid()))
    sys.stdout.flush()
    # the plugin-side link (§6.4 独立服务 token). Losing this must not stop the GUI, so a
    # failure is logged and the rest of the core keeps running without the plugin tools.
    try:
        link.publish(srv.server_address[1])
    except Exception:
        pass
    # Auto-initialize MCP upstream client so the gateway is reachable
    # even before the GUI calls /v1/arena/overview.
    try:
        arena.client(config.load())
    except Exception:
        pass
    # 房间缓存不在安装目录里跟着版本走，但也可能一次都没生成过；开机先默默巡检一遍，
    # 免得聊天那边问模型时永远只能给兜底清单。
    try:
        from . import rooms as rooms_mod
        rooms_mod.warm_async(config.load())
    except Exception:
        pass

    def _autobackup():
        # off the READY path so startup latency never depends on copying files
        try:
            backup.autobackup_if_due(config.load())
        except Exception:
            pass

    import threading
    threading.Thread(target=_autobackup, daemon=True).start()

    def shutdown(*_):
        try:
            link.remove()   # stale pointer file = a plugin knocking on a dead port
        except Exception:
            pass
        try:
            srv.shutdown()
        except Exception:
            pass

    try:
        import signal
        for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, shutdown)
            except Exception:
                pass
    except Exception:
        pass

    if os.environ.get("DFY_CORE_WATCH_STDIN") == "1":
        # The window can be killed outright (任务管理器 / 强杀重启)。那样 before-quit 不会跑，
        # 核心就成孤儿，白占一个回环端口。父进程把那根 stdin 管道一关，我们就自己收摊。
        def _watch_parent():
            try:
                if sys.stdin is not None:
                    sys.stdin.read()
            except Exception:
                pass
            shutdown()
            os._exit(0)

        threading.Thread(target=_watch_parent, daemon=True).start()

    try:
        srv.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            link.remove()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
