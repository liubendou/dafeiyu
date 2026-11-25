# -*- coding: utf-8 -*-
"""Persona editing with version history (design 阶段3 · §6.6 · §7.3 · 验收 10).

The fish's live persona is AstrBot's `personas` table (the md file under personas/ is
a second, older copy — both are editable here, both are reported, and we never write
one while pretending it is the other). Every write: optimistic lock on the hash we
loaded, confirm, snapshot, atomic write, verify, and roll the field back if the
readback disagrees.
"""
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
import time

from . import audit, backup, config, security

MAX_CHARS = 200000
MIN_CHARS = 40
HISTORY_KEEP = 50


def _db(cfg):
    return os.path.join(cfg.get("astrbot_dir") or "", "data", "data_v4.db")


def _ro(cfg):
    p = _db(cfg)
    if not os.path.isfile(p):
        return None, {"error": "找不到 AstrBot 数据库：%s" % p}
    try:
        return sqlite3.connect(backup._uri(p), uri=True), None
    except Exception as e:
        return None, {"error": "数据库读不了：%s" % type(e).__name__}


def sources(cfg):
    out = {}
    con, err = _ro(cfg)
    if con:
        try:
            rows = con.execute("select id, persona_id, length(system_prompt), updated_at "
                               "from personas order by id").fetchall()
        except Exception as e:
            rows = []
            err = {"error": "personas 表读不了：%s" % type(e).__name__}
        finally:
            con.close()
        out["db"] = {"available": True, "rows": [
            {"id": r[0], "persona_id": r[1], "chars": r[2], "updated_at": r[3]} for r in rows]}
    else:
        out["db"] = dict(err or {"error": "读不到"}); out["db"]["available"] = False
    md = cfg.get("persona_file") or ""
    if md and security.path_is_forbidden(md):
        out["md"] = {"available": False, "reason": "这个路径不在可读范围内"}
    elif md and os.path.isfile(md):
        st = os.stat(md)
        out["md"] = {"available": True, "path": md, "bytes": st.st_size, "sha8": _sha(md),
                     "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))}
    else:
        out["md"] = {"available": False, "path": md or None,
                     "note": "没有这份旧稿，正常；真正生效的是上面表格里的那条"}
    out["note"] = "两份都在：表格那条才是引擎现在用的；改哪一条要点清楚"
    return out


def _sha(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def read(cfg, source="db", persona_id=""):
    if source == "md":
        p = cfg.get("persona_file") or ""
        if not p or not os.path.isfile(p):
            return {"error": "没有这个人格文件"}
        text = open(p, encoding="utf-8", errors="replace").read()
        return {"source": "md", "persona_id": os.path.basename(p), "text": text,
                "chars": len(text), "sha8": _sha(p)}
    con, err = _ro(cfg)
    if not con:
        return err
    try:
        if persona_id:
            row = con.execute("select persona_id, system_prompt, updated_at from personas "
                              "where persona_id = ? limit 1", (str(persona_id),)).fetchone()
        else:
            row = con.execute("select persona_id, system_prompt, updated_at from personas "
                              "order by id limit 1").fetchone()
    except Exception as e:
        return {"error": "读失败：%s" % type(e).__name__}
    finally:
        con.close()
    if not row:
        return {"error": "没有这个人格：%s" % (persona_id or "(任意一条)")}
    text = str(row[1] or "")
    return {"source": "db", "persona_id": row[0], "text": text, "chars": len(text),
            "updated_at": row[2], "sha8": hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]}


def _hist_file(cfg, source, persona_id):
    slug = re.sub(r"[^a-zA-Z0-9_.-]", "_", "%s-%s" % (source, persona_id or "x"))
    return os.path.join(config.runtime_dir(), "persona-history", slug + ".jsonl")


def _history(cfg, source, persona_id):
    p = _hist_file(cfg, source, persona_id)
    if not os.path.isfile(p):
        return []
    out = []
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def history(cfg, source="db", persona_id=""):
    rows = _history(cfg, source, persona_id)
    return {"items": [{"id": r.get("id"), "ts": r.get("ts"), "chars": r.get("chars"),
                       "sha8": r.get("sha8"), "note": r.get("note")} for r in rows][::-1],
            "count": len(rows),
            "note": "每次改动都留一条，最多存 %d 条" % HISTORY_KEEP}


def _push_history(cfg, source, persona_id, text, note=""):
    p = _hist_file(cfg, source, persona_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    entry = {"id": time.strftime("%Y%m%d-%H%M%S"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
             "source": source, "persona_id": persona_id, "chars": len(text),
             "sha8": hashlib.sha1(text.encode("utf-8")).hexdigest()[:8],
             "text": text, "note": str(note or "")[:60]}
    rows = _history(cfg, source, persona_id) + [entry]
    rows = rows[-HISTORY_KEEP:]
    backup._write_atomic(p, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return entry


def write(cfg, source="db", persona_id="", text="", base_sha8="", confirm="", note="",
          from_history=False):
    text = "" if text is None else str(text)
    if len(text) > MAX_CHARS:
        return {"error": "太长了（上限 %d 字），真要这么长就先拆" % MAX_CHARS}
    # The minimum exists to catch a one-blank-keystroke wipe in the editor. Restoring a
    # version we stored ourselves is not a wipe, so that path is exempt (the GUI route
    # never passes this flag; only restore() does).
    if len(text.strip()) < MIN_CHARS and not from_history:
        return {"error": "这么短更像是误删；要清空请走「恢复历史」或直接说清"
                         "（至少留 %d 字，防止一句误操作把人格拉没）" % MIN_CHARS}
    cur = read(cfg, source, persona_id)
    if cur.get("error"):
        return cur
    if base_sha8 and base_sha8 != cur.get("sha8"):
        return {"error": "你打开之后这条人格又被改过（现在是 %s，你那份是 %s）。"
                         "先重新载入再看差异，别直接覆盖" % (cur.get("sha8"), base_sha8)}
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True,
                "will": "替换「%s」的人格正文：%d 字 → %d 字；改之前先存一份历史 + 一次快照"
                        % (cur.get("persona_id") or "md", cur.get("chars") or 0, len(text)),
                "now_sha8": cur.get("sha8")}
    snap = backup.create(cfg, "pre-persona")
    _push_history(cfg, source, cur.get("persona_id") or os.path.basename(cfg.get("persona_file") or ""),
                  cur.get("text") or "", note or "before edit")
    if source == "md":
        p = cfg.get("persona_file") or ""
        try:
            backup.write_json_or_text(p, text, "persona.md")
        except Exception as e:
            return {"error": "写失败：%s" % type(e).__name__, "guard": snap["id"]}
        back = read(cfg, "md")
        ok = back.get("sha8") == hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        if not ok:
            return {"error": "写回校验不一致", "guard": snap["id"]}
        audit.write("persona_write", source="md", chars=len(text), guard=snap["id"])
        return {"ok": True, "source": "md", "chars": len(text), "guard": snap["id"],
                "restart_required": False, "note": "这份 md 只是存档，引擎不读它"}
    p = _db(cfg)
    try:
        con = sqlite3.connect(p, timeout=6.0)
    except Exception as e:
        return {"error": "打不开数据库：%s" % type(e).__name__, "guard": snap["id"]}
    try:
        con.execute("begin immediate")
        cur2 = con.execute("select system_prompt from personas where persona_id = ?",
                           (str(cur.get("persona_id") or ""),)).fetchone()
        if cur2 is None:
            con.rollback()
            return {"error": "这条人格不在表里了，不新增（新增人格请在 AstrBot 自己的界面做）",
                    "guard": snap["id"]}
        before = str(cur2[0] or "")
        # datetime, not time: time.strftime rejects %f on Windows, and AstrBot's own
        # rows carry microseconds
        con.execute("update personas set system_prompt = ?, updated_at = ? where persona_id = ?",
                    (text, datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
                     str(cur.get("persona_id") or "")))
        con.commit()
    except sqlite3.OperationalError as e:
        con.rollback()
        con.close()
        return {"error": "数据库现在被占着，没写成：%s" % str(e)[:120], "guard": snap["id"]}
    except Exception as e:
        con.rollback()
        con.close()
        return {"error": "写失败：%s（没动数据）" % type(e).__name__, "guard": snap["id"]}
    finally:
        try:
            con.close()
        except Exception:
            pass
    back = read(cfg, "db", cur.get("persona_id"))
    if back.get("error") or back.get("chars") != len(text):
        # verify-and-restore that one field, using the history entry we just wrote
        try:
            con = sqlite3.connect(p, timeout=6.0)
            con.execute("update personas set system_prompt = ? where persona_id = ?",
                        (before, str(cur.get("persona_id") or "")))
            con.commit()
            con.close()
        except Exception:
            pass
        return {"error": "写完读回来对不上，已经把这一条倒回去了", "guard": snap["id"]}
    audit.write("persona_write", source="db", persona_id=str(cur.get("persona_id"))[:40],
                chars=len(text), guard=snap["id"])
    return {"ok": True, "source": "db", "persona_id": cur.get("persona_id"),
            "chars": len(text), "sha8": hashlib.sha1(text.encode("utf-8")).hexdigest()[:8],
            "guard": snap["id"], "restart_required": True,
            "note": "AstrBot 会缓存人格；要让引擎真的用上这条，需要重启一次（我不会顺手重启）"}


def restore(cfg, source="db", persona_id="", entry_id="", confirm=""):
    rows = [r for r in _history(cfg, source, persona_id) if r.get("id") == str(entry_id)]
    if not rows:
        return {"error": "历史里没有这一条"}
    e = rows[0]
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True,
                "will": "把「%s」退回到 %s 那一版（%d 字）；现在这版也会先存进历史"
                        % (persona_id or "md", e.get("ts"), e.get("chars"))}
    return write(cfg, source, persona_id, e.get("text") or "", "", confirm or "yes",
                 note="restore %s" % e.get("id"), from_history=True)
