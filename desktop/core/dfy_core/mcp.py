"""Minimal MCP client (streamable HTTP, JSON-RPC) — standard library only.

This replaces the old control plane's in-file client. Two rules it exists to
enforce: the endpoint URL and its token are never echoed into an exception, a
log line or a response body, and every call carries a timeout so a wedged
upstream can never hang the desktop core.
"""

import json
import re
import urllib.error
import urllib.request

from . import security

INIT_TIMEOUT = 12
CALL_TIMEOUT = 60
MAX_REPLY = 20000
# tools/list 一开口就是几十 KB（69 个工具带描述）。原来拿 MAX_REPLY 当读上限，
# 正文被截成半截 JSON，界面只好报「响应不是合法 JSON（HTTP 200）」，Arena 页于是长期显示网关未连通。
# 读的时候放宽到 1 MB，真正还给界面的正文仍由 call() 按 MAX_REPLY 截断。
MAX_BODY = 1000000


class McpError(Exception):
    """Message is always safe to show: scrubbed and never contains the endpoint."""


def _safe(text):
    s = str(text or "")
    s = re.sub(r"https?://\S+", lambda m: security.redact(m.group(0)), s)
    return security.redact(s)[:400]


class Mcp:
    def __init__(self, endpoint, timeout=CALL_TIMEOUT):
        self.endpoint = str(endpoint or "")
        self.timeout = timeout
        self.session = None
        self.initialized = False

    # ------------------------------------------------------------------ transport
    def _post(self, payload, timeout=None):
        if not self.endpoint:
            raise McpError("网关未配置上游地址")
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self.session:
            req.add_header("Mcp-Session-Id", self.session)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session = sid
                raw = resp.read(MAX_BODY + 1).decode("utf-8", "replace")
                code = resp.getcode()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(2000).decode("utf-8", "replace")
            except Exception:
                pass
            msg = _safe("HTTP %s %s %s" % (e.code, e.reason, detail))
            raise McpError(msg)
        except Exception as e:
            raise McpError(_safe("%s: %s" % (type(e).__name__, e)))
        # some servers answer SSE framing on the same POST
        if raw.lstrip().startswith("event:") or "\ndata: " in raw[:200]:
            chunks = [l[5:].strip() for l in raw.splitlines() if l.startswith("data:")]
            raw = chunks[-1] if chunks else ""
        if len(raw) > MAX_BODY:
            raise McpError("响应过大（%d 字节上限）" % MAX_BODY)
        try:
            doc = json.loads(raw) if raw.strip() else {}
        except Exception:
            raise McpError("响应不是合法 JSON（HTTP %s）" % code)
        return doc

    # ------------------------------------------------------------------ protocol
    def init(self):
        doc = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05",
                                     "capabilities": {},
                                     "clientInfo": {"name": "dafeiyu-desktop", "version": "0.2"}}},
                         timeout=INIT_TIMEOUT)
        if isinstance(doc, dict) and doc.get("error"):
            raise McpError(_safe(json.dumps(doc["error"], ensure_ascii=False)))
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except McpError:
            pass  # a server that ignores the notification is still usable
        self.initialized = True
        server = (doc.get("result") or {}).get("serverInfo") if isinstance(doc, dict) else None
        return server if isinstance(server, dict) else {}

    def tools(self):
        doc = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        if isinstance(doc, dict) and doc.get("error"):
            raise McpError(_safe(json.dumps(doc["error"], ensure_ascii=False)))
        out = []
        for t in ((doc.get("result") or {}).get("tools") or []) if isinstance(doc, dict) else []:
            if not isinstance(t, dict):
                continue
            out.append({"name": str(t.get("name") or "")[:64],
                        "title": str(t.get("title") or t.get("name") or "")[:64],
                        "annotation": str(t.get("readOnlyHint") or t.get("destructiveHint") or "")[:16]})
        return out

    def call(self, tool, args=None, timeout=None):
        """tools/call -> (ok, text). Text is already redacted and length-capped."""
        payload = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": str(tool), "arguments": args or {}}}
        doc = self._post(payload, timeout=timeout)
        if isinstance(doc, dict) and doc.get("error"):
            return False, _safe(json.dumps(doc["error"], ensure_ascii=False))
        res = (doc.get("result") or {}) if isinstance(doc, dict) else {}
        parts = []
        for c in (res.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(str(c.get("text") or ""))
        text = "\n".join(parts).strip()
        if len(text) > MAX_REPLY:
            text = text[:MAX_REPLY] + "…（已截断）"
        if res.get("isError"):
            return False, _safe(text)
        # redacted, but not length-crippled: 400 chars was fine for errors and
        # wrong for task replies the owner actually wants to read.
        return True, security.redact(text)

    def call_json(self, tool, args=None, timeout=None):
        ok, text = self.call(tool, args, timeout=timeout)
        try:
            return ok, json.loads(text)
        except Exception:
            return ok, {"text": text[:1200]}
