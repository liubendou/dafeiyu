# 大肥鱼 QQ 机器人 · 交接说明

> 本文件为公开发布版，已移除本机路径、真实 QQ 号与群号。

## 一、这是什么

把 DSH（DeepSeek Harness）包装成 QQ 群/私聊里的「大肥鱼」人格机器人，由三个进程协作：

| 组件 | 路径 | 作用 |
|---|---|---|
| NapCat | `napcat\` | 用 QQ 小号登录，对外提供 OneBot11 协议（HTTP 3000 / WS 3001 / WebUI 6099） |
| qq-bridge | `qq-bridge\` | 核心桥接：收 OneBot 消息 → 调 DSH Web API（127.0.0.1:43129）→ 把 AI 回复发回 QQ；含人格注入、记忆、表情、定时任务、控制台 |
| DSH Desktop | 系统安装 | 提供模型能力；通过 `profiles\web\cordis.patch.yml` 挂载 MCP 工具 |

运行参数（`qq-bridge\config.json`）：

- 主人 QQ：`<OWNER_QQ>`；机器人小号：`<BOT_QQ>`
- 模式：`reserved2`（类人仿真：潜水/唤醒/表情/戳一戳）
- DSH：`http://127.0.0.1:43129`，provider `deepseek-account`，model `deepseek-flash`
- 白名单：`allow.private = [<OWNER_QQ>]`，群聊默认不放行

## 二、怎么启动 / 重启

```powershell
# 1) 启动 QQ（NapCat 注入版），-q 快速登录小号
cd $env:USERPROFILE\大肥鱼\napcat
.\launcher-user.bat -q <BOT_QQ>

# 2) 启动桥接（后台）
cd $env:USERPROFILE\大肥鱼\qq-bridge
Start-Process node -ArgumentList 'src/bridge.js' -WorkingDirectory $PWD -WindowStyle Hidden `
  -RedirectStandardOutput "state\detached.log" -RedirectStandardError "state\detached.err.log"
```

- 桥接控制台：http://127.0.0.1:3100
- NapCat WebUI：http://127.0.0.1:6099

## 三、目录速览

- `qq-bridge\src`：桥接主逻辑（消息路由、人格、记忆、表情、语音、图片搜索、控制台）
- `qq-bridge\roles`：人格卡（默认人格见 `roles/小鲸鱼.md`）
- `qq-bridge\plugins\qq-agent-presets`：DSH 代理预设补丁
- `qq-bridge\dsh`：DSH 侧 agent preset 配置
- `desktop\`：Electron 桌面端（`dfy_core` 负责拉起/守护各服务）
- `ops\`：看门狗、NapCat 重启、记忆备份等运维脚本

## 四、常见问题

- 桥接起不来：先确认 DSH Web API（127.0.0.1:43129）与 NapCat OneBot（3000/3001）都已就绪。
- QQ 掉线：`ops\restart-napcat.ps1` 重启 NapCat；看门狗会自动拉起。
- 记忆库：`ops\mem_backup.js` 备份 `state` 下的长期记忆数据库。
