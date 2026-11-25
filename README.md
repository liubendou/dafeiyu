# 大肥鱼（Dafeiyu）项目归档

把 DSH（DeepSeek Harness）包装成 QQ 群/私聊里的「大肥鱼」人格机器人，并配套一个桌面端启动器与运维脚本。

本仓库为归档版：仅保留自研源码与文档，已排除 `node_modules`、构建产物、模型权重、第三方克隆仓库、运行状态与全部本机敏感信息（本机路径、QQ 号、群号、令牌均已替换为占位符）。

## 目录结构

| 目录 | 说明 |
|---|---|
| `qq-bridge/` | 核心桥接（Node.js，MIT）：收 OneBot11 消息 → 调 DSH Web API → 回复 QQ；含人格注入、分层记忆、表情、控制台、权限分级 |
| `desktop/` | 大肥鱼桌面端（Electron）：一个图标、一个窗口，后台带起全部服务 |
| `ops/` | 运维脚本：看门狗、NapCat 重启、记忆备份、控制代理等 |
| `docs/` | 设计文档与脱敏后的交接说明 |

## 架构概览

```
QQ 客户端（小号）
   └─ NapCat（OneBot11 / WebUI）      ← 第三方组件，不在本仓库
        └─ qq-bridge（本仓库）
             ├─ 人格 / 记忆 / 表情 / 定时任务
             └─ DSH Web API（模型能力）
                  └─ desktop（Electron 启动器，本仓库）
```

## 运行前置

- Node.js 20+
- NapCat（QQ 协议端，请从其官方渠道获取）
- DSH（DeepSeek Harness）及可用模型账号

配置方式见 `qq-bridge/config.example.json`（复制为 `config.json` 后填写，`config.json` 已被 gitignore）。

## 说明

- `qq-bridge` 基于 [Derpyu520/qq-bridge](https://github.com/Derpyu520/qq-bridge) v0.2.0-r3 二次开发，遵循 MIT 协议。
- 仓库内所有 QQ 号、群号、本机路径、令牌均为占位符，不代表真实运行配置。
- 第三方组件（NapCat、SnowLuma、RVC/GPT-SoVITS/Applio/CosyVoice 等）不随本仓库分发，请遵守各自许可。
