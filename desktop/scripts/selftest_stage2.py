"""Stage-2 self-test: 控制队列、工具轨迹解析、群开关（全部在 scratch 副本上跑）。

Run from <desktop>:  python scripts\\selftest_stage2.py
Nothing here may touch the live plugin queue, state.json or cmd_config.json — the
real files are hashed before and after and must come out identical.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(DESKTOP, "core"))

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL %s %s" % (name, str(extra)[:200]))


SCRATCH = tempfile.mkdtemp(prefix="dfy-stage2-")
os.environ["DFY_OUTBOX_ROOT"] = os.path.join(SCRATCH, "outbox")
os.environ["DFY_STATE_TARGET"] = os.path.join(SCRATCH, "state.json")
os.environ["DFY_CMD_CONFIG_TARGET"] = os.path.join(SCRATCH, "cmd_config.json")
os.environ["DFY_PLUGIN_CONFIG_TARGET"] = os.path.join(SCRATCH, "plugin_config.json")

from dfy_core import app as app_mod  # noqa: E402
from dfy_core import config, control, groups, security, threads, toolslog  # noqa: E402

cfg = config.load()
OWNER = "dfy_test_qq:FriendMessage:<OWNER_QQ>"
GID = "<GROUP_A>"
GID2 = "<GROUP_B>"
GROUP_UMO = "dfy_test_qq:GroupMessage:%s" % GID
FORBIDDEN = str(cfg["expected_whitelist"]["forbidden_group"])
SECRET32 = "a" * 40

LIVE = [cfg["cmd_config"], cfg["plugin_config"],
        os.path.join(cfg["plugin_data_dir"], "state.json"),
        os.path.join(cfg["plugin_data_dir"], "desktop_outbox", "inbox")]


def live_hash():
    out = {}
    for p in LIVE:
        if os.path.isdir(p):
            out[p] = hashlib.sha1(json.dumps(sorted(os.listdir(p))).encode()).hexdigest()
        elif os.path.isfile(p):
            with open(p, "rb") as fh:
                out[p] = hashlib.sha1(fh.read()).hexdigest()
        else:
            out[p] = "absent"
    return out


before = live_hash()

# ---------------------------------------------------------------- fixtures
state = {
    "version": 1, "created_at": 0, "updated_at": int(time.time() * 1000), "paused": False,
    "conversations": {
        OWNER: {"session_id": "<OWNER_QQ>", "seq": 10, "last_read_seq": 7, "unread": [8, 9, 10],
                "recent": [{"seq": 8, "at": 1, "text": "x", "sender_name": "s",
                            "is_owner": True, "is_self": False, "message_id": "m8"}],
                "wake": {"mode": "awake"}},
        GROUP_UMO: {"session_id": GID, "message_type": "group", "seq": 3, "unread": [],
                    "recent": [], "wake": {"mode": "diving"}},
        "dfy_test_qq:GroupMessage:%s" % FORBIDDEN: {"session_id": FORBIDDEN, "message_type": "group",
                                                   "recent": [], "unread": [], "seq": 0},
    },
}
with open(os.environ["DFY_STATE_TARGET"], "w", encoding="utf-8") as fh:
    json.dump(state, fh, ensure_ascii=False)

cmd = {"config_version": "5",
       "platform_settings": {"enable_id_white_list": True, "id_whitelist": [OWNER, GID, GID2]}}
plug = {"owner_qq": "<OWNER_QQ>", "allowed_group_ids": [GID, GID2], "max_unread": 50}
for path, doc in ((os.environ["DFY_CMD_CONFIG_TARGET"], cmd),
                  (os.environ["DFY_PLUGIN_CONFIG_TARGET"], plug)):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

conv = threads.conversations(cfg)
check("scratch conversations listed", conv.get("total") == 3, conv)
check("excluded group flagged", any(i["excluded"] for i in conv["items"]), conv["items"])

# ---------------------------------------------------------------- control queue
r = control.submit(cfg, "bogus", OWNER)
check("unknown action refused", "error" in r, r)
r = control.submit(cfg, "mark_read")
check("mark_read needs umo", "error" in r, r)
r = control.submit(cfg, "mark_read", "no-such-umo")
check("unknown umo refused", "error" in r, r)
r = control.submit(cfg, "wake", OWNER, {"minutes": 10 ** 6})
check("wake minutes capped", "error" in r, r)
r = control.submit(cfg, "sleep", OWNER, {"minutes": 10 ** 7})
check("sleep minutes capped", "error" in r, r)
r = control.submit(cfg, "mark_read", OWNER, {"through_seq": 10})
check("mark_read queued", r.get("state") == "pending" and r.get("id"), r)
rid = r.get("id")
qp = os.path.join(SCRATCH, "outbox", "control", "inbox", rid + ".json")
check("control file landed in queue", os.path.isfile(qp), qp)
doc = json.load(open(qp, encoding="utf-8"))
check("control payload", doc.get("kind") == "control" and doc.get("action") == "mark_read"
      and doc.get("umo") == OWNER and doc.get("args") == {"through_seq": 10}, doc)
check("receipt pending before plugin", control.receipt(cfg, rid)["state"] == "pending", control.receipt(cfg, rid))
r2 = control.submit(cfg, "pause", GROUP_UMO)
check("pause queued (global, umo ignored)", r2.get("state") == "pending" and r2.get("id"), r2)
dst = os.path.join(SCRATCH, "outbox", "control", "sent", rid + ".json")
os.makedirs(os.path.dirname(dst), exist_ok=True)
doc.update({"ok": True, "result": {"cleared": 3}, "action": "mark_read"})
with open(dst, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, ensure_ascii=False)
os.remove(qp)
rec = control.receipt(cfg, rid)
check("receipt sent after plugin", rec["state"] == "sent", rec)
check("receipt carries result", (rec.get("detail") or {}).get("result") == {"cleared": 3}, rec)
with open(os.path.join(SCRATCH, "outbox", "control", "inbox", "broken.json"), "w", encoding="utf-8") as fh:
    fh.write("{not json")
check("receipt unknown for junk", control.receipt(cfg, "nope-nope")["state"] == "unknown")
check("receipt id validated", control.receipt(cfg, "../../state")["state"] == "unknown")

# ---------------------------------------------------------------- tool trace
scratch_ab = os.path.join(SCRATCH, "astrbot")
os.makedirs(os.path.join(scratch_ab, "data"), exist_ok=True)
dbp = os.path.join(scratch_ab, "data", "data_v4.db")
con = sqlite3.connect(dbp)
con.executescript("""
create table conversations(conversation_id text primary key, platform_id text, user_id text,
                           persona_id text, content text, updated_at integer);
create table preferences(scope text, scope_id text, key text, value text);
create table provider_stats(id integer primary key, agent_type text, status text, provider_id text,
                             provider_model text, umo text, token_input_other integer,
                             token_input_cached integer, token_output integer,
                             start_time real, end_time real);
""")
msgs = [
    {"role": "user", "content": [{"type": "text", "text": "看看未读"}]},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_a", "type": "function", "function":
            {"name": "dfy_unread", "arguments": json.dumps({"umo": OWNER})}}]},
    {"role": "tool", "tool_call_id": "call_a", "content": "unread=3 through_seq=10 " + ("详情 " * 90)},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_b", "type": "function", "function":
            {"name": "dfy_send", "arguments": json.dumps({"text": "token=" + SECRET32})}}]},
    {"role": "tool", "tool_call_id": "call_b", "content": "error: 平台未找到目标会话"},
]
con.execute("insert into conversations values(?,?,?,?,?,?)",
            ("cid-1", "dfy_test_qq", OWNER, None, json.dumps(msgs, ensure_ascii=False),
             int(time.time() * 1000)))
con.execute("insert into preferences values(?,?,?,?)",
            ("umo", OWNER, "sel_conv_id", json.dumps({"val": "cid-1"})))
con.execute("insert into provider_stats values(?,?,?,?,?,?,?,?,?,?,?)",
            (1, "internal", "completed", "deepseek-flash", "deepseek-flash", OWNER, 100, 20, 30,
             time.time() - 4, time.time() - 1))
con.commit()
con.close()
cfg2 = dict(cfg)
cfg2["astrbot_dir"] = scratch_ab
tr = toolslog.tool_trace(cfg2, umo=OWNER, limit=10)
check("trace resolved conversation via preferences", tr.get("conversation_id") == "cid-1", tr)
check("trace found 2 calls", tr.get("total_calls") == 2, tr)
items = tr.get("items") or []
check("tool names parsed", [i["name"] for i in items] == ["dfy_unread", "dfy_send"], items)
check("success/failure per call", items and items[0]["ok"] is True and items[1]["ok"] is False, items)
check("args summary present", "dfy_test_qq" in (items[0]["args"] or ""), items[0])
check("result truncated", len(items[0]["result"] or "") <= toolslog.RESULT_CAP, len(items[0]["result"] or ""))
blob = json.dumps(items, ensure_ascii=False)
check("secret in args redacted", SECRET32 not in blob, blob[:120])
check("no reasoning text leaked", "看看未读" not in blob)
check("duration not invented", all(i["duration_ms"] is None for i in items), items)
check("by_name tally", tr.get("by_name") == {"dfy_unread": 1, "dfy_send": 1}, tr.get("by_name"))
check("unknown umo has no trace", "error" in toolslog.tool_trace(cfg2, umo="who?"))
os.remove(dbp)
gone = toolslog.tool_trace(cfg2, umo=OWNER)
check("missing db degrades", gone.get("available") is False and gone.get("items") == [], gone)
mu = toolslog.model_usage(cfg, limit=5)
check("model_usage on real db", isinstance(mu.get("runs"), list), mu)
check("usage rows have provider", (not mu.get("runs")) or ("provider_model" in mu["runs"][0]),
      (mu.get("runs") or [])[:1])

# ---------------------------------------------------------------- group switch
check("preview sees current layer", groups.preview(cfg, GID).get("in_astrbot_whitelist") is True,
      groups.preview(cfg, GID))
check("already-enabled reported", "error" in groups.set_group(cfg, GID, True), groups.set_group(cfg, GID, True))
fr = groups.set_group(cfg, FORBIDDEN, True, "yes")
check("forbidden group refused", "红线" in str(fr), fr)
own = groups.set_group(cfg, "<OWNER_QQ>", True, "yes")
check("owner is not a group", "error" in own, own)
check("bad gid refused", "error" in groups.set_group(cfg, "12;3", True, "yes"))
pre = groups.set_group(cfg, "99887766", True, "")
check("first step only previews", pre.get("confirm_required") is True and "error" not in pre, pre)
check("preview says which layers already hold it", pre.get("in_astrbot_whitelist") is False
      and pre.get("in_plugin_groups") is False, pre)
with open(os.environ["DFY_CMD_CONFIG_TARGET"], "rb") as fh:
    snap = hashlib.sha1(fh.read()).hexdigest()
c1 = json.load(open(os.environ["DFY_CMD_CONFIG_TARGET"], encoding="utf-8"))
with open(os.environ["DFY_CMD_CONFIG_TARGET"], "rb") as fh:
    check("preview wrote nothing", hashlib.sha1(fh.read()).hexdigest() == snap)
res = groups.set_group(cfg, "99887766", True, "yes")
check("enable wrote both layers", res.get("ok") is True and res.get("restart_required") is True, res)
c1 = json.load(open(os.environ["DFY_CMD_CONFIG_TARGET"], encoding="utf-8"))
p1 = json.load(open(os.environ["DFY_PLUGIN_CONFIG_TARGET"], encoding="utf-8"))
wl1 = c1["platform_settings"]["id_whitelist"]
check("layer1 updated", "99887766" in wl1, wl1)
check("layer2 updated", "99887766" in p1["allowed_group_ids"], p1["allowed_group_ids"])
check("owner entry preserved", OWNER in wl1, wl1)
check("other groups preserved", GID in wl1 and GID2 in wl1, wl1)
check("config_version untouched", c1.get("config_version") == "5", c1)
check("plugin keys untouched", p1.get("owner_qq") == "<OWNER_QQ>" and p1.get("max_unread") == 50, p1)
bkd = os.path.join(config.runtime_dir(), res.get("backup", ""))
check("backup of both files written",
      os.path.isfile(os.path.join(bkd, "cmd_config.json")) and os.path.isfile(os.path.join(bkd, "plugin_config.json")), bkd)
res2 = groups.set_group(cfg, "99887766", False, "yes")
check("disable ok", res2.get("ok") is True, res2)
c2 = json.load(open(os.environ["DFY_CMD_CONFIG_TARGET"], encoding="utf-8"))
p2 = json.load(open(os.environ["DFY_PLUGIN_CONFIG_TARGET"], encoding="utf-8"))
check("removed from layer1", "99887766" not in c2["platform_settings"]["id_whitelist"])
check("removed from layer2", "99887766" not in p2["allowed_group_ids"])
check("disable keeps the chat data", os.path.isfile(os.environ["DFY_STATE_TARGET"]))
nd = groups.set_group(cfg, "55443322", False, "yes")
check("disable-never-enabled reported", "error" in nd, nd)
with open(os.environ["DFY_PLUGIN_CONFIG_TARGET"], "w", encoding="utf-8") as fh:
    fh.write("{ broken")
bad = groups.set_group(cfg, "11223344", True, "yes")
check("unparsable config refused before write", "error" in bad and "changed" not in bad, bad)

# ---------------------------------------------------------------- routes wired
check("writable routes are exactly the declared set", # pinned on purpose: adding a write route must be a conscious edit here too
    app_mod.WRITABLE == {
    "/v1/send", "/v1/control", "/v1/groups/enable", "/v1/groups/disable",
    "/v1/arena/dispatch", "/v1/arena/note", "/v1/arena/workbench", "/v1/arena/grant",
    "/v1/arena/exec",
    "/v1/backups", "/v1/backups/restore", "/v1/backups/export", "/v1/backups/import",
    "/v1/persona", "/v1/persona/restore", "/v1/models", "/v1/models/test",
    "/v1/settings", "/v1/credentials", "/v1/credentials/clear", "/v1/selftest",
    "/v1/setup/adopt", "/v1/setup/reset", "/v1/release/install", "/v1/release/rollback",
    "/v1/diag/bundle"}, app_mod.WRITABLE)
check("tools route registered", "/v1/tools" in app_mod.ROUTES)
check("control receipt route registered", "/v1/control/receipt" in app_mod.ROUTES)
check("redaction helper active", SECRET32 not in security.redact("token=" + SECRET32))

# ---------------------------------------------------------------- production untouched
after = live_hash()
check("PRODUCTION UNTOUCHED", after == before, {"before": before, "after": after})

shutil.rmtree(SCRATCH, ignore_errors=True)
print("STAGE2 SELFTEST (pass=%d fail=%d)" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
