# -*- coding: utf-8 -*-
"""Diagnostic bundle (design §7.2「日志、诊断包自动脱敏」· 验收 11).

The bundle contains *projections* the core can safely print, never the raw config files:
`cmd_config.json` holds a provider API key, `credentials/` holds DPAPI blobs, the ops-dfy
dir holds the MCP address, and the databases hold chat history. Those are exactly the
things a bundle gets emailed around, so they are not in it. Everything that is in it
goes through security.redact once more on the way out.
"""
import io
import json
import os
import re
import sys
import time
import zipfile

from . import audit, backup, config, security

SKIP_HINTS = ("cmd_config", "credentials", "mcp_url", "data_v4.db", "long-memory", "token")
AUDIT_TAIL = 200


def _redact(obj):
    """Return (text_to_write, did_anything_get_masked).

    Structural first: a dict key named api_key/token/... is a reliable signal, and redact()
    masks it by name. Then a text sweep, for secrets sitting inside free-form strings
    (log lines, error messages). Anything unserialisable becomes an error stub, never a leak.
    """
    try:
        before = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": "unserialisable: %s" % type(e).__name__}), True
    struct = security.redact(obj) if isinstance(obj, (dict, list, tuple)) else obj
    try:
        after = json.dumps(struct, ensure_ascii=False, default=str)
    except Exception:
        after = before
    swept = security.redact(after)
    return swept, swept != before


def bundle_dir():
    return os.path.join(config.runtime_dir(), "diagnostics")


def list_bundles(cfg=None):
    d = bundle_dir()
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d), reverse=True):
            if fn.endswith(".zip"):
                p = os.path.join(d, fn)
                out.append({"name": fn, "bytes": os.path.getsize(p),
                            "created_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    return {"items": out[:20], "count": len(out), "dir": d}


def bundle(cfg, note="", include=None):
    include = include or ("status", "settings", "models", "arena", "backups", "ports",
                          "persona", "memory", "audit", "env")
    parts = {}
    errors = {}

    def add(key, fn):
        if key not in include:
            return
        try:
            parts[key] = fn()
        except Exception as e:
            errors[key] = "%s: %s" % (type(e).__name__, str(e)[:120])

    from . import readers
    add("status", lambda: readers.status())
    from . import settings as st
    add("settings", lambda: st.read(cfg))
    from . import modelcfg
    add("models", lambda: modelcfg.read(cfg))

    def _arena():
        from . import arena
        return {"overview": arena.overview(cfg), "tasks": arena.tasks(cfg, limit=5),
                "workbench": arena.workbench_status(cfg), "grant": arena.grant_state(cfg)}
    add("arena", _arena)
    add("backups", lambda: backup.listing(cfg))
    add("ports", lambda: {"listeners": readers.netstat_rows()[0][:80], "report": readers.port_report(cfg)})

    def _persona():
        from . import persona
        s = persona.sources(cfg)
        s["note"] = "只含长度与指纹，人格正文不进诊断包"
        for row in (s.get("db") or {}).get("rows") or []:
            row.pop("text", None)
        return s
    add("persona", _persona)
    from . import memories
    add("memory", lambda: memories.info(cfg))

    def _audit():
        p = os.path.join(config.runtime_dir(), "audit-%s.jsonl" % time.strftime("%Y%m%d"))
        if not os.path.isfile(p):
            return {"error": "今天还没有审计记录"}
        lines = open(p, encoding="utf-8", errors="replace").read().splitlines()[-AUDIT_TAIL:]
        return {"file": os.path.basename(p), "lines": len(lines),
                "tail": security.redact("\n".join(lines))}
    add("audit", _audit)

    def _env():
        return {"app": "大肥鱼 desktop", "core": "0.4.0", "stage": 4,
                "python": "%d.%d.%d" % sys.version_info[:3], "os": os.name,
                "cwd": os.getcwd(), "runtime_dir": config.runtime_dir(),
                "note": "路径里没有令牌；令牌与地址一律不写进来"}
    add("env", _env)

    redactions = 0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        head = {"created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": str(note or "")[:80],
                "parts": sorted(parts), "errors": errors,
                "policy": "不含 cmd_config/凭据目录/数据库/MCP 地址；所有文本再脱敏一次"}
        head_text, head_changed = _redact(head)
        z.writestr("diagnostics-info.json", head_text)
        redactions += 1 if head_changed else 0
        for k in sorted(parts):
            text, changed = _redact(parts[k])
            if changed:
                redactions += 1
            # last-line guard: nothing matching a secret pattern may survive at all
            for pat in (r"sk-[A-Za-z0-9]{8,}", r"token=[^\s\"&]{6,}"):
                text = re.sub(pat, "***", text)
            z.writestr("parts/%s.json" % k, text)
    os.makedirs(bundle_dir(), exist_ok=True)
    tag = time.strftime("%Y%m%d-%H%M%S")
    p = os.path.join(bundle_dir(), "dfy-diag-%s.zip" % tag)
    with open(p, "wb") as fh:
        fh.write(buf.getvalue())
    audit.write("diag_bundle", parts=len(parts), bytes=len(buf.getvalue()), redacted=redactions)
    return {"ok": True, "file": p, "bytes": len(buf.getvalue()), "parts": sorted(parts),
            "errors": errors or None, "redacted_parts": redactions,
            "note": "包里没有原始配置、没有密钥、没有 MCP 地址、没有数据库"}
