# -*- coding: utf-8 -*-
"""Long-memory browse/search, strictly read-only (design 阶段3 · §6.6 事实源不变).

The store is the plugin's own `long-memory.db`. We do not copy it, do not open it for
writing, and do not assume its schema: we find the biggest table that has a text column
and query that. If the layout ever changes, the page says so instead of showing nothing.
"""
import glob
import json
import os
import re
import sqlite3

from . import config, security

TEXT_COLS = ("content", "text", "message", "body", "summary", "note", "key", "value")
MAX_ROWS = 50


def _state_dir(cfg):
    """Where the plugin puts its own state is the plugin's config's business:
    `legacy_state_dir` from the plugin config file (relative paths are relative to
    that file, which is how AstrBot resolves them). We follow the same rule instead of
    guessing a directory that happens to exist on this machine.
    """
    pc = cfg.get("plugin_config") or ""
    if not pc or not os.path.isfile(pc):
        return None
    try:
        doc = json.load(open(pc, encoding="utf-8-sig"))
        sd = str((doc or {}).get("legacy_state_dir") or "").strip()
    except Exception:
        return None
    if not sd:
        return None
    sd = os.path.expandvars(os.path.expanduser(sd))
    if not os.path.isabs(sd):
        sd = os.path.abspath(os.path.join(os.path.dirname(pc), sd))
    return sd


def candidates(cfg):
    out = []
    sd = _state_dir(cfg)
    if sd:
        out.append(os.path.join(sd, "long-memory.db"))
    base = cfg.get("plugin_data_dir") or ""
    if base:
        out.append(os.path.join(base, "long-memory.db"))
        out += sorted(glob.glob(os.path.join(base, "*", "long-memory.db")))
    return out


def db_path(cfg):
    p = os.environ.get("DFY_MEMORY_DB") or cfg.get("memory_db")
    if p and os.path.isfile(p):
        return p
    for cand in candidates(cfg):
        if os.path.isfile(cand):
            return cand
    return None


def _con(cfg):
    p = db_path(cfg)
    if not p:
        return None, {"error": "还没生成过长期记忆（按插件的 legacy_state_dir 与插件数据目录都没找到 long-memory.db）",
                      "looked_in": candidates(cfg) or [cfg.get("plugin_data_dir") or "-"]}
    try:
        return sqlite3.connect("file:%s?mode=ro" % os.path.abspath(p).replace("\\", "/"),
                               uri=True), None
    except Exception as e:
        return None, {"error": "记忆库打不开：%s" % type(e).__name__}


def _tables(con):
    out = []
    for name, in con.execute("select name from sqlite_master where type='table' "
                             "and name not like 'sqlite_%' order by name"):
        try:
            cols = [c[1] for c in con.execute("pragma table_info(%s)" % _q(name))]
            n = con.execute("select count(*) from %s" % _q(name)).fetchone()[0]
        except Exception:
            continue
        tcol = next((c for c in TEXT_COLS if c in [x.lower() for x in cols]), None)
        out.append({"name": name, "rows": n, "cols": cols, "text_col": tcol})
    return out


def _q(name):
    if not re.match(r"^[A-Za-z0-9_]+$", str(name or "")):
        raise ValueError("unexpected table name")
    return '"%s"' % name


def _main_table(tables):
    usable = [t for t in tables if t["text_col"]]
    if not usable:
        return None
    return sorted(usable, key=lambda t: -t["rows"])[0]


def _cell(v):
    if isinstance(v, (bytes, bytearray)):
        return "(二进制 %d 字节)" % len(v)
    if isinstance(v, (int, float)) or v is None:
        return v
    s = " ".join(str(v).split())
    return security.redact(s[:300] + ("…" if len(s) > 300 else ""))


def info(cfg):
    con, err = _con(cfg)
    if not con:
        return {"available": False, **err}
    try:
        tables = _tables(con)
        main = _main_table(tables)
        return {"available": True, "path": os.path.basename(db_path(cfg) or ""),
                "bytes": os.path.getsize(db_path(cfg) or "."),
                "tables": [{"name": t["name"], "rows": t["rows"], "text_col": t["text_col"]}
                           for t in tables],
                "primary_table": main["name"] if main else None,
                "note": "只读；写入由插件自己负责，我们不双写"}
    finally:
        con.close()


def search(cfg, query="", limit=8, table=""):
    con, err = _con(cfg)
    if not con:
        return err
    try:
        tables = _tables(con)
        if table:
            t = next((x for x in tables if x["name"] == str(table)), None)
            if not t:
                return {"error": "没有这张表：%s" % table}
        else:
            t = _main_table(tables)
        if not t:
            return {"error": "记忆库里找不到能搜的文本列，界面先如实空着",
                    "tables": [x["name"] for x in tables]}
        if not t["text_col"] and q:
            return {"error": "「%s」里没有能当正文搜的文本列，换一张表" % t["name"]}
        limit = max(1, min(MAX_ROWS, int(limit or 8)))
        cols = ", ".join(_q(c) for c in t["cols"])
        where, args = "", []
        q = str(query or "").strip()
        if q:
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = "%" + esc + "%"
            where = " where " + " or ".join("%s like ? escape '\\'" % _q(c)
                                            for c in t["cols"] if c.lower() in TEXT_COLS)
            args = [like] * len([c for c in t["cols"] if c.lower() in TEXT_COLS])
        sql = "select %s from %s%s order by rowid desc limit ?" % (cols, _q(t["name"]), where)
        cur = con.execute(sql, args + [limit])
        names = [d[0] for d in cur.description]
        items = [{k: _cell(v) for k, v in zip(names, row)} for row in cur.fetchall()]
        total = None
        try:
            total = con.execute("select count(*) from %s%s" % (_q(t["name"]), where), args).fetchone()[0]
        except Exception:
            pass
        return {"items": items, "count": len(items), "total": total, "table": t["name"],
                "query": q[:60] or None, "mode": "search" if q else "recent",
                "note": "读的是插件的 long-memory.db；没找到就先空着，不编"}
    except Exception as e:
        return {"error": "查不动：%s: %s" % (type(e).__name__, str(e)[:120])}
    finally:
        con.close()
