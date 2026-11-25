"""Stage 1: conversation threads and the group roster.

Deliberately NOT in readers.py: readers.status() is the stage-0 payload and a test
asserts it carries no message body. Everything here does carry text, so it lives
behind its own routes, is redacted, length-capped, and never enters backups.
"""

import time

from . import audit, config, readers, security

TEXT_CAP = 500
LIST_CAP = 200
_MEDIA = ("\u56fe\u7247", "\u8bed\u97f3", "\u89c6\u9891", "\u8868\u60c5", "\u6587\u4ef6", "\u94fe\u63a5")


def _iso(ms):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ms) / 1000.0))
    except Exception:
        return None


def _forbidden(cfg):
    out = {str(x) for x in (cfg.get("excluded_ids") or [])}
    fg = (cfg.get("expected_whitelist") or {}).get("forbidden_group")
    if fg:
        out.add(str(fg))
    return out


def _allowed_ids(cfg):
    try:
        wl = readers.whitelist_report(cfg)
    except Exception:
        return set()
    out = set()
    for key in ("astrbot_id_whitelist", "plugin_allowed_groups"):
        for x in (wl.get(key) or []):
            out.add(str(x))
    return out


def _conv_map(cfg):
    data, err = readers.read_json_capped(config.state_file(cfg))
    if err:
        return None, {}, err
    convs = {}
    if isinstance(data, dict):
        for key in ("conversations", "sessions", "chats"):
            v = data.get(key)
            if isinstance(v, dict):
                convs = v
                break
            if isinstance(v, list):
                convs = {str(i): x for i, x in enumerate(v) if isinstance(x, dict)}
                break
    return data, convs, None


def _msgs_of(conv):
    for k in ("recent", "messages"):
        v = conv.get(k)
        if isinstance(v, list):
            return v
    return []


def _kind_of(conv, key):
    mt = str(conv.get("message_type") or key or "")
    return "group" if "group" in mt.lower() else "private"


def _brief(key, conv, allowed, forbidden):
    msgs = _msgs_of(conv)
    unread = conv.get("unread")
    if isinstance(unread, list):
        unread = len(unread)
    wake = conv.get("wake") if isinstance(conv.get("wake"), dict) else {}
    ident = str(conv.get("session_id") or "")
    incoming = conv.get("last_incoming_at")
    return {
        "umo": str(key),
        "kind": _kind_of(conv, key),
        "label": conv.get("label") or ident or str(key),
        "platform": conv.get("platform"),
        "session_id": ident,
        "message_count": len(msgs),
        "unread": unread if isinstance(unread, int) and not isinstance(unread, bool) else None,
        "last_read_seq": conv.get("last_read_seq"),
        "seq": conv.get("seq"),
        "paused": bool(conv.get("paused")),
        "wake_mode": wake.get("mode"),
        "wake_count": wake.get("wake_count"),
        "sleep_until": wake.get("sleep_until"),
        "last_incoming_at": incoming,
        "last_incoming_iso": _iso(incoming) if incoming else None,
        "last_ai_reply_at": conv.get("last_ai_reply_at"),
        # cmd_config stores the owner as a full "platform:FriendMessage:<qq>"
        # string but groups as bare ids, so match both shapes exactly.
        "in_whitelist": bool(ident) and (ident in allowed or any(
            str(a) == str(key) or str(a).endswith(":" + ident) for a in allowed)),
        "excluded": bool(ident) and ident in forbidden,
    }


def conversations(cfg=None):
    cfg = cfg or config.load()
    data, convs, err = _conv_map(cfg)
    if err:
        return {"error": err, "items": []}
    allowed, forbidden = _allowed_ids(cfg), _forbidden(cfg)
    items = [_brief(k, v, allowed, forbidden) for k, v in convs.items() if isinstance(v, dict)]
    items.sort(key=lambda x: -(x.get("last_incoming_at") or 0))
    return {
        "items": items,
        "total": len(items),
        "global_paused": bool(data.get("paused")) if isinstance(data, dict) else False,
        "state_updated_iso": _iso(data.get("updated_at")) if isinstance(data, dict) else None,
    }


def _media_of(text):
    return [t for t in _MEDIA if ("[" + t + "]") in text]


def messages(cfg=None, umo="", limit=50):
    cfg = cfg or config.load()
    if not isinstance(umo, str) or not umo:
        return {"error": "\u7f3a\u5c11\u4f1a\u8bdd\u53c2\u6570", "items": []}
    data, convs, err = _conv_map(cfg)
    if err:
        return {"error": err, "items": []}
    conv = convs.get(umo)
    if not isinstance(conv, dict):
        # An unknown key is simply unknown: never treated as a path or id to probe.
        return {"error": "\u4f1a\u8bdd\u4e0d\u5b58\u5728", "items": []}
    ident = str(conv.get("session_id") or "")
    if ident and ident in _forbidden(cfg):
        audit.write("denied", path="/v1/messages", reason="excluded")
        return {"error": "\u8be5\u4f1a\u8bdd\u6309\u7ea2\u7ebf\u6392\u9664\uff0c\u4e0d\u63d0\u4f9b\u5185\u5bb9", "items": []}
    try:
        limit = int(limit)
    except Exception:
        limit = 50
    limit = max(1, min(limit, LIST_CAP))
    msgs = _msgs_of(conv)
    items = []
    for m in msgs[-limit:]:
        if not isinstance(m, dict):
            continue
        raw = "" if m.get("text") is None else str(m.get("text"))
        at = m.get("at")
        items.append({
            "seq": m.get("seq"),
            "at": at,
            "iso": _iso(at) if at else None,
            "from_me": bool(m.get("is_self")),
            "is_owner": bool(m.get("is_owner")),
            "sender_name": security.redact(str(m.get("sender_name") or ""))[:60] or None,
            "text": security.redact(raw)[:TEXT_CAP],
            "truncated": len(raw) > TEXT_CAP,
            "media": _media_of(raw),
            "message_id": str(m.get("message_id") or "")[:32] or None,
        })
    unread = conv.get("unread")
    return {
        "umo": umo,
        "label": conv.get("label"),
        "kind": _kind_of(conv, umo),
        "total": len(msgs),
        "returned": len(items),
        "unread": len(unread) if isinstance(unread, list) else unread,
        "last_read_seq": conv.get("last_read_seq"),
        "wake_mode": (conv.get("wake") or {}).get("mode") if isinstance(conv.get("wake"), dict) else None,
        "items": items,
    }


def groups(cfg=None):
    cfg = cfg or config.load()
    data, convs, err = _conv_map(cfg)
    wl = {}
    try:
        wl = readers.whitelist_report(cfg)
    except Exception as e:
        wl = {"errors": ["whitelist_report: %s" % type(e).__name__]}
    allowed, forbidden = _allowed_ids(cfg), _forbidden(cfg)
    rows, skipped = {}, 0
    if not err:
        for k, v in convs.items():
            if not isinstance(v, dict):
                continue
            b = _brief(k, v, allowed, forbidden)
            if b["kind"] != "group":
                continue
            if b["session_id"] in forbidden:
                skipped += 1
                continue
            rows[b["session_id"] or b["umo"]] = b
    for gid in sorted(allowed):
        if gid.isdigit() and gid not in forbidden and gid not in rows:
            rows[gid] = {"umo": None, "kind": "group", "label": "\u7fa4 " + gid, "session_id": gid,
                         "platform": None, "message_count": 0, "unread": None, "last_read_seq": None,
                         "seq": None, "paused": False, "wake_mode": None, "wake_count": None,
                         "sleep_until": None, "last_incoming_at": None, "last_incoming_iso": None,
                         "last_ai_reply_at": None, "in_whitelist": True, "excluded": False}
    want = cfg.get("expected_whitelist") or {}
    return {
        "items": sorted(rows.values(), key=lambda x: str(x.get("session_id"))),
        "expected_groups": want.get("groups") or [],
        "forbidden_group": want.get("forbidden_group"),
        "excluded_from_list": skipped,
        "checks": wl.get("checks"),
        "all_ok": wl.get("all_ok"),
        "state_error": err or None,
    }
