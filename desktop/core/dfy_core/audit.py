"""Append-only JSONL audit, per day, written only inside <desktop>/.runtime."""

import datetime
import os

from . import config, security


def write(event, **fields):
    try:
        d = config.runtime_dir()
        day = datetime.datetime.now().strftime("%Y%m%d")
        path = os.path.join(d, "audit-%s.jsonl" % day)
        rec = {"ts": datetime.datetime.now(datetime.timezone.utc)
               .isoformat(timespec="seconds"), "event": event}
        rec.update(fields)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(security.json_dumps_safe(rec) + "\n")
        return path
    except Exception:
        return None


def tail(limit=50):
    try:
        d = config.runtime_dir()
        day = datetime.datetime.now().strftime("%Y%m%d")
        path = os.path.join(d, "audit-%s.jsonl" % day)
        if not os.path.isfile(path):
            return {"path": None, "lines": []}
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 65536))
            data = fh.read().decode("utf-8", "replace")
        kept = [ln for ln in data.splitlines() if ln.strip()]
        try:
            n = int(limit)
        except Exception:
            n = 50
        return {"path": os.path.basename(path), "bytes": size, "total": len(kept),
                "lines": kept[-n:] if n > 0 else kept}
    except Exception as e:
        # never swallow silently: a broken audit reader must be visible to the UI
        return {"path": None, "lines": [], "total": 0,
                "error": "%s: %s" % (type(e).__name__, e)}


def write_baseline(name, payload):
    """Persist a production file-hash baseline for the stage-0 no-change proof."""
    try:
        d = config.runtime_dir()
        path = os.path.join(d, "baseline-%s.json" % name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(security.json_dumps_safe(payload))
        return path
    except Exception:
        return None
