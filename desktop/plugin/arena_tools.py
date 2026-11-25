# -*- coding: utf-8 -*-
"""大肥鱼桌面 · Arena 工具接入（2b-ii）：给 AstrBot 生产插件用的 7 个聊天工具的实现。

设计文档 §5.4 要求生产插件新增 7 个 Arena 工具，§6.5 规定路径是
「聊天工具 → 本地核心 → Arena 网关」，§6.4 规定「服务间调用使用独立 token，不共用」。
所以这里的做法是：

* 不直连 Arena 网关，也不读 `ops-dfy\\ctl\\mcp_url.txt`：所有能力都问本地核心要，
  权限层和审计只在核心那一处，插件这一侧只负责把结果压成给模型看的文本；
* 用 `.runtime/core-link.json` 里的**服务 token**，只解锁核心那侧 `link.PLUGIN_ROUTES`
  那 6 条 arena 路由；读聊天、读凭据、开授权那些口子拿这个 token 一律 403；
* 权限按 §5.4：只有主人私聊可用。群聊、非主人直接拒，并且拒得 reasons 明确；
  `dfy_arena_exec` 的“默认关闭 + 限时授权”由核心判断（`/v1/arena/grant` 不在这 6 条里，
  所以插件这一侧无法自己给自己开门）。

本文件不 import astrbot，纯标准库：插件那边 `main.py` 只做一行转发，逻辑与测试都在这里，
`desktop/scripts/selftest_arena_tools.py` 可以直接把它当模块跑起来测。
"""

import json
import os
import urllib.error
import urllib.request

DEFAULT_RUNTIME_DIR = os.path.join(os.path.expanduser("~"), u"大肥鱼", "desktop", ".runtime")
LINK_FILE = "core-link.json"
MAX_TEXT = 1400           # 给模型的正文上限；比插件的 tool_result_max_chars 更保守
ROUTES = {
    "tasks": ("/v1/arena/tasks", "GET"),
    "models": ("/v1/arena/models", "GET"),
    "dispatch": ("/v1/arena/dispatch", "POST"),
    "note": ("/v1/arena/note", "POST"),
    "workbench": ("/v1/arena/workbench", "GET"),
    "workbench_action": ("/v1/arena/workbench", "POST"),
    "exec": ("/v1/arena/exec", "POST"),
}
TOOLS = ("dfy_arena_tasks", "dfy_arena_models", "dfy_arena_dispatch",
         "dfy_arena_set_model_note", "dfy_arena_exec", "dfy_workbench_status",
         "dfy_workbench_action")

DENY_TEXT = u"此操作仅主人私聊可用。"          # §5.4 原文口径


def runtime_dir():
    return os.environ.get("DFY_RUNTIME_DIR") or DEFAULT_RUNTIME_DIR


def link_path(base=None):
    return os.path.join(base or runtime_dir(), LINK_FILE)


def read_link(base=None):
    """The pointer document, or None. Tolerant by design: a missing file means the desktop
    app simply isn't running, which is a normal state for the bot."""
    try:
        with open(link_path(base), encoding="utf-8") as fh:
            doc = json.loads(fh.read() or "{}")
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _url(doc, route):
    port = doc.get("port")
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if port <= 0 or port > 65535:
        return None
    return "http://127.0.0.1:%d%s" % (port, route)   # 127.0.0.1 是硬编码，不是来自文件


def post(route, payload, doc=None, timeout=8, base=None):
    """One request to the local core. Returns {"ok": bool, ...} and never raises: the
    model must get a readable Chinese sentence, not a traceback."""
    doc = doc if doc is not None else read_link(base)
    if not doc:
        return {"ok": False, "error": "本地核心没在跑（没找到 %s）" % LINK_FILE}
    if not doc.get("token"):
        return {"ok": False, "error": "本地核心的服务凭据不完整，请重新打开大肥鱼窗口"}
    url = _url(doc, route)
    if not url:
        return {"ok": False, "error": "本地核心登记的端口不可用，请重新打开大肥鱼窗口"}
    data = None
    headers = {"Content-Type": "application/json", "X-Dfy-Service-Token": str(doc["token"])}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers,
                                method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        reason = ""
        try:
            reason = json.loads(body or "{}").get("error", "")
        except ValueError:
            pass
        detail = u"（%s）" % reason if reason else ""
        if e.code == 403:
            return {"ok": False, "error": u"本地核心拒绝了这个调用%s" % detail}
        return {"ok": False, "error": u"本地核心返回 HTTP %d%s" % (e.code, detail)}
    except urllib.error.URLError as e:
        return {"ok": False, "error": u"连不上本地核心：%s" % str(getattr(e, "reason", e))[:120]}
    except TimeoutError:
        return {"ok": False, "error": u"本地核心 %d 秒没回答，先当失败处理" % timeout}
    except Exception as e:                       # socket.error / 别的意外
        return {"ok": False, "error": u"调用本地核心出错：%s" % type(e).__name__}
    try:
        out = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {"ok": False, "error": u"本地核心返回的不是 JSON：%s" % raw[:80].decode("utf-8", "replace")}
    if not isinstance(out, dict):
        return {"ok": False, "error": "本地核心返回了意外的结构"}
    out.setdefault("ok", "error" not in out)
    if out.get("error"):
        out["ok"] = False
    return out


def allowed(ctx):
    """§5.4：只有主人私聊可用。ctx 由插件那侧填（它有 event）。"""
    return bool(ctx) and bool(ctx.get("owner_private"))


def _cap(text, cap):
    text = text or ""
    if len(text) <= cap:
        return text
    return text[:cap] + u"\n…（已截断，共 %d 字，完整内容在大肥鱼窗口的 Arena 页）" % len(text)


def render(out, cap=MAX_TEXT):
    """Turn a core response into the short text the model sees. Failures are spelled out
    instead of hidden — §验收 6 要求不假装成功。

    路由自己写好的 text 优先（"这一步要先确认…" 这类既不是成功也不是失败的话），
    否则一律先看 error。"""
    if not isinstance(out, dict):
        return u"本地核心返回了意外的结构。"
    text = out.get("text")
    if text and (out.get("ok") or out.get("confirm_required")):
        return _cap(str(text), cap)
    if out.get("error"):
        return u"未成功：%s" % str(out["error"])[:200]
    return _cap(str(text or _digest(out)), cap)


def _digest(out):
    """Fallback one-liner from whatever the route returned, keys chosen so nothing
    sensitive (paths, endpoints, ids of other people) leaks into the chat context."""
    bits = []
    for k in ("queued", "id", "count", "returned", "mode", "state", "ok", "items", "rows"):
        if k in out and not isinstance(out[k], (dict,)):
            v = out[k]
            if isinstance(v, list):
                bits.append(u"%s=%d 条" % (k, len(v)))
            else:
                bits.append(u"%s=%s" % (k, v))
    return u"；".join(str(x) for x in bits) or u"已完成"


# --------------------------------------------------------------------- the 7 tools
def tasks(ctx, limit=10):
    if not allowed(ctx):
        return {"ok": False, "error": DENY_TEXT}
    try:
        limit = max(1, min(int(limit or 10), 20))
    except (TypeError, ValueError):
        limit = 10
    out = post(ROUTES["tasks"][0] + "?limit=%d" % limit, None, timeout=8)
    if out.get("ok"):
        rows = out.get("items") or out.get("tasks") or []
        lines = [u"Arena 任务 %d 条：" % len(rows)]
        for r in rows[:limit]:
            if isinstance(r, dict):
                lines.append(u"- %s｜%s｜%s" % (r.get("id") or r.get("file") or "?",
                                               r.get("status") or r.get("state") or "?",
                                               str(r.get("title") or r.get("task") or "")[:60]))
        out["text"] = u"\n".join(lines) if rows else u"队里没有任务。"
    return out


def models(ctx):
    if not allowed(ctx):
        return {"ok": False, "error": DENY_TEXT}
    out = post(ROUTES["models"][0], None, timeout=12)
    if out.get("ok"):
        rows = out.get("items") or out.get("models") or []
        lines = [u"可用模型 %d 个（备注以主人写的为准）：" % len(rows)]
        for m in rows[:12]:
            if isinstance(m, dict):
                note = (m.get("note") or "").strip()
                lines.append(u"- %s%s" % (m.get("id") or m.get("model") or "?",
                                          u"：%s" % note[:60] if note else ""))
        out["text"] = u"\n".join(lines) if rows else u"没读到模型清单。"
    return out


def dispatch(ctx, task="", model="", mode="queue"):
    if not allowed(ctx):
        return {"ok": False, "error": DENY_TEXT}
    task = str(task or "").strip()
    if not task:
        return {"ok": False, "error": u"派活得有内容，task 不能为空"}
    mode = str(mode or "queue").strip().lower()
    if mode not in ("queue", "browser"):
        return {"ok": False, "error": u"mode 只能是 queue（队列派活）或 browser（浏览器派活）"}
    out = post(ROUTES["dispatch"][0], {"task": task[:2000], "model": str(model or "")[:80],
                                      "mode": mode}, timeout=25)
    if out.get("ok"):
        out["text"] = u"已排队：%s（%s）" % (out.get("id") or out.get("file") or "任务已入队",
                                            u"队列" if mode == "queue" else u"浏览器")
    elif out.get("confirm_required"):
        out["text"] = u"这一步要主人在窗口里确认：%s" % str(out.get("will") or "")[:120]
        out["ok"] = True
    return out


def set_note(ctx, model="", note=""):
    if not allowed(ctx):
        return {"ok": False, "error": DENY_TEXT}
    if not str(model or "").strip():
        return {"ok": False, "error": u"要给哪个模型写备注？model 不能为空"}
    out = post(ROUTES["note"][0], {"model": str(model)[:80], "note": str(note or "")[:300]},
               timeout=15)
    if out.get("ok"):
        out["text"] = u"备注已写入 %s。" % model
    return out


def workbench_status(ctx):
    if not allowed(ctx):
        return {"ok": False, "error": DENY_TEXT}
    out = post(ROUTES["workbench"][0], None, timeout=12)
    if out.get("ok"):
        rows = out.get("items") or out.get("instances") or []
        lines = [u"工作台：%d 个实例" % len(rows)]
        for r in rows[:8]:
            if isinstance(r, dict):
                lines.append(u"- %s｜登录 %s｜进程 %s" % (r.get("id") or r.get("name") or "?",
                                                         u"有" if r.get("logged_in") or r.get("login") else u"无",
                                                         r.get("pid") or u"无"))
        out["text"] = u"\n".join(lines)
    return out


def workbench_action(ctx, action="", instance="", confirm=""):
    if not allowed(ctx):
        return {"ok": False, "error": DENY_TEXT}
    action = str(action or "").strip().lower()
    if action not in ("open", "close", "refresh", "status"):
        return {"ok": False, "error": u"action 只能是 open / close / refresh / status"}
    out = post(ROUTES["workbench_action"][0], {"action": action,
                                               "instance": str(instance or "")[:40],
                                               "confirm": str(confirm or "")[:10]}, timeout=25)
    if out.get("confirm_required"):
        out["ok"] = True
        out["text"] = u"这一步要先确认：%s" % str(out.get("will") or out.get("note") or "")[:160]
    elif out.get("ok"):
        out["text"] = u"工作台 %s 已执行：%s" % (action, _digest(out))
    return out


def exec_command(ctx, cmd="", timeout_sec=60):
    """§5.4：默认不可用。这里不做判断，只把请求交给核心——判断和审计都在那边，
    没授权时核心会直接拒；插件这一侧留口子就等于绕过权限层。"""
    if not allowed(ctx):
        return {"ok": False, "error": DENY_TEXT}
    cmd = str(cmd or "").strip()
    if not cmd:
        return {"ok": False, "error": u"要执行什么？cmd 不能为空"}
    try:
        t = max(1, min(int(timeout_sec or 60), 300))
    except (TypeError, ValueError):
        t = 60
    out = post(ROUTES["exec"][0], {"cmd": cmd[:2000], "timeout": t}, timeout=t + 10)
    if out.get("ok"):
        out["text"] = u"已执行，退出码 %s：%s" % (out.get("rc", "?"),
                                                  str(out.get("stdout") or out.get("out") or "")[:400])
    return out


HANDLERS = {"tasks": tasks, "models": models, "dispatch": dispatch, "note": set_note,
            "workbench_status": workbench_status, "workbench_action": workbench_action,
            "exec": exec_command}


def run(tool, ctx, cap=MAX_TEXT, **args):
    """Single entry the plugin's @llm_tool methods call: never raises, always a string."""
    fn = HANDLERS.get(str(tool or ""))
    if fn is None:
        return u"未知工具：%s" % tool
    try:
        out = fn(ctx, **args)
    except TypeError as e:                      # 参数名不匹配 = 我这边的 bug，说清楚
        return u"工具参数不对：%s" % str(e)[:120]
    except Exception as e:
        return u"工具执行出错：%s" % type(e).__name__
    return render(out, cap=cap)
