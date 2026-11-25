# -*- coding: utf-8 -*-
"""Provider configuration read / edit / connect-test (design 阶段3 · §12.1 /v1/models · §7.3).

The file is AstrBot's own `cmd_config.json`; we are an adapter, not a second source of
truth. API keys are projected as "present / length" only — the GUI never receives one,
so it cannot leak into a screenshot, a log line or a diagnostic bundle. The connect
test hits `GET <api_base>/models`, which costs no tokens.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request

from . import audit, backup, config, security

SRC_FIELDS = ("api_base", "timeout", "proxy", "enable")
ENT_FIELDS = ("model", "enable")


def _read_raw(cfg):
    p = cfg.get("cmd_config") or ""
    if not p or not os.path.isfile(p):
        return None, None, {"error": "找不到 AstrBot 的 cmd_config.json"}
    doc, err = backup.read_json(p)
    if err:
        return None, p, {"error": "配置文件读不了：%s" % err}
    sha = hashlib.sha1(open(p, "rb").read()).hexdigest()[:8]
    return doc, p, {"sha8": sha}


def _project(doc):
    sources = {}
    for s in (doc.get("provider_sources") or []):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "")
        key = s.get("key")
        sources[sid] = {"id": sid, "provider": s.get("provider"), "type": s.get("type"),
                        "api_base": security.redact(str(s.get("api_base") or ""))[:160] or None,
                        "timeout": s.get("timeout"), "proxy": s.get("proxy") or None,
                        "has_key": bool(str(key or "").strip()),
                        "key_chars": len(str(key or "")),
                        "custom_headers": bool(s.get("custom_headers"))}
    entries = []
    prov = doc.get("provider")
    if isinstance(prov, list):
        for e in prov:
            if not isinstance(e, dict):
                continue
            entries.append({"id": e.get("id"), "model": e.get("model"),
                            "provider_source_id": e.get("provider_source_id"),
                            "enable": bool(e.get("enable", True)),
                            "default": bool(e.get("is_default")) if "is_default" in e else None})
    default = None
    if isinstance(prov, dict):
        default = prov.get("default_source")
    return sources, entries, default


def read(cfg):
    doc, path, meta = _read_raw(cfg)
    if doc is None:
        return meta
    sources, entries, default = _project(doc)
    return {"available": True, "path": os.path.basename(path), "sha8": meta["sha8"],
            "sources": list(sources.values()), "providers": entries, "default_source": default,
            "note": "密钥只报有没有和多长，内容不出这里；连接测试走 GET /models，不花 token"}


def save(cfg, source_id="", patch=None, entry_id="", base_sha8="", confirm=""):
    patch = patch if isinstance(patch, dict) else {}
    if not source_id and not entry_id:
        return {"error": "得说清改哪一条（source_id 或 provider 条目 id）"}
    bad = [k for k in patch if k not in (SRC_FIELDS if source_id else ENT_FIELDS)]
    if bad:
        return {"error": "这些字段不在可改范围里：%s（要改密钥请走凭据/引擎自己的界面）" % "、".join(bad)}
    doc, path, meta = _read_raw(cfg)
    if doc is None:
        return meta
    if base_sha8 and base_sha8 != meta["sha8"]:
        return {"error": "这个文件在我们打开之后被改过（%s → %s），先重新载入"
                        % (base_sha8, meta["sha8"])}
    before = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    touched = False
    if source_id:
        for s in (doc.get("provider_sources") or []):
            if isinstance(s, dict) and str(s.get("id")) == str(source_id):
                for k, v in patch.items():
                    if k == "timeout":
                        # refuse, never silently clamp: what you typed is what gets saved
                        try:
                            n = int(float(v))
                        except Exception:
                            return {"error": "超时得是个数字（秒，1-600）"}
                        if not 1 <= n <= 600:
                            return {"error": "超时要在 1-600 秒之间；我不替你改成别的数"}
                        v = n
                    elif k == "api_base":
                        v = str(v or "").strip()
                        if v and not v.lower().startswith(("http://", "https://")):
                            return {"error": "api_base 要带 http(s)://"}
                    elif k == "enable":
                        v = bool(v)
                    s[k] = v
                touched = True
        if not touched:
            return {"error": "没有这个来源：%s" % source_id}
    if entry_id:
        hit = False
        for e in (doc.get("provider") or []):
            if isinstance(e, dict) and str(e.get("id")) == str(entry_id):
                e.update({k: (bool(v) if k == "enable" else str(v or "").strip())
                          for k, v in patch.items()})
                hit = True
        if not hit:
            return {"error": "没有这个 provider 条目：%s" % entry_id}
        touched = True
    if json.dumps(doc, ensure_ascii=False, sort_keys=True) == before:
        return {"changed": False, "note": "内容一模一样，没必要写"}
    if str(confirm).lower() not in ("yes", "true", "1", "y", "确认"):
        return {"confirm_required": True,
                "will": "改 %s：%s；改之前先自动备份并校验，不匹配就倒回去"
                        % (os.path.basename(path), "、".join(sorted(patch))),
                "fields": sorted(patch)}
    guard = backup.create(cfg, "pre-model")
    try:
        backup.write_json_atomic(path, doc)
    except Exception as e:
        return {"error": "写失败（没动文件）：%s" % type(e).__name__, "guard": guard["id"]}
    after, err = backup.read_json(path)
    if err:
        backup.write_json_atomic(path, json.loads(before))
        audit.write("modelcfg_rollback", reason="unreadable", guard=guard["id"])
        return {"error": "写完读不回来了，已倒回原样", "guard": guard["id"]}
    if _project(after)[:2] != _project(doc)[:2]:
        backup.write_json_atomic(path, json.loads(before))
        return {"error": "读回来的投影不一致，已倒回原样", "guard": guard["id"]}
    audit.write("modelcfg_save", source_id=str(source_id or "")[:40],
                fields=",".join(sorted(patch))[:80], guard=guard["id"])
    return {"ok": True, "changed": True, "guard": guard["id"], "restart_required": True,
            "sha8": hashlib.sha1(open(path, "rb").read()).hexdigest()[:8],
            "note": "引擎要重启才会重读这份配置；我不会顺手重启 AstrBot"}


def test_connection(cfg, source_id="", timeout=8.0):
    """GET <api_base>/models. No chat content leaves this machine, and it costs nothing."""
    doc, path, meta = _read_raw(cfg)
    if doc is None:
        return meta
    src = next((s for s in (doc.get("provider_sources") or []) if isinstance(s, dict)
                and str(s.get("id")) == str(source_id)), None)
    if not src and len(doc.get("provider_sources") or []) == 1:
        src = doc["provider_sources"][0]
    if not src:
        return {"error": "没有这个来源：%s" % source_id}
    base = str(src.get("api_base") or "").strip().rstrip("/")
    if not base:
        return {"error": "这条来源没填 api_base"}
    if not base.lower().startswith(("http://", "https://")):
        return {"error": "api_base 少了 http(s)://"}
    url = base + "/models"
    security.add_scrub(base)
    req = urllib.request.Request(url, method="GET")
    key = str(src.get("key") or "").strip()
    if key:
        req.add_header("Authorization", "Bearer " + key)
    req.add_header("User-Agent", "dafeiyu-desktop/0.3 connect-test")
    try:
        with urllib.request.urlopen(req, timeout=max(2, min(30, float(timeout)))) as r:
            code, body = r.status, r.read(200000)
    except urllib.error.HTTPError as e:
        code, body = e.code, b""
    except Exception as e:
        audit.write("model_test", ok=False, error=type(e).__name__)
        return {"ok": False, "error": "连不上：%s" % str(e)[:160],
                "note": "地址与密钥都不会出现在这里"}
    n = None
    names = []
    try:
        parsed = json.loads(body.decode("utf-8", "replace"))
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if isinstance(data, list):
            n = len(data)
            names = [str(d.get("id")) for d in data[:8] if isinstance(d, dict)]
    except Exception:
        pass
    audit.write("model_test", ok=200 <= code < 300, http=code, n=n)
    return {"ok": 200 <= code < 300, "http": code, "models": n, "sample": names,
            "note": None if 200 <= code < 300 else "地址通但被拒（多半是密钥或 api_base 不对）"}
