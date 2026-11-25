# -*- coding: utf-8 -*-
"""The settings page's backing store.

Two files, both ours, both in the app data dir: `backup.json` (snapshot policy) and
`gui-prefs.json` (window/UX prefs). Production config (cmd_config.json, plugin config)
is never edited here — that lives behind its own confirm+backup routes. Core-level knobs
like `send_enabled` stay read-only in the GUI: changing them means editing
config/desktop.json and restarting, and we won't pretend otherwise.
"""
import json
import os

from . import audit, backup, config

GUI_KEYS = {"audit_tail": (5, 500, int), "log_tail": (10, 500, int),
            "close_to_tray": (None, None, bool), "autostart": (None, None, bool),
            "confirm_destructive": (None, None, bool), "compact_rows": (None, None, bool)}
GUI_DEFAULTS = {"audit_tail": 60, "log_tail": 120, "close_to_tray": True,
                "autostart": False, "confirm_destructive": True, "compact_rows": False}


def _prefs_path():
    return os.path.join(config.runtime_dir(), "gui-prefs.json")


def _load(path, default):
    if not os.path.isfile(path):
        return dict(default)
    doc, err = backup.read_json(path)
    if err or not isinstance(doc, dict):
        return dict(default)
    return {**default, **doc}


def read(cfg):
    st = backup.settings(cfg)
    prefs = _load(_prefs_path(), GUI_DEFAULTS)
    return {"backup": {"auto_minutes": st["auto_minutes"], "keep": st["keep"]},
            "gui": prefs,
            "core_readonly": {"send_enabled": bool(cfg.get("send_enabled")),
                              "outbox_max_chars": cfg.get("outbox_max_chars"),
                              "read_limit_bytes": cfg.get("read_limit_bytes"),
                              "runtime_dir": config.runtime_dir()},
            "writable": {"backup": ["auto_minutes", "keep"], "gui": sorted(GUI_KEYS)},
            "note": "核心级开关（发送总开关、体积上限）改的是 config/desktop.json，"
                    "要重启才生效，这一版不放开——界面上只读给你看"}


def _clean(section, key, value):
    if section == "backup":
        n = int(value)
        if key == "auto_minutes":
            if not 0 <= n <= 10080:
                raise ValueError("自动备份间隔要在 0-10080 分钟之间（0＝关）")
            return n
        if not 1 <= n <= 200:
            raise ValueError("保留份数要在 1-200 之间")
        return n
    spec = GUI_KEYS.get(key)
    if not spec:
        raise ValueError("这个偏好不在允许清单里：%s" % key)
    lo, hi, typ = spec
    if typ is bool:
        return bool(value)
    n = int(value)
    if lo is not None and not lo <= n <= hi:
        raise ValueError("%s 要在 %d-%d 之间" % (key, lo, hi))
    return n


def save(cfg, section="gui", fields=None):
    section = str(section or "gui")
    if section not in ("backup", "gui"):
        return {"error": "只接受 backup 或 gui 两段；核心段是只读的"}
    fields = fields if isinstance(fields, dict) else {}
    if not fields:
        return {"error": "没说要改什么"}
    allow = ["auto_minutes", "keep"] if section == "backup" else sorted(GUI_KEYS)
    bad = [k for k in fields if k not in allow]
    if bad:
        return {"error": "这些键不在允许清单：%s" % "、".join(map(str, bad))}
    try:
        clean = {k: _clean(section, k, v) for k, v in fields.items()}
    except (ValueError, TypeError) as e:
        return {"error": str(e)[:160]}
    before = dict(backup.settings(cfg)) if section == "backup" else _load(_prefs_path(), GUI_DEFAULTS)
    after = {**before, **clean}
    try:
        if section == "backup":
            backup.save_settings(cfg, auto_minutes=after.get("auto_minutes"),
                                 keep=after.get("keep"))
        else:
            backup._write_atomic(_prefs_path(), json.dumps(after, ensure_ascii=False, indent=1))
    except Exception as e:
        return {"error": "写失败：%s" % type(e).__name__}
    back = backup.settings(cfg) if section == "backup" else _load(_prefs_path(), GUI_DEFAULTS)
    if {k: back.get(k) for k in clean} != clean:
        if section == "backup":
            backup.save_settings(cfg, auto_minutes=before.get("auto_minutes"),
                                 keep=before.get("keep"))
        else:
            backup._write_atomic(_prefs_path(), json.dumps(before, ensure_ascii=False, indent=1))
        return {"error": "写完读回来不一致，已倒回原样"}
    audit.write("settings_save", section=section, keys=",".join(sorted(clean))[:80])
    return {"ok": True, "section": section, "changed": sorted(clean), section: after}
