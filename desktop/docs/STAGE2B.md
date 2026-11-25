# 阶段 2b-i：Arena 网关（桌面端接自己的控制台）

日期 2026-10-30 · 状态 **已上线并通过门禁**（本阶段只到 2b-i：内核 + 界面 + 自检；2b-ii 的插件侧 `dfy_arena_*` 工具待批）

## 这阶段到底要什么

设计文档 `docs/desktop-gui-design-20261030.md` §5.2/§6.4 要求：桌面 App 自己就是 Arena 控制台的 MCP 客户端，
**不再经 8790 那个中转监听**。旧控制面 `~/ops-dfy/ctl/agent_ctl.py` 的七件事全部搬进内核；
「主人备注」是路由依据（不是模型 ID），不确定的事一律报错而不是猜。

## 落地的东西

| 位置 | 内容 |
| --- | --- |
| `core/dfy_core/mcp.py` | 极简 JSON-RPC over HTTP 的 MCP 客户端：`initialize` → `tools/list` → `tools/call`；带 `mcp-session-id`；失败给原话 |
| `core/dfy_core/arena.py` | 7 个能力：`overview / models / set_note / route_preview / dispatch / tasks+task_detail / workbench / exec 门控` |
| `core/dfy_core/app.py` | 7 个只读 GET + 5 个受控 POST 路由；写路由仍走全局白名单（其余一律 405） |
| `src/main/supervisor.mjs` / `index.mjs` | 主进程代理；GET 全放行，POST 只允许那 5 条 |
| `src/renderer/pages/ArenaPage.tsx` | 四个 tab：概览 / 派单 / 模型 / 工作台；模型改成**下拉只列有备注的键**，没备注就明说 |
| `core/dfy_core/security.py` | `_scrub_text` + `add_scrub`：上游地址与其 `token=/key=/secret=/session*` 值进内存即登记，出界（UI/审计/日志/异常文本）一律被抹 |

自检：`scripts/selftest_arena.py` **123 项全过**（桩 MCP 服务器 + 全临时目录，绝不碰生产配置）。
其余三套 38 / 26 / 57 全过，`tsc --noEmit` 0 错，`vite build` ✓。

## 关键决定（都是"宁可拒绝也不猜"）

1. **上游地址只读一次**：`_load_endpoint()` 读完立刻 `add_scrub`；任何路由都不回显地址，只回显 `endpoint_sha8`。
2. **端点校验**：`https` 或 `http://回环`。生产机器上 `ops-dfy/ctl/mcp_url.txt` 里是
   `http://<非回环主机>`，被这条规则拒了 —— 这是 §6.4「不外 expose」在生效，不是 bug。
   要接上：把该文件换成回环地址或 https 地址（阶段 3 会在 GUI 里做掩码输入 + DPAPI 保存）。
3. **备注即路由**：`_note_lookup` 只接受精确/同大小写不敏感的唯一命中；两个键只差大小写 →
   「匹配到多个模型备注」直接报错。备注里没有的模型 → 报错，不回落到默认模型。
4. **派单留痕**：`plan_create{goal,tasks:[{title,acceptance}]}`，同时写本地台账
   `.runtime/arena/requests/<时间戳>-<slug>.json`，任务页读的是台账，不重复消耗上游。
5. **工作台只投影约定的那一个实例**（`profile_3`）。实测发现两件事，界面照实说：
   - `Start Workbench.vbs` 启动的是**工作台程序本身**（`pythonw app/workbench.py`），不是某个实例的浏览器；
   - `instances.json` **不记 pid**，所以「关闭」拒绝执行（不猜进程号），「运行中」返回 `null` 而不是假的 `false`，
     另外把 Firefox 的 `parent.lock` 仅作 `lock_present` 提示暴露（锁可能残留，不能当证据）。
6. **执行权限默认关**：`set_grant(≤30min, confirm=yes)` → `.runtime/arena-grant.json`，到期自删；
   红线正则（`rm -rf / del /s / rd /s / reg delete / diskpart / shutdown / netsh advfirewall / taskkill /im qq / setx / curl|sh / invoke-webrequest|iex`）
   在**已授权之后**再判一次，命中就拒绝且不落执行；输出统一走 `redact`。
7. `浏览器会话` 上游没有对应能力 → `unavailable:true`，界面灰掉并写明原因，不假装成功。

## 本阶段踩到的两个真 bug（都被自检抓到并修掉）

* `mcp.call_json` 返回 dict，被 `[:MAX_REPLY]` 切片 → `KeyError: slice(...)`；截断和抹密要分在不同函数。
* 上游文本回复是 `ensure_ascii=True` 的 `\uXXXX`；显示前按解析结果重新 `ensure_ascii=False` 输出，结构体保留在 `data`。

## 未做（要一句新的"好"）

* 真实派单一次（会写进你自己 Arena 面板）；把某个群的白名单真改一条。
* 2b-ii：插件侧 7 个 `dfy_arena_*` 工具 —— **已完成并装进生产插件、AstrBot 已重启注册**，见下面「2b-ii」一节末尾的安装记录。`browser_*` 仍不做：上游没有对应能力，灰着说明原因。

---

# 2b-ii：插件侧 7 个 Arena 工具（设计文档 §5.4 / §6.4 / §6.5）

日期 2026-10-31 · 状态 **代码完成、真机门禁全绿、已装进生产插件并重启加载**（机主点头之后做完的，记录在本节最后）。

## 接线方式

设计里写死了路线：**聊天工具 → 本地核心 → Arena 网关**，并且「服务间调用使用独立 token，不共用」。插件那侧原来完全不跟核心说话（`desktop_outbox.py` 只读自己的队列目录），所以这次新增的是一条**服务通道**而不是复用 GUI 的钥匙：

| 文件 | 作用 |
| --- | --- |
| `core/dfy_core/link.py` | 每次启动单独铸一枚 64 位十六进制服务 token，写 `.runtime/core-link.json`（`port/pid/routes/started_at`），**核心一退出就删**；只解锁 6 条 arena 路由 |
| `core/dfy_core/app.py` | `_auth(path)` 双轨鉴权：GUI token 全开；服务 token 只能打 `link.PLUGIN_ROUTES`，别的一律 403 `plugin_route`，并且**每次调用写一条 `plugin_call` 审计**（只记路由和方向，不记正文、不记 token） |
| `plugin/arena_tools.py` | 7 个工具的全部逻辑：权限关（非主人私聊直接拒，**在发请求之前**）、参数校验、只打 `127.0.0.1`、错误一律成人话、超长结果截断并指回窗口 |
| `scripts/install_arena_tools.py` | 装 / 查 / 卸：护栏快照 → 原子写 → 读回比对 + 语法自检 → 不对就自己退回快照；`--check` 与 `--dry-run` 只读 |

`/v1/arena/grant` **故意不在**那 6 条里：拿着服务 token 也开不了 `arena_exec`，授权只能在窗口里点。

7 个工具：`dfy_arena_dispatch`、`dfy_arena_tasks`、`dfy_arena_models`、`dfy_arena_set_model_note`、`dfy_arena_exec`、`dfy_workbench_status`、`dfy_workbench_action`。

## 这一轮抓出来的三件事

1. **`send` / `stage1` 自测会把东西写进生产 `.runtime`**（没设 `DFY_RUNTIME_DIR`）。以前只是审计变长；`core-link.json` 一上线就暴露成**僵尸指针文件**——现场读到过一次指向死端口 54252 的残留。两条自测现在都隔离 runtime，accept 会认出僵尸文件并清掉。
2. **少传参数会绕过权限判断**：`dispatch(task)` 这类必填位置参数，模型少给一个就先进了 `TypeError` 分支，压根没走到「仅主人私聊」这一关。改成所有参数带默认值，判断永远先过权限；自测 B 段专门验「拒绝时一个请求都没发出去」。
3. **幂等安装不该再留一份快照**：第二次跑 `install()` 因为重建文本的换行差异被判定「有变化」，白写一次文件还多一份一样内容的快照。改成**原地替换托管块**（块外字节不动），内容与组件都一致时直接 `changed:false` 不落盘。

## 门禁与验收（真机，job `e0093a91`）

`GATE OK / GATE_RC=0`：`stage1 38/0 · send 26/0 · stage2 57/0 · arena 123/0 · stage3 112/0 · stage4 158/0 · watchdog 10/0 · **arena_tools 57/0**`；`tsc`/`vite` 通过，出包 `dafeiyu-app-0.4.0-light.zip` 459 940 B / 72 个文件，`release.verify ok=True files=77`；**accept 67 PASS · 0 FAIL · 6 NA**（第 10 节是这次新增的 15 条）。

`arena_tools` 57 条里值得点名的：A 段起真核心验指针文件的形状与生命周期（`routes=6`、token 64 位、退出即删）、服务 token 打 `/v1/status`/`/v1/credentials`/`/v1/arena/grant` 全部 403、审计里有 `plugin_call` 但没有 token 本体；B 段用桩核心跑 7 个工具（拒绝零请求、只带服务头不带 GUI 头、`host` 被写成外网也只打 `127.0.0.1`）；C 段在仿制插件文件上跑完「装 → 幂等 → 写坏自动回滚 → 卸」，并对**真**插件 `main.py` 做只读读检：拼上托管块后非空行一字不差、语法过、7 个 `async def` 确实落在 `DafeiyuSocial` 类里。

accept 第 10 节的现场证据：`LINK_FILE=fresh port=54846 pid=18832 routes=6 connect=True token_len=64`；同一个 token 读凭据 → `403 plugin_route`；`/v1/arena/grant` → 403；两枚 token 不互换（拿 GUI token 冒充服务头也拒）；生产审计 8 413 行 `token=` / `sk-` / `Set-Cookie` **0 命中**；轻量包自带 `plugin/arena_tools.py` 与 `scripts/install_arena_tools.py`（缺任何一个 `verify` 就拒包）。

## 装与不装，一句话的事

```
python scripts\install_arena_tools.py --dry-run     # 只看计划，一个字都不写
python scripts\install_arena_tools.py                # 装：护栏快照 + 读回比对 + 语法自检，失败自动回滚
python scripts\install_arena_tools.py --check         # 装得对不对
```
装完必须重启 AstrBot 才会注册这 7 个工具（工具是启动时挂上去的）。卸载只摘托管块，插件其他内容不动。

## 装进生产插件（2026-10-31 11:28，机主点头之后）

顺序是：整目录归档 → `--dry-run` → 安装 → `--check` → 读回生产文件 → **重启一次 AstrBot** → 用生产目录里那份代码打活核心。

| 步骤 | 机器上读回来的 |
| --- | --- |
| 归档 | `plugin-20261031-112856.zip`：15 个文件 / 76 720 B，含 `main.py`（`.runtime/backups/` 里） |
| 安装护栏 | `guard=...\.runtime\backups\plugin-main.py.20261031-112857`，`module_verified=True verified=True` |
| 安装结果 | `ok=True glue=inserted main_bytes 33605→38423 module_changes=True` |
| `--check` | `main.py True (block=True, tools=7/7)`、`arena_tools sha8 9e2fea59 == 期望`、结论 **已装且一致** |
| 生产文件读回 | 895 行 / 38 423 B，`@filter.llm_tool` 装饰器 **7 个**（`dfy_arena` 5 + `dfy_workbench` 2），托管块 **恰好 1 处**，AST 解析过 |
| AstrBot 日志 | `Added llm tool: dfy_arena_tasks / _models / _dispatch / _set_model_note / _exec / dfy_workbench_status / _action`（7 条），插件初始化 `owner=<OWNER_QQ>`，`desktop outbox started`，`WebUI is ready`，`aiocqhttp(OneBot v11) 适配器已连接`，新 pid 14580/16576，6185/6199 在听，无 Traceback |
| 活核心实测（只读） | 从生产插件目录 import 那份 `arena_tools.py`：`tasks`→「队里没有任务」、`models`→7 个模型带机主备注、`workbench_status`→实例 3（登录 有 / 进程 无） |
| 边界实测 | 非机主 `dispatch` → 「此操作仅主人私聊可用」；`exec` → HTTP 400「执行权限没开：先在 Arena 页的工具标签里限时授权」 |
| 审计 | 8491 行，`leak_hits=0`，`plugin_call` 4 条（只记 `path/method/via`，不记参数），服务 token 在审计里出现 **0** 次 |
| 没牵连到桌面核心 | 核心 18832 的父进程仍是窗口 19780，link 的 `pid/port` 与实际一致，`routes=6` |

两条要记住的：

1. **真人那条还没验**：机主在 QQ 私聊里让模型真调用一个工具，需要发真消息（授权已用完）。目前证明的是「工具注册上了 + 链路两端都通 + 边界拦得住」，不是「模型会正确使用它们」。
2. **运维脚本别把后台启动器的输出接进会缓冲的管道**：这次我写了 `start-astrbot-background.ps1 | Select-Object -Last 3`，被拉起的 AstrBot 继承了同一个 stdout 句柄，那条作业就一直挂着（要等 AstrBot 退出才结束），而 AstrBot 本身启动正常。以后要么直接调、要么用 `Start-Process -RedirectStandardOutput`。

装机之后又把整条门禁在真机重跑了一遍（`gate.py all` → `GATE OK / GATE_RC=0`），数字比上面那轮各进一格：`arena 124/0`（多一条：本测试的审计越不越界，见下）、`arena_tools 58/0`（多一条 C20：装过的插件再装应逐字节无事可做）、accept **68 PASS · 0 FAIL · 5 NA**（那条「生产插件还没装」的 NA 转成 PASS），包重建到 465 029 B / `verify ok=True files=77`。顺手修掉两处**因为真装上了而过期的断言**，都不是产品缺陷：

1. `selftest_arena.py` 原来要求生产审计里连 `arena_` 都不许出现——可 §6.5 要求的恰恰是「插件每次调用都在生产审计留痕」，两条互相打脸。改成只查自己：审计写到哪、本测试的私料有没有漏进生产。
2. `accept_stage4.py` 10.4 拿「装完的文件」和「原始文件」比非空行——装过之后原始文件本身已含托管块，比出来当然"多了一块"。改成两边都先剥块再比，并同时报告 `complete/up_to_date/module_sha`。

回滚口径：`plugin-main.py.20261031-112857` 是安装前的 `main.py`（33 605 B，sha256 前 8 位 `cc8e67bf`）；整目录还有那个 zip。`install_arena_tools.py --uninstall` 只摘托管块。
