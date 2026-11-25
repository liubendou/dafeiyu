# -*- coding: utf-8 -*-
"""Config backup / restore / encrypted export (design §6.6, §7.2, §8.3, 阶段3).

Everything the GUI can change in production goes through here first: one snapshot per
write, verified by hash, with an automatic rollback if the file we wrote back doesn't
match. SQLite sources are copied with the sqlite backup API (WAL-safe) and are
deliberately NOT restorable wholesale while AstrBot is running — see restore().
"""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile

from . import audit, config, security

KINDS = ("json", "text", "sqlite")
TAG_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9_-]{1,20}$")


def root(cfg=None):
    return os.path.join(config.runtime_dir(), "backups")


def exports_dir():
    return os.path.join(config.runtime_dir(), "exports")


def settings(cfg=None):
    p = os.path.join(config.runtime_dir(), "backup.json")
    doc = {"auto_minutes": 720, "keep": 20}
    if os.path.isfile(p):
        try:
            got = json.load(open(p, encoding="utf-8"))
            if isinstance(got, dict):
                doc.update({k: got[k] for k in ("auto_minutes", "keep") if k in got})
        except Exception:
            pass
    doc["auto_minutes"] = max(0, min(10080, int(doc.get("auto_minutes") or 0)))
    doc["keep"] = max(1, min(200, int(doc.get("keep") or 20)))
    return doc


def save_settings(cfg, auto_minutes=None, keep=None):
    doc = settings(cfg)
    if auto_minutes is not None:
        doc["auto_minutes"] = max(0, min(10080, int(auto_minutes)))
    if keep is not None:
        doc["keep"] = max(1, min(200, int(keep)))
    os.makedirs(config.runtime_dir(), exist_ok=True)
    _write_atomic(os.path.join(config.runtime_dir(), "backup.json"),
                  json.dumps(doc, ensure_ascii=False, indent=1))
    audit.write("backup_settings", auto_minutes=doc["auto_minutes"], keep=doc["keep"])
    return {"ok": True, **doc}


def _uri(path):
    return "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"


def read_json(path, cap=8 * 1024 * 1024):
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read(cap).decode("utf-8", "replace")), None
    except FileNotFoundError:
        return None, "missing"
    except Exception as e:
        return None, "unreadable: %s" % type(e).__name__


def write_json_atomic(path, doc):
    _write_atomic(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def _write_atomic(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def targets(cfg):
    """What a snapshot covers. `persona_db`/`memory_db` are copied but not restorable
    here (see restore), because the engine is live and owns those files."""
    from . import memories
    ab = cfg.get("astrbot_dir") or ""
    rows = [
        ("cmd_config", cfg.get("cmd_config"), "json"),
        ("plugin_config", cfg.get("plugin_config"), "json"),
        ("persona_md", cfg.get("persona_file"), "text"),
        ("astrbot_db", os.path.join(ab, "data", "data_v4.db") if ab else None, "sqlite"),
        ("memory_db", memories.db_path(cfg), "sqlite"),
    ]
    desk_env = os.environ.get("DFY_DESKTOP_CONFIG")
    if desk_env:
        rows.append(("desktop_config", desk_env, "json"))
    else:
        desk = getattr(config, "DESKTOP_DIR", None) or os.getcwd()
        rows.append(("desktop_config", os.path.join(desk, "config", "desktop.json"), "json"))
    out = []
    for name, path, kind in rows:
        if not path or not os.path.isfile(path):
            continue
        if security.path_is_forbidden(path):
            continue
        out.append({"name": name, "path": path, "kind": kind})
    return out


def _snap_file(src, dst, kind):
    if kind == "sqlite":
        con = sqlite3.connect(_uri(src), uri=True)
        dst_con = sqlite3.connect(dst)
        try:
            con.backup(dst_con, pages=256)
        finally:
            dst_con.close()
            con.close()
        return os.path.getsize(dst)
    shutil.copy2(src, dst)
    return os.path.getsize(dst)


def _sha(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def create(cfg, label="manual"):
    label = re.sub(r"[^a-zA-Z0-9_-]", "", str(label or "manual"))[:20] or "manual"
    tag = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), label)
    if os.path.isdir(os.path.join(root(cfg), tag)):
        tag = "%s-%s" % (tag, int(time.time()) % 97)
    d = os.path.join(root(cfg), tag)
    os.makedirs(d, exist_ok=True)
    files = []
    for t in targets(cfg):
        fn = t["name"] + (".db" if t["kind"] == "sqlite" else os.path.splitext(t["path"])[1] or ".bin")
        dst = os.path.join(d, fn)
        try:
            size = _snap_file(t["path"], dst, t["kind"])
        except Exception as e:
            # one source failing must not lose the rest of the snapshot
            files.append({"name": t["name"], "error": type(e).__name__})
            continue
        files.append({"name": t["name"], "kind": t["kind"], "file": fn,
                      "bytes": size, "sha8": _sha(dst), "src_bytes": os.path.getsize(t["path"])})
    manifest = {"tag": tag, "label": label, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "files": files}
    _write_atomic(os.path.join(d, "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=1))
    n = os.path.join(config.runtime_dir(), "backup-last-auto.txt")
    if label == "auto":
        _write_atomic(n, tag)
    kept = prune(cfg)
    audit.write("backup_create", tag=tag, files=len([f for f in files if "file" in f]),
                errors=len([f for f in files if "error" in f]))
    return {"ok": True, "id": tag, "files": files, "pruned": kept,
            "note": "快照在应用数据目录里，不含 MCP 地址与令牌文件"}


def prune(cfg):
    """Keep the newest `keep` snapshots; oldest go first."""
    keep = settings(cfg)["keep"]
    rs = list_dirs(cfg)
    extra = [r["id"] for r in rs[keep:]]
    for tag in extra:
        try:
            shutil.rmtree(os.path.join(root(cfg), tag))
        except Exception:
            pass
    return len(extra)


def list_dirs(cfg):
    d = root(cfg)
    if not os.path.isdir(d):
        return []
    out = []
    for tag in sorted(os.listdir(d), reverse=True):
        if not TAG_RE.match(tag) or not os.path.isdir(os.path.join(d, tag)):
            continue
        man = None
        mp = os.path.join(d, tag, "manifest.json")
        if os.path.isfile(mp):
            man, _ = read_json(mp)
        files = (man or {}).get("files") or []
        out.append({"id": tag, "created_at": (man or {}).get("created_at"),
                    "label": (man or {}).get("label"),
                    "files": len([f for f in files if f.get("file")]),
                    "errors": len([f for f in files if f.get("error")]),
                    "bytes": sum(int(f.get("bytes") or 0) for f in files),
                    "items": files})
    return out


def listing(cfg):
    rs = list_dirs(cfg)
    st = settings(cfg)
    last_auto = None
    p = os.path.join(config.runtime_dir(), "backup-last-auto.txt")
    if os.path.isfile(p):
        last_auto = open(p, encoding="utf-8").read().strip() or None
    exp = []
    d2 = exports_dir()
    if os.path.isdir(d2):
        for fn in sorted(os.listdir(d2), reverse=True)[:10]:
            if fn.endswith(".dfyexp"):
                fp = os.path.join(d2, fn)
                exp.append({"file": fp, "name": fn, "bytes": os.path.getsize(fp)})
    return {"items": rs, "count": len(rs), "dir": root(cfg), "exports": exp,
            "auto_minutes": st["auto_minutes"], "keep": st["keep"], "last_auto": last_auto,
            "targets": [{"name": t["name"], "kind": t["kind"], "bytes": os.path.getsize(t["path"])}
                        for t in targets(cfg)],
            "note": "sqlite 只快照不整库回滚：引擎在跑，整库覆盖会丢掉它自己的并发写入"}


def restore(cfg, backup_id, name="", confirm=""):
    """Copy one file back from a snapshot, verify it, roll back if it disagrees."""
    tag = str(backup_id or "").strip()
    if not TAG_RE.match(tag):
        return {"error": "备份编号格式不对（看列表里的 id）"}
    d = os.path.join(root(cfg), tag)
    man, err = read_json(os.path.join(d, "manifest.json"))
    if err:
        return {"error": "这个快照没有清单，可能不完整：%s" % err}
    want = str(name or "").strip()
    entries = [f for f in (man.get("files") or []) if f.get("file")]
    if want:
        entries = [f for f in entries if f["name"] == want]
        if not entries:
            return {"error": "这个快照里没有 %s" % want}
    plan = [f for f in entries if f["kind"] in ("json", "text")]
    skipped = [{"name": f["name"], "why": "sqlite 不整库回滚；用「人格历史」按条还原"}
               for f in entries if f["kind"] == "sqlite"]
    if not plan:
        return {"error": "没有可还原的文件（sqlite 需要停引擎后手工做，见说明）", "skipped": skipped}
    tmap = {t["name"]: t["path"] for t in targets(cfg)}
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True,
                "will": "把 %s 里的 %d 个配置写回原位置：%s；写回之前会先把现在的状态再存一份"
                        % (tag, len(plan), "、".join(f["name"] for f in plan)),
                "skipped": skipped}
    # take a guard snapshot of the *current* state before overwriting anything
    guard = create(cfg, "pre-restore")
    done, bad = [], []
    for f in plan:
        src = os.path.join(d, f["file"])
        dst = tmap.get(f["name"])
        if not dst or not os.path.isfile(src):
            bad.append({"name": f["name"], "why": "找不到原路径或快照文件"})
            continue
        try:
            shutil.copy2(src, dst)
        except Exception as e:
            bad.append({"name": f["name"], "why": type(e).__name__})
            continue
        if _sha(dst) != f.get("sha8"):
            bad.append({"name": f["name"], "why": "写回后校验不一致，正在倒回去"})
            gsrc = os.path.join(root(cfg), guard["id"], f["name"] + os.path.splitext(dst)[1])
            if os.path.isfile(gsrc):
                shutil.copy2(gsrc, dst)
            continue
        done.append({"name": f["name"], "sha8": f["sha8"], "path": dst})
    audit.write("backup_restore", tag=tag, restored=len(done), failed=len(bad), guard=guard["id"])
    out = {"ok": bool(done), "restored": done, "failed": bad, "guard": guard["id"],
           "skipped": skipped, "restart_required": True,
           "note": "已生效的文件要重启引擎才会重读；恢复前我又存了一份（编号见 guard）"}
    if not done:
        out["error"] = "一个都没还原成，当前状态没被动过"
    return out


def _zip_bytes(cfg):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in targets(cfg):
            if t["kind"] == "sqlite":
                continue  # an export is for moving config, not a live database
            try:
                z.write(t["path"], t["name"] + os.path.splitext(t["path"])[1])
            except Exception:
                pass
        z.writestr("export-info.json", json.dumps(
            {"created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "note": "不含 sqlite 库、不含 MCP 地址与令牌文件",
             "targets": [t["name"] for t in targets(cfg)]}, ensure_ascii=False))
    return buf.getvalue()


def export(cfg, note=""):
    """Encrypted with DPAPI: only this Windows user can open it (design §7.2)."""
    data = _zip_bytes(cfg)
    blob = security.dpapi_protect(data) if hasattr(security, "dpapi_protect") else None
    from . import credentials
    if blob is None:
        blob = credentials.protect(data)
    if not blob:
        return {"error": "这台机器加密不了，我不导出明文包"}
    os.makedirs(exports_dir(), exist_ok=True)
    tag = time.strftime("%Y%m%d-%H%M%S")
    p = os.path.join(exports_dir(), "dfy-export-%s.dfyexp" % tag)
    _write_atomic(p, json.dumps({"magic": "dfy-export-v1", "kind": "dpapi-current-user",
                                 "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "note": str(note or "")[:80], "bytes": len(data),
                                 "sha8": hashlib.sha1(data).hexdigest()[:8],
                                 "blob": base64.b64encode(blob).decode("ascii")},
                                ensure_ascii=False, indent=1))
    audit.write("backup_export", file=os.path.basename(p), bytes=len(data))
    return {"ok": True, "file": p, "bytes": len(data),
            "note": "只有当前 Windows 用户解得开；换机器/重装系统前要重新导"}


def inspect(cfg, path):
    p = _safe_export_path(cfg, path)
    if isinstance(p, dict):
        return p
    doc, err = read_json(p)
    if err:
        return {"error": "这不是导出包：%s" % err}
    from . import credentials
    data = credentials.unprotect(base64.b64decode(doc.get("blob") or ""))
    if not data:
        return {"error": "解不开（不是当前用户的包，或文件坏了）"}
    ok = hashlib.sha1(data).hexdigest()[:8] == doc.get("sha8")
    names = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
    except Exception as e:
        return {"error": "包内容坏了：%s" % type(e).__name__}
    return {"ok": ok, "file": p, "created_at": doc.get("created_at"), "entries": names,
            "verified": ok, "bytes": len(data)}


def _safe_export_path(cfg, path):
    """A bare filename is fine (we resolve it inside the export dir); anything that
    would reach outside that directory is refused instead of being read."""
    d = os.path.abspath(exports_dir())
    raw = str(path or "").strip().strip('"')
    if not raw:
        return {"error": "要给导出包的文件名或路径"}
    p = raw if os.path.isabs(raw) else os.path.join(d, os.path.basename(raw))
    p = os.path.abspath(p)
    if not p.startswith(d + os.sep):
        return {"error": "只能读导出目录里的文件：%s" % d}
    if not os.path.isfile(p):
        return {"error": "文件不在导出目录里：%s" % os.path.basename(p)}
    return p


def import_archive(cfg, path, confirm=""):
    # resolve the path once and reuse it: inspect() accepts a bare filename, but the
    # confirmed write-back must not re-derive it from whatever the caller passed
    p = _safe_export_path(cfg, path)
    if isinstance(p, dict):
        return p
    info = inspect(cfg, p)
    if info.get("error") or not info.get("verified"):
        return info if info.get("error") else {"error": "包校验没通过，不动现有配置"}
    files = [n for n in info["entries"] if n != "export-info.json"]
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True,
                "will": "把导出包里的 %d 个配置覆盖回本机：%s；覆盖前先做一次快照"
                        % (len(files), "、".join(files)),
                "created_at": info.get("created_at")}
    from . import credentials
    doc, err = read_json(p)
    if err or not isinstance(doc, dict):
        return {"error": "导出包读不回来：%s" % err}
    data = credentials.unprotect(base64.b64decode(doc.get("blob") or ""))
    if not data:
        return {"error": "解不开（不是当前 Windows 用户的包，或文件坏了）"}
    guard = create(cfg, "pre-import")
    tmap = {t["name"]: t["path"] for t in targets(cfg)}
    done, bad = [], []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if n == "export-info.json":
                continue
            key = n.split(".", 1)[0]
            dst = tmap.get(key)
            if not dst:
                continue
            try:
                write_json_or_text(dst, z.read(n).decode("utf-8", "replace"), n)
            except Exception as e:
                bad.append({"name": key, "why": type(e).__name__})
                continue
            done.append(key)
    audit.write("backup_import", restored=len(done), failed=len(bad), guard=guard["id"])
    if not done:
        return {"error": "一个都没写回去（包里的东西本机不认或内容坏了）", "failed": bad,
                "guard": guard["id"]}
    return {"ok": True, "restored": done, "failed": bad, "guard": guard["id"],
            "restart_required": True,
            "note": "覆盖前的状态在 guard 编号里，随时可以再倒回去"}


def write_json_or_text(path, text, filename=""):
    """Store either shape verbatim; JSON is re-serialized so a corrupt payload never
    half-writes (we validate before touching the file)."""
    if filename.endswith(".json") or text.lstrip()[:1] in ("{", "["):
        doc = json.loads(text)  # raises on garbage -> caller sees the error, file untouched
        write_json_atomic(path, doc)
    else:
        _write_atomic(path, text)


def due(last_iso, minutes, now=None):
    now = now or time.time()
    if not last_iso or minutes <= 0:
        return bool(minutes > 0)
    try:
        then = time.mktime(time.strptime(str(last_iso)[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return True
    return (now - then) >= minutes * 60


_last_try = [0.0]


def autobackup_if_due(cfg, force=False):
    """Called on core start and when the backup page is opened; no timer thread, the
    core lives and dies with the window."""
    st = settings(cfg)
    if st["auto_minutes"] <= 0 and not force:
        return {"skipped": "auto backup off"}
    p = os.path.join(config.runtime_dir(), "backup-last-auto.txt")
    last = None
    if os.path.isfile(p):
        mp = os.path.join(root(cfg), open(p, encoding="utf-8").read().strip(), "manifest.json")
        man, _ = read_json(mp)
        last = (man or {}).get("created_at")
    if not force and not due(last, st["auto_minutes"]):
        return {"skipped": "还没到点", "last": last}
    if time.time() - _last_try[0] < 5 and not force:
        return {"skipped": "刚刚试过了"}
    _last_try[0] = time.time()
    return create(cfg, "auto")

