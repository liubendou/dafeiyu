# 第四轮补充 —— 2026-10-26 晚：看门狗真 bug、toolResultPruner 重复注册、/compact 根因

## 1. 看门狗把 DSH 端口认错 → 机器人整体哑掉（已修，已实测）
- 现象：重启 DSH 后，桥接每 30 秒「连接 DSH 事件流… → 已结束（0 帧）」，DSH 调用一律 401「Pair your phone again」。
- 根因：`ops-dfy\watchdog.ps1` 把 DSH API 端口写死 43129；端口探测取「任一 DSH 进程的第一个非 43129 端口」，而 DSH Desktop 自己有桌面端端口 43127 → 把桌面端口当成 harness 端口写回 `config.json` → 桥接连到没人配对的实例。
- 修复：端口从 config.json 读；只认 harness 进程（`harness-node-entry`）的监听端口；已有 DSH 实例不重复启动；端口变了自动改配置并重启桥接（先删 stale bridge.lock）。
- 实测：重启后端口=43129、桥接 18:36:39 成功调 DSH 并发出 1 条消息、watchdog 不再重复拉起。

## 2. toolResultPruner 重复注册（已修，证据在日志）
- `profiles\web\cordis.patch.yml` 顶层 `- id: tool-result-pruner`（disabled: false）在根组合注册了一遍，三个 preset 的 per-agent 隔离组里又各注册一遍 → cordis 抛 `service "toolResultPruner" has been registered` → compaction 组整体挂载失败。
- 顶层改为 `disabled: true` 后，harness.log 里 `has been registered` 告警消失（最后一次是修复前的 10:15:23Z）。

## 3. /compact 仍然不存在（根因已定位，未修完）
- DSH 侧原话：`/ctx-handover: this session's preset exposes no /compact command, so no compaction backend is reachable.`（`dsh-switchman/host/context-watch.js:668`）。
- 机制（同文件 L369-377）：`/compact` 只在每个 preset 的 per-agent 隔离组里（`isolate: { compaction: true }`），根插件拿不到，switchman 靠 `ctx.commands.find(agent, "compact")` 借用。
- 试过并回滚：给组加 `commands`/`tokenMeter` realm（→ pruner 等待态）、把四件套拍平成官方 base 预设写法（→ 仍无命令）、组内补 token-meter（→ 重复注册）。
- 结论：卡在 cordis `isolate` 的服务可见性/注册语义，需要读 `@deepseek-ai/cordis` 的 isolate 实现；不做无依据改动。
- 用户侧临时办法：DSH 里新开一个会话（老会话上下文只增不减）。

## 4. 这一轮对其余事项的决策
| 事项 | 决策 | 理由 |
|---|---|---|
| 群聊是否也放开打断 | **保持现状** | 只有 @/点名/提问这类明确指向机器人的消息才打断；群里普通消息不该把观察等待打断，否则 AI 反复空转 |
| 摘掉 open-sea-skin | **不摘** | 它在 patch 里是 `disabled: true`，引擎找不到该条目只报一条 warning，不影响功能；摘它反而丢掉了「以后装了完整 harness 也要禁用它」的意图 |
| 给上游提 issue | **草稿已写，等你贴** | 机器上没有 GitHub 凭证（未装 gh、无 token），无法代提；issue 正文见 `第四轮_上游issue草稿.md` |
| DSH 升级 | **不升级** | 上游 rc.3 的 fs-local 与本机字节一致，没修 BigInt 那个 bug；换 asar 风险大于收益 |
| /compact | **未修完**，见上 | |
