"""AstrBot 插件市场（只读）：把官方注册表拉下来，和本机已装的插件对一遍。

注册表就是 AstrBot 面板用的那一份（AstrBotDevs/AstrBot_Plugins_Collection 的 plugins.json）。
拉得到就刷新缓存，拉不到就用上次的缓存；一次都没拉过就老实说没有，不编数据。

2026-10-31 实测：这台机器到 GitHub raw 的下载在 30-60 s 徘徊，同步刷新会把窗口卡住，
所以「有缓存时」刷新一律丢给后台线程，接口立刻带着旧数据返回 refreshing=True，
页面自己轮询；只有「一次缓存都没有」的首屏才同步等（最多 BLOCK_TIMEOUT 秒）。

安装/更新最终要 AstrBot 重启才生效，所以这里只做「看清楚 + 指路」：
真装的动作留给 AstrBot 面板，装完什么时候重启由主人决定。
"""

import gzip
import json
import os
import re
import threading
import time
import urllib.request

from . import config

REGISTRY = ("https://raw.githubusercontent.com/AstrBotDevs/AstrBot_Plugins_Collection/"
            "main/plugins.json")
CACHE_TTL = 600
MAX_BYTES = 4 * 1024 * 1024
TIMEOUT = 20          # 首屏同步等多久
BG_TIMEOUT = 120      # 后台线程可以慢慢拉
BLOCK_TIMEOUT = 12    # 没缓存时最多让页面等这么久

_LOCK = threading.Lock()
_STATE = {"running": False, "started": 0.0, "done": 0.0, "error": None}


def astrbot_dir(cfg):
    return str(cfg.get("astrbot_dir") or "")


def plugins_dir(cfg):
    return os.path.join(astrbot_dir(cfg), "data", "plugins")


def cache_path(cfg):
    return os.path.join(config.runtime_dir(), "plugins-market.json")


def _read_json(path, cap=MAX_BYTES):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return json.loads(fh.read(cap)), None
    except FileNotFoundError:
        return None, "missing"
    except Exception as e:
        return None, type(e).__name__


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _meta(text):
    """metadata.yaml 只要几个标量字段，正则比拉一个 YAML 依赖进来划算。"""
    out = {}
    for key, pattern in (("name", r"^\s*name\s*:\s*(.+?)\s*$"),
                         ("author", r"^\s*author\s*:\s*(.+?)\s*$"),
                         ("desc", r"^\s*desc\s*:\s*(.+?)\s*$"),
                         ("version", r"^\s*version\s*:\s*(.+?)\s*$"),
                         ("repo", r"^\s*repo\s*:\s*(.+?)\s*$")):
        m = re.search(pattern, text, re.M)
        if m:
            out[key] = m.group(1).strip().strip('"').strip("'")[:240]
    return out


def installed(cfg):
    """本机 data/plugins 下每个目录的元数据。读不出来就只报目录名，不猜。"""
    root = plugins_dir(cfg)
    items = []
    if not os.path.isdir(root):
        return {"dir": root, "available": False, "items": [], "count": 0, "error": None}
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not os.path.isdir(p):
            continue
        doc = {"dir_name": name, "path": p}
        for meta in ("metadata.yaml", "metadata.yml"):
            f = os.path.join(p, meta)
            if os.path.isfile(f):
                try:
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        doc.update(_meta(fh.read(20000)))
                except Exception as e:
                    doc["meta_error"] = type(e).__name__
                break
        doc["display"] = str(doc.get("name") or name)[:80]
        items.append(doc)
    return {"dir": root, "available": True, "items": items, "count": len(items), "error": None}


def _fetch(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": "dafeiyu-desktop/0.4", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES)
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "replace"))


def _entries(doc):
    """注册表是 {plugin_id: {...}}；也兼容 {"data": {...}} / 列表两种打包方式。"""
    if isinstance(doc, dict) and isinstance(doc.get("data"), (dict, list)):
        doc = doc["data"]
    if isinstance(doc, dict):
        pairs = list(doc.items())
    elif isinstance(doc, list):
        pairs = [(str(x.get("id") or x.get("name") or ""), x) for x in doc if isinstance(x, dict)]
    else:
        pairs = []
    out = []
    for key, val in pairs:
        if not isinstance(val, dict):
            continue
        pid = str(val.get("id") or key or "")[:80]
        if not pid:
            continue
        tags = val.get("tags") or val.get("category") or []
        out.append({"id": pid,
                    "name": str(val.get("display_name") or val.get("name") or pid)[:80],
                    "author": str(val.get("author") or "")[:60],
                    "desc": str(val.get("desc") or val.get("description") or "")[:300],
                    "repo": str(val.get("repo") or val.get("repo_url") or "")[:200],
                    "version": str(val.get("version") or "")[:32],
                    "tags": [str(t)[:24] for t in tags][:8] if isinstance(tags, list) else []})
    return out


def _pull(cfg, cached, timeout):
    """真正去拉一次，成功就落盘。异常往上抛，由调用方决定怎么交代。"""
    items = _entries(_fetch(REGISTRY, timeout=timeout))
    if not items:
        raise ValueError("注册表是空的")
    doc = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "at_ts": time.time(),
           "items": items, "registry": REGISTRY}
    try:
        _write_json(cache_path(cfg), doc)
    except Exception:
        pass
    return doc


def _worker(cfg):
    try:
        _pull(cfg, {}, BG_TIMEOUT)
        _STATE["error"] = None
    except Exception as e:
        _STATE["error"] = "联网失败，缓存保持原样（%s: %s）" % (type(e).__name__, str(e)[:100])
    finally:
        _STATE["done"] = time.time()
        _STATE["running"] = False


def refresh_async(cfg):
    """后台拉注册表。已经在拉了就不重复开线程。"""
    with _LOCK:
        if _STATE["running"]:
            return False
        _STATE.update({"running": True, "started": time.time(), "error": None})
    t = threading.Thread(target=_worker, args=(cfg,), name="plugins-market", daemon=True)
    t.start()
    return True


def _cache_view(cached, age, error):
    source = "cache" if cached.get("items") else "none"
    # 后台刚拉成功的那一份：数据其实就是网络来的，别让界面显示“cache”骗人
    if source == "cache" and not error and _STATE["done"] and time.time() - _STATE["done"] < 90:
        source = "network"
    return {"items": cached.get("items") or [], "at": cached.get("at"),
            "source": source,
            "age_s": int(age) if cached.get("items") else 0, "error": error,
            "refreshing": bool(_STATE["running"])}


def market(cfg, refresh=False, ttl=CACHE_TTL):
    """注册表（带缓存）。有缓存时刷新 = 后台拉，接口立刻返回；没缓存时同步等一会儿。"""
    cached, why = _read_json(cache_path(cfg))
    cached = cached if isinstance(cached, dict) else {}
    age = time.time() - float(cached.get("at_ts") or 0)
    if not refresh and cached.get("items") and age < ttl:
        out = _cache_view(cached, age, None)
        return out
    if cached.get("items"):
        if refresh:
            refresh_async(cfg)
        out = _cache_view(cached, age, _STATE["error"])
        return out
    # 一次都没缓存：只能同步等，但不能让页面无限等
    try:
        doc = _pull(cfg, cached, min(BLOCK_TIMEOUT, TIMEOUT))
        return {"items": doc["items"], "at": doc["at"], "source": "network",
                "age_s": 0, "error": None, "refreshing": bool(_STATE["running"])}
    except Exception as e:
        return {"items": [], "at": None, "source": "none", "age_s": 0,
                "error": "拉不到插件注册表（%s: %s）" % (type(e).__name__, str(e)[:120]),
                "refreshing": bool(_STATE["running"])}


def view(cfg, refresh=False, q=""):
    """市场 + 已装，一次给界面。装没装按 repo / 目录名 / 显示名三种方式认。"""
    mk = market(cfg, refresh=refresh)
    ins = installed(cfg)
    by_repo, by_name = {}, {}
    for it in ins.get("items") or []:
        repo = str(it.get("repo") or "").rstrip("/").lower()
        if repo:
            by_repo[repo] = it
        by_name[str(it.get("dir_name") or "").lower()] = it
        by_name[str(it.get("display") or "").lower()] = it
    items = []
    needle = str(q or "").strip().lower()
    for m in mk.get("items") or []:
        repo = str(m.get("repo") or "").rstrip("/").lower()
        hit = by_repo.get(repo) or by_name.get(m["id"].lower()) or by_name.get(m["name"].lower())
        row = dict(m)
        row["installed"] = bool(hit)
        row["installed_dir"] = (hit or {}).get("dir_name")
        row["installed_version"] = (hit or {}).get("version")
        if needle and needle not in (" ".join([m["id"], m["name"], m["author"], m["desc"]]).lower()):
            continue
        items.append(row)
    return {"items": items, "count": len(items), "installed": ins.get("items") or [],
            "installed_count": ins.get("count") or 0, "installed_dir": ins.get("dir"),
            "market": {k: mk.get(k) for k in ("at", "source", "age_s", "error", "refreshing")},
            "registry": REGISTRY,
            "note": "安装要在 AstrBot 面板里点，装完重启 AstrBot 才生效（重启需主人授权）"}
