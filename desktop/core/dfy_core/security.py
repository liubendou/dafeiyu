"""Startup token, Host/Origin checks, redaction, and the structural secret deny-list.

The deny-list exists so "never read the secret files" is enforced by code rather
than by remembering to be careful.
"""

import hmac
import json
import os
import re
import secrets

TOKEN_ENV = "DFY_CORE_TOKEN"
MASK = "***"

# Any path containing one of these is unreadable by this process, even if the
# caller asks for it. Deliberately broader than strictly necessary.
DENY_PATH_SUBSTRINGS = (
    "mcp_url.txt",
    "webui.json",
    "dashboard_credentials",
    "passkey.json",
    "credential",
    ".enc",
    ".pem",
    ".key",
    "id_rsa",
    "cookie",
)

# Keys whose values must never leave the core, whatever the caller asked for.
REDACT_KEY = re.compile(
    r"(?i)(token|password|passwd|secret|cookie|authorization|credential"
    r"|passkey|access[_-]?key|api[_-]?key|private[_-]?key|qrcode|qr_code)"
)

# Bare secrets that can appear inside free-form text (log lines, error strings).
REDACT_VALUE = [
    (re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]{8,}"), r"\1" + MASK),
    (re.compile(r"(?i)(authorization\s*[:=]\s*)\S+"), r"\1" + MASK),
    (re.compile(r"\b[0-9a-f]{32,}\b"), MASK),
    # 自由文本里的 key=value / token:value 形状（隧道 URL 上的令牌就是这种写法）
    (re.compile(r"(?i)\b(token|api[_-]?key|apikey|access[_-]?key|secret|passwd|password)"
                r"(\s*[:=]\s*)[^\s&,\"']+"), r"\1\2***"),
    # OpenAI 形状的真 key：sk-...，日志里最常见的一次泄漏就是这么进来的
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{10,}\b"), MASK),
    # 已经序列化成一行的配置片段：{"api_key": "…"} / {"token": "…"}
    (re.compile(r'(?i)("(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|secret|token'
                r'|access_token|refresh_token|password|passwd|authorization|cookie|credentials?)"'
                r'\s*:\s*")[^"]*(")'), r"\1***\2"),
]


def new_token():
    return secrets.token_urlsafe(48)


def token_from_env():
    return (os.environ.get(TOKEN_ENV) or "").strip()


def token_ok(expected, given):
    if not expected:
        return False
    return hmac.compare_digest(str(expected), str(given or ""))


def path_is_forbidden(path):
    p = str(path or "").replace("\\", "/").lower()
    return any(s in p for s in DENY_PATH_SUBSTRINGS)


_RUNTIME_SCRUB = []


def add_scrub(value):
    """Register a secret learned at runtime (the gateway endpoint, its bearer token)
    so that every egress path masks it. Kept in memory only — never written out.
    """
    v = str(value or "")
    if len(v) >= 6 and v not in _RUNTIME_SCRUB:
        _RUNTIME_SCRUB.append(v)


def _scrub_text(s):
    for pat, repl in REDACT_VALUE:
        s = pat.sub(repl, s)
    for secret in _RUNTIME_SCRUB:
        if secret in s:
            s = s.replace(secret, MASK)
    return s


def redact(obj, _depth=0):
    """Recursively strip secret-looking keys/values. Safe to call on anything."""
    if _depth > 12:
        return MASK
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if REDACT_KEY.search(str(k)):
                out[k] = MASK
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, _depth + 1) for v in obj]
    if isinstance(obj, str):
        return _scrub_text(obj)
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return _scrub_text(str(obj))


def json_dumps_safe(obj):
    """Never fail on encoding, never leak through a stray encoder path."""
    try:
        return json.dumps(redact(obj), ensure_ascii=False, default=str,
                          separators=(",", ":"))
    except Exception:
        return '{"ok":false,"error":"encode_failure"}'


def host_ok(host_header, port):
    """Reject DNS-rebinding style callers: only loopback names for our own port."""
    h = str(host_header or "").strip().lower()
    if not h:
        return False
    if h.startswith("["):  # [::1]:1234
        return h.startswith("[::1]:") and h.endswith(":%d" % port)
    if ":" not in h:
        return h in ("127.0.0.1", "localhost")
    name, _, hp = h.rpartition(":")
    if hp != str(port):
        return False
    return name in ("127.0.0.1", "localhost")


def origin_ok(origin_header, port):
    """The core is called by the Electron main process only, so a browser Origin
    is unexpected by default. The Vite dev server origin is allowed explicitly."""
    if not origin_header:
        return True
    o = str(origin_header).strip().lower()
    allowed = (
        "http://127.0.0.1:%d" % port,
        "http://localhost:%d" % port,
        "http://127.0.0.1:5199",
        "http://localhost:5199",
        "null",
    )
    return o in allowed
