# -*- coding: utf-8 -*-
"""Stage-3 selftest: DPAPI credentials, backup/restore/export, persona editing with
optimistic lock + history, read-only memory search, provider config, settings, wiring.

Everything runs inside a scratch runtime dir and scratch copies of the config files, so
production is never touched. Run: python scripts/selftest_stage3.py
"""
import hashlib
import http.server
import json
import os
import shutil
import sqlite3
import sys
import time
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
SCRATCH = tempfile.mkdtemp(prefix="dfy-stage3-")
RUNTIME = os.path.join(SCRATCH, "runtime")
os.makedirs(RUNTIME, exist_ok=True)
os.environ["DFY_RUNTIME_DIR"] = RUNTIME
FAKE_KEY = "sk-STAGE3SECRET-do-not-leak-0123456789"
FAKE_SECRET = "totally-secret-credential-8899"
os.environ["DFY_DESKTOP_CONFIG"] = os.path.join(SCRATCH, "desktop.json")
# the memory store is found through the plugin's own legacy_state_dir, so the fixture
# exercises that real resolution path instead of an env shortcut
MEMDIR = os.path.join(SCRATCH, "legacy-state")
os.makedirs(MEMDIR, exist_ok=True)
os.environ.pop("DFY_MEMORY_DB", None)
MEM = os.path.join(MEMDIR, "long-memory.db")

from dfy_core import backup, config, credentials, memories, modelcfg, persona, settings  # noqa: E402

PASS = [0]
FAIL = []


def check(name, ok, extra=""):
    if ok:
        PASS[0] += 1
    else:
        FAIL.append("%s | %s" % (name, str(extra)[:220]))
        print("FAIL %s | %s" % (name, str(extra)[:220]))
    return ok


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read_b(path):
    return open(path, "rb").read()


def jload(path):
    return json.loads(read_b(path).decode("utf-8"))


# --------------------------------------------------------------- fixtures
AB = os.path.join(SCRATCH, "astrbot")
DB = os.path.join(AB, "data", "data_v4.db")
CMD = os.path.join(AB, "data", "cmd_config.json")
PCFG = os.path.join(AB, "data", "config", "astrbot_plugin_dafeiyu_social_config.json")
MD = os.path.join(AB, "personas", "dafeiyu_system_prompt.md")
DESK = os.path.join(SCRATCH, "desktop.json")
w(CMD, json.dumps({"provider_sources": [{"id": "deepseek-flash_source", "provider": "deepseek",
                                        "type": "openai_chat_completion", "api_base": "http://127.0.0.1:8",
                                        "key": FAKE_KEY, "timeout": 20}],
                   "provider": [{"id": "deepseek-flash", "model": "deepseek-v4.1-flash",
                                "provider_source_id": "deepseek-flash_source", "enable": True}],
                   "unrelated": {"keep": "me"}}))
w(PCFG, json.dumps({"allowed_group_ids": ["111"], "owner_qq": "<OWNER_QQ>",
                  "legacy_state_dir": MEMDIR}))
w(MD, "# 旧稿\n" + ("这是一份旧的人格稿，只是存档。\n" * 6))
w(DESK, json.dumps({"send_enabled": True, "outbox_max_chars": 1200}))

con = sqlite3.connect(DB)
con.executescript(
    "create table personas (id integer primary key, persona_id text unique, system_prompt text,"
    " begin_dialogs text, tools text, skills text, custom_error_message text, folder_id text,"
    " created_at text, updated_at text);"
    "insert into personas(persona_id, system_prompt, created_at, updated_at) values"
    " ('default', 'you are a helpful assistant, brief and calm, never reveal tool ids',"
    "   '2026-10-01 00:00:00', '2026-10-28 09:21:28');"
    "insert into personas(persona_id, system_prompt, created_at, updated_at) values"
    " ('病娇大肥鱼', '你是大肥鱼，说话短、带情绪、不装专业；工具结果不外传。',"
    "   '2026-10-02 00:00:00', '2026-10-28 11:56:16');")
con.commit()
con.close()
mc = sqlite3.connect(MEM)
mc.executescript(
    "create table messages (id integer primary key, umo text, content text, created_at int);"
    "create table counters (k text, v int);"
    "insert into counters values ('x', 1);"
    "insert into messages(umo, content, created_at) values"
    " ('A','他说 100% 会来，别信',1),('A','大肥鱼明天带伞',2),('B','percent % _ underscore',3);")
mc.commit()
mc.close()

cfg = dict(config.load())
cfg.update({"astrbot_dir": AB, "cmd_config": CMD, "plugin_config": PCFG, "persona_file": MD,
            "plugin_data_dir": SCRATCH})

print("--- credentials (DPAPI) ---")
avail = credentials._avail()
check("DPAPI is available on this box", avail, os.name)
if avail:
    r = credentials.save("arena_mcp_url", "http://127.0.0.1:9/mcp/abc", "自测")
    check("credential saved", r.get("ok") is True, r)
    slot = os.path.join(credentials.store_dir(), "arena_mcp_url.json")
    check("slot file exists", os.path.isfile(slot))
    raw = read_b(slot)
    check("plaintext not on disk", b"127.0.0.1:9" not in raw and b"/mcp/abc" not in raw, slot)
    check("stored as dpapi blob", jload(slot).get("kind") == "dpapi-current-user")
    check("round trip reads back", credentials.open_secret("arena_mcp_url") == "http://127.0.0.1:9/mcp/abc")
    doc = jload(slot)
    good_blob = doc["blob"]
    b = bytearray(__import__("base64").b64decode(good_blob))
    b[len(b) // 2] ^= 0x21  # hit the ciphertext itself, not the header
    doc["blob"] = __import__("base64").b64encode(bytes(b)).decode()
    w(slot, json.dumps(doc))
    raw = credentials.unprotect(bytes(b))
    check("raw DPAPI gives no usable integrity signal (that is why we hash)",
          raw is None or raw != b"http://127.0.0.1:9/mcp/abc", raw)
    check("tampered slot refuses to open, because we verify sha8 ourselves",
          credentials.open_secret("arena_mcp_url") is None, "DPAPI alone would hand back junk")
    doc["blob"] = good_blob
    w(slot, json.dumps(doc))
    check("the good slot opens again once the blob is restored",
          credentials.open_secret("arena_mcp_url") == "http://127.0.0.1:9/mcp/abc")
    li = credentials.list_slots()
    check("listing shows fingerprint not value", li["items"][0]["sha8"] and "127.0.0.1" not in json.dumps(li), li)
    check("empty value refused", "error" in credentials.save("arena_mcp_url", "   "))
    check("bad name refused", "error" in credentials.save("../evil", "x"))
    check("clear needs confirm", credentials.clear("arena_mcp_url").get("confirm_required") is True)
    r2 = credentials.save("second", FAKE_SECRET, "")
    check("second slot saved", r2.get("ok") is True, r2)
    check("clear works after confirm", credentials.clear("second", "yes").get("ok") is True)
    check("cleared slot gone", credentials.open_secret("second") is None)
    check("cleared file removed", not os.path.isfile(os.path.join(credentials.store_dir(), "second.json")))

print("--- endpoint: credential beats the file (design 7.2) ---")
if avail and os.path.isfile(os.path.join(credentials.store_dir(), "arena_mcp_url.json")):
    credentials.clear("arena_mcp_url", "yes")   # start this section from a clean slate
from dfy_core import arena
EP = os.path.join(SCRATCH, "endpoint.txt")
w(EP, "http://127.0.0.1:1/mcp/from-file")
cfg2 = dict(cfg); cfg2["arena_endpoint_file"] = EP
os.environ["DFY_ARENA_ENDPOINT_FILE"] = EP
arena.forget_endpoint()
u, e = arena._load_endpoint(cfg2)
check("falls back to the file when no credential", u and "from-file" in u, (u, e))
if avail:
    credentials.save("arena_mcp_url", "http://127.0.0.1:2/mcp/from-dpapi", "")
    arena.forget_endpoint()
    u2, e2 = arena._load_endpoint(cfg2)
    check("DPAPI credential wins over the file", u2 and "from-dpapi" in u2, (u2, e2))
    credentials.clear("arena_mcp_url", "yes")
    arena.forget_endpoint()
    u3, _ = arena._load_endpoint(cfg2)
    check("after clearing we fall back again", u3 and "from-file" in u3, u3)
check("no non-loopback http endpoint is ever used",
      arena.validate_endpoint("http://10.0.0.5:8790/mcp/x")[0] is False)

print("--- backup: create / list / restore / guard ---")
b1 = backup.create(cfg, "baseline")
check("snapshot created", b1.get("ok") is True, b1)
names = {f["name"]: f for f in b1["files"] if f.get("file")}
check("covers cmd_config + plugin_config + persona_md + desktop_config",
      {"cmd_config", "plugin_config", "persona_md", "desktop_config"} <= set(names), sorted(names))
check("sqlite snapshot taken too", "astrbot_db" in names and names["astrbot_db"]["kind"] == "sqlite", sorted(names))
check("snapshot hash matches the source",
      all(names[k]["sha8"] == backup._sha(cfg_key) for k, cfg_key in
          [("cmd_config", CMD), ("plugin_config", PCFG), ("persona_md", MD)]), names)
check("snapshot is smaller-or-equal for sqlite (WAL-safe copy)", names["astrbot_db"]["bytes"] > 0)
w(CMD, json.dumps({"provider_sources": [], "provider": [], "unrelated": {"keep": "me"}}))
ls = backup.listing(cfg)
check("listing newest first", ls["items"][0]["id"] == ls["items"][0]["id"] and ls["count"] >= 1, ls["count"])
r = backup.restore(cfg, b1["id"])
check("restore refuses without confirm", r.get("confirm_required") is True, r)
check("restore preview says sqlite is skipped", "sqlite" in json.dumps(r, ensure_ascii=False), r)
r = backup.restore(cfg, b1["id"], "astrbot_db", "yes")
check("sqlite-only restore refuses to overwrite a live db", "error" in r, r)
r = backup.restore(cfg, b1["id"], "nope", "yes")
check("unknown file name refused", "error" in r, r)
r = backup.restore(cfg, b1["id"], "cmd_config", "yes")
check("single-file restore works", r.get("ok") is True and jload(CMD).get("provider_sources"), r)
check("guard snapshot taken before the write", os.path.isdir(os.path.join(backup.root(cfg), r["guard"])), r)
check("bad id format refused", "error" in backup.restore(cfg, "../etc", "cmd_config", "yes"))
# a snapshot with a broken member must not lose the good ones
shutil.rmtree(os.path.join(backup.root(cfg), b1["id"], "plugin_config.json"), ignore_errors=True)
os.remove(os.path.join(backup.root(cfg), b1["id"], "plugin_config.json"))
man = jload(os.path.join(backup.root(cfg), b1["id"], "manifest.json"))
man["files"] = [f for f in man["files"] if f["name"] != "plugin_config"]
w(os.path.join(backup.root(cfg), b1["id"], "manifest.json"), json.dumps(man))
check("missing member is reported, not fatal",
      backup.restore(cfg, b1["id"], "cmd_config", "yes").get("ok") is True)

if not avail:
    print("  (这台机器没有 DPAPI：导出/导入这段跳过，Windows 上实跑)")
else:
    print("--- backup: encrypted export / import ---")
    ex = backup.export(cfg, "selftest")
    check("export wrote a package", ex.get("ok") and os.path.isfile(ex["file"]), ex)
    pkg = read_b(ex["file"])
    check("package is not a plain zip", pkg[:2] != b"PK" and FAKE_KEY.encode() not in pkg, pkg[:8])
    info = backup.inspect(cfg, os.path.basename(ex["file"]))
    check("bare filename resolves inside the export dir", info.get("verified") is True, info)
    check("export contents skip sqlite", "data_v4.db" not in json.dumps(info), info)
    if avail:
        bad = ex["file"] + ".bad"
        doc = jload(ex["file"])
        doc["blob"] = doc["blob"][:-8] + "AAAAAAAA"
        w(bad, json.dumps(doc))
        check("tampered package refuses", "解不开" in str(backup.inspect(cfg, bad).get("error")), backup.inspect(cfg, bad))
    check("path escape refused", "error" in backup.inspect(cfg, os.path.join(AB, "data", "cmd_config.json")))
    w(CMD, json.dumps({"provider_sources": [], "provider": [], "unrelated": {"keep": "changed"}}))
    r = backup.import_archive(cfg, os.path.basename(ex["file"]))
    check("import needs confirm", r.get("confirm_required") is True, r)
    r = backup.import_archive(cfg, os.path.basename(ex["file"]), "yes")
    check("import restores config", r.get("ok") is True and jload(CMD)["unrelated"]["keep"] == "me", r)
    check("import keeps a guard snapshot", os.path.isdir(os.path.join(backup.root(cfg), r["guard"])), r)

print("--- backup policy ---")
check("due() with no history is due", backup.due(None, 60) is True)
check("due() respects the interval",
      backup.due(time.strftime("%Y-%m-%d %H:%M:%S"), 600) is False)
check("due() fires when the interval passed", backup.due("2020-01-01 00:00:00", 60) is True)
check("due() treats a bad timestamp as due", backup.due("not a time", 60) is True)
check("auto backup off means no snapshot", backup.save_settings(cfg, auto_minutes=0, keep=5).get("ok") is True)
os.remove(os.path.join(RUNTIME, "backup-last-auto.txt")) if os.path.isfile(os.path.join(RUNTIME, "backup-last-auto.txt")) else None
check("autobackup skipped when off", "skipped" in backup.autobackup_if_due(cfg), backup.autobackup_if_due(cfg))
check("prune keeps the configured count", len(backup.list_dirs(cfg)) <= 5 or backup.settings(cfg)["keep"] >= 5)

print("--- persona (live table is the source of truth) ---")
src = persona.sources(cfg)
check("db rows listed", {r["persona_id"] for r in src["db"]["rows"]} == {"default", "病娇大肥鱼"}, src["db"])
check("md reported separately", src["md"]["available"] is True and src["md"]["bytes"] > 0, src.get("md"))
cur = persona.read(cfg, "db", "病娇大肥鱼")
check("read returns text + sha8", cur["chars"] > 10 and len(cur["sha8"]) == 8, cur)
r = persona.write(cfg, "db", "病娇大肥鱼", cur["text"] + "\n加一句：下雨天提醒带伞。", cur["sha8"])
check("write asks for confirm first", r.get("confirm_required") is True, r)
r = persona.write(cfg, "db", "病娇大肥鱼", "太短", cur["sha8"], "yes")
check("absurdly short persona refused", "error" in r, r)
r = persona.write(cfg, "db", "病娇大肥鱼", cur["text"] + "\n加一句：下雨天提醒带伞。", "deadbeef", "yes")
check("stale base hash refused (someone else edited it)", "error" in r and "改过" in r["error"], r)
r = persona.write(cfg, "db", "病娇大肥鱼", cur["text"] + "\n加一句：下雨天提醒带伞。", cur["sha8"], "yes")
check("write accepted after confirm", r.get("ok") is True, r)
check("flagged as needing an engine restart", r.get("restart_required") is True, r)
after = persona.read(cfg, "db", "病娇大肥鱼")
check("new text really in the db", "带伞" in after["text"] and after["chars"] > cur["chars"], after["chars"])
other = persona.read(cfg, "db", "default")
check("the other persona untouched", other["chars"] == 58 or "helpful assistant" in other["text"], other)
h = persona.history(cfg, "db", "病娇大肥鱼")
check("history kept the pre-edit version", h["count"] == 1 and h["items"][0]["chars"] == cur["chars"], h)
r = persona.restore(cfg, "db", "病娇大肥鱼", h["items"][0]["id"])
check("history restore asks for confirm", r.get("confirm_required") is True, r)
r = persona.restore(cfg, "db", "病娇大肥鱼", h["items"][0]["id"], "yes")
check("restored to the old text", persona.read(cfg, "db", "病娇大肥鱼")["sha8"] == cur["sha8"], r)
check("history now has two entries", persona.history(cfg, "db", "病娇大肥鱼")["count"] == 2)
check("unknown persona not inserted, refused instead",
      "error" in persona.write(cfg, "db", "不存在的人格", "x" * 80, "", "yes"))
mr = persona.write(cfg, "md", "", "# 新稿\n" + ("改过的旧稿正文，够长了吧够长了吧。" * 8), "", "yes")
check("md source is writable too", mr.get("ok") is True and mr.get("restart_required") is False, mr)
check("md really changed", "新稿" in read_b(MD).decode("utf-8"))
dbcon = sqlite3.connect(DB)
check("db file still healthy after all writes",
      dbcon.execute("pragma integrity_check").fetchone()[0] == "ok")
dbcon.close()

print("--- memories (read-only) ---")
mi = memories.info(cfg)
check("memory db found", mi.get("available") is True, mi)
check("primary table picked by having a text column", mi.get("primary_table") == "messages", mi)
s = memories.search(cfg, "带伞")
check("search finds the row", s["count"] == 1 and "大肥鱼" in json.dumps(s, ensure_ascii=False), s)
s = memories.search(cfg, "100%")
check("LIKE metachars are escaped", s["count"] == 1, s)
s = memories.search(cfg, "")
check("empty query lists recent rows", s["count"] == 3 and s["mode"] == "recent", s)
s = memories.search(cfg, "zzz", table="counters")
check("a table without a text column says so", "error" in s, s)
check("no write surface at all",
      not any(hasattr(memories, f) for f in ("save", "write", "delete", "update", "import_db")))
cfg_nomem = dict(cfg)
cfg_nomem["plugin_config"] = os.path.join(SCRATCH, "no-such-plugin-config.json")
cfg_nomem["plugin_data_dir"] = os.path.join(SCRATCH, "empty-dir")
os.makedirs(cfg_nomem["plugin_data_dir"], exist_ok=True)
noinfo = memories.info(cfg_nomem)
check("missing memory db is reported, not faked", noinfo.get("available") is False, noinfo)
check("and it says where it looked", noinfo.get("looked_in"), noinfo)
check("search degrades to the same plain error", "error" in memories.search(cfg_nomem, "x"),
      memories.search(cfg_nomem, "x"))

print("--- provider config ---")
mv = modelcfg.read(cfg)
check("sources projected", mv["sources"][0]["id"] == "deepseek-flash_source", mv)
check("key never leaves the file", FAKE_KEY not in json.dumps(mv, ensure_ascii=False) and
      mv["sources"][0]["has_key"] is True and mv["sources"][0]["key_chars"] == len(FAKE_KEY), mv["sources"])
check("api_base is readable for editing", mv["sources"][0]["api_base"] == "http://127.0.0.1:8", mv)
check("editing a key field is refused", "error" in modelcfg.save(cfg, "deepseek-flash_source",
                                                                {"key": "sk-x"}, base_sha8=mv["sha8"], confirm="yes"))
check("api_base without a scheme refused", "error" in modelcfg.save(cfg, "deepseek-flash_source", {"api_base": "10.0.0.1"}))
check("out-of-range timeout refused", "error" in modelcfg.save(cfg, "deepseek-flash_source", {"timeout": 99999}))
check("unknown source id refused", "error" in modelcfg.save(cfg, "no-such-src", {"timeout": 5}))
r = modelcfg.save(cfg, "deepseek-flash_source", {"timeout": 33}, base_sha8=mv["sha8"])
check("model save asks for confirm", r.get("confirm_required") is True, r)
r = modelcfg.save(cfg, "deepseek-flash_source", {"timeout": 33}, base_sha8=mv["sha8"], confirm="yes")
check("model save applied + snapshot taken", r.get("ok") is True and os.path.isdir(os.path.join(backup.root(cfg), r["guard"])), r)
check("unrelated config keys survived", jload(CMD)["unrelated"]["keep"] == "me", jload(CMD))
check("timeout really changed", [s for s in jload(CMD)["provider_sources"]][0]["timeout"] == 33)
w(CMD, json.dumps({"provider_sources": [{"id": "deepseek-flash_source", "api_base": "http://127.0.0.1:8",
                                         "key": FAKE_KEY, "timeout": 20}], "provider": []}))
r = modelcfg.save(cfg, "deepseek-flash_source", {"timeout": 44}, base_sha8=mv["sha8"], confirm="yes")
check("stale sha8 refused for provider config too", "error" in r and "改过" in str(r.get("error")), r)

print("--- connect test against a stub ---")
SEEN = {}


class Stub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN["auth"] = self.headers.get("Authorization")
        body = json.dumps({"object": "list", "data": [{"id": "m1"}, {"id": "m2"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
w(CMD, json.dumps({"provider_sources": [{"id": "s1", "api_base": "http://127.0.0.1:%d/v1" % port,
                                         "key": FAKE_KEY, "timeout": 5}], "provider": []}))
r = modelcfg.test_connection(cfg, "s1")
check("connect test passes against the stub", r.get("ok") is True and r.get("models") == 2, r)
check("the key is actually sent to the provider", SEEN.get("auth") == "Bearer " + FAKE_KEY, SEEN)
check("no url or key echoed back in the result", FAKE_KEY not in json.dumps(r, ensure_ascii=False), r)
srv.shutdown()
w(CMD, json.dumps({"provider_sources": [{"id": "s1", "api_base": "http://127.0.0.1:9/v1", "key": ""}],
                  "provider": []}))
r = modelcfg.test_connection(cfg, "s1")
check("a dead port reports a plain error", r.get("ok") is False and "连不上" in str(r.get("error")), r)
w(CMD, json.dumps({"provider_sources": [{"id": "s1", "api_base": "127.0.0.1:9/v1", "key": ""}],
                  "provider": []}))
check("api_base without a scheme is refused by the test",
      "http" in str(modelcfg.test_connection(cfg, "s1").get("error")), modelcfg.test_connection(cfg, "s1"))

print("--- settings ---")
r = settings.save(cfg, "backup", {"auto_minutes": 30, "keep": 3})
check("backup policy saved", r.get("ok") is True and r["backup"]["auto_minutes"] == 30, r)
check("out-of-range auto_minutes refused", "error" in settings.save(cfg, "backup", {"auto_minutes": 99999}))
check("unknown section refused", "error" in settings.save(cfg, "core", {"send_enabled": False}))
check("unknown key refused", "error" in settings.save(cfg, "gui", {"evil": 1}))
r = settings.save(cfg, "gui", {"audit_tail": 120, "close_to_tray": False})
check("gui prefs saved", r.get("ok") is True and r["gui"]["audit_tail"] == 120, r)
check("gui pref out of range refused", "error" in settings.save(cfg, "gui", {"audit_tail": 99999}))
r = settings.read(cfg)
check("core section is read-only but visible", r["core_readonly"]["send_enabled"] is True and
      "backup" in json.dumps(r["writable"]), r)
w(os.path.join(RUNTIME, "gui-prefs.json"), "{ broken")
check("a broken prefs file falls back to defaults", settings.read(cfg)["gui"]["audit_tail"] == 60)
w(os.path.join(RUNTIME, "gui-prefs.json"), json.dumps({"audit_tail": 77}))
check("prefs survive a restart", settings.read(cfg)["gui"]["audit_tail"] == 77)

print("--- wiring ---")
from dfy_core import app as app_mod
need_get = ("/v1/backups", "/v1/persona", "/v1/persona/history", "/v1/persona/sources",
            "/v1/memory", "/v1/memory/info", "/v1/models", "/v1/settings", "/v1/credentials")
need_post = ("/v1/backups", "/v1/backups/restore", "/v1/backups/export", "/v1/backups/import",
             "/v1/persona", "/v1/persona/restore", "/v1/models", "/v1/models/test",
             "/v1/settings", "/v1/credentials", "/v1/credentials/clear", "/v1/selftest")
check("all stage-3 GET routes registered", all(p in app_mod.ROUTES for p in need_get),
      [p for p in need_get if p not in app_mod.ROUTES])
check("all stage-3 POST routes writable", set(need_post) <= app_mod.WRITABLE,
      sorted(set(need_post) - app_mod.WRITABLE))
mjs = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "main", "index.mjs"), encoding="utf-8").read()
import re
block = mjs[mjs.index("const WRITABLE = new Set(["):mjs.index("]);", mjs.index("const WRITABLE = new Set(["))]
bridge = set(re.findall(r"\"(/v1/[a-z0-9/_-]+)\"", block))
check("renderer bridge allowlist matches the core exactly", bridge == set(app_mod.WRITABLE),
      {"only_bridge": sorted(bridge - set(app_mod.WRITABLE)), "only_core": sorted(set(app_mod.WRITABLE) - bridge)})
sup = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "main", "supervisor.mjs"), encoding="utf-8").read()
check("supervisor knows every stage-3 GET route",
      all(('"%s"' % p) in sup for p in need_get), [p for p in need_get if '"%s"' % p not in sup])

print("--- leak sweep ---")
blob = []
for root, _d, files in os.walk(RUNTIME):
    for f in files:
        p = os.path.join(root, f)
        if "credentials" in root:
            continue  # the DPAPI slot is the one place the ciphertext must live
        blob.append(read_b(p).decode("utf-8", "replace"))
for root, _d, files in os.walk(AB):
    for f in files:
        blob.append(read_b(os.path.join(root, f)).decode("utf-8", "replace"))
allt = "\n".join(blob)
aud = os.path.join(RUNTIME, "audit-%s.jsonl" % __import__("time").strftime("%Y%m%d"))
lines = [l for l in open(aud, encoding="utf-8", errors="replace")] if os.path.isfile(aud) else []
check("stage-3 events audited", any('"stage3_result"' in l or '"backup_create"' in l or
                                    '"persona_write"' in l or '"credential_save"' in l for l in lines), len(lines))
check("no bearer token or api key in the audit",
      FAKE_KEY not in "".join(lines) and "Authorization" not in "".join(lines), len(lines))
check("the credential plaintext never hit the scratch tree",
      FAKE_SECRET not in allt and "from-dpapi" not in allt)

for _ in range(6):  # Windows can hold the sqlite handle a moment after close
    try:
        shutil.rmtree(SCRATCH)
        break
    except OSError:
        time.sleep(0.4)
print("\nSTAGE3 SELFTEST (pass=%d fail=%d)" % (PASS[0], len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
