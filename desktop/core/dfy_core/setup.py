# -*- coding: utf-8 -*-
"""First-run wizard state: environment checks, read-only inventory, adopt mode.

Design §8.2: when production data is already on this machine the wizard recommends
"接管" and must first do a read-only inventory. Nothing here writes to any file the
bot owns — the only thing `adopt` writes is our own marker plus a snapshot of the files
we might later be allowed to change.
"""
import hashlib
import json
import os
import shutil
import time

from . import audit, backup, config, security

STATE_NAME = "setup.json"
REQUIRED_PORTS = ("astrbot_webui", "astrbot_onebot")


def state_path():
    return os.path.join(config.runtime_dir(), STATE_NAME)


def _sha_head(path, limit=4 * 1024 * 1024):
    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            for _ in range(64):  # a bounded read: a diagnostic hash, not a file digest
                chunk = fh.read(65536)
                if not chunk:
                    break
                h.update(chunk)
                limit -= len(chunk)
                if limit <= 0:
                    break
    except Exception:
        return None
    return h.hexdigest()[:8]


def state(cfg):
    doc, err = backup.read_json(state_path())
    if err or not isinstance(doc, dict):
        doc = {}
    return {"done": bool(doc.get("done")), "mode": doc.get("mode"), "at": doc.get("at"),
            "inventory_sha8_head": doc.get("inventory_sha8_head"),
            "wizard_needed": not bool(doc.get("done")),
            "note": None if doc.get("done") else "还没跑过首次启动向导（不影响直接用，界面上有跳过）"}


def _ok(name, detail, level="info"):
    return {"name": name, "ok": True, "level": level, "detail": detail}


def _bad(name, detail, level="warn"):
    return {"name": name, "ok": False, "level": level, "detail": detail}


def checks(cfg):
    """Environment + connectivity checks the wizard shows. `block` means adopting would
    be unsafe; `warn` means we can go ahead but should say so."""
    ab = cfg.get("astrbot_dir") or ""
    desk = getattr(config, "DESKTOP_DIR", None) or os.getcwd()
    out = []
    py = "%d.%d" % (os.sys.version_info[0], os.sys.version_info[1])
    out.append(_ok("python", "本机核心用 Python %s" % py) if os.sys.version_info >= (3, 10)
               else _bad("python", "需要 Python 3.10+，现在是 %s" % py, "block"))
    exe = os.path.join(desk, "node_modules", "electron", "dist", "electron.exe")
    out.append(_ok("electron", "本机 Electron 运行时在位") if os.path.isfile(exe)
               else _bad("electron", "没找到 electron.exe：用完整版包，或在 desktop 目录 npm install", "warn"))
    # 这个工程的入口是 package.json 的 main（src/main/index.mjs），vite 只产 out/renderer；
    # 按不存在的 out/main/index.js 判死刑会把好机器说成坏的
    entry = ""
    try:
        pj = json.loads(open(os.path.join(desk, "package.json"), encoding="utf-8").read())
        entry = str(pj.get("main") or "")
    except Exception:
        entry = ""
    entry_ok = bool(entry) and os.path.isfile(os.path.join(desk, *entry.replace("\\", "/").split("/")))
    built = os.path.join(desk, "out", "renderer", "index.html")
    if entry_ok and os.path.isfile(built):
        out.append(_ok("build", "入口 %s 与渲染产物都在位" % entry))
    else:
        what = ("入口缺失：package.json 的 main=%r" % entry) if not entry_ok else "没跑过 vite build（缺 out/renderer/index.html）"
        out.append(_bad("build", what + "；接管配置不需要它，但窗口会是空白", "warn"))
    out.append(_ok("astrbot_dir", ab) if os.path.isdir(ab)
               else _bad("astrbot_dir", "没找到 AstrBot 目录（全新机器就选「新建」）", "warn"))
    prod = []
    for key, label in (("cmd_config", "AstrBot 配置"), ("plugin_config", "插件配置"),
                       ("persona_file", "人格 md")):
        p = cfg.get(key) or ""
        if p and os.path.isfile(p):
            st = os.stat(p)
            prod.append({"label": label, "path": p, "bytes": st.st_size,
                         "sha8_head": _sha_head(p), "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
    out.append(_ok("existing_data", "%d 份现有配置可接管" % len(prod)) if prod
               else _bad("existing_data", "没有现成配置，走新建流程", "warn"))
    from . import readers
    for pname in REQUIRED_PORTS:
        port = (cfg.get("ports") or {}).get(pname)
        up = bool(port) and readers._tcp_open(port)
        out.append(_ok("port:" + pname, "127.0.0.1:%s 在听" % port) if up
                   else _bad("port:" + pname, "127.0.0.1:%s 没在听（接管模式需要引擎活着）" % port,
                             "warn" if pname != "astrbot_webui" else "warn"))
    try:
        free = shutil.disk_usage(config.runtime_dir()).free
        out.append(_ok("disk", "%.1f GB 可用" % (free / 1e9)) if free > 500 * 1024 * 1024
                   else _bad("disk", "磁盘只剩 %.1f GB" % (free / 1e9), "warn"))
    except Exception as e:
        out.append(_bad("disk", "读不到磁盘余量：%s" % type(e).__name__))
    try:
        t = os.path.join(config.runtime_dir(), ".write-test")
        with open(t, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(t)
        out.append(_ok("writable", "应用数据目录可写"))
    except Exception as e:
        out.append(_bad("writable", "应用数据目录写不了：%s" % type(e).__name__, "block"))
    blockers = [c["name"] for c in out if c["level"] == "block" and not c["ok"]]
    return {"items": out, "ok": not blockers, "blockers": blockers,
            "existing_files": prod, "recommend": "adopt" if prod else "fresh",
            "note": "检测到现有生产数据时默认推荐接管，并且先做只读盘点（§8.2）"}


def inventory(cfg):
    """The read-only pass the wizard shows before you adopt: what the fish currently is."""
    inv = {"files": [], "counts": {}, "errors": []}
    for key, label in (("cmd_config", "cmd_config.json"), ("plugin_config", "plugin_config.json"),
                       ("persona_file", "persona md")):
        p = cfg.get(key) or ""
        if p and os.path.isfile(p):
            inv["files"].append({"label": label, "name": os.path.basename(p),
                                 "bytes": os.path.getsize(p), "sha8_head": _sha_head(p),
                                 "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    from . import memories, persona, threads
    try:
        inv["counts"]["personas"] = len((persona.sources(cfg).get("db") or {}).get("rows") or [])
    except Exception as e:
        inv["errors"].append("personas: %s" % type(e).__name__)
    try:
        mi = memories.info(cfg)
        inv["counts"]["memory_available"] = bool(mi.get("available"))
        inv["counts"]["memory_bytes"] = mi.get("bytes")
        rows = {t.get("name"): t.get("rows") for t in (mi.get("tables") or [])}
        primary = mi.get("primary_table")
        # 数字给人看：接管之前先知道有多少历史（键名不靠猜，表名来自 memories.info）
        inv["counts"]["messages"] = rows.get("messages", rows.get(primary))
        inv["counts"]["facts"] = rows.get("group_facts")
    except Exception as e:
        inv["errors"].append("memory: %s" % type(e).__name__)
    try:
        conv = threads.conversations(cfg)
        inv["counts"]["sessions"] = len(conv.get("items") or conv.get("conversations") or [])
    except Exception as e:
        inv["counts"]["sessions"] = None
    from . import outbox
    try:
        q = outbox.queue_state(cfg)
        inv["counts"]["outbox"] = {k: q.get(k) for k in ("inbox", "sent", "failed") if k in q}
    except Exception:
        pass
    inv["note"] = ("只读盘点，不动任何生产文件、不重启引擎；sha8_head 只取文件前 4MB，"
                   "用来判断有没有变，不是完整摘要")
    return inv


def adopt(cfg, mode="adopt", confirm=""):
    mode = str(mode or "adopt")
    if mode not in ("adopt", "fresh"):
        return {"error": "只认 adopt（接管现有）或 fresh（本机新建）"}
    ck = checks(cfg)
    if ck["blockers"]:
        return {"error": "有几项是硬阻塞：%s；先修这些再接管" % "、".join(ck["blockers"])}
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True,
                "will": "记下「%s」这个选择，并对将要接管的文件做一次快照（只读复制，不动原文件）"
                        % ("接管现有数据" if mode == "adopt" else "本机新建"),
                "files": [os.path.basename(f["path"]) for f in ck["existing_files"]],
                "note": "这一步只写我们自己的完成标记和一份快照，不改上面任何一个文件"}
    snap = backup.create(cfg, "handover")
    inv = inventory(cfg)
    doc = {"done": True, "mode": mode, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "guard": snap["id"], "inventory_sha8_head": [f.get("sha8_head") for f in inv["files"]],
           "checks_ok": ck["ok"]}
    backup._write_atomic(state_path(), json.dumps(doc, ensure_ascii=False, indent=1))
    audit.write("setup_adopt", mode=mode, guard=snap["id"], files=len(inv["files"]))
    return {"ok": True, **doc, "snapshot": snap["id"],
            "note": "已记录；界面不会再弹向导（想重看就在设置里点「重新运行向导」）"}


def reset(confirm=""):
    """Let the owner re-run the wizard. Only removes our own marker."""
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True, "will": "只是清掉本 App 的完成标记，不动任何生产配置"}
    try:
        os.remove(state_path())
    except FileNotFoundError:
        pass
    except Exception as e:
        return {"error": "清不掉：%s" % type(e).__name__}
    audit.write("setup_reset")
    return {"ok": True}
