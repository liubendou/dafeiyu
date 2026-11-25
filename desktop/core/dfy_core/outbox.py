"""Desktop -> plugin send queue.

The core never talks to QQ and never holds a credential. It writes one request
file into the plugin's inbox; the plugin sends it through AstrBot's own platform
connection and moves the file to sent/ or failed/. Receipts are polled from here.

Setting DFY_OUTBOX_ROOT redirects the whole queue, which is how the self-test
proves the plumbing without putting a message on the wire.
"""

import json
import os
import re
import secrets
import time

from . import audit, config, security

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_CHARS = 1200
INDEX_NAME = "outbox-index.json"
BODY_CAP = 16384


def dirs(cfg):
    root = (os.environ.get("DFY_OUTBOX_ROOT") or cfg.get("outbox_dir")
            or os.path.join(cfg["plugin_data_dir"], "desktop_outbox"))
    return {k: os.path.join(root, k) for k in ("inbox", "sent", "failed")}


def limits(cfg):
    try:
        cap = int(cfg.get("outbox_max_chars") or MAX_CHARS)
    except Exception:
        cap = MAX_CHARS
    return {"enabled": bool(cfg.get("send_enabled", True)), "max_chars": max(1, cap)}


def _index_path(cfg):
    return os.path.join(config.runtime_dir(), INDEX_NAME)


def _load_index(cfg):
    try:
        with open(_index_path(cfg), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_index(cfg, d):
    try:
        tmp = _index_path(cfg) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(dict(list(d.items())[-200:]), fh, ensure_ascii=False)
        os.replace(tmp, _index_path(cfg))
    except Exception:
        pass


def _state(cfg, rid):
    rid = str(rid or "")
    if not ID_RE.match(rid):
        return "unknown"
    dd = dirs(cfg)
    if os.path.isfile(os.path.join(dd["sent"], rid + ".json")):
        return "sent"
    if os.path.isfile(os.path.join(dd["failed"], rid + ".json")):
        return "failed"
    if os.path.isfile(os.path.join(dd["inbox"], rid + ".json")):
        return "pending"
    return "unknown"


def submit(cfg, umo, text, idem=""):
    """Queue one outgoing message. Returns {id,state} or {error}."""
    from . import threads

    lim = limits(cfg)
    if not lim["enabled"]:
        audit.write("send_refused", reason="disabled")
        return {"error": "发送已在设置里关闭"}
    body = str(text or "").strip()
    if not body:
        return {"error": "消息为空"}
    if len(body) > lim["max_chars"]:
        return {"error": "超过 %d 字上限" % lim["max_chars"]}
    umo = str(umo or "")
    if not umo:
        return {"error": "缺少会话"}
    # Only conversations the plugin already knows about, and never an excluded one.
    hit = None
    for c in (threads.conversations(cfg).get("items") or []):
        if c.get("umo") == umo:
            hit = c
            break
    if hit is None:
        audit.write("send_refused", reason="unknown_umo")
        return {"error": "未知会话，不能发送"}
    if hit.get("excluded"):
        audit.write("send_refused", reason="excluded", target=umo)
        return {"error": "该会话按红线排除，不提供发送"}

    idx = _load_index(cfg)
    if idem and idem in idx:
        prev = str((idx[idem] or {}).get("id") or "")
        st = _state(cfg, prev)
        if st in ("pending", "sent"):
            return {"id": prev, "state": st, "deduplicated": True}

    rid = "%s-%s" % (time.strftime("%H%M%S"), secrets.token_hex(4))
    if not ID_RE.match(rid):
        return {"error": "internal id"}
    rec = {
        "id": rid,
        "umo": umo,
        "text": body,
        "created_at": int(time.time() * 1000),
        "platform": hit.get("platform"),
        "message_type": "GroupMessage" if hit.get("kind") == "group" else "FriendMessage",
        "session_id": hit.get("session_id"),
        "via": "desktop",
    }
    if idem:
        rec["idem"] = str(idem)[:64]
    inbox = dirs(cfg)["inbox"]
    try:
        os.makedirs(inbox, exist_ok=True)
        tmp = os.path.join(inbox, rid + ".json.tmp")
        final = os.path.join(inbox, rid + ".json")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
    except Exception as e:
        audit.write("send_error", error="%s: %s" % (type(e).__name__, e))
        return {"error": "队列写入失败：%s" % type(e).__name__}
    if idem:
        idx[str(idem)[:64]] = {"id": rid, "at": rec["created_at"]}
        _save_index(cfg, idx)
    audit.write("send_queued", target=umo, id=rid, chars=len(body))
    return {"id": rid, "state": "pending", "chars": len(body)}


def receipt(cfg, rid):
    st = _state(cfg, rid)
    out = {"id": str(rid or ""), "state": st}
    if not ID_RE.match(str(rid or "")):
        return out
    dd = dirs(cfg)
    for kind in ("sent", "failed"):
        p = os.path.join(dd[kind], "%s.json" % rid)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = {}
        if isinstance(data, dict):
            # the receipt may name the conversation; never echo a message body
            out["detail"] = {k: security.redact(str(data[k]))[:200]
                             for k in ("ok", "error", "chars", "at", "umo") if k in data}
        break
    return out


def queue_state(cfg):
    dd = dirs(cfg)

    def n(p):
        try:
            return len([f for f in os.listdir(p) if f.endswith(".json")])
        except Exception:
            return None

    lim = limits(cfg)
    return {"enabled": lim["enabled"], "max_chars": lim["max_chars"],
            "inbox": n(dd["inbox"]), "sent": n(dd["sent"]), "failed": n(dd["failed"]),
            "root_present": os.path.isdir(dd["inbox"]),
            "note": "发送经 AstrBot 插件的 outbox 工作者；核心不直接连 QQ、不持凭据"}
