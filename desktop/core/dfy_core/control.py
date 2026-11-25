"""Control queue: 已读 / 暂停 / 恢复 / 唤醒 / 睡眠, through the plugin's own store.

Same shape as sending: drop one JSON file into the plugin's queue, let the plugin
call its existing SocialStore methods, read the receipt back. The desktop never
edits state.json, so there is no chance of racing the in-memory copy.
"""

import json
import os
import re
import secrets
import time

from . import audit, config, outbox, security

ACTIONS = {"mark_read", "pause", "resume", "wake", "sleep"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def dirs(cfg):
    root = (os.environ.get("DFY_OUTBOX_ROOT") or cfg.get("outbox_dir")
            or os.path.join(cfg["plugin_data_dir"], "desktop_outbox"))
    c = os.path.join(root, "control")
    return {k: os.path.join(c, k) for k in ("inbox", "sent", "failed")}


def submit(cfg, action, umo="", args=None):
    action = str(action or "").strip()
    if action not in ACTIONS:
        return {"error": "不支持的动作：%s" % security.redact(action)[:40]}
    umo = str(umo or "").strip()
    if action in ("mark_read", "wake", "sleep") and not umo:
        return {"error": "这个动作需要指定会话"}
    if action in ("pause", "resume"):
        umo = ""
    clean = {}
    if isinstance(args, dict):
        for k, v in args.items():
            if k in ("through_seq", "minutes") and isinstance(v, (int, float, str)):
                try:
                    clean[k] = max(0, int(float(v)))
                except Exception:
                    return {"error": "参数 %s 不是数字" % k}
    if action == "wake" and clean.get("minutes", 0) > 24 * 60:
        return {"error": "唤醒上限 1440 分钟"}
    if action == "sleep" and clean.get("minutes", 0) > 7 * 24 * 60:
        return {"error": "睡眠上限 10080 分钟"}
    if umo:
        from . import threads
        hit = next((c for c in (threads.conversations(cfg).get("items") or []) if c.get("umo") == umo), None)
        if hit is None:
            audit.write("control_refused", reason="unknown_umo", action=action)
            return {"error": "未知会话"}
        if hit.get("excluded"):
            audit.write("control_refused", reason="excluded", action=action)
            return {"error": "该会话按红线排除"}

    rid = "%s-%s" % (time.strftime("%H%M%S"), secrets.token_hex(4))
    if not ID_RE.match(rid):
        return {"error": "internal id"}
    rec = {"kind": "control", "id": rid, "action": action, "umo": umo or None,
           "args": clean, "created_at": int(time.time() * 1000), "via": "desktop"}
    d = dirs(cfg)["inbox"]
    try:
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, rid + ".json.tmp")
        final = os.path.join(d, rid + ".json")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
    except Exception as e:
        audit.write("control_error", error="%s: %s" % (type(e).__name__, e))
        return {"error": "队列写入失败：%s" % type(e).__name__}
    audit.write("control_queued", action=action, target=umo or "-", id=rid)
    return {"id": rid, "state": "pending", "action": action}


def receipt(cfg, rid):
    rid = str(rid or "")
    out = {"id": rid, "state": "unknown"}
    if not ID_RE.match(rid):
        return out
    dd = dirs(cfg)
    for kind in ("sent", "failed", "inbox"):
        p = os.path.join(dd[kind], rid + ".json")
        if not os.path.isfile(p):
            continue
        out["state"] = {"sent": "sent", "failed": "failed", "inbox": "pending"}[kind]
        if kind in ("sent", "failed"):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                detail = {}
                for k in ("ok", "error", "action", "result"):
                    if k not in data:
                        continue
                    v = data[k]
                    if isinstance(v, (dict, list)):
                        # keep the shape the UI reads (e.g. result.cleared), still redacted
                        try:
                            v = json.loads(security.redact(json.dumps(v, ensure_ascii=False))[:500])
                        except Exception:
                            v = security.redact(str(v))[:160]
                    else:
                        v = security.redact(str(v))[:160]
                    detail[k] = v
                out["detail"] = detail
            except Exception:
                pass
        break
    else:
        # control receipts share the state machine's shape with sends
        st = outbox._state(cfg, rid)
        if st in ("sent", "failed"):
            out["state"] = st
    return out
