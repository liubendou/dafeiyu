# -*- coding: utf-8 -*-
"""agent_ctl.py -- control plane that lets the QQ persona (her) drive this PC and me.

Listens on 127.0.0.1:8790 (loopback only).  The QQ MCP server (mcp-arena-control.js)
calls it; the persona never sees raw sockets.

  GET  /help          English usage guide (deliberately short: it is fed to the model)
  GET  /models        model catalog + owner's notes
  POST /models/note   {"model": "...", "note": "..."}   -> store owner's note
  POST /task          {"task": "...", "model": "...", "note": "..."} -> dispatch job to the Arena agent
  GET  /tasks         pending jobs (from the request inbox)
  POST /exec          {"cmd": "..."}  -> run a command on this PC (ASCII only, audited)
  GET  /workbench     Liubendou Workbench instances (running / logged in / proxy)
  POST /workbench     {"action": "open|close|status", "instance": "profile_1"}

Secrets: the Arena MCP endpoint is read from mcp_url.txt next to this file and is
never printed, logged or returned.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
MCP_URL_FILE = HERE / "mcp_url.txt"
NOTES_FILE = HERE / "model_notes.json"
REQ_DIR = HERE / "requests"
AUDIT_FILE = HERE / "audit.log"
AGENT_ID_FILE = HERE / "agent_id.txt"
WB_ROOT = Path(os.environ.get("WB_ROOT", r"C:\Users\<user>\AppData\Local\LiubendouWorkbench"))
PORT = int(os.environ.get("AGENT_CTL_PORT", "8790"))
# Owner explicitly scoped current Arena work to profile_3; do not inspect other profiles.
BROWSER_PROFILE_SCOPE = "profile_3"

_log_lock = threading.Lock()


def audit(kind: str, detail: str) -> None:
    try:
        with _log_lock:
            with open(AUDIT_FILE, "a", encoding="utf-8") as fh:
                fh.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), kind, detail.replace("\n", " ")[:500]))
    except Exception:
        pass


# --------------------------------------------------------------------------- MCP client
class Mcp:
    """Minimal MCP (streamable HTTP) client: initialize + tools/call."""

    def __init__(self) -> None:
        self.url = ""
        self.sid = ""
        self._lock = threading.Lock()

    def endpoint(self) -> str:
        if not self.url:
            self.url = MCP_URL_FILE.read_text(encoding="utf-8").strip()
        return self.url

    def _post(self, payload: dict, timeout: int = 120):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.endpoint(), data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self.sid:
            req.add_header("Mcp-Session-Id", self.sid)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            sid = resp.headers.get("Mcp-Session-Id") or ""
            if sid:
                self.sid = sid
        return raw

    @staticmethod
    def _parse(raw: str) -> dict:
        raw = (raw or "").strip()
        if not raw:
            return {}
        if raw.startswith("{"):
            try:
                return json.loads(raw)
            except Exception:
                pass
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except Exception:
                    continue
        return {"_raw": raw[:400]}

    def init(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qq-agent-ctl", "version": "1.0"},
            },
        }
        self._post(payload)
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            pass

    def call(self, tool: str, args: dict, timeout: int = 120) -> str:
        with self._lock:
            if not self.sid:
                self.init()
            payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}
            try:
                raw = self._post(payload, timeout=timeout)
            except Exception:
                self.sid = ""
                self.init()
                raw = self._post(payload, timeout=timeout)
        data = self._parse(raw)
        try:
            return data["result"]["content"][0]["text"]
        except Exception:
            if "error" in data:
                return "MCP error: " + json.dumps(data["error"], ensure_ascii=False)[:400]
            return json.dumps(data, ensure_ascii=False)[:800]


MCP = Mcp()


# --------------------------------------------------------------------------- model notes
DEFAULT_CATALOG = [
    {"id": "deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash",
     "note": "DEFAULT. Fast and cheap. Owner rule: never auto-upgrade to Pro."},
    {"id": "deepseek-v4-pro-0813", "label": "DeepSeek V4 Pro 0813",
     "note": "Pro tier. Only when the owner explicitly asks for Pro."},
    {"id": "gpt-5", "label": "GPT-5", "note": ""},
    {"id": "claude-sonnet-4-5", "label": "Claude Sonnet 4.5", "note": ""},
    {"id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash", "note": ""},
    {"id": "kimi-k2.6", "label": "Kimi K2.6", "note": ""},
]


def load_notes() -> dict:
    if NOTES_FILE.exists():
        try:
            data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    seed = {}
    for item in DEFAULT_CATALOG:
        seed[item["id"]] = {"label": item["label"], "note": item["note"]}
    save_notes(seed)
    return seed


def save_notes(data: dict) -> None:
    tmp = NOTES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(NOTES_FILE)


def models_view() -> dict:
    notes = load_notes()
    rows = []
    for mid, info in notes.items():
        rows.append({"model": mid, "label": info.get("label", mid), "note": info.get("note", "")})
    return {"models": rows, "hint": "owner note wins: pick the model whose note matches the job"}


# --------------------------------------------------------------------------- dispatch
def agent_id() -> str:
    if AGENT_ID_FILE.exists():
        text = AGENT_ID_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    text = MCP.call("agent_register", {"name": "qq-bot", "description": "QQ persona dispatch channel"})
    m = re.search(r"(ag_[A-Za-z0-9]+)", text)
    if not m:
        raise RuntimeError("agent_register failed")
    AGENT_ID_FILE.write_text(m.group(1), encoding="utf-8")
    return m.group(1)


def dispatch_task(task: str, model: str = "", note: str = "") -> dict:
    task = (task or "").strip()
    if not task:
        return {"ok": False, "error": "empty task"}
    aid = agent_id()
    title = task if len(task) <= 90 else task[:87] + "..."
    acceptance = note or "Owner asked for it in QQ; deliver the result and report back with evidence."
    steps = []
    if model:
        steps.append("Use model %s (owner note: %s)" % (model, note or "n/a"))
    steps.append("Do the work")
    steps.append("Report back in QQ with evidence")
    payload = {
        "goal": "Job handed over by the QQ persona",
        "tasks": [{
            "ref": "qqjob",
            "title": title,
            "acceptance": acceptance,
            "steps": steps,
        }],
        "replace": False,
    }
    plan_text = MCP.call("plan_create", payload)
    REQ_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title)[:40].strip("-").lower() or "job"
    path = REQ_DIR / ("%s-%s.json" % (time.strftime("%Y%m%d-%H%M%S"), slug))
    path.write_text(json.dumps({
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "task": task,
        "model": model,
        "note": note,
        "agent_id": aid,
        "plan": plan_text.strip(),
        "state": "pending",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    audit("dispatch", "%s | model=%s" % (title, model or "-"))
    return {"ok": True, "task": task, "model": model, "note": note,
            "plan": plan_text.strip(), "request_file": path.name}


def pending_tasks(include_history: bool = False) -> dict:
    items = []
    now = time.time()
    done_states = {"done", "completed", "complete", "failed", "cancelled", "archived"}
    if REQ_DIR.exists():
        for p in sorted(REQ_DIR.glob("*.json"), reverse=True)[:100]:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("kind") == "inspect_arena_notes":
                continue  # internal read-only routing probe; not an owner task
            state = str(data.get("state") or "pending").lower()
            stamp = data.get("updated_at") or data.get("created_at") or ""
            age_seconds = 0
            try:
                age_seconds = now - time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                pass
            # Default board is current work only. Old unclaimed queue items and completed
            # jobs stay on disk for history but do not clutter the current summary.
            if not include_history:
                if state in done_states:
                    continue
                created_day = str(data.get("created_at") or "")[:10]
                today = time.strftime("%Y-%m-%d")
                if state in {"pending", "queued"} and created_day and created_day < today:
                    continue
            items.append({
                "file": p.name,
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at") or data.get("created_at"),
                "task": data.get("task"),
                "model": data.get("model"),
                "note": data.get("note"),
                "kind": data.get("kind", "mcp"),
                "profile": data.get("profile"),
                "state": state,
                "summary": str(data.get("summary") or data.get("result") or "")[:600],
                "next_step": str(data.get("next_step") or "")[:240],
            })
    plan = ""
    try:
        plan = MCP.call("task_status", {}, timeout=45).strip()[:1000]
    except Exception:
        plan = ""
    plan_lower = plan.lower()
    completed_markers = ("全部完成", "completed", "complete (", "0 pending", "no active")
    if not include_history and (not items or any(mark in plan_lower for mark in completed_markers)):
        plan = ""
    summary = ""
    if items:
        summary = str(items[0].get("summary") or items[0].get("task") or "")[:600]
    return {"pending": items[:20], "summary": summary, "plan": plan, "history": include_history}

# --------------------------------------------------------------------------- exec
def run_command(cmd: str) -> dict:
    cmd = (cmd or "").strip()
    if not cmd:
        return {"ok": False, "error": "empty command"}
    if any(ord(ch) > 127 for ch in cmd):
        return {"ok": False, "error": "non-ASCII command rejected; write non-ASCII text into a file instead"}
    args = {"command": cmd, "why": "QQ owner asked the bot to operate this PC"}
    out = ""
    for attempt in range(3):
        out = MCP.call("run_command", args, timeout=180)
        if "\u7ec8\u7aef\u6b63\u5fd9" not in out:
            break
        time.sleep(4)
    audit("exec", cmd)
    return {"ok": True, "output": out}


# --------------------------------------------------------------------------- workbench
def wb_instances() -> dict:
    meta_path = WB_ROOT / "instances.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    run_dir = WB_ROOT / "run"
    rows = []
    for name, info in sorted(meta.items()):
        state_file = run_dir / (name + ".json")
        running = False
        pid = 0
        err = ""
        if state_file.exists():
            try:
                st = json.loads(state_file.read_text(encoding="utf-8"))
                pid = int(st.get("pid") or 0)
                running = bool(st.get("running", True))
                err = str(st.get("error") or "")
            except Exception:
                pass
        rows.append({
            "instance": name,
            "name": info.get("name", name),
            "login": info.get("login", "?"),
            "model_note": info.get("model_note", info.get("note", "")),
            "open_count": info.get("open_count", 0),
            "running": running,
            "pid": pid,
            "error": err,
        })
    return {"root": str(WB_ROOT), "instances": rows}


def wb_set_note(instance: str, note: str) -> dict:
    instance = (instance or "").strip()
    note = (note or "").strip()[:160]
    meta_path = WB_ROOT / "instances.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    except Exception:
        return {"ok": False, "error": "instances metadata is unreadable"}
    if instance not in meta:
        return {"ok": False, "error": "unknown instance"}
    meta[instance]["model_note"] = note
    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(meta_path)
    audit("workbench-model-note", "%s = %s" % (instance, note))
    return {"ok": True, "instance": instance, "model_note": note}


def wb_action(action: str, instance: str) -> dict:
    action = (action or "").strip().lower()
    instance = (instance or "").strip()
    if action == "status":
        return {"ok": True, **wb_instances()}
    if not instance:
        return {"ok": False, "error": "instance required"}
    if action == "open":
        py = WB_ROOT / ".venv" / "Scripts" / "python.exe"
        launcher = WB_ROOT / "app" / "instance_launcher.py"
        if not launcher.exists():
            return {"ok": False, "error": "launcher missing"}
        cmd = [str(py), str(launcher), instance]
        subprocess.Popen(cmd, cwd=str(WB_ROOT / "app"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x00000008)  # DETACHED_PROCESS
        audit("workbench-open", instance)
        return {"ok": True, "action": "open", "instance": instance}
    if action == "close":
        state_file = WB_ROOT / "run" / (instance + ".json")
        pid = 0
        if state_file.exists():
            try:
                pid = int(json.loads(state_file.read_text(encoding="utf-8")).get("pid") or 0)
            except Exception:
                pid = 0
        killed = []
        if pid:
            for args in (["taskkill", "/PID", str(pid), "/T"], ["taskkill", "/PID", str(pid), "/T", "/F"]):
                try:
                    subprocess.run(args, capture_output=True, timeout=30)
                except Exception:
                    pass
            killed.append(pid)
        audit("workbench-close", "%s pid=%s" % (instance, pid))
        return {"ok": True, "action": "close", "instance": instance, "killed": killed}
    return {"ok": False, "error": "unknown action"}


def _browser_profiles_to_inspect() -> list[str]:
    """Return only the owner-selected transport profile, never other Workbench instances."""
    return [str(row.get("instance") or "").strip()
            for row in wb_instances().get("instances", [])
            if row.get("running")
            and str(row.get("instance") or "").strip() == BROWSER_PROFILE_SCOPE]


def _read_arena_conversation_notes(timeout_seconds: int = 25) -> list[dict]:
    """Ask the selected already-running profile to read its existing Arena tabs."""
    profiles = _browser_profiles_to_inspect()
    if not profiles:
        raise RuntimeError("no already-running Workbench profile is available for read-only inspection")
    REQ_DIR.mkdir(parents=True, exist_ok=True)
    jobs = []
    for profile in profiles:
        path = REQ_DIR / ("browser-inspect-" + uuid.uuid4().hex + ".json")
        data = {"kind": "inspect_arena_notes", "profile": profile, "state": "pending",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        jobs.append((profile, path))
    deadline = time.time() + max(1, min(int(timeout_seconds), 40))
    terminal = {"done", "blocked", "failed", "cancelled"}
    while time.time() < deadline:
        states = []
        for profile, path in jobs:
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                current = {}
            states.append(str(current.get("state") or "pending").lower())
        if all(state in terminal for state in states):
            break
        time.sleep(0.25)
    reports = []
    timed_out = False
    for profile, path in jobs:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("a read-only Arena inspection record could not be read") from exc
        state = str(data.get("state") or "pending").lower()
        if state not in terminal:
            data.update(state="cancelled", summary="Inspection timed out; no dispatch was created.",
                        updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            timed_out = True
            continue
        if state != "done":
            raise RuntimeError("read-only inspection failed in a running profile; refusing to route")
        try:
            result = json.loads(data.get("result") or "{}")
        except Exception as exc:
            raise RuntimeError("read-only inspection returned invalid JSON; refusing to route") from exc
        pages = result.get("pages")
        if not isinstance(pages, list):
            raise RuntimeError("read-only inspection did not return a page list")
        for page in pages:
            if not isinstance(page, dict):
                raise RuntimeError("read-only inspection returned an invalid page record")
            candidates = page.get("model_note_candidates")
            if not isinstance(candidates, list):
                raise RuntimeError("read-only inspection omitted sidebar candidates")
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                row = dict(candidate)
                row["profile_transport_id"] = profile
                row["page_url"] = str(page.get("page_url") or "")
                reports.append(row)
    if timed_out:
        raise RuntimeError("read-only inspection timed out in at least one running profile; refusing to route")
    return reports


def _unique_arena_note_match(rows: list[dict], model: str) -> dict:
    wanted = (model or "").strip().casefold()
    if not wanted:
        raise RuntimeError("model is required for Arena conversation-note routing")
    matches = []
    uncertain = []
    for row in rows:
        note_value = str(row.get("model_note") or "").strip().casefold()
        tokens = {str(token).strip().casefold() for token in row.get("model_tokens", [])
                  if str(token).strip()}
        if note_value == wanted:
            matches.append(row)
        elif wanted in tokens:
            uncertain.append(row)
    if uncertain:
        raise RuntimeError("a sidebar row contains the requested model among ambiguous text; refusing to guess")
    if not matches:
        raise RuntimeError("no Arena conversation note exactly matches the requested model")
    if len(matches) != 1:
        raise RuntimeError("multiple Arena conversations match that model note; refusing to guess")
    return matches[0]


def browser_route_preview(model: str = "") -> dict:
    """Read-only live preflight: resolve a unique sidebar note without creating a send task."""
    try:
        rows = _read_arena_conversation_notes()
        candidate = _unique_arena_note_match(rows, model)
    except Exception as exc:
        return {"ok": False, "sent": False, "error": str(exc)}
    href = str(candidate.get("href") or "").strip()
    profile = str(candidate.get("profile_transport_id") or "").strip()
    if not href or not profile:
        return {"ok": False, "sent": False,
                "error": "the unique note has no stable conversation link in the selected profile"}
    return {"ok": True, "sent": False, "profile_scope": BROWSER_PROFILE_SCOPE,
            "model_note": candidate.get("model_note"), "conversation_href": href,
            "page_url": candidate.get("page_url"),
            "route_source": "Arena conversation's left-sidebar note",
            "note_rows_inspected": len(rows)}


def browser_dispatch(task: str, model: str = "", note: str = "") -> dict:
    # Routing is based only on the note attached to an Arena conversation. Profile IDs
    # are used only as transport for already-running inspectors after a note is found.
    task = (task or "").strip()
    model = (model or "").strip()
    if not task:
        return {"ok": False, "error": "empty task; nothing was sent", "sent": False}
    try:
        rows = _read_arena_conversation_notes()
        candidate = _unique_arena_note_match(rows, model)
    except Exception as exc:
        audit("browser-dispatch-blocked", str(exc))
        return {"ok": False, "error": str(exc), "sent": False}
    href = str(candidate.get("href") or "").strip()
    profile = str(candidate.get("profile_transport_id") or "").strip()
    if not href or not profile:
        error = "the unique note has no stable conversation link or active browser transport; nothing was sent"
        audit("browser-dispatch-blocked", error)
        return {"ok": False, "error": error, "sent": False,
                "matched_model_note": candidate.get("model_note")}
    REQ_DIR.mkdir(parents=True, exist_ok=True)
    path = REQ_DIR / ("browser-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".json")
    data = {"kind": "browser", "profile": profile, "task": task, "model": model,
            "conversation_href": href, "note": (note or "").strip(), "state": "pending",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    audit("browser-dispatch", "%s | model-note=%s" % (path.name, model))
    return {"ok": True, "sent": False, "file": path.name, "model": model,
            "matched_model_note": candidate.get("model_note"),
            "route_source": "Arena conversation's left-sidebar note",
            "status": "queued for the existing browser worker; dispatch occurs only after its final revalidation"}


def browser_wait(file: str, timeout_seconds: int = 15) -> dict:
    name = Path(str(file or "")).name
    if not name.startswith("browser-") or not name.endswith(".json"):
        return {"ok": False, "error": "file must be a browser task filename returned by browser dispatch"}
    target = REQ_DIR / name
    if not target.exists():
        return {"ok": False, "error": "browser task not found", "file": name}
    deadline = time.time() + min(max(int(timeout_seconds or 0), 0), 18)
    terminal = {"done", "failed", "blocked", "cancelled"}
    while True:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return {"ok": False, "error": "browser task record unreadable", "file": name}
        state = str(data.get("state") or "pending").lower()
        result = {"ok": True, "file": name, "profile": data.get("profile"), "state": state,
                  "summary": str(data.get("summary") or "")[:600],
                  "next_step": str(data.get("next_step") or "")[:240],
                  "result": str(data.get("result") or "")[:6000]}
        if state in terminal:
            return result
        if time.time() >= deadline:
            result["timed_out"] = True
            return result
        time.sleep(1)


# --------------------------------------------------------------------------- HTTP
HELP_TEXT = """QQ bot control channel (127.0.0.1:8790). Use these tools, in English, short.

1) Browser dispatch: arena_browser_dispatch(task, model, note?) -> route only from each conversation's note in Arena's standard left sidebar, never from Workbench profile metadata. Require a successful read, one exact match, a stable conversation link, and a final browser-side recheck; missing, duplicate, ambiguous, or unreadable notes stop. Current scope is profile_3 only, per owner instruction; it never inspects or opens/closes profiles 1 or 2. Poll arena_browser_wait(file, timeout_seconds?) only for a task actually returned by browser dispatch.
2) Task board: arena_tasks() -> active items + short summary only. arena_task_history() -> history only when the owner explicitly asks. arena_task_report(file, state, summary, next_step?, result?) records actual progress.
3) arena_dispatch(task, model?, note?) creates an MCP queue job and does NOT paste into a browser. Do not use it as a substitute when the owner asks for browser dispatch.
4) Model choice: arena_models() -> catalog + owner notes; flash is default, Pro only when the owner asks. arena_set_model_note(model, note) stores an owner note.
5) Operate this PC: arena_exec(command) -> one ASCII command, audited. Never for destructive deletes or long jobs.
6) Workbench: workbench_status() includes each profile's model_note; workbench_set_note(instance, model_note) changes that note only. workbench_action(action, instance) is open | close | status, and only when the owner explicitly asks.
Do not guess profile/model mappings. Keep QQ replies to 1-3 sentences."""


class Handler(BaseHTTPRequestHandler):
    server_version = "agent-ctl/1.0"

    def log_message(self, fmt, *args):  # keep stdout quiet
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8", "replace") or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        try:
            if path == "/help":
                return self._send(200, {"ok": True, "help": HELP_TEXT})
            if path == "/models":
                return self._send(200, {"ok": True, **models_view()})
            if path == "/tasks":
                return self._send(200, {"ok": True, **pending_tasks(False)})
            if path == "/tasks/history":
                return self._send(200, {"ok": True, **pending_tasks(True)})
            if path == "/workbench":
                return self._send(200, {"ok": True, **wb_instances()})
            if path == "/status":
                return self._send(200, {"ok": True, "service": "agent_ctl",
                                        "mcp": "configured" if MCP_URL_FILE.exists() else "missing",
                                        "pending": len(pending_tasks().get("pending", []))})
            return self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            return self._send(500, {"ok": False, "error": str(exc)[:300]})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        body = self._body()
        try:
            if path == "/models/note":
                model = str(body.get("model") or "").strip()
                note = str(body.get("note") or "").strip()
                if not model:
                    return self._send(400, {"ok": False, "error": "model required"})
                notes = load_notes()
                entry = notes.get(model) or {"label": model, "note": ""}
                entry["note"] = note
                notes[model] = entry
                save_notes(notes)
                audit("model-note", "%s = %s" % (model, note))
                return self._send(200, {"ok": True, "model": model, "note": note})
            if path == "/task":
                return self._send(200, dispatch_task(str(body.get("task") or ""),
                                                     str(body.get("model") or ""),
                                                     str(body.get("note") or "")))
            if path == "/browser/route_preview":
                return self._send(200, browser_route_preview(str(body.get("model") or "")))
            if path == "/browser/dispatch":
                return self._send(200, browser_dispatch(str(body.get("task") or ""),
                                                       str(body.get("model") or ""),
                                                       str(body.get("note") or "")))
            if path == "/browser/wait":
                return self._send(200, browser_wait(str(body.get("file") or ""),
                                                    int(body.get("timeout_seconds") or 15)))
            if path == "/task/result":
                name = Path(str(body.get("file") or "")).name
                if not name:
                    return self._send(400, {"ok": False, "error": "file required"})
                target = REQ_DIR / name
                if not target.exists():
                    return self._send(404, {"ok": False, "error": "request not found"})
                try:
                    data = json.loads(target.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                data["state"] = str(body.get("state") or "done")
                data["summary"] = str(body.get("summary") or body.get("result") or "").strip()[:600]
                data["next_step"] = str(body.get("next_step") or "").strip()[:240]
                data["result"] = str(body.get("result") or "")[:6000]
                data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                audit("task-result", "%s -> %s" % (name, data["state"]))
                return self._send(200, {"ok": True, "file": name, "state": data["state"]})
            if path == "/exec":
                return self._send(200, run_command(str(body.get("cmd") or "")))
            if path == "/workbench/note":
                return self._send(200, wb_set_note(str(body.get("instance") or ""),
                                                   str(body.get("model_note") or "")))
            if path == "/workbench":
                return self._send(200, wb_action(str(body.get("action") or ""),
                                                 str(body.get("instance") or "")))
            return self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            audit("error", "%s %s" % (path, str(exc)[:200]))
            return self._send(500, {"ok": False, "error": str(exc)[:300]})


def main() -> None:
    REQ_DIR.mkdir(parents=True, exist_ok=True)
    load_notes()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    audit("start", "agent_ctl listening on 127.0.0.1:%d" % PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
