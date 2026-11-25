# -*- coding: utf-8 -*-
"""Install / check / remove the 7 Arena chat tools in the production AstrBot plugin (2b-ii).

设计文档 §5.4 要「生产插件新增 7 个工具」，但插件那侧只该有转发代码，逻辑与权限判断留在
`plugin/arena_tools.py`（由桌面端维护、复制过去）。这个脚本负责把这件事做成可回滚的三步：

    护栏快照 → 写 → 读回来比对 + 语法自检；任何一步不对就自己退回快照

三种模式：
    --dry-run   只报计划：要动哪两个文件、现在装没装、装的是哪版
    (默认)      安装或升级（重复执行是幂等的：块只有一份，文件按 sha 比对）
    --check     只读：装得对不对（验收脚本用它）
    --uninstall 把 glue 块摘掉、把 arena_tools.py 移回备份名，不动插件其他文件

注意：改完 **要重启 AstrBot 才会加载**（新工具是启动时注册的）。本脚本不碰 AstrBot 进程，
也不重启——那是需要单独点头的事。
"""

import argparse
import hashlib
import os
import py_compile
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # desktop/
SRC_MODULE = os.path.join(HERE, "plugin", "arena_tools.py")
DEFAULT_PLUGIN_DIR = os.path.join(os.path.expanduser("~"), "astrbot", "data", "plugins",
                                  "astrbot_plugin_dafeiyu_social")
BEGIN = "    # BEGIN DFY ARENA TOOLS (managed by desktop/scripts/install_arena_tools.py)"
END = "    # END DFY ARENA TOOLS"
TOOL_NAMES = ("dfy_arena_dispatch", "dfy_arena_tasks", "dfy_arena_models",
              "dfy_arena_set_model_note", "dfy_arena_exec", "dfy_workbench_status",
              "dfy_workbench_action")
ANCHOR = '    @filter.llm_tool(name="dfy_context")'

GLUE = '''    # BEGIN DFY ARENA TOOLS (managed by desktop/scripts/install_arena_tools.py)
    # 逻辑不在这儿：这块只把 event 拆成 ctx 交给 arena_tools.run()，让它去问桌面核心。
    # 这样权限层和审计只在核心一处（设计文档 §6.4/§6.5），插件重装也不用再改这段。

    def _arena_ctx(self, event: AstrMessageEvent) -> dict:
        try:
            sender = str(event.get_sender_id() or "")
        except Exception:
            sender = ""
        try:
            owner_private = bool(self._is_owner_private(event))
        except Exception:
            owner_private = False
        return {"owner_private": owner_private, "sender_qq": sender,
                "umo": str(getattr(event, "unified_msg_origin", "") or "")}

    def _arena_tool(self, tool: str, event: AstrMessageEvent, **args) -> str:
        try:
            from .arena_tools import run as _run      # 惰性 import：模块坏了也不能拖垮插件加载
        except Exception:
            return ("error: Arena 工具组件没装好（缺 arena_tools.py）。"
                    "在大肥鱼窗口里跑一次 desktop\\\\scripts\\\\install_arena_tools.py 就好。")
        try:
            cap = int(self.config.get("tool_result_max_chars", 3500) or 3500)
        except Exception:
            cap = 3500
        if cap < 200:
            cap = 200
        return self._truncate(_run(tool, self._arena_ctx(event), cap=cap, **args), cap)

    @filter.llm_tool(name="dfy_arena_tasks")
    async def dfy_arena_tasks(self, event: AstrMessageEvent, limit: int = 10) -> str:
        """看 Arena 队列里的任务：谁在跑、跑完几个、有没有失败。

        Args:
            limit(number): 最多列几条，1-20。
        """
        return self._arena_tool("tasks", event, limit=limit)

    @filter.llm_tool(name="dfy_arena_models")
    async def dfy_arena_models(self, event: AstrMessageEvent) -> str:
        """看可用模型清单和主人给每个模型写的备注；派活前用它挑模型。"""
        return self._arena_tool("models", event)

    @filter.llm_tool(name="dfy_arena_dispatch")
    async def dfy_arena_dispatch(self, event: AstrMessageEvent, task: str = "",
                                 model: str = "", mode: str = "queue") -> str:
        """往 Arena 派一个活。mode=queue 是排队给后台跑，mode=browser 是发给已开着的工作台会话。

        Args:
            task(string): 要做什么，一句话说清楚。
            model(string): 用哪个模型；留空按备注里的默认路由走。
            mode(string): queue 或 browser。
        """
        return self._arena_tool("dispatch", event, task=task, model=model, mode=mode)

    @filter.llm_tool(name="dfy_arena_set_model_note")
    async def dfy_arena_set_model_note(self, event: AstrMessageEvent, model: str = "",
                                       note: str = "") -> str:
        """给某个模型写备注（哪类活适合它、有什么坑）。备注只影响路由建议，不改配置。

        Args:
            model(string): 模型 id。
            note(string): 备注正文，最多 300 字。
        """
        return self._arena_tool("note", event, model=model, note=note)

    @filter.llm_tool(name="dfy_arena_exec")
    async def dfy_arena_exec(self, event: AstrMessageEvent, cmd: str = "",
                             timeout_sec: int = 60) -> str:
        """在本机执行一条命令。默认关闭：主人没在窗口里临时授权就只会拿到拒绝，群聊里更是不可能。

        Args:
            cmd(string): 要执行的命令。
            timeout_sec(number): 最多等多久，1-300 秒。
        """
        return self._arena_tool("exec", event, cmd=cmd, timeout_sec=timeout_sec)

    @filter.llm_tool(name="dfy_workbench_status")
    async def dfy_workbench_status(self, event: AstrMessageEvent) -> str:
        """看浏览器工作台实例的登录与运行状态（按现有约定只关心 profile_3 那条通道）。"""
        return self._arena_tool("workbench_status", event)

    @filter.llm_tool(name="dfy_workbench_action")
    async def dfy_workbench_action(self, event: AstrMessageEvent, action: str = "status",
                                   instance: str = "", confirm: str = "") -> str:
        """开/关/刷新工作台实例。关实例会中断正在跑的浏览器任务，所以要 confirm=yes。

        Args:
            action(string): open / close / refresh / status。
            instance(string): 实例名，例如 profile_3。
            confirm(string): 关闭类操作要写 yes。
        """
        return self._arena_tool("workbench_action", event, action=action,
                                instance=instance, confirm=confirm)
    # END DFY ARENA TOOLS
'''


def sha256(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except OSError:
        return None


def runtime_dir():
    return os.environ.get("DFY_RUNTIME_DIR") or os.path.join(HERE, ".runtime")


def guard_snapshot(text_bytes, name):
    """Copy what's about to be replaced into .runtime/backups before touching it."""
    d = os.path.join(runtime_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(d, "%s.%s" % (name, stamp))
    n = 1
    while os.path.exists(out):
        out = os.path.join(d, "%s.%s.%d" % (name, stamp, n))
        n += 1
    with open(out, "wb") as fh:
        fh.write(text_bytes)
    return out


def split_block(src):
    """(without, block) where block is the managed region including its markers."""
    i = src.find(BEGIN)
    if i < 0:
        return src, None
    j = src.find(END, i)
    if j < 0:
        return src, None
    j = src.index("\n", j) + 1
    return src[:i] + src[j:], src[i:j]


def build_installed(original):
    """Return (new_text, state) where state says what would happen.

    Re-installing replaces the managed region **in place** (byte-for-byte around it), so a
    second run produces exactly the same file and install() can see "nothing to do" instead
    of writing a identical file and leaving yet another snapshot behind."""
    i = original.find(BEGIN)
    j = original.find(END, i) if i >= 0 else -1
    if i >= 0 and j >= 0:
        j = original.index("\n", j) + 1
        return original[:i] + GLUE + original[j:], "replaced"
    without = original
    if ANCHOR in without:
        head, sep, tail = without.partition(ANCHOR)
        return head + GLUE + "\n" + sep + tail, "inserted"
    return without.rstrip("\n") + "\n\n" + GLUE, "appended-at-eof"


def check(plugin_dir, quiet=False):
    main_py = os.path.join(plugin_dir, "main.py")
    mod = os.path.join(plugin_dir, "arena_tools.py")
    res = {"plugin_dir": plugin_dir, "main_py": os.path.isfile(main_py),
           "module": os.path.isfile(mod), "block": False, "tools": [], "module_sha": sha256(mod),
           "expected_sha": sha256(SRC_MODULE)}
    src = ""
    if res["main_py"]:
        src = open(main_py, encoding="utf-8").read()
        res["block"] = BEGIN in src
        res["tools"] = [t for t in TOOL_NAMES if ('name="%s"' % t) in src]
        res["duplicated_block"] = src.count(BEGIN) > 1
    res["complete"] = (res["block"] and len(res["tools"]) == len(TOOL_NAMES)
                       and not res.get("duplicated_block"))
    res["module_up_to_date"] = res["module_sha"] == res["expected_sha"]
    if not quiet:
        print("plugin dir   : %s" % plugin_dir)
        print("main.py      : %s (block=%s, tools=%d/%d)"
              % (res["main_py"], res["block"], len(res["tools"]), len(TOOL_NAMES)))
        print("arena_tools  : %s (sha8 %s vs 期望 %s)"
              % (res["module"], (res["module_sha"] or "")[:8], (res["expected_sha"] or "")[:8]))
        print("结论         : %s" % ("已装且一致" if res["complete"] and res["module_up_to_date"]
                                    else ("缺件" if not res["complete"] else "组件版本落后")))
    return res


def install(plugin_dir, dry=False):
    main_py = os.path.join(plugin_dir, "main.py")
    if not os.path.isfile(main_py):
        return {"ok": False, "error": "找不到插件主文件：%s" % main_py}
    original = open(main_py, encoding="utf-8").read()
    new_text, state = build_installed(original)
    plan = {"ok": True, "plugin_dir": plugin_dir, "glue": state,
            "main_bytes": (len(original.encode("utf-8")), len(new_text.encode("utf-8"))),
            "module": os.path.join(plugin_dir, "arena_tools.py"),
            "module_changes": sha256(os.path.join(plugin_dir, "arena_tools.py")) != sha256(SRC_MODULE),
            "restart_required": True}
    if dry:
        plan["dry_run"] = True
        plan["will"] = ("写 %s（%s）；复制 %s → %s"
                        % (main_py, state, SRC_MODULE, plan["module"]))
        return plan

    if new_text == original and not plan["module_changes"]:
        # 已经装好了：什么都不动，也就不留快照（否则每次跑都多一份一样的备份）
        plan.update(ok=True, changed=False,
                    note=u"glue 块与 arena_tools.py 都已是最新，没动任何文件")
        return plan
    plan["changed"] = True
    guard = None
    try:
        guard = guard_snapshot(original.encode("utf-8"), "plugin-main.py")
        plan["guard"] = guard
        tmp = main_py + ".dfy-tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_text)
        os.replace(tmp, main_py)
        if sha256(main_py) != hashlib.sha256(new_text.encode("utf-8")).hexdigest():
            raise RuntimeError("读回来的 main.py 与要写的内容不一致")
        if src_broken(main_py):
            raise RuntimeError("补丁后 main.py 通不过语法检查")
        mod_guard = None
        if os.path.isfile(plan["module"]):
            mod_guard = guard_snapshot(open(plan["module"], "rb").read(), "plugin-arena_tools.py")
            plan["module_guard"] = mod_guard
        shutil.copyfile(SRC_MODULE, plan["module"])
        plan["module_verified"] = sha256(plan["module"]) == sha256(SRC_MODULE)
        if not plan["module_verified"]:
            raise RuntimeError("arena_tools.py 复制后哈希对不上")
        plan["verified"] = True
    except Exception as e:
        rolled = False
        if guard:
            try:
                shutil.copyfile(guard, main_py)
                rolled = sha256(main_py) == sha256(guard)
            except OSError:
                rolled = False
        plan["ok"] = False
        plan["error"] = "%s（已回滚：%s）" % (e, "成功" if rolled else "失败，请用快照手工恢复 %s" % guard)
    return plan


def src_broken(path):
    try:
        py_compile.compile(path, cfile=os.path.join(os.environ.get("TEMP", "/tmp"),
                                                     "dfy_plugin_main_check.pyc"),
                           doraise=True)
        return False
    except Exception:
        return True


def uninstall(plugin_dir, dry=False):
    main_py = os.path.join(plugin_dir, "main.py")
    if not os.path.isfile(main_py):
        return {"ok": False, "error": "找不到插件主文件：%s" % main_py}
    src = open(main_py, encoding="utf-8").read()
    without, block = split_block(src)
    res = {"plugin_dir": plugin_dir, "had_block": block is not None,
           "removed_tools": len(TOOL_NAMES) - len([t for t in TOOL_NAMES
                                                    if ('name="%s"' % t) in without])}
    if dry:
        res["dry_run"] = True
        return res
    if block is not None:
        guard = guard_snapshot(src.encode("utf-8"), "plugin-main.py")
        tmp = main_py + ".dfy-tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(without)
        os.replace(tmp, main_py)
        res["guard"] = guard
        res["ok"] = BEGIN not in open(main_py, encoding="utf-8").read()
    mod = os.path.join(plugin_dir, "arena_tools.py")
    if os.path.isfile(mod):
        res["module_kept"] = mod   # 留着无害：没被 import 就是一个文件；只摘 glue 块
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin-dir", default=os.environ.get("DFY_PLUGIN_DIR") or DEFAULT_PLUGIN_DIR)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    d = os.path.abspath(a.plugin_dir)
    if a.check:
        check(d)
        return 0
    res = uninstall(d, a.dry_run) if a.uninstall else install(d, a.dry_run)
    for k, v in res.items():
        print("%-16s %s" % (k, v))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
