"""Read-only probes. Nothing here writes, restarts, or sends.

Design constraints honoured:
  * secret files are never opened (security.path_is_forbidden gates every read)
  * chat bodies are never returned, only counts and timestamps
  * every read is size-capped and never raises into the caller
"""

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request

from . import config, security


def _tcp_open(port, host="127.0.0.1", timeout=0.5):
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _http_probe(url, timeout=1.2):
    """Return the HTTP status code, or None. Loopback only, never authenticated."""
    try:
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "dfy-core/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(getattr(r, "status", 200) or 200)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except Exception:
        return None


def read_json_capped(path, limit=None):
    """Safely read one JSON file. Returns (data, error)."""
    if not path:
        return None, "no path"
    if security.path_is_forbidden(path):
        return None, "forbidden by deny-list"
    if not os.path.isfile(path):
        return None, "not found"
    limit = int(limit or config.load().get("read_limit_bytes", 4194304))
    try:
        size = os.path.getsize(path)
        if size > limit:
            return None, "too large (%d B)" % size
        with open(path, "rb") as fh:
            raw = fh.read(size)
        return json.loads(raw.decode("utf-8", "replace")), None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def netstat_rows():
    """Parse `netstat -ano` once. Returns (rows, error)."""
    try:
        out = subprocess.run("netstat -ano", shell=True, capture_output=True,
                             timeout=8).stdout.decode("gbk", "replace")
    except Exception as e:
        return [], "netstat failed: %s" % e
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        proto = parts[0].upper()
        if proto not in ("TCP", "UDP"):
            continue
        local = parts[1]
        state = parts[3].upper() if proto == "TCP" else ""
        if proto == "TCP" and state not in ("LISTENING", "ESTABLISHED"):
            continue
        host, _, port = local.rpartition(":")
        rows.append({
            "proto": proto,
            "local": local,
            "host": host.strip("[]"),
            "port": int(port) if port.isdigit() else None,
            "state": state,
            "pid": parts[-1],
            "loopback": host.strip("[]") in ("127.0.0.1", "::1", "localhost"),
        })
    return rows, None


def port_report(cfg):
    rows, err = netstat_rows()
    ports = cfg["ports"]
    listeners, established = {}, {}
    for name, p in ports.items():
        try:
            p = int(p)
        except Exception:
            listeners[name] = {"port": p, "listening": False, "loopback_only": True, "pids": []}
            established[name] = 0
            continue
        lst = [r for r in rows if r["port"] == p and r["state"] == "LISTENING"]
        listeners[name] = {
            "port": p,
            "listening": bool(lst),
            "loopback_only": all(r["loopback"] for r in lst) if lst else True,
            "pids": sorted({r["pid"] for r in lst}),
        }
        established[name] = sum(1 for r in rows
                               if r["port"] == p and r["state"] == "ESTABLISHED")
    exposed = [{"port": r["port"], "local": r["local"], "pid": r["pid"]}
               for r in rows
               if r["port"] in set(int(p) for p in ports.values() if str(p).isdigit())
               and not r["loopback"] and r["state"] == "LISTENING"]
    return listeners, established, exposed, err


# Only these images count as "ours" for the exposure self-check. The desktop
# launcher is named after the project, hence the escaped literal.
SELF_IMAGES = ("electron.exe", "python.exe", "pythonw.exe", "\u5927\u80a5\u9c7c.exe")


def process_names():
    """pid -> image name from one tasklist call."""
    try:
        import csv
        import io
        out = subprocess.run("tasklist /fo csv /nh", shell=True, capture_output=True,
                             timeout=8).stdout.decode("gbk", "replace")
        names = {}
        for r in csv.reader(io.StringIO(out)):
            if len(r) >= 2:
                names[r[1]] = r[0].lower()
        return names
    except Exception:
        return {}


def own_listeners():
    """Non-loopback LISTENING sockets held by processes WE started.

    Windows services own dozens of ephemeral 0.0.0.0 listeners (RPC endpoint
    mapper etc.). Flagging those as "exposed" made the self-check cry wolf, so
    the verdict is scoped to our own image names; the raw system count is still
    reported for context.
    """
    rows, err = netstat_rows()
    names = process_names()
    ours = []
    for r in rows:
        if r["state"] != "LISTENING" or r["loopback"]:
            continue
        img = names.get(r["pid"], "")
        if img in SELF_IMAGES:
            ours.append({"port": r["port"], "local": r["local"], "pid": r["pid"], "image": img})
    system_total = sum(1 for r in rows if r["state"] == "LISTENING" and not r["loopback"])
    return {"ours": ours, "ours_count": len(ours),
            "system_non_loopback": system_total, "netstat_error": err}


def plugin_state(cfg):
    """Counts and recency only. Message bodies never leave the core."""
    path = config.state_file(cfg)
    data, err = read_json_capped(path)
    if err:
        return {"error": err, "sessions": [], "session_count": 0}
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = 0
    raw = None
    if isinstance(data, dict):
        # Live shape is {"conversations": {"<platform>:<Type>:<id>": {...}}, ...}
        for key in ("conversations", "sessions", "chats"):
            if isinstance(data.get(key), (dict, list)):
                raw = data[key]
                break
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = list(enumerate(raw))
    else:
        items = []
    sessions = []
    for key, s in items:
        if not isinstance(s, dict):
            continue
        msgs = s.get("messages")
        if not isinstance(msgs, list):
            msgs = s.get("recent") if isinstance(s.get("recent"), list) else []
        last_ts = None
        for m in reversed(msgs):
            if isinstance(m, dict):
                for tk in ("time", "ts", "time_created", "at"):
                    if m.get(tk):
                        last_ts = m.get(tk)
                        break
            if last_ts:
                break
        unread = s.get("unread")
        if isinstance(unread, list):
            unread = len(unread)
        wake = s.get("wake") if isinstance(s.get("wake"), dict) else {}
        label = s.get("label") if isinstance(s.get("label"), str) else None
        paused = s.get("paused")
        if paused is None:
            paused = data.get("paused")
        sessions.append({
            "key": str(key),
            "kind": "group" if ("group" in str(key).lower()) else "private",
            "label": label,
            "message_count": len(msgs),
            "unread": unread if isinstance(unread, int) and not isinstance(unread, bool) else None,
            "paused": bool(paused),
            "sleep_until": s.get("sleep_until") or wake.get("sleep_until"),
            "last_message_at": last_ts,
            "last_incoming_at": s.get("last_incoming_at"),
            "last_ai_reply_at": s.get("last_ai_reply_at"),
        })
    return {
        "sessions": sorted(sessions, key=lambda x: str(x["key"])),
        "session_count": len(sessions),
        "state_bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
        "state_age_seconds": round(time.time() - mtime, 1) if mtime else None,
        "top_level_keys": sorted(data.keys())[:24] if isinstance(data, dict) else [],
    }


def whitelist_report(cfg):
    """Verify the production whitelist still matches the handover record."""
    want = cfg["expected_whitelist"]
    plat, plat_err = read_json_capped(cfg["cmd_config"])
    plug, plug_err = read_json_capped(cfg["plugin_config"])
    out = {"errors": [e for e in (plat_err, plug_err) if e]}
    got_wl = None
    owner_qq = None
    if isinstance(plat, dict):
        # This AstrBot keeps platform_settings at the top level; older layouts nest
        # it under platforms[]. Try both so the check does not silently read "empty".
        cands = [(plat.get("platform_settings") or {}).get("id_whitelist")]
        for pf in (plat.get("platforms") or []):
            if isinstance(pf, dict):
                cands.append((pf.get("platform_settings") or {}).get("id_whitelist"))
        for wl in cands:
            if wl:
                got_wl = [str(x) for x in wl]
                break
    got_groups = None
    if isinstance(plug, dict):
        pl = plug.get("plugin_settings") if isinstance(plug.get("plugin_settings"), dict) else plug
        ag = pl.get("allowed_group_ids")
        if isinstance(ag, list):
            got_groups = [str(x) for x in ag]
    tokens = set(str(x) for x in (got_wl or [])) | set(str(x) for x in (got_groups or []))
    owner = str(want["owner"])
    # exact token or exact ":<owner>" suffix; a substring match would let a
    # different QQ number that merely contains the owner's pass the check.
    owner_ok = owner in tokens or any(str(t).endswith(":" + owner) for t in tokens)
    forbidden = str(want["forbidden_group"])
    forbidden_present = forbidden in tokens or any(str(t).endswith(":" + forbidden) for t in tokens)
    if isinstance(plug, dict):
        owner_qq = plug.get("owner_qq")
    checks = [
        {"name": "主人私聊在白名单", "ok": bool(owner) and owner_ok,
         "detail": owner},
        {"name": "插件 owner_qq 与主人一致", "ok": str(owner_qq) == str(want["owner"]),
         "detail": str(owner_qq) if owner_qq is not None else "缺失"},
    ]
    for g in want["groups"]:
        checks.append({"name": "群 %s 已启用" % g,
                       "ok": g in tokens or any(str(t).endswith(":" + g) for t in tokens),
                       "detail": ""})
    checks.append({"name": "旧群 %s 仍被排除" % want["forbidden_group"],
                   "ok": not forbidden_present, "detail": ""})
    out.update({
        "astrbot_id_whitelist": got_wl,
        "plugin_allowed_groups": got_groups,
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
    })
    if isinstance(plug, dict):
        pl = plug.get("plugin_settings") if isinstance(plug.get("plugin_settings"), dict) else plug
        out["plugin_flags"] = {k: pl[k] for k in ("strict_tool_only", "suppress_after_tool_send",
                                                   "max_recent", "max_unread") if k in pl}
    return out


def persona_info(cfg):
    p = cfg.get("persona_file") or ""
    if not p or security.path_is_forbidden(p) or not os.path.isfile(p):
        return {"available": False, "reason": "not found or forbidden"}
    st = os.stat(p)
    lines = 0
    try:
        with open(p, "rb") as fh:
            for _ in fh:
                lines += 1
    except Exception:
        lines = 0
    return {"available": True, "bytes": st.st_size, "lines": lines,
            "mtime": int(st.st_mtime * 1000), "name": os.path.basename(p)}


def status():
    """The one payload the stage-0 UI renders."""
    cfg = config.load()
    started = time.time()
    ports = cfg["ports"]
    astrbot = {
        "webui_port": ports["astrbot_webui"],
        "reachable": _tcp_open(ports["astrbot_webui"]),
        "http_status": _http_probe("http://127.0.0.1:%d/" % ports["astrbot_webui"]),
        "onebot_port": ports["astrbot_onebot"],
        "onebot_listening": _tcp_open(ports["astrbot_onebot"]),
    }
    napcat = {
        "webui_port": ports["napcat_webui"],
        "reachable": _tcp_open(ports["napcat_webui"]),
        "http_status": _http_probe("http://127.0.0.1:%d/" % ports["napcat_webui"]),
        "note": "仅探活；不读 webui.json，token 不经过本进程",
    }
    legacy = {}
    for name in ("napcat_http", "napcat_ws", "legacy_bridge", "legacy_arena", "legacy_dsh"):
        legacy[name] = {"port": ports[name], "listening": _tcp_open(ports[name])}
    arena = {
        "gateway_listening": False,
        "legacy_8790_in_use": legacy["legacy_arena"]["listening"],
        "note": "阶段 0 不接入 Arena；8790 归 shortdrama-studio，不杀进程、不复活旧控制面",
    }
    listeners, established, exposed, ns_err = port_report(cfg)
    # Arena 上游是主人自营的 MCP 端点：这里只看本地事实（endpoint 文件 + 台账目录），
    # 不发网络请求 —— /v1/status 已经要探 4 个端口，够慢了；可达性由 Arena 页的 overview 报。
    try:
        from . import arena as _arena_mod
        _ap = _arena_mod.paths(cfg)
        _url, _err = _arena_mod._load_endpoint(cfg)
        _qdir = _ap["requests"]
        _queued = len([n for n in os.listdir(_qdir) if n.endswith(".json")]) if os.path.isdir(_qdir) else 0
        if _url:
            a_state = "ok"
            a_detail = "队列派活可用；待办 %d 条；浏览器派发未接入" % _queued
            try:
                _wb = _arena_mod.workbench_status(cfg)
                if _wb.get("items") and (_wb["items"][0] or {}).get("alive"):
                    a_detail += "；工作台在线"
            except Exception:
                pass
        else:
            a_state = "warn"
            a_detail = "还没接 Arena 上游：%s" % (_err or "未配置")
    except Exception as _e:
        a_state, a_detail = "warn", "Arena 状态读不出来：%s" % type(_e).__name__
    state = plugin_state(cfg)
    wl = whitelist_report(cfg)
    qq_ok = napcat["reachable"] and established.get("astrbot_onebot", 0) > 0
    blocks = {
        "qq": {"label": "QQ 连接", "state": "ok" if qq_ok else ("warn" if napcat["reachable"] else "down"),
               "detail": "NapCat %s；OneBot established=%d" % ("在线" if napcat["reachable"] else "离线",
                                                              established.get("astrbot_onebot", 0))},
        "messaging": {"label": "消息服务", "state": "ok" if astrbot["onebot_listening"] else "down",
                      "detail": "AstrBot %s；监听 %s" % ("在线" if astrbot["reachable"] else "离线",
                                                        ", ".join(str(v["port"]) for v in listeners.values()
                                                                  if v["listening"]) or "无")},
        "model": {"label": "模型", "state": "ok" if astrbot["reachable"] else "warn",
                  "detail": "由 AstrBot 提供；阶段 0 不做推理探测"},
        "arena": {"label": "Arena", "state": a_state, "detail": a_detail},
    }
    return {
        "ok": all(b["state"] in ("ok", "warn", "disabled") for b in blocks.values()),
        "generated_at": int(started * 1000),
        "duration_ms": int((time.time() - started) * 1000),
        "core": {"pid": os.getpid(), "version": "0.1.0", "mode": "read-only"},
        "blocks": blocks,
        "whitelist": wl,
        "plugin": {"state": state, "persona": persona_info(cfg)},
        "services": {"astrbot": astrbot, "napcat": napcat, "legacy": legacy, "arena": arena},
        "network": {"listeners": listeners, "established": established,
                    "non_loopback_watch_ports": exposed, "netstat_error": ns_err,
                    "own": own_listeners(),
                    "verdict": "ok" if not exposed and not own_listeners()["ours"] else "EXPOSED"},
        "production": {
            "astrbot_dir": os.path.isdir(cfg["astrbot_dir"]),
            "handoff_doc": os.path.isfile(os.path.join(cfg["astrbot_dir"], "HANDOFF_20261029.md")),
            "design_doc": os.path.isfile(os.path.join(config.PROJECT_DIR, "docs",
                                                       "desktop-gui-design-20261030.md")),
            "touched": False,
        },
        "config_error": cfg.get("_config_error"),
    }
