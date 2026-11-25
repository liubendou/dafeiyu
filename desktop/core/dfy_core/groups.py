"""Group whitelist writes — both config layers, or nothing.

A group is "enabled" only when AstrBot's `platform_settings.id_whitelist` AND the
plugin's `allowed_group_ids` agree. Half-writing them is exactly the failure mode
that makes the fish ignore a group silently, so a change touches both files or
fails and restores both.

Order per the design: back up -> write -> re-read verify -> (caller restarts).
Red lines: the owner's private entry can never be removed, and the excluded legacy
group can never be added.

Set DFY_CMD_CONFIG_TARGET / DFY_PLUGIN_CONFIG_TARGET to point both at scratch
copies; that is how the self-test exercises the real code path without touching
production.
"""

import json
import os
import shutil
import time

from . import audit, config, security

GID_MIN, GID_MAX = 4, 20


def targets(cfg):
    return (os.environ.get("DFY_CMD_CONFIG_TARGET") or cfg["cmd_config"],
            os.environ.get("DFY_PLUGIN_CONFIG_TARGET") or cfg["plugin_config"])


def _whitelist_holder(doc):
    """Return the dict that actually holds id_whitelist (two AstrBot layouts)."""
    ps = doc.get("platform_settings")
    if isinstance(ps, dict) and isinstance(ps.get("id_whitelist"), list):
        return ps
    for pf in (doc.get("platforms") or []):
        if isinstance(pf, dict):
            s = pf.get("platform_settings")
            if isinstance(s, dict) and isinstance(s.get("id_whitelist"), list):
                return s
    return None


def _plugin_holder(doc):
    pl = doc.get("plugin_settings")
    return pl if isinstance(pl, dict) and pl else doc


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_atomic(path, doc):
    tmp = path + ".dfy.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def preview(cfg, group_id):
    gid = str(group_id or "").strip()
    cmd_p, plug_p = targets(cfg)
    cur_wl, cur_ag = [], []
    try:
        holder = _whitelist_holder(_read(cmd_p))
        cur_wl = [str(x) for x in ((holder or {}).get("id_whitelist") or [])]
        cur_ag = [str(x) for x in ((_plugin_holder(_read(plug_p)).get("allowed_group_ids")) or [])]
    except Exception as e:
        return {"error": "配置读取失败：%s" % type(e).__name__}
    return {"group_id": gid, "in_astrbot_whitelist": gid in cur_wl,
            "in_plugin_groups": gid in cur_ag, "astrbot_entries": len(cur_wl),
            "plugin_entries": len(cur_ag)}


def set_group(cfg, group_id, enable, confirm=""):
    gid = str(group_id or "").strip()
    if not (gid.isdigit() and GID_MIN <= len(gid) <= GID_MAX):
        return {"error": "群号格式不对"}
    want = cfg.get("expected_whitelist") or {}
    if str(want.get("forbidden_group")) == gid:
        audit.write("group_refused", gid=gid, reason="excluded")
        return {"error": "这个群按红线永久排除，不写入白名单"}
    if str(want.get("owner")) == gid:
        return {"error": "这是主人的 QQ 号，不是群"}
    cmd_p, plug_p = targets(cfg)
    try:
        cmd_doc = _read(cmd_p)
        plug_doc = _read(plug_p)
    except Exception as e:
        return {"error": "配置读取失败：%s" % type(e).__name__}
    holder = _whitelist_holder(cmd_doc)
    if holder is None:
        return {"error": "找不到 id_whitelist，放弃写入"}
    pl = _plugin_holder(plug_doc)
    wl = [str(x) for x in (holder.get("id_whitelist") or [])]
    ag = [str(x) for x in (pl.get("allowed_group_ids") or [])]
    # Nothing-to-do / not-allowed is decided from the real current state, before we
    # ever ask for a confirmation — otherwise the UI can confirm a no-op.
    if enable and gid in wl and gid in ag:
        return {"error": "该群已经是启用状态", "changed": False}
    if not enable:
        if gid == str(want.get("owner")):
            return {"error": "主人私聊不能被停用"}
        if gid not in wl and gid not in ag:
            return {"error": "该群本来没启用", "changed": False}
    if str(confirm) != "yes":
        # two-step from the UI: show what will change, then write
        p = {"group_id": gid, "in_astrbot_whitelist": gid in wl, "in_plugin_groups": gid in ag,
             "astrbot_entries": len(wl), "plugin_entries": len(ag), "confirm_required": True,
             "will": ("把 %s 加入回复白名单并同步群资料" if enable else "把 %s 移出回复白名单（历史保留，不删数据）") % gid}
        return p

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(config.runtime_dir(), "backups", "groups-%s" % stamp)
    try:
        os.makedirs(backup, exist_ok=True)
        shutil.copy2(cmd_p, os.path.join(backup, "cmd_config.json"))
        shutil.copy2(plug_p, os.path.join(backup, "plugin_config.json"))
    except Exception as e:
        return {"error": "备份失败，取消写入：%s" % type(e).__name__}

    old_wl, old_ag = list(wl), list(ag)
    new_wl = (wl + [gid]) if (enable and gid not in wl) else ([x for x in wl if x != gid] if not enable else wl)
    new_ag = (ag + [gid]) if (enable and gid not in ag) else ([x for x in ag if x != gid] if not enable else ag)
    holder["id_whitelist"] = new_wl
    pl["allowed_group_ids"] = new_ag
    try:
        _write_atomic(cmd_p, cmd_doc)
        _write_atomic(plug_p, plug_doc)
    except Exception as e:
        for src in ("cmd_config.json", "plugin_config.json"):
            try:
                shutil.copy2(os.path.join(backup, src), cmd_p if src.startswith("cmd") else plug_p)
            except Exception:
                pass
        audit.write("group_write_error", gid=gid, error=type(e).__name__)
        return {"error": "写入失败，已从备份恢复：%s" % type(e).__name__}

    # verify by re-reading from disk, not from memory
    try:
        v1 = [str(x) for x in (_whitelist_holder(_read(cmd_p)).get("id_whitelist") or [])]
        v2 = [str(x) for x in (_plugin_holder(_read(plug_p)).get("allowed_group_ids") or [])]
    except Exception as e:
        v1 = v2 = None
        verify_err = str(e)
    else:
        verify_err = None
    want_in = enable
    good = (v1 is not None and (gid in v1) == want_in and (gid in v2) == want_in)
    if not good:
        shutil.copy2(os.path.join(backup, "cmd_config.json"), cmd_p)
        shutil.copy2(os.path.join(backup, "plugin_config.json"), plug_p)
        holder2 = _whitelist_holder(_read(cmd_p))
        audit.write("group_rollback", gid=gid, reason="verify mismatch: %s" % (verify_err or "layers disagreed"))
        return {"error": "校验不一致，已回滚到备份", "rolled_back": True}

    audit.write("group_change", gid=gid, enable=bool(enable),
                wl_before=len(old_wl), wl_after=len(new_wl),
                ag_before=len(old_ag), ag_after=len(new_ag))
    return {
        "ok": True,
        "changed": True,
        "group_id": gid,
        "enabled": bool(enable),
        "backup": "backups/groups-%s" % stamp,
        "restart_required": True,
        "note": "两层配置已一致；AstrBot 需要重启或热加载插件才对下一条消息生效",
    }
