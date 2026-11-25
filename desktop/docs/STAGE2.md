# 阶段 2a — 聊天闭环 + 群开关 + 工具轨迹

日期：2026-10-30（Asia/Tokyo）。范围来自设计文档 §九 的阶段 2 前半：
**已读、暂停/恢复、工具调用轨迹、群组启用/停用**。Arena 网关（任务/模型备注/工具权限/工作台 + 7 个
`dfy_arena_*`）是 2b，见文末。

## 1. 已读 / 暂停 / 恢复 / 唤醒 / 睡眠 —— 走插件，不碰 state.json

设计红线是「单一事实源」：`state.json` 只有插件能写。所以控制动作复用阶段 1b 已经验证过的
**原子文件队列 + 回执**机制，只多了一个 `control/` 子队列：

```
桌面 core  POST /v1/control {action, umo, args}
   └─写→ <plugin_data>/…/desktop_outbox/control/inbox/<HHMMSS>-<hex8>.json
插件 worker（同一条循环，1.2s）
   ├─ mark_read → store.mark_read(umo, through_seq)      返回 {"cleared": n}
   ├─ pause / resume → store.set_paused(bool)             返回 {"paused": bool}
   ├─ wake  → store.wake(umo, minutes, reason="desktop")
   ├─ sleep → store.sleep(umo, minutes, reason="desktop")
   └─移→ control/sent/（成功）或 control/failed/（失败），桌面 GET /v1/control/receipt?id= 轮询
```

要点：

- `control` **不受 `is_paused()` 限制**——否则「恢复」这条指令永远进不去（发送队列才需要被暂停挡住）。
- 白名单判定仍用插件自己的 `_is_allowed_umo`：桌面没有第二套规则，也就没法绕过它。
- 核心侧参数校验：未知动作 / 缺 umo / 未知会话 / 被排除的旧群 → 直接 400，不入队；
  `wake ≤ 1440 分钟`、`sleep ≤ 10080 分钟`。
- 可写路由从 1 条变成 4 条，`index.mjs` 与 `app.py` 各自持有一份同样的白名单，其余 POST 一律 405。

界面：消息页会话标题右侧三个按钮（标记已读 / 暂停自动回复 / 恢复自动回复），结果写成一句话提示；
暂停中会在标题栏挂 `自动回复已暂停` 红标（读的是 `global_paused`，不是本地猜的状态）。

## 2. 工具调用轨迹 —— 读 AstrBot 自己的记录

`data_v4.db` 里 `conversations.content` 本来就是完整消息列表，含 `tool_calls` 和 `role="tool"`
的结果；`preferences(scope='umo').sel_conv_id` 又能把 QQ 会话映射到对应的 conversation。
所以**不需要**新加一份 JSONL 轨迹日志，也就不会出现两份互相矛盾的历史：

```
GET /v1/tools?umo=<umo>&limit=20
  → items: [{name, args(≤160 字), ok, result(≤260 字), duration_ms: null, call_id, index}]
    total_calls, by_name, conversation_id, note
```

- `ok` 的判据是插件返回文本是否以 `error:` 开头（插件就是这么报错的），不是猜测。
- 只显示工具名 / 参数摘要 / 结果摘要：**思维链与被注入的 system prompt 一律不出现**（自检里有断言）。
- AstrBot 没记每次调用的时间戳，所以 **耗时显示为「未记录」而不是编一个数**；同理，卡片挂在
  「本会话轨迹」区而不是硬贴到某条气泡下面——气泡与调用之间没有可靠的时间锚点，假装对齐比不画更糟。
- 全程 `mode=ro` 打开；库不存在时 `available:false` + 一句说明，不抛异常。
- 顺带 `model_usage()` 读 `provider_stats`（模型 / token / 状态 / 时长），是阶段 3「模型设置」页的事实源。

## 3. 群组启用 / 停用 —— 两层配置一起写，或都不写

一个群只有同时出现在

1. `astrbot/data/cmd_config.json` → `platform_settings.id_whitelist`
2. `astrbot/data/config/astrbot_plugin_dafeiyu_social_config.json` → `allowed_group_ids`

里才算真的启用；只写一层就是「大肥鱼看着在、但装死」的那种事故。`core/dfy_core/groups.py`：

- **两步确认**：第一次 POST 不带 `confirm` → 只回预览（当前两层各含不含、会改什么）；
  UI 弹出确认条，用户点「确认写入」才带 `confirm:"yes"` 重发。
- 「已经是启用状态 / 该群本来没启用 / 主人私聊不能被停用」**在要确认之前就判**，避免让用户确认一个空操作。
- 写前把两份文件都备份到 `.runtime/backups/groups-<时间戳>/`；写完**从磁盘重读校验**两层一致，
  不一致立刻从备份还原并返回 `rolled_back:true`。
- 只动 `id_whitelist` / `allowed_group_ids` 这两个键，其余键原样保留（`config_version` 等有断言）；
  解析不了的文件直接拒绝写入，不会顺手「修好」它。
- 停用只停回复，历史与 state.json 一个字都不删。
- 旧群 `<FORBIDDEN_GROUP>` 永久拒绝（红线），写它之前就被拦下并记 `group_refused` 审计事件。
- 返回 `restart_required:true`：AstrBot 需要重启/热加载才对下一条消息生效——**页面上明说，不假装立刻生效**。
  自动重启属于托盘那一步（阶段 3），这里不偷偷重启服务。
- 未启用的群默认不显示在列表里，勾选「显示未启用的群」才列出来；不做批量拉群。

## 4. 证据

离线（`scripts/selftest_stage2.py`，全部在临时 scratch 副本上跑，跑完核对生产文件哈希不变）：

```
STAGE2 SELFTEST (pass=57 fail=0)
```

覆盖：控制队列校验/入队/回执（含伪造插件回执）、损坏文件不炸 worker、轨迹解析（成功+失败两种、
截断、脱敏、无思维链、不编耗时）、缺库降级、群开关两层一致性、备份落地、预览不写字节、
坏配置拒写、`PRODUCTION UNTOUCHED`。

真实服务（一次性核心驱动同一批路由，生产数据）：

```
PASS  owner conversation present                 unread=0 seq=122 global_paused=False
PASS  trace route answers                        14 次调用 / 显示 8 条
      dfy_reply ok=True / dfy_person_get ok=True / dfy_people_search ok=True
PASS  no chain-of-thought in cards ; duration not fabricated
PASS  mark_read queued → plugin executed mark_read   回执 {"cleared": 0}
PASS  unread cleared in store
PASS  pause took effect（0.4s 可见）→ resume took effect（状态还原）
PASS  both control receipts sent ; receipts expose result shape
PASS  群开关预览只读：{"error":"该群已经是启用状态","changed":false}，两份配置文件哈希不变
      control/inbox 0 · control/sent 3 · control/failed 0
```

门禁：`tsc --noEmit` 0 错、`vite build ✓ 454ms`、`selftest_stage1 38/38`、`selftest_send 26/26`、
`accept_stage0 → verdict: CLEAN`（`group_result` 等事件进了审计，`lines containing the NapCat token or
webui reads: 0`）。

## 5. 生产改动与回滚

| 文件 | 变化 | 备份 |
| --- | --- | --- |
| `astrbot/data/plugins/astrbot_plugin_dafeiyu_social/desktop_outbox.py` | `82b07d87c641cac2 → e4c50a627ea6623e`（11894 B，新增 control 队列） | `.runtime/backups/desktop-outbox.py.20261030-211055` |
| AstrBot 进程 | 重启一次（预授权动作），旧 pid 14580 已退出，6185/6199/6099 恢复监听 | 与阶段 1b 的 `plugin-main.py.20261030-204728` 一起回滚 |
| `cmd_config.json` / 插件配置 / `state.json` | **未改**（只做预览与只读校验） | — |

回滚方法：把上表备份复制回原位 → 重启 AstrBot（`ops-dfy/start-astrbot-background.ps1`）。
`.runtime/intentional.json` 已登记（2 条），`baseline-production-20261030.json` 无需重基线。

## 6. 还没做（2b 及以后）

- Arena 网关：`~/ops-dfy/ctl/agent_ctl.py`（32,574 B）+ `model_notes.json` + `requests/` 是现有控制面，
  8790 端口已确认**没有**在听（不会去复活它）。要做的是把这 7 个能力
  （dispatch / tasks / models / set_model_note / exec / workbench_status / workbench_action）搬进桌面：
  核心侧起一个随机回环端口 + 每次启动换 token、只允许核心调用、高风险默认关闭且需限时授权、
  群与陌生人一律拒绝、每次调用记审计；插件侧接上 7 个 `dfy_arena_*`。
  工作台只允许检查/控制 `profile_3`。
- 「浏览器派活」在网关不可用时**置灰并写明原因**，不静默失败。
- 事件推送 `/v1/events`（SSE）仍未实现，现在靠 4~7s 轮询。
- 工具卡片与消息气泡的精确对齐：需要插件侧带时间戳的轨迹，等有真实需求再加，宁缺毋假。
