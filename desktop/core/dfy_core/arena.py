"""Arena gateway: the seven capabilities of the old 8790 control plane, rebuilt
inside the desktop core.

What changed versus `~/ops-dfy/ctl/agent_ctl.py`:

* no fixed port and no unauthenticated listener — the gateway is in-process, the
  only socket is the core's own random loopback port with a per-boot token;
* the upstream MCP endpoint (and its bearer token) is loaded once, added to the
  runtime redaction list immediately, and never returned by any route;
* `exec` is off unless the owner grants it for a bounded number of minutes;
* groups and strangers cannot reach any of it — that gate is in `app.py`, and the
  plugin re-checks it with its own `_is_allowed_umo`;
* every call is audited with a result summary and elapsed time.

Browser dispatch is intentionally not wired here yet: when it is unavailable the
UI disables it with a reason instead of pretending (design §5.3).
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse

from . import audit, config, security

TASK_MIN, TASK_MAX = 8, 2000
MAX_REPLY = 1400
WB_FIELDS = ("name", "id", "profile", "profile_id", "pid", "running", "state",
             "logged_in", "loggedIn", "login", "proxy", "model_note", "note",
             "created_at", "last_opened", "open_count", "updated_at")
NOTE_KEYS = 40
GRANT_MAX_MINUTES = 30


def paths(cfg):
    """All overridable by env so the self-test runs on scratch copies only."""
    e = os.environ.get
    root = os.path.join(config.runtime_dir(), "arena")
    return {"root": root,
            "requests": e("DFY_ARENA_REQUESTS_DIR") or cfg.get("arena_requests_dir") or os.path.join(root, "requests"),
            "notes": e("DFY_ARENA_NOTES_FILE") or cfg.get("arena_notes_file") or os.path.join(cfg["ops_dfy_dir"], "ctl", "model_notes.json"),
            "endpoint_file": e("DFY_ARENA_ENDPOINT_FILE") or cfg.get("arena_endpoint_file") or os.path.join(
                cfg["ops_dfy_dir"], "ctl", "mcp_url.txt"),
            "agent_id_file": e("DFY_ARENA_AGENT_ID_FILE") or os.path.join(cfg["ops_dfy_dir"], "ctl", "agent_id.txt"),
            "grant": os.path.join(config.runtime_dir(), "arena-grant.json"),
            "wb_root": e("DFY_ARENA_WB_ROOT") or cfg.get("arena_workbench_root") or os.path.join(
                os.path.expanduser("~"), "AppData", "Local", "LiubendouWorkbench"),
            "wb_scope": e("DFY_ARENA_PROFILE_SCOPE") or cfg.get("arena_profile_scope") or "profile_3"}


def validate_endpoint(url):
    """Only an https endpoint or an http one bound to loopback is acceptable.

    Deliberately a pure function: the rule has to be testable without ever reading
    a real credential file, and a URL with no explicit port is still valid.
    """
    u = str(url or "").strip()
    if not u:
        return False, "未配置上游地址"
    try:
        parsed = urllib.parse.urlparse(u)
    except Exception:
        return False, "上游地址解析不了"
    host = (parsed.hostname or "").strip("[]").lower()
    if parsed.scheme == "https" and host:
        return True, None
    if parsed.scheme == "http" and host in ("127.0.0.1", "localhost", "::1"):
        return True, None
    return False, "上游地址不是本机回环或 https，已拒绝使用"


# ------------------------------------------------------------------ upstream
_ENDPOINT = {"url": None, "loaded": False, "error": None}
_CLIENT = None


def forget_endpoint():
    """Drop the cached upstream address (after the owner saves or clears it in the GUI)."""
    _ENDPOINT["loaded"] = False
    _ENDPOINT["url"] = None
    _ENDPOINT["error"] = None


def _load_endpoint(cfg, force=False):
    if _ENDPOINT["loaded"] and not force:
        return _ENDPOINT["url"], _ENDPOINT["error"]
    _ENDPOINT["loaded"] = True
    p = paths(cfg)["endpoint_file"]
    url = (os.environ.get("DFY_ARENA_MCP_URL") or "").strip()
    if not url:
        # §7.2: an address the owner pasted into the GUI lives under DPAPI, and that
        # beats the plaintext file the old control plane used. The file stays as the
        # fallback so the existing scripts keep working.
        from . import credentials
        url = (credentials.open_secret("arena_mcp_url") or "").strip()
    if not url:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                url = fh.read().strip()
        except OSError as e:
            _ENDPOINT["error"] = "未配置上游地址（%s）" % type(e).__name__
            return None, _ENDPOINT["error"]
    good, verr = validate_endpoint(url)
    if not good:
        _ENDPOINT["error"] = verr
        _ENDPOINT["url"] = None
        return None, verr
    # Learn the secret so that redact() masks it everywhere, then forget nothing:
    # the URL stays in this process only.
    security.add_scrub(url)
    for m in re.finditer(r"[?&](?:token|key|secret|session[^=]*)=([^&#\s]+)", url):
        if len(m.group(1)) >= 6:
            security.add_scrub(m.group(1))
    _ENDPOINT["url"] = url
    _ENDPOINT["error"] = None
    return url, None


def fingerprint(cfg):
    url, _ = _load_endpoint(cfg)
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8] if url else None


def client(cfg, force=False):
    """A live MCP client, or None if the gateway has no usable upstream."""
    global _CLIENT
    url, err = _load_endpoint(cfg, force=force)
    if not url:
        return None
    if _CLIENT is None or force:
        from . import mcp
        _CLIENT = mcp.Mcp(url)
        try:
            _CLIENT.init()
        except Exception as e:
            _CLIENT.error = security.redact(str(e))[:200]
    return _CLIENT


def _call(cfg, tool, args, timeout=45):
    from . import mcp
    t0 = time.time()
    c = client(cfg)
    if c is None:
        return {"ok": False, "error": _ENDPOINT.get("error") or "网关未就绪", "ms": 0}
    try:
        ok, text = c.call(tool, args, timeout=timeout)
    except mcp.McpError as e:
        ok, text = False, security.redact(str(e))[:MAX_REPLY]
    except Exception as e:
        ok, text = False, security.redact("%s: %s" % (type(e).__name__, e))[:MAX_REPLY]
    text = security.redact(str(text if text is not None else ""))
    ms = int((time.time() - t0) * 1000)
    audit.write("arena_call", tool=tool, ok=bool(ok), ms=ms,
                err=None if ok else text[:160])
    if not ok:
        return {"ok": False, "error": text[:MAX_REPLY], "ms": ms}
    out = {"ok": True, "reply": text[:MAX_REPLY], "ms": ms}
    try:  # an upstream that answers with JSON usually escapes non-ASCII; un-escape it
        parsed = json.loads(text)     # so the card reads as Chinese, not as \uXXXX
        if isinstance(parsed, dict):
            out["data"] = {k: security.redact(v) for k, v in list(parsed.items())[:12]} if parsed else {}
            out["reply"] = security.redact(json.dumps(parsed, ensure_ascii=False))[:MAX_REPLY]
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------- models
def _read_json(path, cap=262144):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read(cap)
        return json.loads(raw), None
    except FileNotFoundError:
        return None, "missing"
    except Exception as e:
        return None, "%s" % type(e).__name__


def _astrbot_models(cfg):
    doc, err = _read_json(cfg["cmd_config"])
    out = []
    if isinstance(doc, dict):
        for s in (doc.get("provider_sources") or []):
            if isinstance(s, dict):
                out.append({"id": str(s.get("id") or "")[:48], "provider": str(s.get("provider") or "")[:32],
                            "model": str(s.get("model") or s.get("model_settings", {}).get("model") or "")[:48]})
        cur = doc.get("provider") if isinstance(doc.get("provider"), dict) else {}
        if cur.get("default_source") or cur.get("model"):
            out.append({"id": str(cur.get("default_source") or "")[:48], "provider": "current",
                        "model": str(cur.get("model") or "")[:48]})
    return out, err


def _note_lookup(notes, *cands):
    """Find the owner note for a model: exact key, then exact model name, then a
    casefold-equal key. Never a substring match — a wrong note sends the job to the
    wrong conversation, so ambiguity is an error, not a coin flip.
    """
    cands = [str(c).strip() for c in cands if c and str(c).strip()]
    # collisions are checked first: if two note keys differ only by case, an exact
    # hit on one of them is still a coin flip for the reader, so it must error out.
    low = {}
    for k in notes:
        low.setdefault(str(k).strip().casefold(), []).append(k)
    for c in cands:
        hits = low.get(c.casefold()) or []
        if len(hits) > 1:
            return "AMBIGUOUS", None
        if len(hits) == 1:
            return hits[0], notes[hits[0]]
    return None, None


def models(cfg):
    """模型清单 = 每个工作台实例 Arena 左侧栏里的房间（模型名 + 会话链接）。

    主人要的语义是「实例里真实开着的会话」，不是某个配置文件里抄来的清单：
    每个实例打开 Arena 之后，左侧栏每一行就是一个房间，行里写着模型名。
    工作台没跑、或一次都还没巡检过时，才退回 AstrBot 的 provider 列表撑场面，并在 source 里标出来。
    """
    notes, nerr = _read_json(paths(cfg)["notes"])
    notes = notes if isinstance(notes, dict) else {}
    from . import rooms as rooms_mod
    snap = rooms_mod.cached(cfg)
    cards, seen = [], set()
    for row in (snap.get("items") or []):
        key = str(row.get("model_note") or "").strip()
        href = str(row.get("href") or "").strip()
        prof = str(row.get("profile") or "").strip()
        if not prof or not (key or href):
            continue
        rid = "%s|%s" % (prof, href or key)
        if rid in seen:
            continue
        seen.add(rid)
        nk, note = _note_lookup(notes, key) if key else (None, None)
        cards.append({"id": rid[:120], "provider": row.get("profile_label") or prof,
                      "profile": prof, "model": key or None, "href": href or None,
                      "url": row.get("url") or None, "row_text": row.get("row_text") or "",
                      "source": "workbench",
                      "note_key": nk if nk not in (None, "AMBIGUOUS") else None,
                      "note_ambiguous": nk == "AMBIGUOUS",
                      "note": security.redact(str(note or ""))[:300] or None})
    source = "workbench"
    if not cards:  # 一台工作台都没读到时才退回 AstrBot 的 provider 清单
        source = "astrbot"
        found, _cerr = _astrbot_models(cfg)
        for m in found:
            key = m["id"] or m["model"]
            if not key or key in seen:
                continue
            seen.add(key)
            nk, note = _note_lookup(notes, m["model"], m["id"])
            cards.append({"id": key, "provider": m["provider"], "model": m["model"],
                          "source": "astrbot",
                          "note_key": nk if nk not in (None, "AMBIGUOUS") else None,
                          "note_ambiguous": nk == "AMBIGUOUS",
                          "note": security.redact(str(note or ""))[:300] or None})
    # 备注表里有、但没有任何真实房间对得上的键：只当路由候选记下来，
    # 绝不再塞进 items 冒充「可用模型」——否则主人会看到一堆自己根本没开的模型名。
    note_only = []
    for k, v in list(notes.items())[:NOTE_KEYS]:
        if str(k) in seen or any(c.get("note_key") == str(k) for c in cards):
            continue
        note_only.append({"key": str(k), "note": security.redact(str(v))[:300]})
    recent = {}
    try:
        from . import toolslog
        for r in (toolslog.model_usage(cfg, limit=20).get("runs") or []):
            mid = str(r.get("provider_id") or "")
            if mid and mid not in recent:
                recent[mid] = {"status": r.get("status"), "tokens": (r.get("token_input_other") or 0)
                                + (r.get("token_output") or 0), "duration_s": r.get("duration_s")}
    except Exception:
        pass
    for c in cards:
        c["last_run"] = recent.get(c.get("model") or c.get("id"))
    keys = sorted({c["note_key"] for c in cards if c.get("note_key")})
    hint = ""
    warming = False
    if not cards:
        # 一台工作台都没读到：不许把「没数据」说成「模型清单」，也不许拿旧备注顶上。
        warming = bool(rooms_mod.warm_async(cfg).get("started"))
        if snap.get("empty"):
            hint = "工作台里还没读到过 Arena 房间（实例没在跑，或还没巡检过）"
        elif snap.get("errors"):
            hint = "读侧栏没读全：" + "；".join(str(e) for e in (snap.get("errors") or [])[:2])
        else:
            hint = "这次没读到 Arena 房间"
        if warming:
            hint += "；正在后台读一遍侧栏，过十几秒再问一次"
    return {"items": cards, "count": len(cards), "note_keys": keys, "source": source,
            "hint": hint, "warming": warming, "note_only": note_only,
            "profiles": sorted({c["profile"] for c in cards if c.get("profile")}),
            "rooms": {"at": snap.get("at"), "profiles": snap.get("profiles") or {},
                      "errors": snap.get("errors") or [], "empty": snap.get("empty", True)},
            "notes_error": nerr if nerr != "missing" else None,
            "routing_key": "owner model note",
            "note": "按实例从 Arena 侧栏读房间；备注是路由依据，模型名只是显示用"}


def set_note(cfg, model, note):
    model = str(model or "").strip()[:64]
    if not model:
        return {"error": "要给哪个模型写备注？"}
    note = security.redact(str(note or "").strip())[:300]
    p = paths(cfg)["notes"]
    doc, err = _read_json(p)
    if err and err != "missing":
        return {"error": "备注文件读不了：%s" % err}
    doc = doc if isinstance(doc, dict) else {}
    before = dict(doc)
    if note:
        doc[model] = note
    else:
        doc.pop(model, None)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = None
    if os.path.isfile(p):
        d = os.path.join(config.runtime_dir(), "backups", "arena-notes-%s" % stamp)
        os.makedirs(d, exist_ok=True)
        backup = os.path.join(d, "model_notes.json")
        shutil.copy2(p, backup)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        tmp = p + ".dfy.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception as e:
        if backup:
            shutil.copy2(backup, p)
        return {"error": "写入失败已还原：%s" % type(e).__name__}
    now, verr = _read_json(p)
    got = (now or {}).get(model) if isinstance(now, dict) else None
    if (note and got != note) or (not note and got is not None):
        if backup:
            shutil.copy2(backup, p)
        audit.write("arena_note_rollback", model=model)
        return {"error": "写后校验不一致，已回滚", "rolled_back": True}
    audit.write("arena_note", model=model, chars=len(note), changed=(before.get(model) != note))
    return {"ok": True, "model": model, "note": note or None,
            "backup": os.path.relpath(backup, config.runtime_dir()).replace("\\", "/") if backup else None,
            "note_effect": "队列派活立刻用新备注；已派出去的任务不受影响"}


# ------------------------------------------------------------------- dispatch
def _slug(text):
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", str(text or "").lower()).strip("-")
    return (s[:28] or "task")


def dispatch(cfg, task, model="", note="", mode="queue"):
    task = str(task or "").strip()
    if len(task) < TASK_MIN:
        return {"error": "任务描述太短（至少 %d 字）" % TASK_MIN}
    if len(task) > TASK_MAX:
        return {"error": "任务描述太长（上限 %d 字）" % TASK_MAX}
    if str(mode or "queue") != "queue":
        return {"error": "浏览器派活还没接进来：它依赖工作台会话，接好前这个选项是灰的",
                "unavailable": True}
    model = security.redact(str(model or "").strip())[:64] or None
    note = security.redact(str(note or "").strip())[:300] or None
    pv = route_preview(cfg, model)
    if pv.get("error"):
        return {"error": pv["error"]}
    d = paths(cfg)["requests"]
    os.makedirs(d, exist_ok=True)
    rid = time.strftime("%Y%m%d-%H%M%S")
    rec = {"created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "task": task[:TASK_MAX],
           "model": model, "note": note, "mode": "queue", "state": "queued",
           "agent_id": _agent_id(cfg), "via": "desktop"}
    name = "%s-%s.json" % (rid, _slug(task))
    res = _call(cfg, "plan_create", {"goal": task,
                                     "tasks": [{"title": task, "acceptance": note or ""}]},
                timeout=60)
    rec["state"] = "queued" if res.get("ok") else "failed"
    rec["result"] = str(res.get("reply") if res.get("ok") else res.get("error") or "")[:MAX_REPLY]
    rec["ms"] = res.get("ms")
    rec["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        return {"error": "台账写入失败：%s" % type(e).__name__}
    audit.write("arena_dispatch", ok=bool(res.get("ok")), model=model or "-",
                chars=len(task), ms=res.get("ms"), file=name)
    if not res.get("ok"):
        return {"error": res.get("error"), "file": name, "state": "failed"}
    return {"ok": True, "file": name, "state": "queued", "ms": res.get("ms"),
            "reply": rec["result"][:600], "route": pv.get("route"),
            "data": res.get("data")}


def _agent_id(cfg):
    try:
        with open(paths(cfg)["agent_id_file"], "r", encoding="utf-8") as fh:
            return fh.read().strip()[:32] or None
    except Exception:
        return None


def tasks(cfg, limit=20):
    d = paths(cfg)["requests"]
    items = []
    try:
        names = sorted((n for n in os.listdir(d) if n.endswith(".json")), reverse=True)
    except OSError:
        names = []
    for n in names[:max(1, min(int(limit), 50))]:
        doc, err = _read_json(os.path.join(d, n), cap=65536)
        if not isinstance(doc, dict):
            items.append({"file": n, "state": "unreadable", "error": err})
            continue
        items.append({"file": n, "task": security.redact(str(doc.get("task") or ""))[:200],
                      "state": doc.get("state"), "model": doc.get("model"),
                      "note": doc.get("note"), "created_at": doc.get("created_at"),
                      "updated_at": doc.get("updated_at"), "ms": doc.get("ms"),
                      "mode": doc.get("mode") or "queue",
                      "result": security.redact(str(doc.get("result") or ""))[:400]})
    counts = {}
    for it in items:
        counts[it.get("state") or "?"] = counts.get(it.get("state") or "?", 0) + 1
    return {"items": items, "total": len(items), "counts": counts,
            "dir": "arena/requests",
            "note": "本地台账；上游任务状态用「刷新」按任务号查"}


def task_detail(cfg, name):
    name = str(name or "")
    if not re.match(r"^[A-Za-z0-9._\-\u4e00-\u9fff]{1,80}\.json$", name):
        return {"error": "文件名不合法"}
    p = os.path.join(paths(cfg)["requests"], name)
    if not os.path.isfile(p):
        return {"error": "找不到这条任务"}
    doc, err = _read_json(p, cap=262144)
    if not isinstance(doc, dict):
        return {"error": err or "读不出来"}
    out = {k: doc.get(k) for k in ("created_at", "updated_at", "task", "model", "note",
                                  "state", "mode", "agent_id", "via", "ms", "plan", "result")}
    for k in ("task", "result", "note", "plan"):
        if isinstance(out.get(k), str):
            out[k] = security.redact(out[k])[:4000]
    out["file"] = name
    return out


def route_preview(cfg, model):
    """Where this task would land — and an explicit error instead of a guess."""
    model = str(model or "").strip()
    if not model:
        return {"route": {"mode": "queue", "target": "自动（沿用当前默认模型）", "note": None,
                          "via": "default"}}
    notes, err = _read_json(paths(cfg)["notes"])
    notes = notes if isinstance(notes, dict) else {}
    key, note = _note_lookup(notes, model)
    if key == "AMBIGUOUS":
        return {"error": "「%s」匹配到多个模型备注（大小写撞车），先并成一条" % model}
    if key:
        return {"route": {"mode": "queue", "target": key,
                          "note": security.redact(str(note or ""))[:200], "via": "owner note"}}
    found, _ = _astrbot_models(cfg)
    hit = [m for m in found
           if model.casefold() in (str(m.get("id", "")).casefold(), str(m.get("model", "")).casefold())]
    if len(hit) > 1:
        return {"error": "同时匹配到 %d 个模型，选一个明确的" % len(hit)}
    if len(hit) == 1:
        nk, n2 = _note_lookup(notes, hit[0]["model"], hit[0]["id"])
        if nk in (None, "AMBIGUOUS"):
            return {"error": "这个模型还没有主人备注；备注才是路由依据，先在模型页写一条"}
        return {"route": {"mode": "queue", "target": nk,
                          "note": security.redact(str(n2 or ""))[:200], "via": "astrbot source"}}
    return {"error": "既不是备注也不是已知模型 ID；从列表里选一个，或先写一条备注"}


# ------------------------------------------------------------------- workbench
def _pid_alive(pid):
    try:
        pid = int(pid)
    except Exception:
        return None
    if pid <= 0:
        return None
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=8).stdout or ""
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) > 1 and parts[1].strip().strip('"') == str(pid):
            return True
    return False


def _wb_rows(doc):
    """LiubendouWorkbench/instances.json is a dict keyed by profile id, not a list.
    Accept both shapes; never invent an entry."""
    if isinstance(doc, list):
        return [dict(x) for x in doc if isinstance(x, dict)]
    if not isinstance(doc, dict):
        return []
    lst = doc.get("instances")
    if isinstance(lst, list):
        return [dict(x) for x in lst if isinstance(x, dict)]
    out = []
    for k, v in doc.items():
        if isinstance(v, dict):
            row = dict(v)
            row.setdefault("_key", str(k))
            out.append(row)
    return out


def workbench_status(cfg):
    p = paths(cfg)
    scope = str(p["wb_scope"])
    root = os.path.expandvars(os.path.expanduser(str(p["wb_root"])))
    doc, err = _read_json(os.path.join(root, "instances.json"))
    if err in ("missing", "unreadable"):
        return {"available": False, "error": None if err == "missing" else err, "scope": scope,
                "items": [], "note": "没有工作台安装记录，这个 tab 只读着就行"}
    vbs_path = os.path.join(root, "Start Workbench.vbs")
    items = []
    for it in _wb_rows(doc):
        ident = str(it.get("_key") or it.get("id") or it.get("profile") or it.get("name") or "").strip()
        if ident != scope:  # 硬限定：只看约定好的那一个实例，绝不把别的实例投影出来
            continue
        row = {k: it.get(k) for k in WB_FIELDS if it.get(k) is not None}
        row["key"] = ident
        prof = os.path.join(root, "instances", ident)
        row["profile_dir"] = os.path.isdir(prof)
        # instances.json does not record a pid, so "running" cannot be known honestly.
        # A Firefox/Camoufox profile lock is a hint, not proof: it can survive a crash.
        row["lock_present"] = os.path.isfile(os.path.join(prof, "parent.lock"))
        pid = it.get("pid")
        row["pid"] = int(pid) if isinstance(pid, int) and pid > 0 else None
        row["alive"] = _pid_alive(row["pid"]) if row["pid"] else None
        for tk in ("created_at", "last_opened"):
            try:
                row[tk + "_iso"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(row[tk])))
            except Exception:
                pass
        items.append(row)
    return {"available": bool(items), "scope": scope, "items": items,
            "launcher": (vbs_path if os.path.isfile(vbs_path) else False),
            "note": "只投影约定的那一个实例；其余实例不读也不显示"}


def workbench_action(cfg, action, instance=None, confirm=""):
    """open = launch the workbench program; close = kill its recorded pid.

    Two honest limits found on this machine: 'Start Workbench.vbs' starts the workbench
    manager (pythonw app/workbench.py) rather than a specific instance's browser, and
    instances.json stores no pid. So close is refused instead of guessing a process.
    """
    p = paths(cfg)
    if instance and str(instance).strip() not in ("", str(p["wb_scope"])):
        return {"error": "只允许 %s，其余实例不在本机的授权范围内" % p["wb_scope"]}
    if action == "status":
        return workbench_status(cfg)
    st = workbench_status(cfg)
    if action == "open":
        if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
            return {"confirm_required": True,
                    "will": "启动工作台程序（它自己会开窗口）。这不会单独打开某个实例的浏览器。",
                    "launcher": st.get("launcher")}
        vbs = st.get("launcher")
        if not vbs:
            return {"error": "找不到启动脚本，不猜路径：%s"
                            % os.path.join(os.path.expandvars(os.path.expanduser(str(p["wb_root"]))),
                                          "Start Workbench.vbs")}
        try:
            proc = subprocess.Popen(["wscript.exe", vbs])
        except Exception as e:
            return {"error": "启动失败：%s" % str(e)[:160]}
        audit.write("arena_workbench_open", pid=proc.pid, scope=p["wb_scope"])
        return {"ok": True, "started": True, "pid": proc.pid,
                "note": "工作台程序已启动；具体开哪个实例请在它的窗口里选，我不替它点"}
    if action == "close":
        row = (st.get("items") or [{}])[0]
        pid = row.get("pid")
        if not pid:
            return {"error": "没有可关闭的进程号：instances.json 不记 pid，我不猜要关哪个进程。"
                             "请在工作台窗口里关，或把进程号填进来"}
        if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
            return {"confirm_required": True, "will": "只结束进程号 %s 这一个进程（不 /T、不杀整棵进程树）" % pid}
        try:
            r = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15)
        except Exception as e:
            return {"error": "关闭失败：%s" % str(e)[:160]}
        ok = r.returncode == 0
        audit.write("arena_workbench_close", pid=pid, ok=ok)
        return {"ok": ok, "closed": ok,
                "detail": security.redact((r.stdout or "") + (r.stderr or ""))[:200] or None}
    return {"error": "不认识的工作台动作：%s（可用 status / open / close）" % action}


# ------------------------------------------------------------------------ exec
def grant_state(cfg):
    p = paths(cfg)["grant"]
    doc, err = _read_json(p)
    until = doc.get("until") if isinstance(doc, dict) else None
    try:
        left = int(until - time.time())
    except Exception:
        left = 0
    if left <= 0:
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
        return {"active": False, "seconds_left": 0}
    return {"active": True, "seconds_left": left, "minutes_left": round(left / 60.0, 1)}


def set_grant(cfg, minutes=10, confirm=""):
    if str(confirm) != "yes":
        return {"confirm_required": True,
                "will": "允许大肥鱼在你电脑上执行命令，%s 分钟后自动收回" % max(1, min(int(minutes or 10), GRANT_MAX_MINUTES))}
    minutes = max(0, min(int(minutes or 10), GRANT_MAX_MINUTES))
    p = paths(cfg)["grant"]
    if minutes == 0:
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        audit.write("arena_grant", minutes=0)
        return {"ok": True, "active": False, "note": "执行权限已收回"}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    doc = {"until": int(time.time()) + minutes * 60, "granted_at": int(time.time()), "by": "desktop"}
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)
    os.replace(tmp, p)
    audit.write("arena_grant", minutes=minutes)
    return {"ok": True, "active": True, "minutes": minutes, "note": "到点自动关闭，之后所有执行请求一律拒绝"}


DENIED_COMMANDS = re.compile(
    r"(?i)(\brm\s+-[rf]|remove-item.*-recurse.*-force|\bdel\s+/[sq]|format\s+[a-z]:|"
    r"reg\s+delete|rd\s+/s|diskpart|shutdown|cipher\s+/|netsh\s+advfirewall|"
    r"taskkill\s+/im\s+(qq|wechat)|\bsetx\b|\bcurl\b[^|]*\|\s*(sh|bash)|invoke-webrequest.*iex)")


def exec_command(cfg, cmd, timeout=120):
    g = grant_state(cfg)
    if not g.get("active"):
        audit.write("arena_exec_denied", reason="no grant")
        return {"error": "执行权限没开：先在 Arena 页的工具标签里限时授权"}
    cmd = str(cmd or "").strip()
    if not cmd:
        return {"error": "没有命令"}
    if len(cmd) > 800:
        return {"error": "命令太长（上限 800 字）"}
    if DENIED_COMMANDS.search(cmd):
        audit.write("arena_exec_denied", reason="red line", cmd=security.redact(cmd)[:120])
        return {"error": "这条命令落在红线上（递归删除/格式化/改防火墙/登出 QQ 这类），不执行"}
    t0 = time.time()
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=max(1, min(int(timeout), 600)))
    except subprocess.TimeoutExpired:
        audit.write("arena_exec", ok=False, ms=int((time.time() - t0) * 1000), rc="timeout")
        return {"error": "超时已中断（%ds）" % timeout}
    except Exception as e:
        audit.write("arena_exec", ok=False, error=type(e).__name__)
        return {"error": "执行失败：%s" % type(e).__name__}
    ms = int((time.time() - t0) * 1000)
    out = security.redact(((r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")))[:4000]
    audit.write("arena_exec", ok=(r.returncode == 0), rc=r.returncode, ms=ms,
                cmd=security.redact(cmd)[:160])
    return {"ok": r.returncode == 0, "rc": r.returncode, "ms": ms, "output": out,
            "seconds_left": grant_state(cfg).get("seconds_left")}


# ------------------------------------------------------------------ overview
def overview(cfg):
    p = paths(cfg)
    url, err = _load_endpoint(cfg)
    st = {"configured": bool(url), "endpoint_sha8": fingerprint(cfg),
          "error": None if url else (err or "未配置上游地址"),
          "reachable": None, "agent_id": _agent_id(cfg), "tools": None,
          "grant": grant_state(cfg), "mode": "queue",
          "browser_available": False, "browser_reason": "浏览器派活还没接入（依赖工作台会话匹配）",
          "profile_scope": p["wb_scope"], "requests": len(os.listdir(p["requests"])) if os.path.isdir(p["requests"]) else 0}
    c = client(cfg)
    if c is not None:
        try:
            tools = c.tools()
            st["reachable"] = True
            st["tools"] = len(tools)
            st["tool_names"] = sorted(t["name"] for t in tools)[:80]
        except Exception as e:
            st["reachable"] = False
            st["error"] = security.redact(str(e))[:200]
    try:
        wb = workbench_status(cfg)
        st["browser_available"] = bool(wb.get("items")) and wb.get("launcher") and \
            bool((wb["items"][0] or {}).get("alive"))
        st["workbench"] = wb
    except Exception as e:
        st["workbench"] = {"error": type(e).__name__}
    return st

