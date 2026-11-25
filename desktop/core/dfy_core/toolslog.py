"""Tool-call trace, read out of AstrBot's own store (data_v4.db, read-only).

AstrBot already persists each conversation including function calls and their
results, so this stays the single source of truth: the desktop reads it, it does
not keep a second log. Two things are deliberately never surfaced here: the
model's own reasoning text and the injected system prompt — only the tool name,
an argument summary and a result summary.

AstrBot records no per-call timestamps, so 耗时 is reported as unavailable rather
than invented, and cards are listed per conversation instead of being glued under
a bubble we cannot prove they belong to.
"""

import json
import os
import sqlite3

from . import config, security

MAX_ITEMS = 60
ARGS_CAP = 160
RESULT_CAP = 260


def _path(cfg):
    return os.path.join(cfg["astrbot_dir"], "data", "data_v4.db")


def _connect(cfg):
    p = _path(cfg)
    if not os.path.isfile(p):
        return None, "data_v4.db not found"
    try:
        con = sqlite3.connect("file:%s?mode=ro" % p.replace(os.sep, "/"), uri=True, timeout=2.0)
        return con, None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:120])


def _summarize(value, cap):
    if value is None:
        return None
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = " ".join(s.split())
    return security.redact(s)[:cap]


def _conv_id(cur, umo):
    """preferences maps a umo to its selected conversation; fall back to user_id."""
    if not umo:
        return None
    try:
        row = cur.execute(
            "select value from preferences where scope='umo' and scope_id=? and key='sel_conv_id' limit 1",
            (umo,)).fetchone()
        if row:
            raw = row[0]
            try:
                d = json.loads(raw)
                return d.get("val") if isinstance(d, dict) else None
            except Exception:
                return str(raw)
    except Exception:
        pass
    try:
        row = cur.execute("select conversation_id from conversations where user_id=? "
                          "order by updated_at desc limit 1", (umo,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def available(cfg=None):
    cfg = cfg or config.load()
    return os.path.isfile(_path(cfg))


def tool_trace(cfg=None, umo="", limit=20):
    cfg = cfg or config.load()
    try:
        limit = max(1, min(int(limit or 20), MAX_ITEMS))
    except Exception:
        limit = 20
    con, err = _connect(cfg)
    if err:
        return {"error": err, "items": [], "available": False}
    try:
        cur = con.cursor()
        cid = _conv_id(cur, umo)
        if cid:
            row = cur.execute("select content, updated_at from conversations where conversation_id=?",
                              (cid,)).fetchone()
        elif umo:
            return {"error": "该会话还没有可解析的模型轨迹", "items": [], "available": True}
        else:
            row = cur.execute("select content, updated_at from conversations order by updated_at desc limit 1").fetchone()
        if not row or not row[0]:
            return {"error": "轨迹为空", "items": [], "available": True}
        try:
            msgs = json.loads(row[0])
        except Exception as e:
            return {"error": "content 解析失败：%s" % type(e).__name__, "items": [], "available": True}
        if not isinstance(msgs, list):
            return {"error": "content 结构不是消息列表", "items": [], "available": True}

        results = {}
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "tool":
                key = str(m.get("tool_call_id") or m.get("id") or "")
                results[key] = m.get("content")
        items = []
        by_name = {}
        for idx, m in enumerate(msgs):
            if not isinstance(m, dict) or m.get("role") != "assistant":
                continue
            for tc in (m.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = str(fn.get("name") or tc.get("name") or "?")
                raw_res = results.get(str(tc.get("id") or ""), None)
                res = _summarize(raw_res, RESULT_CAP)
                ok = not (res or "").strip().lower().startswith("error")
                by_name[name] = by_name.get(name, 0) + 1
                items.append({
                    "index": idx,
                    "name": name,
                    "args": _summarize(fn.get("arguments"), ARGS_CAP),
                    "ok": ok,
                    "result": res,
                    "duration_ms": None,
                    "call_id": str(tc.get("id") or "")[:24] or None,
                })
        tail = items[-limit:]
        return {
            "items": tail,
            "total_calls": len(items),
            "returned": len(tail),
            "by_name": by_name,
            "conversation_id": cid,
            "umo": umo or None,
            "conversation_updated_at": row[1],
            "available": True,
            "note": "来源 AstrBot data_v4.db（只读）；无逐条时间戳，故不显示耗时",
        }
    except Exception as e:
        return {"error": "trace failed: %s: %s" % (type(e).__name__, str(e)[:120]), "items": []}
    finally:
        try:
            con.close()
        except Exception:
            pass


def model_usage(cfg=None, limit=6):
    """Recent provider runs: what actually answered, how many tokens."""
    cfg = cfg or config.load()
    con, err = _connect(cfg)
    if err:
        return {"error": err, "runs": []}
    try:
        cur = con.cursor()
        runs = []
        try:
            cols = [r[1] for r in cur.execute("pragma table_info('provider_stats')")]
        except Exception:
            cols = []
        if not cols:
            return {"runs": [], "note": "provider_stats 不可读"}
        want = [c for c in ("agent_type", "status", "provider_id", "provider_model",
                            "token_input_other", "token_input_cached", "token_output",
                            "start_time", "end_time", "umo") if c in cols]
        sql = "select %s from provider_stats order by id desc limit %d" % (",".join(want), max(1, min(int(limit), 20)))
        for r in cur.execute(sql):
            d = dict(zip(want, r))
            st, en = d.get("start_time"), d.get("end_time")
            try:
                d["duration_s"] = round(float(en) - float(st), 1) if st and en else None
            except Exception:
                d["duration_s"] = None
            for k in ("start_time", "end_time"):
                d.pop(k, None)
            runs.append(d)
        return {"runs": runs, "columns": cols}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:120]), "runs": []}
    finally:
        try:
            con.close()
        except Exception:
            pass
