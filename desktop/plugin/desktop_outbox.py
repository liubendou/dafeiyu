"""Desktop outbox: the only sanctioned way for the 大肥鱼 desktop app to send QQ.

The desktop writes one JSON file per request into
``<plugin_data>/astrbot_plugin_dafeiyu_social/desktop_outbox/inbox/``. This worker
sends it through AstrBot's own platform connection, then moves the request to
``sent/`` or ``failed/`` with a receipt the desktop polls.

``control/inbox`` uses the same contract for the things that must go through the
plugin's own store instead of being edited from outside: 标记已读、暂停/恢复、
唤醒/睡眠. That keeps one owner of ``state.json`` and means a desktop pause can
never be bypassed by a queued send (and a resume can still get through while
replies are paused, because control is not gated by ``is_paused``).

Why this shape: no new listener, no token on the desktop side, and the whitelist
gate is the plugin's existing ``_is_allowed_umo`` — a group the owner never
enabled cannot be reached from the desktop either, by construction rather than by
a second copy of the rules.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from astrbot.api.event import MessageChain

MAX_BODY = 1200
PER_CYCLE = 2
CYCLE_SECONDS = 1.2
STALE_SECONDS = 900
MAX_INBOX = 20
CONTROL_PER_CYCLE = 4
CONTROL_ACTIONS = ("mark_read", "pause", "resume", "wake", "sleep")
ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class DesktopOutbox:
    def __init__(self, plugin, root: Path, logger) -> None:
        self.plugin = plugin
        self.root = Path(root)
        self.logger = logger
        self.inbox = self.root / "inbox"
        self.sent = self.root / "sent"
        self.failed = self.root / "failed"
        self.c_in = self.root / "control" / "inbox"
        self.c_sent = self.root / "control" / "sent"
        self.c_fail = self.root / "control" / "failed"
        self._task = None
        self.sent_total = 0
        self.failed_total = 0
        self.control_total = 0

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        for d in (self.inbox, self.sent, self.failed, self.c_in, self.c_sent, self.c_fail):
            d.mkdir(parents=True, exist_ok=True)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="dafeiyu-desktop-outbox")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    def status(self) -> dict:
        return {
            "running": bool(self._task and not self._task.done()),
            "inbox": len(list(self.inbox.glob("*.json"))) if self.inbox.is_dir() else 0,
            "sent_total": self.sent_total,
            "failed_total": self.failed_total,
            "control_inbox": len(list(self.c_in.glob("*.json"))) if self.c_in.is_dir() else 0,
            "control_total": self.control_total,
        }

    # -------------------------------------------------------------------- loop
    async def _run(self) -> None:
        while True:
            try:
                await self._cycle()
                self._control_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a bad request must never kill the worker
                self.logger.warning("desktop outbox cycle failed: %s", exc)
            await asyncio.sleep(CYCLE_SECONDS)

    async def _cycle(self) -> None:
        if not self.inbox.is_dir():
            return
        if self.plugin.store.is_paused():
            # 主人把社交停了，桌面的手不能绕过它：请求留在队列里，不丢也不发。
            return
        names = sorted(p.name for p in self.inbox.glob("*.json"))
        if not names:
            return
        if len(names) > MAX_INBOX:
            self.logger.warning("desktop outbox backlog %d, serving %d per cycle", len(names), PER_CYCLE)
        served = 0
        now = int(time.time() * 1000)
        for name in names:
            if served >= PER_CYCLE:
                break
            path = self.inbox / name
            rid = name[:-5] if name.endswith(".json") else name
            try:
                req = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.failed_total += 1
                self._move(path, self.failed, {"id": rid, "ok": False,
                                               "error": "unreadable request: %s" % exc,
                                               "at": now})
                served += 1
                continue
            rid = self._rid(req, rid)
            if rid is None:
                self.failed_total += 1
                self._move(path, self.failed, {"id": name[:40], "ok": False, "error": "bad id", "at": now})
                served += 1
                continue
            if now - int(req.get("created_at") or 0) > STALE_SECONDS * 1000:
                self.failed_total += 1
                self._move(path, self.failed, {"id": rid, "ok": False, "error": "stale", "at": now})
                served += 1
                continue
            res = await self._send_one(req)
            res["id"] = rid
            res["at"] = int(time.time() * 1000)
            if res.get("ok"):
                self.sent_total += 1
                self._move(path, self.sent, res)
            else:
                self.failed_total += 1
                self._move(path, self.failed, res)
            served += 1

    async def _send_one(self, req: dict) -> dict:
        umo = str(req.get("umo") or "")
        body = str(req.get("text") or "").strip()
        if not body:
            return {"ok": False, "error": "消息为空"}
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY]
        if not self.plugin._is_allowed_umo(umo):
            return {"ok": False, "error": "目标不在白名单"}
        try:
            ok = await self.plugin.context.send_message(umo, MessageChain().message(body))
        except Exception as exc:
            self.logger.warning("desktop outbox send raised: %s", exc)
            return {"ok": False, "error": "发送异常：%s" % type(exc).__name__}
        if not ok:
            return {"ok": False, "error": "平台未找到目标会话或发送失败"}
        # Bookkeeping only after a confirmed send, and a bookkeeping failure must
        # not be reported as a send failure (that would invite a duplicate send).
        try:
            self.plugin.store.record_outgoing(
                umo,
                text=body,
                message_id="desk-%s" % str(req.get("id") or "")[:16],
                platform=str(req.get("platform") or ""),
                message_type=str(req.get("message_type") or ""),
                session_id=str(req.get("session_id") or ""),
            )
        except Exception as exc:
            self.logger.warning("desktop outbox record failed (already sent): %s", exc)
        return {"ok": True, "chars": len(body), "umo": umo}

    # ----------------------------------------------------------------- control
    def _control_cycle(self) -> int:
        """Serve 已读/暂停/恢复/唤醒/睡眠. Deliberately NOT gated by is_paused."""
        if not self.c_in.is_dir():
            return 0
        names = sorted(p.name for p in self.c_in.glob("*.json"))[:CONTROL_PER_CYCLE]
        done = 0
        for name in names:
            path = self.c_in / name
            rid = name[:-5] if name.endswith(".json") else name
            try:
                req = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(req, dict):
                    raise ValueError("not an object")
            except Exception as exc:
                self.control_total += 1
                self._move(path, self.c_fail, {"id": rid, "ok": False,
                                                "error": "unreadable request: %s" % exc,
                                                "at": int(time.time() * 1000)})
                done += 1
                continue
            rid = self._rid(req, rid) or name[:40]
            res = self._apply_control(req)
            res["id"] = rid
            res["action"] = str(req.get("action") or "")
            res["at"] = int(time.time() * 1000)
            self.control_total += 1
            self._move(path, self.c_sent if res.get("ok") else self.c_fail, res)
            self.logger.info("desktop control %s -> %s", res["action"],
                             "ok" if res.get("ok") else res.get("error"))
            done += 1
        return done

    def _apply_control(self, req: dict) -> dict:
        action = str(req.get("action") or "")
        if action not in CONTROL_ACTIONS:
            return {"ok": False, "error": "不支持的动作：%s" % action[:30]}
        store = getattr(self.plugin, "store", None)
        if store is None:
            return {"ok": False, "error": "store 尚未就绪"}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        umo = str(req.get("umo") or "").strip()

        def _int(key, default=0):
            try:
                return max(0, int(float(args.get(key, default))))
            except Exception:
                return default

        try:
            if action == "mark_read":
                if not self.plugin._is_allowed_umo(umo):
                    return {"ok": False, "error": "目标不在白名单"}
                removed = int(store.mark_read(umo, through_seq=_int("through_seq")))
                return {"ok": True, "chars": removed, "result": {"cleared": removed}, "umo": umo}
            if action in ("pause", "resume"):
                paused = bool(store.set_paused(action == "pause"))
                return {"ok": True, "result": {"paused": paused}}
            if not self.plugin._is_allowed_umo(umo):
                return {"ok": False, "error": "目标不在白名单"}
            reason = str(req.get("reason") or "desktop")[:40]
            if action == "wake":
                info = store.wake(umo, minutes=_int("minutes"), reason=reason)
            else:
                info = store.sleep(umo, minutes=_int("minutes") or 30, reason=reason)
            brief = {}
            if isinstance(info, dict):
                for key in ("mode", "until", "sleep_until", "infinite"):
                    if info.get(key) is not None:
                        brief[key] = info[key]
            return {"ok": True, "result": brief, "umo": umo}
        except Exception as exc:  # a bad control file must not kill the worker
            return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _rid(req: dict, fallback: str) -> str | None:
        for cand in (str(req.get("id") or ""), str(fallback or "")):
            cand = cand.strip()
            if cand and len(cand) <= 40 and set(cand) <= ID_CHARS:
                return cand
        return None

    def _move(self, src: Path, dst_dir: Path, payload: dict) -> None:
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            (dst_dir / ("%s.json" % payload["id"])).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            self.logger.warning("desktop outbox receipt write failed: %s", exc)
        try:
            src.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.logger.warning("desktop outbox cleanup failed: %s", exc)
