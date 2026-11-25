"""六本豆工作台 · 每个实例自己的 Arena 房间（左侧栏会话 + 模型名）只读读取。

模型列表的真实来源不是 AstrBot 的 provider 配置，而是**每个实例打开的那个 Arena 页面**：
工作台左侧栏一行一个会话，行文本里写着模型名（模型备注）。所以这里按实例去问工作台，
而不是猜、也不是拿别处的清单顶替。

读取走工作台自己的任务通道，不直接碰浏览器：

    ~/ops-dfy/ctl/requests/<任意名>.json
        {"profile": "profile_3", "kind": "inspect_arena_notes", "state": "pending", ...}

`instance_launcher.py` 看到自己这个 profile 的 pending 巡检任务，就用 Playwright 只读地扫
一遍已有 Arena 标签页的侧栏，把 `model_note` / `href` / `row_text` 写回同一个文件。

这里只做三件事：列实例、发起一次只读巡检、把结果缓存成 UI 和插件要的形状。
往会话里发任务（`kind: "browser"`）不在这里——那是另一条需要单独授权的路径。
"""

import json
import os
import subprocess
import threading
import time
import uuid

from . import config

INSPECT_KIND = "inspect_arena_notes"
DONE_STATES = ("done", "blocked", "failed", "timeout", "failed_pick")
CACHE_TTL = 600          # 10 分钟内直接用缓存：开页面不用每次都去敲浏览器
DEFAULT_WAIT = 25
POLL = 0.5


def tasks_dir():
    """工作台任务通道。自测用 DFY_ARENA_TASKS_DIR 指到临时目录。"""
    return os.environ.get("DFY_ARENA_TASKS_DIR") or os.path.join(
        os.path.expanduser("~"), "ops-dfy", "ctl", "requests")


def wb_root(cfg):
    e = os.environ.get("DFY_ARENA_WB_ROOT") or cfg.get("arena_workbench_root")
    return os.path.expandvars(os.path.expanduser(str(
        e or os.path.join(os.path.expanduser("~"), "AppData", "Local", "LiubendouWorkbench"))))


def cache_path(cfg):
    """房间缓存放在「跟着用户走」的地方，不跟着安装版本走。

    runtime_dir() 是 %LOCALAPPDATA%/Dafeiyu/app-<ver>/.runtime，每升一版就换一个新目录，
    缓存跟着丢；一丢 /v1/arena/models 就只能拿兜底清单冒充模型表。
    DFY_ARENA_CACHE 只给自测用。
    """
    env = os.environ.get("DFY_ARENA_CACHE")
    if env:
        return env
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Dafeiyu", "userdata", "arena", "rooms.json")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except FileNotFoundError:
        return None, "missing"
    except Exception as e:
        return None, type(e).__name__


def _write_json(path, doc):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _alive_pids():
    """一次 tasklist 拿全量 pid，别每个实例都起一个进程。拿不到就返回 None（未知）。"""
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=8).stdout or ""
    except Exception:
        return None
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].strip('"').isdigit():
            pids.add(int(parts[1].strip('"')))
    return pids


def instances(cfg):
    """工作台的实例清单 + 它自己 run/<profile>.json 里的运行态（pid/端口/标签页）。"""
    root = wb_root(cfg)
    meta, err = _read_json(os.path.join(root, "instances.json"))
    meta = meta if isinstance(meta, dict) else {}
    alive = _alive_pids()
    out = []
    for key in sorted(meta.keys()):
        it = meta.get(key) or {}
        if not isinstance(it, dict):
            continue
        run, _ = _read_json(os.path.join(root, "run", key + ".json"))
        run = run if isinstance(run, dict) else {}
        pid = run.get("pid") if isinstance(run.get("pid"), int) else None
        running = bool(pid and (alive is None or pid in alive)) and str(run.get("phase") or "") == "running"
        out.append({"key": key,
                    "name": str(it.get("name") or key)[:40],
                    "login": str(it.get("login") or "")[:20],
                    "open_count": it.get("open_count"),
                    "last_opened": it.get("last_opened"),
                    "running": running,
                    "pid": pid,
                    "port": run.get("port"),
                    "phase": run.get("phase"),
                    "tabs": [str(t)[:120] for t in (run.get("tabs") or [])][:8],
                    "arena_tabs": [str(t) for t in (run.get("tabs") or []) if "arena.ai" in str(t)][:4]})
    return {"available": bool(out), "root": root, "items": out,
            "running": [x["key"] for x in out if x["running"]],
            "error": None if out else (err if err != "missing" else None),
            "note": "实例清单来自工作台自己的 instances.json / run/*.json，不猜"}


def _new_task(profile, note):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return {"profile": str(profile)[:32], "kind": INSPECT_KIND, "state": "pending",
            "created_at": now, "updated_at": now, "requested_by": "dfy-desktop",
            "note": str(note or "")[:200]}


def _rows_from_result(res):
    """把 launcher 回写的 result 摊平成房间行。兼容两种历史形状。"""
    if isinstance(res, str):
        try:
            doc = json.loads(res)
        except Exception:
            return [], []
    else:
        doc = res if isinstance(res, dict) else {}
    pages = doc.get("pages")
    if not isinstance(pages, list):
        pages = [doc] if doc else []
    rows = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        base = str(page.get("page_url") or "")
        for c in (page.get("model_note_candidates") or []):
            if not isinstance(c, dict):
                continue
            href = str(c.get("href") or "")[:200]
            tokens = [str(t)[:40] for t in (c.get("model_tokens") or [])][:8]
            note = str(c.get("model_note") or "").strip()[:64]
            if not note and len(tokens) == 1:
                note = tokens[0]
            rows.append({"model_note": note, "model_tokens": tokens, "href": href,
                         "row_text": str(c.get("row_text") or "")[:300],
                         "page_url": base[:200], "row_index": c.get("row_index")})
    return rows, pages


def inspect(cfg, profile, timeout=DEFAULT_WAIT, note=""):
    """给一个实例发一次只读巡检任务并等它回来。只读：只扫侧栏，不发任何消息。"""
    d = tasks_dir()
    if not os.path.isdir(d):
        return {"ok": False, "profile": profile, "state": "no-channel",
                "error": "工作台任务通道不存在：%s" % d, "rows": []}
    task = _new_task(profile, note)
    path = os.path.join(d, "browser-inspect-%s-%s.json" % (
        str(profile).replace("_", ""), uuid.uuid4().hex))
    try:
        _write_json(path, task)
    except Exception as e:
        return {"ok": False, "profile": profile, "state": "write-failed",
                "error": "%s: %s" % (type(e).__name__, e), "rows": []}
    deadline = time.time() + max(2, float(timeout))
    doc = None
    while time.time() < deadline:
        time.sleep(POLL)
        got, _ = _read_json(path)
        if isinstance(got, dict) and str(got.get("state") or "").lower() in DONE_STATES:
            doc = got
            break
    if doc is None:
        return {"ok": False, "profile": profile, "state": "timeout", "file": os.path.basename(path),
                "error": "实例没在规定时间里回话（可能没在跑，或这个实例没有 Arena 标签页）",
                "summary": "", "rows": []}
    rows, pages = _rows_from_result(doc.get("result"))
    state = str(doc.get("state") or "")
    return {"ok": state == "done", "profile": profile, "state": state,
            "file": os.path.basename(path), "summary": str(doc.get("summary") or "")[:200],
            "next_step": str(doc.get("next_step") or "")[:160],
            "pages": len(pages), "rows": rows, "at": str(doc.get("updated_at") or "")}


def snapshot(cfg, refresh=False, profiles=None, timeout=DEFAULT_WAIT, ttl=CACHE_TTL):
    """每个实例读一遍侧栏，合并成房间清单并落缓存。refresh=False 且缓存新鲜就直接用缓存。"""
    cached, _ = _read_json(cache_path(cfg))
    cached = cached if isinstance(cached, dict) else {}
    age = time.time() - float(cached.get("at_ts") or 0)
    if not refresh and cached.get("items") and age < float(ttl):
        cached["cached"] = True
        cached["age_s"] = int(age)
        return cached
    inst = instances(cfg)
    targets = [str(p) for p in (profiles or inst["running"])]
    items, per, errors = [], {}, []
    for prof in targets:
        r = inspect(cfg, prof, timeout=timeout)
        per[prof] = {"state": r["state"], "summary": r.get("summary"), "rows": len(r.get("rows") or []),
                     "at": r.get("at")}
        if r.get("error"):
            errors.append("%s: %s" % (prof, r["error"]))
        label = next((x["name"] for x in inst["items"] if x["key"] == prof), prof)
        for row in (r.get("rows") or []):
            href = row.get("href") or ""
            key = "%s|%s" % (prof, href or row.get("row_text"))
            if any(x["id"] == key for x in items):
                continue
            url = ""
            if href.startswith("/") and row.get("page_url"):
                url = row["page_url"].split("/agent")[0].rstrip("/") + href
            items.append({"id": key[:160], "profile": prof, "profile_label": label,
                          "model_note": row.get("model_note") or "",
                          "model_tokens": row.get("model_tokens") or [],
                          "href": href, "url": url[:240],
                          "row_text": row.get("row_text") or "",
                          "row_index": row.get("row_index")})
    doc = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "at_ts": time.time(),
           "profiles": per, "items": items, "errors": errors,
           "targets": targets, "cached": False, "age_s": 0}
    try:
        _write_json(cache_path(cfg), doc)
    except Exception as e:
        errors.append("缓存没写成：%s" % type(e).__name__)
        doc["errors"] = errors
    return doc


_WARM = {"thread": None}


def warm_async(cfg, profiles=None, timeout=DEFAULT_WAIT):
    """后台跑一次只读巡检，不让调用方干等。

    /v1/arena/models 走的是只读缓存：缓存空的时候就地巡检要卡二十几秒，聊天工具那边
    12 秒就超时。所以这里起个线程去读侧栏，本次调用先回缓存里已有的东西。
    """
    th = _WARM.get("thread")
    if th is not None and th.is_alive():
        return {"started": False, "reason": "already-warming"}

    def _run():
        try:
            snapshot(cfg, refresh=True, profiles=profiles, timeout=timeout)
        except Exception:
            pass

    th = threading.Thread(target=_run, name="dfy-arena-warm", daemon=True)
    _WARM["thread"] = th
    th.start()
    return {"started": True}


def cached(cfg):
    """只读缓存，绝不触发巡检（给 /v1/arena/models 这种要秒回的路径用）。"""
    doc, err = _read_json(cache_path(cfg))
    if not isinstance(doc, dict):
        return {"at": None, "items": [], "profiles": {}, "errors": [], "empty": True,
                "error": None if err == "missing" else err}
    doc["empty"] = not bool(doc.get("items"))
    return doc
