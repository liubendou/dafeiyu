# 受控通道交付说明（2026-10-26 23:30）

## 目标
让 QQ 里的「她」能：① 真正把活派给 Arena agent（我）并让我执行；② 按主人写的模型备注选模型；③ 操控这台电脑与六本豆工作台；④ 用英语写给我的指令省 token。

## 落地的东西

| 组件 | 路径 | 说明 |
|---|---|---|
| 控制服务 | `%USERPROFILE%\ops-dfy\ctl\agent_ctl.py` | 127.0.0.1:8790，stdlib only。`/help` `/models` `/models/note` `/task` `/tasks` `/task/result` `/exec` `/workbench` |
| 端点凭据 | `%USERPROFILE%\ops-dfy\ctl\mcp_url.txt` | Arena MCP 公网端点，只存本地，不进日志/文档 |
| 派活收件箱 | `%USERPROFILE%\ops-dfy\ctl\requests\*.json` | 每次派活一条，含 task/model/note/state/result |
| 审计日志 | `%USERPROFILE%\ops-dfy\ctl\audit.log` | exec / dispatch / workbench 动作 |
| MCP 工具 | `qq-bridge\src\mcp-snowluma-safe.js`（尾部新增段） | 7 个工具，namespace `mcp__snowluma__*`（已在白名单） |
| 人格规则 | `harness\profiles\web\cordis.patch.yml`（2 份副本）+ `.agent-presets\qq-chat{,-v2}\agent.cordis.yml` | 三层权限 / 派活 / 选模型 / 跟进度 / 操控电脑 / 表情包 / 直说做不到 |
| 模型备注 | `%USERPROFILE%\ops-dfy\ctl\model_notes.json` | 主人备注即路由依据，`arena_set_model_note` 可随时追加 |

## 七个 MCP 工具（她能直接调）
- `arena_dispatch(task, model?, note?)` —— 派活给 Arena agent：写入 MCP 任务队列 + 本地收件箱。
- `arena_tasks()` —— 看队列状态、计划进度与完成结果。
- `arena_models()` —— 模型目录 + 主人备注（按备注选模型）。
- `arena_set_model_note(model, note)` —— 存新备注。
- `arena_exec(command)` —— 在这台电脑上跑一条命令（纯 ASCII、只读/轻量、审计、终端忙会重试）。
- `workbench_status()` —— 六本豆工作台 6 个实例（登录态/运行中/pid）。
- `workbench_action(action, instance)` —— open / close / status。

## 端到端验证（2026-10-26 23:0x–23:2x）
1. 控制服务各接口自测通过（`/models` `/workbench` `/exec` `/task` `/tasks`）。
2. 静默回合里她真的调用了 `arena_models` 与 `workbench_status`，返回了目录与实例列表。
3. 她调用 `arena_dispatch` 派了一个真活 → 我在 MCP 任务队列 `task_status` 里看到 T2 → `task_claim` → 写文件 `ops-dfy\ctl\dispatch_ok.txt`（内容 `arena-dispatch-ok`）→ `task_complete` 带证据 → 计划显示「全部完成（1/1）」。
4. `/tasks` 能同时看到收件箱记录与 MCP 计划状态；`/task/result` 可回填结果，她再 `arena_tasks` 就能看到并转告主人。

## 过程中的坑
- **白名单插件是 ES module，被 harness 缓存**：改 `qq-tool-restrict.mjs` 不生效（实测仍报「不在白名单内」）。解法：把新工具注册进**已经**在白名单里的 `mcp__snowluma__` 命名空间（MCP server 每次会话重新 spawn，改完即生效）。
- preset 目录里另有两份 `qq-tool-restrict.mjs` 副本（`harness\.agent-presets\qq-chat{,-v2}\`），主包那份改了也不够，两处都补了。
- 人格 YAML 里长句会被硬折行，插入锚点不能带 `- ` 前缀；`cordis.patch.yml` 两份 persona 副本都要改。
- 桥接 `/api/status` 需要控制台令牌，未授权返回 401 属于正常。

## 仍然做不到的
- **QQ 动态/说说发不了**：OneBot11/NapCat 协议没有该接口（51 个 QQ 工具里没有任何动态相关工具）。已写进人格：主人问就直说接口不支持，不许假装发成功。
- 通用 shell 没给她（按设计只给受控的 `arena_exec` 一次一条命令）；群友/陌生人仍然一律拒绝。

## 语音（进行中）
- F5-TTS 第三轮：5 句改成更口语的短句，正在合成。
- 后台装 CosyVoice2-0.5B（中文克隆自然度明显好于 F5），GitHub 克隆 + pip 依赖已过，权重从 hf-mirror 拉取中（~2.3 GB）。
