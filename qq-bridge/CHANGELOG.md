# CHANGELOG

本仓库（qq-bridge-enhanced）基于上游 [Derpyu520/qq-bridge](https://github.com/Derpyu520/qq-bridge) `v0.2.0-r3`。
上游更早的版本历史见 [docs/guides/DSH_020_ADAPTATION.md](docs/guides/DSH_020_ADAPTATION.md) 与上游 Release 页面。

## 第五轮 —— 2026-10-26 晚：主人专属行为、思考等级降到最低、语音库就绪、记忆加固

### 思考等级降到最低
- 现象：每轮都按 `high` 思考，首句回复 2–3 秒起步，偶发 12 秒。
- 处理：`config.json` 的 `dsh.reasoningEffort` `high` → `off`（合法档位 off/minimal/low/medium/high/xhigh/max）。备份 `config.json.bak-20261026-4`。
- 验证：bridge.log 18:50:10 `已设置会话模型 … -> deepseek-official/deepseek-flash (off)`；实测多数消息 2–3 秒。

### 主人专属：安抚不冷暴力 + 哄到原谅 + 更主动 + 回得快
- 人格新增【主人专属 · 情绪、主动与响应速度】：只对 ownerQQ 私聊生效；察觉主人心情不好先接住情绪（不冷暴力、不敷衍、不讲道理）；主人明确说「好了/原谅你了」才算翻篇；主人没说「别主动找我」时可以主动聊；主人消息优先尽快给首句。
- 主动消息参数：`socialV2.proactive` 间隔 30–90 分钟 → 10–30 分钟，空闲阈值 15 分钟 → 5 分钟，概率 0.3 → 0.6。
- 生效位置：`profiles\web\cordis.patch.yml`（真正生效的两块）+ 两个生成出来的 preset 文件；生成文件是产物，patch 才是源（这轮踩过：只改生成文件完全没反应）。

### 语音库就绪（原来工具在、库是空的）
- `socialV2.voice.enabled` 与 `socialV2.tools.sendVoice` 显式打开；人格里「【发语音（默认没开）】」改成「【发语音（语音库已就绪）】」，否则模型会认为语音没开而拒绝发。
- 语音库 `qq-bridge\audio\`：01-greet / 02-comfort / 03-soothe / 04-checkin / 05-goodnight 共 5 条（合成音；主人录自己的声音可直接替换同目录同名文件）。第二批音色已替换（见下节）。
- 实测：`/api/socialV2/send-voice` 返回 `verified: true`、messageId 1261443355（ffprobe 缺失只影响时长校验，不影响发送）。

### 长期记忆加固
- 现状：长期记忆在 `state\long-memory.db`（SQLite）+ WAL；此前 WAL 积到 4,148,872 B 未落盘，崩溃/断电会丢最近写入。
- 处理：新增 `ops-dfy\mem_backup.js` —— `PRAGMA wal_checkpoint(TRUNCATE)` + 每日滚动备份（保留 14 份），并挂进看门狗每 2 分钟执行一次。
- 验证：WAL 4,148,872 B → 0 B，主库 94 KB → 131 KB，备份 `ops-dfy\memory-backups\long-memory-20261026-1848.db`。
- 记忆一共四层，全在磁盘上、重启不丢：长期记忆 SQLite、会话历史（DSH session log）、轻量记忆 `social-v2.json`、黑话库。

## 语音第二批音色 —— 2026-10-26 深夜：换掉难听的合成音

### 改动
- 主人嫌第一组合成音难听 → 在可选音色里选了第二个候选，重新生成 5 条语音并覆盖语音库。
- 语音库 `qq-bridge\audio\`：01-greet / 02-comfort / 03-soothe / 04-checkin / 05-goodnight 全部换成新音色，并按新文案重录（问候 / 安慰 / 安抚 / 主动问候 / 晚安）。
- 旧的一组没有删，挪到 `docs\runs\20261026-perf-fix\audio-v1\`：语音库目录里只留当前 5 条，AI 不会误选旧音。
- 生成方式：本地合成 → base64 上传 → 远端 node 解码写盘（`ops-tmp\decode_audio_v2.js`）；逐条校验 sha256 与本地一致、文件头为 ID3。
- 语音库目录在 `send-voice.mjs list-voice` 里显示为「共 5 个音频」（之前把备份放进去会变成 10 个）。

### 验证（5 条全部真实发到主人私聊，网关回读确认是语音段）
| 语音 | 大小 | messageId | 回读校验 |
|---|---|---|---|
| 01-greet | 42.2 KB | 1523065173 | ✅ 语音段确认 |
| 02-comfort | 37.4 KB | 790253149 | ✅ 语音段确认 |
| 03-soothe | 36.5 KB | 1274315821 | ✅ 语音段确认 |
| 04-checkin | 49.2 KB | 1554850467 | ✅ 语音段确认 |
| 05-goodnight | 32.0 KB | 1465927858 | ✅ 语音段确认 |

- 03-soothe 第一次被「每分钟最多 2 条」限流拦下，等过窗口重发成功 → 限流本身工作正常。
- `ffprobe` 仍未安装，只影响发送前的时长自检（不影响发送与回读校验）。

### 第三条路：接入微软神经语音（Edge TTS，机器本地直连）
- 这台机器没装 Edge，但可以直接用微软「阅读朗读」接口生成神经语音：已装 `msedge-tts`（npm，位于 `ops-dfy\tts\`），本机直连可用。
- 音色：zh-CN-XiaoxiaoNeural（晓晓·温柔女声）/ zh-CN-XiaoyiNeural（晓伊·年轻女声）/ zh-CN-YunxiNeural（云希·男声）/ zh-CN-YunyangNeural（云扬·播报腔）。
- 已把 4 条试听发到主人私聊，每条都会自报家门：messageId 1413116329（晓晓）/ 383015928（晓伊）/ 1818937964（云希）/ 609456354（云扬）。
- 试听样本留在 `ops-dfy\tts\samples\`，**没有**放进语音库 —— 语音库里仍只有当前 5 条，AI 不会误选试听音；主人选定音色后，用同一个音色重录 5 条并覆盖。
- 已知问题：接口偶发 `read ECONNRESET` 断连，同一条重试 2–3 次总能成功；这是微软的免费朗读接口，长期可用性不保证。

### 定版：语音库换成微软「晓晓」神经语音（2026-10-26 夜，当前生效）
- 主人试听 4 条微软神经音后选定 **zh-CN-XiaoxiaoNeural（晓晓·温柔女声）**；用同音色**重新生成** 5 条并覆盖语音库。
- 5 条大小：01-greet 60,768 B / 02-comfort 52,704 B / 03-soothe 50,976 B / 04-checkin 59,184 B / 05-goodnight 42,480 B；文件头 `fff364`（MPEG-1 Layer 3，无 ID3 标签），24 kHz / 48 kbps 单声道。
- 实测全部发到主人私聊、网关回读确认是语音段：messageId 373108111 / 860300634 / 1755978551 / 914872593 / 728049575。
- 历史版本归档：第一套合成音在 `docs\runs\20261026-perf-fix\audio-v1\`，第二批合成音在 `audio-v2\`，当前生效的这一套在 `ops-dfy\tts\final\`。
- 生成工具留在 `ops-dfy\tts\`：`edge_final_voice2.js`（重录脚本，改脚本里的 voice id 就能换音色）、`msedge-tts`（npm 依赖）、`samples\`（4 条试听）、`final\`（当前 5 条）。换音色 = 改 voice id → 重跑脚本 → 覆盖 `qq-bridge\audio\`，不需要重启桥接。
- 踩坑：微软免费朗读接口偶发 `read ECONNRESET` 断连，同一条重试 2–4 次总能成功；脚本已内置重试（最多 10 次、间隔 8 秒）。
- 踩坑：语音限流计数在**内存**里（`voiceSendTimes`，滚动 1 小时窗口；每分钟 2 条 / 每小时 10 条）。一小时里发满 10 条后连发被拒，等窗口过去要半小时，**重启桥接**即可清零 —— 本次已重启（杀 bridge.js + 删 state\bridge.lock，看门狗 ~66 秒自愈，PID 43232/45564 → 34832）。限流配置**没有**改，仍是每分钟 2 / 每小时 10。

### 待主人决定
- 已定版：微软「晓晓」神经语音（当前生效的 5 条就是它）。仍然难听的话：主人自己录 5 句同名覆盖（流程已验证），或者用归档在 `audio-v2\` 的那套合成音。
- 另一条路：接真正的 TTS（Windows 自带 SAPI / Edge TTS），音色更自然，但要装依赖，先不动。

## 第四轮修复 —— 2026-10-26 晚（看门狗真 bug + /compact 根因定位）

### 修复：看门狗把 DSH 端口认错，导致机器人整体哑掉
- 现象：重启 DSH 后，桥接日志每 30 秒「连接 DSH 事件流… → 已结束（本次收到 0 帧）」，任何 DSH 调用返回 401「Pair your phone again」，机器人无法调用模型。
- 原因：`ops-dfy\watchdog.ps1` 把 DSH 的 API 端口写死成 43129，且端口探测取「任一 DSH 进程的第一个非 43129 监听端口」——DSH Desktop 自己还有一个桌面端端口（43127），脚本把桌面端端口当成 harness 端口写回 `config.json` 的 `dsh.baseUrl`，桥接于是连到一个没人配对的实例。
- 处理：① 端口改为从 `config.json` 的 `dsh.baseUrl` 读取；② 只认 harness 进程（命令行含 `harness-node-entry`）监听的端口；③ 已有 DSH 实例时不再重复启动；④ 端口变了自动改写 config.json 并重启桥接（先删 stale `state\bridge.lock`）。
- 验证：重启 DSH 后 `config.json` 端口 = 43129（harness 真端口），桥接 18:36:39 成功调用「已设置会话模型」并发出 1 条消息；watchdog.log 显示「DSH still on 43129」，不再重复拉起。
- 回滚：改动前的四个 DSH 组合文件在 `ops-dfy\backup-compact-20261026\`；看门狗旧逻辑即本节「原因」所述。

### 修复：toolResultPruner 重复注册（compaction 组整体挂载失败的直接原因）
- 现象：DSH 日志 `warn agent-preset-registry: agent preset qq-chat-v2: tool-result-pruner (...): service "toolResultPruner" has been registered`。
- 原因：`profiles\web\cordis.patch.yml` 顶层有一个 `- id: tool-result-pruner`（disabled: false）在根组合注册了一遍，而三个 preset 的 per-agent 隔离组里又各注册一遍 → cordis 抛重复注册 → 整个 compaction 组挂载失败。
- 处理：顶层那条改为 `disabled: true`（每个 preset 组内已各有一份）。
- 验证：重启后 harness.log 里 `has been registered` 告警不再出现（最后一次出现在修复前的 10:15:23Z）。
- 回滚：该条改回 `disabled: false` 并重启 DSH。

### 已定位未修完：/compact 命令始终不存在
- 现象：DSH 侧报 `/ctx-handover: this session's preset exposes no /compact command, so no compaction backend is reachable.`（`dsh-switchman/host/context-watch.js:668`）。
- 机制（同文件 L369-377 注释）：`/compact` 只存在于**每个 preset 的 per-agent 隔离组**（`isolate: { compaction: true }`）里，根插件拿不到；switchman 用 `ctx.commands.find(agent, "compact")` 借用。
- 已排除：命令不在 `commands.list` ≠ 不存在（agent 作用域命令不出现在根注册表）；给隔离组加 `commands`/`tokenMeter` realm 会让 pruner 变成等待态；把四件套拍平成官方 base 预设的写法同样无效。以上试法均已回滚，当前配置 = 修复前状态 + 已修的重复注册。
- 结论：还差 cordis `isolate` 的语义细节（组内哪些服务可见/可注册），需读 `@deepseek-ai/cordis` 的 isolate 实现才能定，不做无依据的改动。
- 用户侧临时办法：DSH 里**新开一个会话**（上下文只增不减，老会话必然越聊越慢）。


## 运维修复 —— 2026-10-26（回复慢 / DSH 报错）

### 修复：每条消息固定多等 10 秒
- 现象：私聊每条消息 12–13 秒才回。逐段计时发现 `qq_send_message` 本身只花 0.2 秒，慢在 `qq_wait_for_messages` —— 消息到达后它还要再等一个「静默窗口」才把消息交给模型。
- 原因：`config.json` 的 `socialV2.wait.minQuietAfterNewMs = 10000` 是**硬下限**（`src/bridge.js` 里 `quietMs = max(minQuietAfterNewMs, 模型传入值)`，模型传 0 也会被抬到 10 秒），`defaultQuietMs = 8000` 又把默认值抬高。
- 处理：`minQuietAfterNewMs` 10000→2500、`defaultQuietMs` 8000→2500。防抢话保护保留：15 秒内连发 ≥3 条、或最后一句没说完，仍走 12 秒窗口。
- 验证：`state/qq-activity.log` 实测 16:52:26→16:52:30（4 s）、16:53:33→16:53:37（4 s）、16:53:59→16:54:04（5 s）；修复前同口径 12–13 s。
- 回滚：用 `qq-bridge/config.json.bak-20261026-1` 覆盖回 `config.json` 并重启桥接。

### 修复：每轮会话模型选择必然失败两次
- 现象：`state/bridge.log` 反复出现 `session.selectModel failed: session/model-unavailable: Select an available model before sending a message`（16:12 / 16:18 / 16:25 / 16:42 各两次）。
- 原因：`config.json` 里 `dsh.provider = deepseek-account`，但本机 DSH 的模型目录（`session.modelCatalog`）`routableProviders` 只有 `deepseek-official` 与 `deepseek-modlens`，**没有 deepseek-account**。
- 处理：`dsh.provider` 改为 `deepseek-official`（与 DSH profile 的 `agent-default-model` 一致）。
- 验证：修复后 `bridge.log` 出现 `已设置会话模型 session-… -> deepseek-official/deepseek-flash`，`model-unavailable` 计数归零。
- 回滚：同上备份。

### 修复：DSH 技能插件刷屏 + 用户自己的 skills 加载不了
- 现象：DSH `logs/harness.log` 里 `skill-registry: skill provider "filesystem" skipped: … Cannot mix BigInt and other types` 累计上万条（10-17 起）；DSH 里 `~/.dsh/skills`、`~/.agents/skills` 的 skills 加载不出来。
- 原因：DSH 0.2.1 列举 app.asar 内自带 skills 目录时抛 BigInt 错误，整个 skill provider 被跳过。
- 处理：DSH profile 的 `cordis.patch.yml` 给 `skill-filesystem` 指定 asar 之外的 `bundledSkillDir`（`%USERPROFILE%\.dsh\skills-empty`，新建空目录）；`%APPDATA%\dsh-desktop\restart-dsh.ps1` 启动前设置 `DSH_BUNDLED_SKILL_DIR`（供宿主组合实例使用，下次 DSH 启动生效）。
- 验证：改后 YAML 解析通过（js-yaml，含 `!!js` 自定义标签）；DSH 重启后不再连续刷屏。
- 回滚：还原 `cordis.patch.yml`（改前版本 `4c2bbcde…`）与 `restart-dsh.ps1`（改前版本 `1f67189b…`）。

### 调整：会话繁忙时的回复检查间隔
- `socialV2.autoReplyCheckMs` 30000→10000（原 30 秒，日志里的「30s/45s 后检查」），减少 busy 队列的额外等待。

### 说明
- **未改动任何代码**：`src/bridge.js`、`src/mcp-snowluma-safe.js`、DSH 插件源码全部原样，只改配置。
- 配置改写后做了结构比对：24 个顶层键一致，差异恰好只有上述 4 项，密钥类字段长度未变。

### 过程中的一次事故（如实记录）
- 16:55 重启 DSH 时 QQ/NapCat 客户端在同窗口掉线（无崩溃转储），约 2.5 分钟（16:55→16:57:41）消息进不来；用项目自带 `napcat/launcher-user.bat -q <BOT_QQ>` 拉起，桥接看门狗 16:57:41 自愈并重连 `ws://127.0.0.1:3001`。掉线原因未定位到确凿证据。

### 遗留建议（本轮未动）
- 开机无自启：注册表 Run 只有 `QQNT`（主号），bot 小号的 NapCat、桥接、DSH 都没有自启项 → 建议加开机脚本（DSH → NapCat → 桥接）。
- 建议加看门狗计划任务：检查 3001 端口，不通就拉 `launcher-user.bat`。
- `restart.bat` 只杀 `Name='node.exe'`，而当前桥接以 `DSH Desktop.exe`（ELECTRON_RUN_AS_NODE）运行 → 直接用会起**双实例**互相抢消息；本轮改用「按 PID + 按命令行匹配」重启。
- DSH 桌面版每次启动可能换端口，`config.dsh.baseUrl` 是写死的 → 建议做动态发现。


### 修复：每次唤醒固定先等 8 秒（批量窗口）
- 现象：私聊常规路径每条消息固定多等 8 秒。`scheduleWakeV2` 用 `batchWindowMs` 排一个定时器才真正唤醒（`src/bridge.js` L12420-12429），日志里的「计划唤醒 … 8s 后发送」就是它。
- 处理：`socialV2.wake.batchWindowMs` 8000→2000（仍能把连发几条合并成一次唤醒）。

### 修复：唤醒限流过紧
- `socialV2.wake.maxWakePerMinute` 1→4、`maxWakePerHour` 12→40。命中限流时不是丢弃而是**延后重试**，最多压 60 秒；按小时上限命中会压一小时，对私聊来说等于哑掉。

### 修复：5 分钟沉睡前观察会压住私聊
- 实测：`qq_wait_for_messages` 从 17:06:16 等到 17:10:30（4 分 14 秒），期间 17:06:12 的消息一直没回（`tool-calls.jsonl` + `qq-activity.log` 对齐）。原因是唤醒提示让模型用 `preSleepWaitMs` 做「沉睡前观察」，而观察窗口内会话被判忙。
- 处理：`socialV2.wake.preSleepWaitMs` 300000→60000，`socialV2.wait.maxMs` 600000→120000（任何长轮询最多 2 分钟）。

### 审计：DSH 本体（第三方程序）
- 技能插件刷屏的根因定位到 `app.asar` 内 `@deepseek-ai/dsh-fs-local/lib/index.js` L243/L258 `mode: Number(info.mode & 511n)`：asar 内路径下 Electron 垫片返回普通 Stats，与 BigInt 字面量按位与就抛 `Cannot mix BigInt and other types`（L270 包装成 FsError）。上游 rc.3 的该文件与本机**字节一致**，未修 → 只能用 `bundledSkillDir` 绕开。
- 逐个核对了 profile 里 16 个第三方插件声明的外部端点：`@liustack/modlens` 是模型 provider（私聊若被切过去就走第三方端点，桥接已强制 `deepseek-official`）、`@liustack/modsearch` 是搜索 provider、`open-sea-skin` 版本已过时（等于没装）、`dsh-switchman` 缺 /compact。审计时 DSH 对外连接全为 127.0.0.1。
- 纠正上一轮误报：harness.log 里的「401/429」是时间戳数字被误匹配，日志里没有真实鉴权失败或限流。

### 审计：项目自身代码
- 又找出三处真缺陷：批量窗口 8 秒、唤醒限流 1/分钟且 12/小时、沉睡前观察 5 分钟压住消息（均已修）。
- 仍未修（已在审计报告列出）：pendingWakeKey 泄漏可致 30 分钟静默、等待租约 15 分钟、`restart.bat` 只杀 node.exe 会起双实例、消息到达时无法打断正在进行的观察等待。


### 代码改动：消息到达时打断观察等待（真代码修改，已备份回滚点）
- 现象：AI 在「沉睡前观察」的长轮询里睡着时，你发的新消息要等到观察窗口结束才被看见（实测最长 5 分钟）。
- 改动：`src/bridge.js`
  - 新增 `activeWaitAborts`（按归一后的会话 key 记录正在进行的等待的打断器）。
  - `/api/socialV2/wait` 处理函数登记打断器：`abortWait` 只翻标志位，三个等待循环（reply 静默等待 / 新消息等待 / 到达后的静默窗）都加了 `!waitInterrupted` 判断，命中后立刻返回。
  - 被打断时也会把新消息交给模型（`newMessages` 带上 `arrived || waitInterrupted`），并在响应里返回 `interrupted` / `interruptedReason` 便于排查。
  - `scheduleWakeV2` 里对 `private/atMention/nameMention/question/poke/ownerRelay` 这些「对方直接开口」的原因，先调用 `abortActiveWaitsV2` 结束该会话正在进行的观察等待。
- 为什么不重复回复：等待被打断时返回的 `newMessages` 会经 `noteModelMessagesV2` 标记为已处理，回合结束后补发的唤醒会因「消息已不在未读里」被跳过 ✓。
- 验证：`node --check` 通过（备份 `src/bridge.js.bak-20261026-waitabort`，已归档到 `docs/runs/20261026-perf-fix/`），重启后真实对话中触发「打断观察等待」日志。

### 代码改动：pendingWakeKey 租约 30 分钟 → 5 分钟，并留日志
- `src/bridge.js` `armPendingWakeLease`：租约从 30 分钟缩短到 5 分钟（正常回合 1~3 分钟，等待上限已收到 2 分钟，5 分钟足够覆盖活着的回合），到期解除 busy 标记时打一行日志，以后再出现「它不理我」能直接定位到是哪个会话卡住。

### 修复：restart.bat 会起双实例
- 桥接现在是用 `DSH Desktop.exe`（ELECTRON_RUN_AS_NODE）跑的，而 `restart.bat` 只杀 `Name='node.exe'` → 旧实例没被杀掉，新实例再起一个，两个实例抢消息、互相覆盖 state。
- 现在按**命令行**匹配 `bridge.js`（排除 `mcp-*` / `node_modules` / `restart.bat` 自身），cmd 包装进程也一起收掉；文件保持 CRLF。

### 新增：开机自启 + 看门狗（计划任务）
- `%USERPROFILE%\ops-dfy\watchdog.ps1`（UTF-8 BOM，PARSE_OK）：每 2 分钟检查
  - OneBot WS 3001 不通 → 用 `napcat/launcher-user.bat -q <BOT_QQ>` 拉起 QQ/NapCat（今天实际掉过一次，2.5 分钟消息进不来）；
  - DSH 43129 不通 → 启动 DSH Desktop，并检测它这次用的端口；端口变了就改写 `config.json` 的 `dsh.baseUrl` 再重启桥接（否则机器人直接哑掉）；
  - 桥接 3100 不通 → 按与当前一致的启动方式重启桥接。
  - 带单实例锁，2 分钟内不会重复执行。
- 计划任务：`DafishuBotWatchdog`（每 2 分钟）+ `DafishuBotStartup`（登录时）。
- 实测：手动杀掉桥接后，17:30:03 看门狗发现并重启，17:30:04 桥接已重新连上 NapCat 与 DSH ✓。


- 补两处语义/触发点：`arrived` 在被打断时补成 true（否则模型把 `timeout=true` 读成「等了很久没人说话」，白白再等一轮）；控制台的「手动唤醒」也接上打断（操作者明确要唤醒，就不该被模型自己的长轮询拖着）。
- **确定性验证**：控制台令牌起一个 `timeoutMs=60000` 的等待，4 秒后调用 `/api/socialV2/wake` → 等待在 **4105 ms** 就返回，响应含 `"interrupted": true, "interruptedReason": "manualWake"` ✓（不打断的话它会等满 60 秒）。
- 真实对话里的路径（对方发消息 → `scheduleWakeV2` 的 `private` 原因 → 打断）用的是同一个 `abortActiveWaitsV2`，等下一次真实对话即可看到「打断观察等待」日志。


## v1.1.0 —— 2026-10-22

新功能版本：**定时任务**（按时区到点发消息或叫 AI 来说）、**定时静默**（按时间段让 AI 停下来省 token）；**切换 / 修改人格在二代仿真里真正生效**（以前要等会话重建）；切到 agent 时可以带上私聊长期记忆。
发布说明：[docs/release/RELEASE_NOTES_v1.1.0.md](docs/release/RELEASE_NOTES_v1.1.0.md)。

### 升级注意（从 v1.0.3）
- 沿用 `config.json`、`state/`、`interface-platform/state/`；执行 `npm ci`，重启控制中心（桥接和接口平台都要重启才会用上新代码）。
- **要重启一次 DSH**：新的 MCP 工具 `qq_schedule_*` 由 DSH 启动的 MCP 服务注册，只重启桥接时 AI 还看不到它们。preset 没有变化，不需要重跑 `setup-dsh.mjs`。
- 新增的状态文件（都在 `state/` 里、0600）：`scheduler.json`（定时任务）、`silence-schedule.json`（定时静默）、`role-seen.json`（各会话读到的角色卡版本，只存指纹）。没有旧数据格式变化。
- 升级后第一次唤醒，已有的二代会话会各收到一次当前角色卡（之前没有读卡记录），之后只在换卡时再给。

### 修复：二代仿真切换 / 修改人格后会话仍用旧角色卡
- 用户反馈：控制台切换人格后，群里 AI 的语气没变，像是「切换失败」。原因是二代仿真的会话只在建立时的引导唤醒里调用一次 `qq_get_prompt` 读完整角色卡，之后每次唤醒只有一行「【当前角色】名字」；切换、清除或修改当前人格都要等会话重建才生效。
- 现在桥接按「会话 + 角色卡指纹」记下每个会话读到的是哪一版卡（AI 调用 `qq_get_prompt`，或唤醒提示带着新卡被 DSH 接受时记下；只存指纹，不存内容）。
  指纹对不上时，下一次唤醒把新卡全文放在提示最前面，并说明旧卡作废；记忆、群规则和会话都不动。引导唤醒不重复给。
- 控制台「人格与角色 → 切换当前启用的人格」新增生效方式：**下次唤醒时重新读卡**（默认）/ **立即切换**（马上给还没读到新卡的会话各安排一次 `roleRefresh` 唤醒；已读到的跳过，定时静默中的等静默结束）。切换后显示有几个会话会读到新卡。
- 「编辑人格提示词」的保存按钮下方也有同样的生效方式（只对当前启用的人格起作用；编辑其它人格不影响任何会话）。两处选项各管各的按钮，两处都点也只会让每个会话读一次：已读到新卡的会话自动跳过，还在合并窗口里的读卡唤醒不会重复排。
- 升级后第一次唤醒，已有的二代会话会各收到一次当前角色卡（之前没有读卡记录）。
- 聊天模式 / 一代仿真每条消息都注入角色卡，本来就立即生效，不受影响。`roles/README.md` 里「下一条消息即生效」的说法按模式分开写清楚。
- 新模块 `src/role-refresh.js`；新测试 `scripts/test-role-refresh.mjs`。

### 新功能：定时静默（运行总览 → 静默开关）
- 按时间段自动让 AI 停下来省 token：多个时间段，各带开关；哪些天 = 每天 / 工作日 / 周末 / 自选（可跳过法定节假日）；跨午夜以开始那天为准；带 UTC 偏移的时区下拉。
  说明见 [docs/guides/SILENCE.md](docs/guides/SILENCE.md)。
- 「一键填入 DeepSeek 高峰时段」取自价目表（北京时间工作日 09:00–12:00、14:00–18:00，跳过节假日），不重复添加。
- 静默期间默认：**停止一切唤醒**（原来的手动静默只拦发送、AI 仍被定时唤醒去思考，照样花钱）、群消息照样记进未读、结束时给被直接找过的会话补一次唤醒、
  定时任务「叫 AI 来说」暂停（任务可单独设「静默时段内：照常执行」，预览会标出会跳过的那几次）、固定文字照发、管理员私聊不受影响（可勾选一起静默）。
- 手动开关优先；定时静默期间点「恢复正常」= 这一段临时恢复，下一段照常。`/api/status` 带上静默来源，卡片显示「静默中 · 定时（名称），到 HH:mm」。
- 静默开关卡片改成整行宽的普通布局（原来的紧凑两栏会把新面板挤在一栏里）。
- 定时任务页面的「群消息免打扰」改名为「定时任务免打扰」，并注明只拦定时任务发往群的消息、不影响 AI 平时被 @ 时回复。
- 新模块 `src/silence-schedule.js`；新测试 `scripts/test-silence-schedule.mjs`；控制台离线夹具接入真实的静默模块。

### 改进：切到 agent 时可以带上私聊长期记忆
- 用户反馈：切到 agent L2 后「没有 qq-chat-v2 的记忆」。交接原本只带最近的私聊往来；现在可以把二代仿真里 AI 在管理员私聊记下的话题 / 想说的话 / 印象一并交接给 agent（最多 1500 字，逐行脱敏；只取管理员私聊，不含任何群）。
- 开关在控制台「二代仿真 → 工具开关」的「切到 agent 时带上私聊长期记忆」（`socialV2.tools.handoverMemory`，默认开）。同一处补上了定时任务工具 `qq_schedule_*` 的开关。
- 文档补充：桥接重启时 agent 会话按安全规则回落归档；交接记录从升级到 v1.0.3 后才开始记。
- `test-mode-handover.mjs` 新增 ⑥。

### 新功能：定时任务（第一阶段）
- 桥接控制台「聊天行为」下新增「定时任务」页面：总开关、默认时区（出厂北京时间）、AI 登记开关、可改的安全限制（留空恢复默认）、任务列表（启用 / 试发 / 编辑 / 删除）、
  新建与编辑表单（实时预览接下来 5 次）、执行记录。说明见 [docs/guides/SCHEDULER.md](docs/guides/SCHEDULER.md)。
- 重复方式：一次性 / 每天 / 每周 / 工作日 / 每月几号 / 每月第几个周几 / 每年 / 特殊日期 / 每隔 N 天 / 每隔 N 分钟 / 每天随机几次 / 每月随机几次 / **高级：cron 表达式**（只能在控制台设置）；
  都可以加开始 / 结束日期、排除日期、跳过法定节假日、共执行几次后停止。随机时刻按「任务 + 日期」确定，重启不重抽。
- 内容：固定文字（桥接直接发；发给管理员时任何模式都能发）或叫 AI 来说（唤醒那个会话自己的 AI，提示里带上【定时任务】）。
- 发送对象：管理员私聊、白名单好友、白名单群，可多选。时区按任务计算，与机器时区无关；时区下拉列出全部时区并带当前 UTC 偏移（如「Asia/Shanghai（UTC+8，北京时间）」），时间文字也带偏移。
- 安全：总开关和限制只能在控制台 / 管理员命令里改；AI 只能在管理员私聊里用 `qq_schedule_*` 登记，默认只能发给管理员私聊，发往群的要开关并且先暂停待确认；
  登记后桥接另发确认；限额（任务数、每任务 / 每天 / 每群条数、最小间隔）、定时任务免打扰（只拦定时任务发往群的消息）、固定时间随机偏移、错过补发窗口；新建 / 修改 / 删除写入审计日志。
- 管理员私聊命令 `/remind`（桥接直接处理，不进模型）。
- 新模块 `src/scheduler/`（time / schedule / engine / commands / notes）；新 MCP 工具 `qq_schedule_create / list / update / delete`（工具开关 `socialV2.tools.schedule`，默认开）。
- 新测试 `scripts/test-scheduler.mjs`；控制台离线夹具接入真实的调度引擎（假发送）。
- 已知限制：「工作日」不含调休上班的周末（内置节假日表只有放假日）；农历日期、AI 没发出时的备用文字、导出 / 导入留到第二阶段。

## v1.0.3 —— 2026-10-21

在 v1.0.2 的基础上的修复版本：**切换 agent 等级不再失忆**；引用一条（较早的）合并转发时，AI 能看到并打开里面的内容，这条消息也不再卡 30 秒；普通图片不再被误当成表情。
发布说明：[docs/release/RELEASE_NOTES_v1.0.3.md](docs/release/RELEASE_NOTES_v1.0.3.md)。

### 升级注意（从 v1.0.2）
- 沿用 `config.json`、`state/`、`interface-platform/state/`；执行 `npm ci`，重启控制中心（桥接和接口平台都要重启才会用上新代码）。没有数据格式变化，不需要重跑 `setup-dsh.mjs`。

### 修复：切换 agent L1~L3 / 自动回落后 AI「失忆」（设计缺陷）
- 现象（用户反馈）：切到 agent 会自动回落到二代仿真，回落又把 DSH 会话归档，「一用 L2 就几乎失忆」。原因：每次模式 / 级别切换，桥接把**所有**会话退役归档再新建，
  连从没挂过工具的群聊会话也一起归档；新会话里也没有任何「刚才聊到哪了」。
- 新模块 `src/mode-handover.js`：
  - **聊天会话休眠复用**：进 closed-agent 时，非 closed-agent 建的、带明确 preset 的会话（qq-chat / qq-chat-v2）不归档，只停在途工作、撤销映射、轮换令牌，
    记进 `sessions.json` 的 `dormantSessions`；回到同一模式、同一 preset 时原样复用（DSH 侧已不存在则新建）。过期（`admin.dormantMaxMs`，默认 24 小时）、
    模式 / preset 不同、不再被允许、`/reset`、清空工作区时归档。群会话恢复后第一条消息带一句休眠提示。
  - **owner 私聊交接**：`state/admin/owner-transcript.json` 记 owner 私聊往来（最多 60 条，0600，路径 / 凭据打码；只记 owner 发的和模型真正发出去的，
    管理通道命令 / 确认码 / TOTP 本来就不交给模型、不会进来）。owner 会话因切换换新时注入最近往来，从休眠恢复时注入 agent 期间的往来，标明「不是新指令」；不含任何群聊原文。
  - 高权限会话不休眠：agent 会话回落、`/panic`、L1→L2 仍然停掉并归档。
  - 休眠保留「已引导 / 已看过的消息」：恢复后按正常原因唤醒，不再重发一遍首次引导（`【引导唤醒】`、初始提示词）、不把看过的消息重新喂一遍；休眠作废归档时才清，下一个新会话照常引导。（本机实测发现：恢复后曾多发一次引导唤醒。）
- 新配置 `admin.hibernateChatSessions`（默认 true）、`admin.dormantMaxMs`、`admin.handover`（默认 true）；文档 `ADMIN_CHANNEL.md` 新增「切换不失忆」。
- 新测试 `scripts/test-mode-handover.mjs`；测试夹具补注入 `role-card.js`（以前 agent 模式下 owner 私聊这条路径在夹具里跑不通）。

### 修复：引用合并转发时内容丢失、整条消息卡 30 秒
- 现象：群友引用一条（较早的）合并转发，AI 只看到「[引用消息]」，这条消息也晚了 30 秒才进未读。实测 macOS NapCat 4.18 读这种消息 `get_msg` / `get_forward_msg` 都要约 76 秒，而 SDK 和接口平台都只等 30 秒。
- 新模块 `src/quote-forward.js`：慢取缓存（后台长超时 120 秒、并发去重、成功缓存、失败短暂负缓存）与「最多等一会儿」。
- 收消息时引用解析最多等 4 秒（同一条消息里只解析一次，判断「是否引用机器人」复用结果），等不到先放带 id 的占位文字；原消息后台取到后缓存。
- 被引用消息里的合并转发 id 在归属校验通过后登记为本会话可见，并记到这条消息的 `forwardIds` 上；跨群 / 跨私聊的不登记。
- `qq_get_message_detail` 对加载中的 id 返回 `pending:true` 和「稍后再查」；工具说明同步更新。
- 读合并转发走缓存（30 分钟、最多 200 条），嵌套预览并行、各自最多等 30 秒，还在加载的标出来；MCP 端超时改为 180 秒。
- 发送引用时「加载中」不算确认目标归属（仍最多等 30 秒，和以前一样）。
- 接口平台：`get_msg` / `get_forward_msg` 用 `limits.slowReadTimeoutMs`（默认 120 秒），其它动作仍按 `requestTimeoutMs`（30 秒）。
- 新测试 `scripts/test-quote-forward.mjs`。

### 修复：文件名是 32 位哈希的普通图片被误当成表情
- 普通 QQ 图片的文件名本身就是 32 位哈希（`<32 位 HEX>.jpg`），旧的「从文件名推 emoji_id」规则会把它们当成表情：先去拼一个必然 404 的表情 CDN 地址，日志也错标成「表情获取失败」（实测：引用的合并转发里那张普通图片，`sub_type=0`）。
- 现在只有真的是表情才按表情处理：`kind=sticker`（mface 市场表情）、带 `emoji_id`、`sub_type=1`，或市场表情特有的「两位前缀-32 位 hex」文件名（前缀必须等于 id 的前两位，例如 `f9-f96b….gif`）。
- `test-media-search.mjs` 新增回归：哈希文件名的普通图片先走 get_image、失败时只试消息自带的链接、不拼表情 CDN；真表情的四种情况仍先走 CDN。

## v1.0.2 —— 2026-10-20

在 v1.0.1 的基础上：换 SnowLuma 目录（例如升级到 2.x）时，新目录缺数据会先提醒迁移；补充 SnowLuma WebUI 登录相关的常见问题；
修复 Windows 实机部署 v1.0.1 时发现的问题（Release ZIP 中文文件名乱码、重置会话清不掉等待租约等）；黑话研究未确认的 AI 推测显示在控制台；控制台可以选择 DSH 的提供方 / 模型 / 思考强度（GitHub issue #1）。
发布说明：[docs/release/RELEASE_NOTES_v1.0.2.md](docs/release/RELEASE_NOTES_v1.0.2.md)。

### 控制台选择 DSH 模型（提供方 / 模型 / 思考强度），保存即生效（GitHub issue #1）
- 「DSH 模型与思考强度」卡片改为提供方、模型、思考强度三个联动下拉，外加「看图能力」。选项全部来自 DSH 的模型目录（`session.modelCatalog`），不另维护清单，
  在 DSH 里配好的其它提供方桥接也能用；目录读不到时只显示当前配置和内置档位，并标注「目录不可用」。保存后对已有会话的下一条消息生效，不用重启桥接。
- 默认值不变：`deepseek-official` / `deepseek-flash` / `max`（DSH 桌面版 0.2.1 的 `deepseek-account` 同样按 DeepSeek 自家处理）。
- 服务端硬校验：提供方 / 模型不在目录里 → 400；档位不在该模型公布的列表里 → 400（附上支持的档位）；目录读不到 → 503 且不写 `config.json`。
  无档位的模型强度下拉置灰，`selectModel` 不带 `reasoningEffort`；配置里的档位该模型不支持时交给模型默认并记一条日志。
- **`off` 档放开**：档位以目录为准，选「关闭思考」时提示二代仿真的工具调用、读群判断和人格一致性可能变差。
- **不能看图的模型自动把图片改成文字**：目录不公布 `inputModalities`，所以看图能力用「已知表 + 未知」表达（`dsh.vision`：`auto` / `yes` / `no`）。
  `auto` 下只有核实过的 `deepseek-flash` 收图；其它模型的聊天图片、表情改成「[图片1：当前模型 … 不能看图，已省略]」（不下载图片），
  工具查询图片只返回文字，表情图 / 形象图对 AI 拒绝返回——避免整条消息报 `UNSUPPORTED_CONTENT` / `MODEL_DOES_NOT_SUPPORT_IMAGES`。
- **第三方提供方提示隐私**：选中非 DeepSeek 自家的提供方时提示「QQ 聊天内容会发给它处理」，第一次保存前再确认一次。
- 价格表只覆盖 DeepSeek 自家价目：其它模型（含第三方的同名模型）在下拉里标「价格未覆盖」，花费按 Flash 估算并标「估算」。
- 安全：`POST /api/dsh/model` 只有控制台能调（agent token 白名单本来就不含 `/api/dsh/*`），QQ 里的 AI 和私聊管理通道都改不了模型。
- 实现：新模块 `src/dsh-model.js`（目录 60 秒缓存与并发去重、校验、selectModel 参数、看图判断、提示文案、图片降级），`bridge.js` 只做接线；旧接口 `POST /api/dsh/effort` 保留并同样按目录校验。
- 新测试 `scripts/test-dsh-model.mjs`（模块 + 桥接接口端到端 + 图片降级）；`test-audit-bridge` 的 agent token 拒绝清单加上 `POST /api/dsh/model`；控制台离线夹具与 `test-console-ui` 改为新流程（夹具里的第三方提供方是虚构的）。
- 文档：`CONFIGURATION.md` 新增模型选择一节；`DSH_020_ADAPTATION.md` 更新 `off` 档与桌面版提供方名的说明。

### 黑话研究：未确认的 AI 推测显示在控制台
- 词条新增 `lastGuess`（`{ meaning, usage, example, at }`）：研究结果未确认时保存 AI 的推测，只给控制台看。旧词库没有这个字段，读入为 `null`，不需要迁移。
- 控制台「候选词-待研究」卡片显示「AI 推测（未确认，时间）」与「采用为含义」按钮：只把推测填进编辑框的空栏位，保存仍由管理员点，保存后词条仍是候选（移到「已研究」）。
- 安全：推测不写 meaning / usage / example，不算「已研究」，不能凭它确认；`buildSlangContext` 与给 AI 的 `publicSlangEntry` 都是字段白名单，不含 `lastGuess`；`GET /api/slang` 本来就拒绝 agent token；接口不接受外部写入 `lastGuess`；重新研究时也不把上次的推测喂回去。
- 研究确认、管理员填了含义、单条 / 批量确认时清空；再次研究仍未确认时覆盖为最新推测（没给推测则保留上一次）。
- 新测试 `scripts/test-slang-guess.mjs`；控制台离线夹具加一条带推测的演示候选。

### 修复（来自 Windows 实机部署 v1.0.1 的反馈）
- **Release ZIP 中文文件名在 Windows 上乱码、解压失败**（`roles/小鲸鱼.md`、`roles/傲娇助手.md`、`assets/deepseek娘.png`）：macOS 自带的 zip 不设「文件名是 UTF-8」标志，
  Windows 的解压按旧代码页解读。发布脚本改用自己的 ZIP 实现（`scripts/zip-utf8.mjs`，只依赖 Node 自带的 zlib）：每个条目都带 UTF-8 标志，`.command` / `.sh` 保留可执行位，
  三个系统生成的包一致，不再依赖系统的 zip / tar。v1.0.0、v1.0.1 的包都有这个问题。
- **黑话研究日志口径**：以前打印「已更新 N 条」用的是解析到的条数，未确认（不写库）的也算进去，管理端以为研究成功、词库却没变。
  现在分别报「写入 / 解析 / 未确认」，未确认的仍不写库，但把 AI 的推测（前 120 字）写进日志，方便判断该手填还是换关键词重研。
- **重置会话清不掉 `qq_wait_for_messages` 的等待租约**：控制台 / 控制中心的「重置会话」、QQ 里的 `/reset`、二代仿真全量重置都漏了清租约，
  新会话被旧租约挡住最长 15 分钟（「该会话已有一个等待中的 qq_wait_for_messages」）。现在三处都调用完整清理，归一后的会话 key 也一并清。
- **接口平台里默认生成的 SnowLuma 实例标红**（从 v0.4.x 升级时平台第一次启动，`snowluma-main` 既没有目录也没有令牌）：控制中心知道 SnowLuma 目录时自动替它补上，
  连接器按账号读令牌；用户自己配过令牌的实例不动。
- **两个测试在 Windows 上失败**：接口平台测试用 `URL.pathname` 当路径（Windows 上是 `/C:/…`）；生成 Linux 服务文件时用了平台相关的 `path.join`（Windows 上变成反斜杠）。
  都改为 `fileURLToPath` / `path.posix`，另外两处同样写法一并修正，并加回归测试禁止再用 `URL.pathname` 当路径。
- 部署文档：第 13 节补「换目录后必做」清单（先重置会话再删旧目录、重启 MCP、工具开关改后重置会话、带上 `interface-platform/state/`、SnowLuma 换目录走更新助手、不要双击旧目录的启动器），常见错误补四条。
- 新测试 `scripts/test-deploy-fixes.mjs`，`test-platform.mjs` 加 ⑧（自动补 SnowLuma 目录）。
- 测试夹具 `audit-bridge-harness.mjs` 的临时目录先取 realpath：macOS 默认临时目录是软链接，`test-audit-bridge` / `test-voice` 的语音库路径断言在真实 Mac 上会失败（只改测试，语音库的 symlink 防护不变）。

### 升级注意（从 v1.0.1）
- 沿用 `config.json`、`state/`、`interface-platform/state/`；执行 `npm ci`，重启控制中心。没有数据格式变化，不需要重跑 `setup-dsh.mjs`。

### 换 SnowLuma 目录前提醒迁移数据
- 在控制中心换到新的 SnowLuma 目录时，如果新目录里既没有账号配置（`config/onebot_<QQ号>.json`）也没有 WebUI 密码（`config/webui.json`），而旧目录有，**先不切换**，提醒用「更新助手」迁移：
  直接换过去 SnowLuma 会当成全新安装——生成新的随机 WebUI 密码（浏览器里保存的旧密码失效）、要重新扫码登录、重新开 OneBot WebSocket。确认框里点「去迁移（推荐）」会打开更新助手并填好新目录；坚持不迁移要再确认一次才切换。
- 背景：SnowLuma 1.14 与 2.x 的 `webui.json` 格式、密码规则完全相同，升级本身不会让密码失效；失效通常是新目录没带上旧的 `config/`（或旧版从没改过初始密码，每次启动都会重新生成）。

### 文档：SnowLuma WebUI 登录的常见问题
- 部署文档「常见错误」补三条：升级后旧密码登录不了（新目录没带上 `config/`，或初始密码从没改过）；
  输对密码也登不进、提示「登录尝试过多」（SnowLuma 同一地址 5 次输错锁 15 分钟，锁定期间正确密码也被拒，重启 SnowLuma 或等 15 分钟即解除；`localhost` 与 `127.0.0.1` 分开计数）；
  改密码提示「新密码不得与旧密码相同」（说明填的当前密码就是现在存着的密码，直接用它登录）。


在 v1.0.0 的基础上**增加 Linux 支持**（Windows、macOS 原有行为不变），适配 SnowLuma 2.x，DSH 探活更稳，macOS 第一次运行自动生成无窗口启动 App。
发布说明：[docs/release/RELEASE_NOTES_v1.0.1.md](docs/release/RELEASE_NOTES_v1.0.1.md)。

### 升级注意（从 v1.0.0）
- 沿用 `config.json`、`state/`、`interface-platform/state/`；执行 `npm ci`，重启控制中心（桥接、接口平台随之重启）。preset 没有变化，不需要重跑 `setup-dsh.mjs`。
- 用 SnowLuma 2.x 的 **Lite 版**需要本机 Node 24+；**完整版**自带 Node，不受影响。账号配置格式与 1.14 相同，可以用控制中心的「更新助手」把 `config/`、`data/` 迁移到新版本目录。
- Linux：`npm run linux-service -- --enable` 生成并启动 systemd 用户服务（见部署文档 10.1）。

### macOS：第一次运行自动生成无窗口启动 App
- 第一次双击 `control-center.command` 时，如果仓库根目录还没有「QQ Bridge 控制中心.app」，会自动生成（同 `npm run mac-app`）并在访达里选中它；这一次仍在终端里运行，以后双击 App 即可。生成失败只提示、不影响启动；设 `QQ_BRIDGE_NO_MAC_APP=1` 可关闭。
- App 仍然不入库、不进 Release 包（下载来的未签名 App 会被 macOS 拦下，本机生成的不会）。

### Linux 支持（增量，Windows / macOS 行为不变）
- **SnowLuma 2.x**（2.0 起正式支持 Linux x64 / arm64；账号配置的位置与格式与 1.14 相同，接口平台的账号发现和桥接的令牌自愈不用改）：
  控制中心识别 Windows（`launcher.bat`、自带 `node.exe`）与 Linux（`launcher.sh`、自带 `./node`）两种发行包；Linux 用 `bash ./launcher.sh` 启动。
  读 `package.json` 的 `engines.node`（2.x 是 `>=24`）与 `release.json` 的目标平台：Lite 版本机 Node 不够、或发行包与本机平台 / 架构不符时，不启动并写明原因。
  检查更新按本机系统与架构建议完整版（`win-x64.zip` / `linux-x64.tar.gz` / `linux-arm64.tar.gz`）。
- 控制中心页面：Linux 上也列「本机 SnowLuma 程序」、可「开启 SnowLuma」；Node 要求按发行包显示。
- Linux 的「浏览…」：有 zenity 用 zenity，否则 kdialog；都没有（服务器）时手动填路径。
- **NapCat 在 Linux**：启动文件可选 `.sh` 脚本（用 bash 运行）或可执行文件；版本从启动文件旁的 `package.json` 读；更新提示 Linux 安装方式。
- **Linux 无窗口启动**：`npm run linux-service`（`-- --enable` 直接启用）生成 systemd 用户服务（`--no-open`、崩溃自动重启、`KillMode=mixed` 先让控制中心按顺序停子进程）与桌面启动项。
- 新测试 `scripts/test-linux-support.mjs`（含 Windows / macOS 原有行为不变的断言）。

### DSH 探活更稳：不再「失败一次就报不可用」
- 桥接每 5 秒探活一次 DSH；现在**连续 2 次**（约 10 秒）失败才判定「DSH 不可用」并让 QQ 消息入队。DSH 很忙时偶尔一次没及时回应（例如本机满负载渲染时），不再刷出误报；偶发失败一次只在恢复时记一笔「DSH 探活偶发失败 1 次（原因），已恢复」。
- 端口重新识别不受影响：第一次失败就开始（DSH 0.2.1 换端口后照样很快跟上）。
- 日志写明失败次数和原因：超时 / 连接被拒（DSH 没在运行或换了端口）/ 连接被中断 / 鉴权失败 / HTTP 状态 / 其他。新模块 `src/lib/dsh-health.js`，测试 `scripts/test-dsh-health.mjs`。

## v1.0.0 —— 2026-10-19

第一个正式版本：支持 **Windows 与 macOS**；新增**接口平台**（桥接经它接入 QQ 后端，可同时接 SnowLuma 与 NapCat）；
控制中心统一管理桥接、接口平台与 QQ 后端（两边都有无窗口启动入口）；兼容 DSH 0.2.0-rc.2 与 0.2.1；增量合入上游 r4 的修复。
发布说明：[docs/release/RELEASE_NOTES_v1.0.0.md](docs/release/RELEASE_NOTES_v1.0.0.md)。

### 升级注意（从 v0.4.2）
- 沿用 `config.json` 与 `state/`；执行 `npm ci`，**重跑 `node scripts/setup-dsh.mjs` 并重启 DSH**（preset 去掉了 `{{cwd}}`，DSH 0.2.1 需要）。
- 想经接口平台收发：控制中心 → 接口平台 → 开启后端 →「设置 → 接入桥接」→ 重启桥接。不接入时桥接照旧直连 SnowLuma。
- macOS：`npm run mac-app` 生成无窗口启动 App；第一次读 NapCat 配置时允许「访问其他 App 的数据」。
- 更新 / 备份时把 `interface-platform/state/`（接口平台的实例配置与令牌）一起带上。
- 接口平台的版本号与项目统一（1.0.0），只在 `interface-platform/package.json` 一处维护；`test-platform` 检查两者一致。英文 README 同步更新。
- 发布包（`npm run release:zip`）默认输出到仓库外、与仓库同级的 `release/` 文件夹（历次版本都放在这里，不占仓库目录）；`--out` 可改。

### macOS 无窗口启动、NapCat 扫码、日志用词
- 新增 `npm run mac-app`：在本机生成「QQ Bridge 控制中心.app」（对应 Windows 的 `control-center-hidden.vbs`）：没有终端窗口和 Dock 图标，后台启动控制中心并打开页面；再次双击重新打开页面；页面上退出控制中心后 App 随之退出。生成的 .app 不入库。
- NapCat 登录二维码直接显示在控制中心：NapCat 由控制中心启动、未登录且生成了新二维码时，开启卡提示、程序区显示二维码（`GET /api/napcat/qrcode`，需令牌、文件位置固定、只给 PNG），登录后自动消失。NapCat 日志里同时插入二维码小图，换新后旧图标「已过期」；下载日志不含图片数据。
- NapCat 程序区更好找：开启卡上加「管理 NapCat 程序 →」；接口平台没运行时实例列表里也有「本机 NapCat 程序」。
- 桥接日志按实际后端称呼：经接口平台时写「QQ 后端（经接口平台 · NapCat 4.18.38）已连接」，不再一律写「SnowLuma 已连接」（`src/lib/qq-backend-label.js`）。
- 离线测试：`test-qq-backend-label.mjs`、`test-mac-app.mjs`，`test-napcat-manage.mjs` 补二维码。

### NapCat 管理：版本、检查更新、目录与启动文件
- 控制中心的 NapCat 程序区显示版本（运行中由 NapCat 经 `get_version_info` 自报；Windows 也可从安装文件读）、「检查更新」（只读 GitHub 最新 Release，只提示不下载）、「打开下载页」（macOS → Mac 安装器）。
- 新增「NapCat 设置」：QQ.app（macOS）/ 启动文件（Windows）、NapCat 目录、快速登录 QQ 号，带校验；改目录同步给接口平台的 NapCat 实例。不用再手改 `state/control-center.json`。
- 「浏览…」改用系统原生窗口模块 `src/launcher/pick-dialog.mjs`：macOS 用 osascript，Windows 用 PowerShell；标题与路径经 argv / 环境变量传入，不拼进脚本。只接受固定用途（`/api/pick`）。SnowLuma 的「浏览…」在 macOS 上也能用。
- 接口平台：链路连上后调用一次 `get_version_info`，在链路快照里给出 `backend: { appName, appVersion, protocolVersion }`。
- 离线测试 `scripts/test-napcat-manage.mjs`（接入 test:audit）。

### 接口平台并入仓库
- `interface-platform/` 从与桥接平行的目录搬进本仓库，作为独立子程序（自己的 `package.json`、测试与 `state/`）；`state/` 不入库，发布包随 `git ls-files` 自动包含平台。
- 控制中心默认按 `<仓库>/interface-platform` 找平台，旧布局（同级 `../interface-platform`）仍然认；`platformDir` 与 `QQ_BRIDGE_PLATFORM_DIR` 不变。
- `test-platform.mjs` 找不到平台改为失败（不再跳过），新增 ⑥：桥接与平台互不导入对方的模块、`.gitignore` 排除平台 `state/`。
- 离线测试用 `QQ_BRIDGE_NAPCAT_HOME` 指向假目录，不再读真实 NapCat 目录（macOS 上那会弹「访问其他 App 的数据」并卡住测试）。`.DS_Store` 加入 `.gitignore`。

### 控制中心：状态栏与桥接控制台入口
- 底栏 DSH 指示以桥接自己的连通结论（dshReady）为准，不再因为控制中心没探到 DSH 0.2.1 的随机端口而显示「不可达」；总体徽章同步。
- 底栏「SnowLuma」改为「接入后端」：走接口平台时列出平台里各实例（如「NapCat 已连接」），桥接直连时才显示直连的 SnowLuma；版本行只在选了 SnowLuma 目录时显示其版本。
- 「打开桥接控制台」从顶栏移到桥接卡片（启动桥接与重启之间），点击时自动带上桥接控制台令牌（经控制中心鉴权的 POST 取得，控制中心页面不保存）；旁边保留「手动输入令牌」。
- 桥接控制台的令牌页会先用本机浏览器里上次保存的令牌自动试一次，失效或走手动入口时才弹窗。
- 桥接控制台黑话库：「候选词」拆成「候选词-待研究」和「候选词-已研究」两栏（有含义 / 用法 / 示例之一即为已研究，标签上显示条数）；只是页面分栏，数据仍是候选，确认 / 拒绝 / 研究接口不变。
- 新增 macOS 启动入口 `control-center.command`（双击在终端里打开）。从其他 App 里启动时，macOS 会以那个 App 的名义反复询问「访问其他 App 的数据」。
- 接口平台：账号目录**读不了**（权限 / macOS 隐私保护）不再被当成「账号从目录消失」而断开链路，保留现有链路并在实例上报 `discoveryError`；只有目录里真的没了才关。
  控制中心底栏「接入后端」改按真实链路（open）判断，读不到目录时显示原因。NapCat 自动登录设置的读取缓存 10 分钟，不再每次刷新都读沙盒容器。
- NapCat 卡片识别 NapCat 自己的自动登录设置（`config/webui.json` 的 `autoLoginAccount`，只读）。

### NapCat 下 AI 收藏表情
- NapCat 的 `add_custom_face` 只接受本地文件路径（`base64://` 会报 ENAMETOOLONG），回执也不含 emoji_id。现在 base64 被拒且网关识别为 NapCat 时，
  把桥接已校验的表情字节写进桥接自建的临时文件（随机名、0600，macOS 放在 QQ 沙盒容器的 tmp，用完即删）再交给它；按 MD5 在收藏列表里找到刚收藏的表情，
  备注改用 NapCat 的 `set_custom_face_desc`。SnowLuma 的行为不变；消息里的原始 URL / AI 给的路径仍然不会交给网关。临时目录可用 `socialV2.sticker.collect.tempDir` 指定。

### 兼容 DSH 桌面版 0.2.1-rc.2（同时保持兼容 0.2.0-rc.2）
- **preset 不再使用 `{{cwd}}`**：0.2.1 的提示词变量只剩 provider / model，`suffix: Your working directory is {{cwd}}.` 会让每一轮直接报错
  （`unknown prompt variable "{{cwd}}" in section "deployment:persona-suffix"`）。5 个 preset 都不再覆盖 suffix，改用当前 DSH 版本自带的默认后缀；已重新生成 bundle patch。
- **自动发现 DSH 随机端口**：0.2.1 桌面版每次启动监听一个随机回环端口（不再写死 19387）。`dsh.baseUrl` 留空时，固定端口连不上就查 DSH 桌面版进程的监听端口
  （macOS `lsof`，Windows `netstat` + `tasklist`），再用未登录 401 的特征文字确认是 DSH；DSH 不可用期间桥接每 10 秒重新识别，DSH 重启换端口后自动跟上。
- **macOS 识别桌面版**：Mac 上没有 `~/.dsh/dsh-runtimes`，改读 App 包 Info.plist 的版本号；之前 DSH 还没起来时会被误判成命令行版（3080）。
- Windows 上的 `netstat`/`tasklist` 发现路径只做了离线解析测试，尚未在 Windows 实机验证。

### 合入上游 Derpyu520/qq-bridge v0.2.0-r4（增量合并，本仓库的增强功能全部保留）
- **回合判忙租约**：回合只按「起始时间 + 15 分钟租约」判忙，turn/end 丢失或 DSH 重启丢回合时不再永久卡在「会话繁忙」（上游 #11/#19/#23/#26；本机 Mac 部署曾复现）。
  本仓库另在控制台 `/api/socialV2/state` 增加只读的 `busy` 诊断字段。
- **DSH 设置 user 层优先**：控制台切的模式不再被 5 秒轮询改回去（上游 #22）；DSH 设置页 ownerQQ 留空不再抹掉 config.json 的值。保留本仓库 v0.3.0 管理控制器的外部模式判定。
- **机器人身份写进唤醒提示词**（昵称 + QQ 号，上游 #29），不再把 @自己 当成 @别人；引用旧消息里的昵称不算被叫到。
- **会话初始提示词**：控制台为每个会话预写圈子主题/氛围（`state/initial-prompts.json`），只在首次唤醒注入；AI 读不到也改不了。
- **图片/表情链路**：safe-fetch 校验全部 DNS 结果 + IPv4 优先 + 逐地址切换（修 AAAA 先返回时每次白等 20 秒）；表情优先走 CDN、按 emoji_id/文件名推 gxh 地址兜底；
  落库保留 kind/emojiId/summary/subType。图片识别上限改为 `socialV2.media`（默认 8MB / 64MP / 5 张）。本仓库的表情上限 `socialV2.sticker.maxBytes`（10MB）照旧单独生效。
- **联网搜索**：新增 `src/web-search.js`，优先复用 DSH 的 DeepSeek 联网搜索，Bing RSS 兜底（配置 `webSearch`）。
- **稳定性**：`qq_wait_for_messages` 改走 `node:http` 长轮询（绕开 undici 300 秒 headersTimeout）、DSH 控制调用统一 15 秒超时、事件流重连指数退避；SnowLuma 对象形态的 error 拆出详情记日志。
- **体检修复**：`/api/send/*` 失败路径的 ReferenceError、MCP 对象 error 显示成 `[object Object]`、md-to-plain 的 ReDoS 与「2 * 3 * 4」被当斜体删掉、表情/黑话按码点截断不切坏 emoji、搜索空结果提示被缓存吞掉。
- 测试：移植上游 `test-audit-regressions`、`test-issue-regressions`、`test-media-search`、`test-v2-initial-prompt`，并接入 `test:audit`。
- 未合入：上游各包的版本号（本仓库保持自己的版本号）。

### 新增
- **接口平台 M1**（仓库内的独立子程序 `interface-platform/`，零依赖，Windows / macOS 通用；桥接与控制中心只经 HTTP 接口与它通信）：统一管理接入实例、账号、事件与发送，后端是可替换的连接器。
  本轮提供 QQ 的 SnowLuma 连接器（Windows）与 NapCat 连接器（macOS / Windows，连接用户自己安装的 NapCat；macOS 上已实机验证收发），都按目录自动发现每个账号；统一数据接口（`/v1`，SSE 事件流 + 带状态的发送请求）、
  管理接口（`/v1/admin`，与数据令牌分离）和 OneBot v11 兼容入口（过渡层：桥接只改配置即可经过平台）。见 interface-platform/README.md 与 interface-platform/docs/CONTRACT.md。
  **M1 不是完整交付**：Mac 原生 QQ（M3）、完整功能矩阵（M4）尚未实现；Windows 上经接口平台的实机链路尚未验收。
- **控制中心改为顶部大标签**：「桥接」「接口平台」两个标签，切换只改变显示、不启停任何模块；接口平台页有概览、接入实例、账号、日志、设置。
  SnowLuma 的目录选择、启停、更新助手移到「接口平台 → 接入实例」，仍然只由用户亲手点击。
- **管理员私聊跨群查看与群长期规则**（`socialV2.tools.ownerGroupAccess`，默认关闭）：管理员本人私聊的 AI 可以只读查看白名单群的消息和记忆（`qq_owner_peek_group`），并给群设长期规则（`qq_owner_group_rule`，由桥接写进群会话记忆、每次唤醒注入）；群里发言仍走托付，会话隔离不变。
- **`socialV2.sticker.maxBytes`**（默认 10MB，范围 256KB～40MB）：收藏 / 发送表情的单张大小上限单独可配；图片识别（含 `qq_get_sticker_image`）改用上游 r4 的 `socialV2.media.maxBytes`（默认 8MB）。
- **`snowluma.viaPlatform`**：「接入桥接」一键把桥接端点改为平台兼容入口（原值备份在 `state/platform-link-backup.json`，可「恢复直连」）；
  开启后 token 自愈不再扫描 SnowLuma 目录、不跟随发现到的端点，避免绕过平台直连。

## v0.4.2 —— 2026-10-17

### 新增
- **管理员私聊托付群会话**（`qq_ask_group_session`，默认关闭，开关 `socialV2.tools.ownerRelay`）：在私聊里让 AI「去某个群说两句」时，
  私聊会话不能直接往群里发（会话令牌只能发到自己的会话，以前会报「令牌不对」）。现在它可以把这件事托付给那个群**自己的**会话，
  由群会话看着群里的上下文自己组织语言发言；会话隔离不变。只有管理员私聊能托付，目标必须是白名单群，有限频、审计和私聊通知。
  见 docs/guides/CONFIGURATION.md。

### 修复
- 控制台「切换当前启用的人格」的下拉列表被折叠面板裁掉、看不到下面的人格：列表改为在页面里展开，面板跟着变高，人格多时可以滚动。

## v0.4.1 —— 2026-10-16

根据第一次真实部署（DSH 桌面版 + SnowLuma + 新 QQ 号）的报告修复。

### 修复
- **SnowLuma 握手被拒后永远连不上**：有的 SnowLuma 版本拒绝 WS 握手时只触发 `error`、不触发 `close`，
  SDK 的重连和桥接的 token 自愈都挂在 `close` 上，结果一直不连。现在 `error` 时（未连接）也触发自愈，
  并新增**连接看门狗**（`src/lib/ws-watchdog.mjs`）：未连接时按 10 秒起、最长 60 秒的退避重试，连上即停。
- **AI 出错时无从排查**：回合出错 / 消息未被接受时，原来只把一句话发到 QQ、不写日志，而且错误里带 credential 字样
  会被整条替换成「含敏感信息，已隐藏」。现在（`src/lib/agent-errors.mjs`）：日志写**错误码 + 脱敏后的详情**；
  QQ 上显示错误码（群聊等审计会话不显示原文）；缺凭据 / 余额不足 / 模型不可用这类**配置错误**私聊提醒 owner（30 分钟限频）。

### 改进：DSH 桌面版开箱即用
- `dsh.baseUrl` 默认留空 = **自动识别**：桌面版固定端口 `19387`，命令行版 `3080`（`src/lib/dsh-endpoint.mjs`）；
  桥接、控制中心状态栏、`probe:permission`、`self-test` 都用同一套识别。显式填写的地址照用（已有配置不受影响）。
- `setup-dsh.mjs` 自动识别 profile（桌面版 `desktop` / 命令行版 `web`），`dsh` 不在 PATH 时自动使用桌面版自带的 `dsh.cmd`
  安装 profile bundle。
- `control-center.bat` 找不到 Node.js 时给出提示并暂停；`control-center-hidden.vbs` 弹窗提示，不再静默失败。

### 控制台
- 「切换当前启用的人格」输入框：获得焦点时列出已有人格，输入时按**开头**过滤（不区分大小写），点击或 ↑↓ + 回车填入；仍可手动输入。

### 文档
- 部署指南：桌面版的 profile / 端口 / `dsh.cmd` 位置；新增「配置 DSH 模型凭据」一节（API Key → `deepseek-official`，
  账号登录 → `deepseek-account`）；装好或更换 Node 后要重跑 setup；DSH 沙箱可能不允许在工作区写可执行文件；
  `reasoningEffort` 的快慢取舍；「1006 正常」的说明补上 non-101 的情况；常见错误表新增三条。

### 升级注意
- 已经在 `config.json` 里写了 `dsh.baseUrl` 的保持不变即可；想用自动识别就改成 `""`。
- 新增离线测试 `test-snowluma-reconnect` / `test-agent-errors` / `test-dsh-endpoint`，`test:audit` 现为 40 个脚本。

## v0.4.0 —— 2026-10-16

### 新增：控制中心（docs/guides/CONTROL_CENTER.md）
- 一个只监听本机的网页（`control-center.bat` / `control-center-hidden.vbs` / `npm run control-center`，默认 `127.0.0.1:3099`），
  左侧是桥接的终端输出与固定命令集，右侧是 SnowLuma，底部是整体状态（桥接 / SnowLuma / QQ 账号与接收链路 / DSH / 模式级别 / 版本）。
- 桥接与 SnowLuma 作为控制中心的子进程运行：按钮启动、停止（桥接先保存状态再退出）、桥接崩溃自动重启（可关）；
  日志可过滤、只看错误、暂停、下载，显示前抹掉令牌与凭据，同时写入 `state/logs/`。
- SnowLuma：用 Windows 文件夹选择框选择你自己解压的目录（不放进项目），显示版本与完整版/Lite 版、已配置账号；
  **只在你点击时**按原样运行它自带的 `launcher.bat`；打开它的 WebUI（它禁止被嵌入，所以在新标签页打开）；
  检查 GitHub 上的最新版本；**更新助手**：你下载解压新版本后，备份并迁移 `config/`、`data/` 到新目录并切换（不下载、不替换它的程序文件）。
- 选择 SnowLuma 目录时自动同步 `config.json` 的 `snowluma.homeDir`。
- 桥接新增 `POST /api/admin/shutdown`（优雅退出，仅控制台令牌）；`/api/status` 增加 `botAccount` 与 `version`。

### 规则变更（经维护者确认）
- 允许**由用户亲手点击**控制中心按钮启动/停止 SnowLuma；仍然不自动启动、不下载、不安装、不修改 SnowLuma，
  桥接/MCP/AI 依旧没有任何启停 SnowLuma 的途径。`LICENSE` 第三方说明与 L6 测试已同步（新增 `scripts/test-control-center.mjs`）。
- `.gitignore` 与发布脚本排除 SnowLuma 目录与安装包。

### 文档
- 部署指南补充**桌面版 DSH** 的路径（版本检测、端口、bundle 安装的替代办法），仓库地址改为 h-D-ACE/dsh-qq，日常启动改为控制中心。

## v0.3.0 —— 2026-10-16

适配：DSH `@deepseek-ai/dsh@0.2.0-rc.2`（未变），SnowLuma ≥ 1.14.20（对照 1.14.22 源码），Node.js ≥ 22.13。

### 新增：人际连续性（docs/guides/CONTINUITY.md）
- 全局人物库 `state/social/people.json`：以 QQ 号为唯一主键，按群记录群名片/昵称及变更历史、成员关系、认识程度；
  同名不同号绝不合并，小号关联只能由管理员标注。
- 分层记忆：群内 / 私聊 / 全局三个作用域；共享策略 `none` / `identity`（默认）/ `shared`，群级可调严；
  **可见范围在注入时由程序过滤**，私聊来源记忆任何策略下都不外流；AI 申请的跨群共享需管理员批准。
- 二代唤醒提示词新增【在场人物】小节（只含本轮出现的人，有字数上限）。
- 成员事件：处理 `group_increase` / `group_decrease` / `group_card` / `group_recall`；入群按「陌生 / 别处认识 / 回归」分类，
  写入二代消息流并按配置唤醒（不强制欢迎）；事件去重；连上 SnowLuma 后成员名单对账（首次部署不把老成员当新人，补记断线期间的入退群）。
- 跨群受控查询（默认关闭，有向链接 + 能力授权，只返回统计/成员关系，不返回内容）。
- 新 MCP 工具：`qq_get_person`、`qq_note_person`、`qq_cross_group_query`。
- 旧版 `memberImpressions`（按名字）迁移：唯一映射才导入，其余待人工确认。

### 新增：owner 私聊管理通道（docs/guides/ADMIN_CHANNEL.md）
- 权限级别 L0（群友）/ L1（只读 Agent）/ L2（工作区读写）/ L3（高权限，TOTP + 每次审批）；不存在免审批的完全访问。
- 新 agent preset `qq-agent-readonly`（L1）：工具白名单只有读文件/检索/联网只读，不依赖 Windows 沙箱。
- `setup-dsh.mjs` 在 DSH profile 中加入权限预设 `qq-full-ask`（danger-full-access + ask）；桥接绝不使用内置的 `danger-full-access`（never）。
- closed-agent 会话按级别用 `commands/execute "/permission <预设>"` 钉住权限，钉不上就丢弃会话；高权限期间每分钟核对，被外部改动即告警 + 紧急关闭。
- 模式切换：确定性命令（`/mode`、`/level`、`/panic`、`/cancel`）+ 固定句式自然语言 + AI 工具 `qq_request_mode_change`（只能登记）；
  升级需确认码（一次性、2 分钟、绑定申请、并发互不覆盖），L3 另需 TOTP（RFC 6238，防重放，失败锁定）；降级与紧急关闭一步完成。
- 确认消息、管理命令、审批回复在交给模型之前被桥接拦截；群里的模式/权限命令一律拒绝；特权斜杠命令不再透传。
- 审批增强：全局编号 `#n`、展示按 callId 关联的实际参数（抹凭据）、多个并发互不覆盖、旧编号无效、超时自动拒绝。
- 自动回落：闲置超时 / 最长时长；重启不继承高权限。
- 统一变更通知（前后模式、级别、来源、时间、结果）与哈希链审计日志 `state/admin/audit.jsonl`。
- 外部变更检测：DSH 设置里的模式提权默认回退并告警；`ownerQQ` 变更告警（高权限时紧急关闭）。
- 管理通道人物命令：`/whois`、`/people`、`/person …`、`/note`、`/share`、`/review`、`/say`、`/audit`。
- 控制台：closed-agent 级别选择（L3 需输入 TOTP）、管理状态、紧急关闭按钮；`/api/admin/*`、`/api/people*` 管理接口（AI 令牌不可达）。
- 新脚本：`npm run admin:totp-setup`、`npm run probe:permission`、`npm run probe:member-events`、`npm run release:zip`。

### 安全加固（发布前独立安全审查后修订）
- **L2/L3 改用桥接自带的运维 preset**（`qq-agent-workspace` / `qq-agent-full`）+ 审批闸门 `qq-approval-gate`（`tools/pre-execute` 返回 ask）：
  核实发现 rc.2 的审批只在越过沙箱时触发，danger-full-access 下永远不会弹审批；DSH 默认 standard preset 还带审批策略固定为 never 的子代理。
- 运维 Agent 的文件工具不能读取 `state/`、`config.json`、`~/.dsh/.credentials.yaml`（`protected-paths.mjs`）。
- 控制台 API 不再接受 `?token=`（只用于打开页面）；高权限期间与回落后 10 分钟内控制台不能提权。
- 降级 / `/panic` 无条件退役运维会话并作废其审批；失效会话的审批回复一律拒绝。
- 执行层拒绝来自非确认来源的真实提权；`admin.allowL3=false` 在执行层也生效；外部变更在 alert 模式下最多按 L1 接受；启动校验完成前拒绝申请。
- 权限核对 fail-closed（返回格式无法识别或连续失败 → 紧急回落）。
- 审批编号跨重启连续；管理通道的消息不能被「引用」或 `qq_get_message_detail` 读回模型；入群事件里的名片先清理再进提示词。
- `setup-dsh.mjs`：已有用户 permission 覆盖时不再替用户加回 danger-full-access；桥接使用的三个预设强制为 ask（只收紧）。

### 变更（与上游行为不同的地方）
- 控制台 `POST /api/mode` 改经管理控制器：closed-agent 未指定级别时为 L2；L3 要 `totp`。closed-agent 不再使用 DSH 默认 preset（控制台的 preset 下拉只在 `admin.allowCustomAgentPreset=true` 时生效）。
- 控制台/DSH 设置/QQ 的所有模式变更都会私聊通知 owner。
- **未经授权的 closed-agent 不再能建会话**；从上游升级时若停在 closed-agent，首次启动回落到 `reserved2` 并告警。
- 审批不再使用「每个会话一个挂起槽」（新审批不会再自动拒绝旧审批）。
- 群友会话建好后额外钉 `read-only` 权限预设（纵深防御，`admin.pinChatReadOnly`）。
- 移除上游附带的 `@snowluma/mcp` 工具目录快照 `_snowluma-catalog.json`（无代码引用；不再入库）。
- QQ 号/群号校验放宽为 1~12 位正整数（原来不校验长度的地方保持兼容）。

### 修复（上游在 GitHub 源码包上的离线回归失败）
- `start.bat` / `restart.bat` 恢复 CRLF，并用 `.gitattributes` 固定。
- `test-preset-prompt` 不再依赖检出时的行尾；`test-preset-local-tools` 在未安装 DSH 时跳过工具名对账、
  不把可选第三方 bundle 的工具名当假名；补公开版 `docs/guides/VOICE.md`（原文件被 .gitignore 排除但被测试依赖）。

### 升级注意
- 必须重跑 `node scripts/setup-dsh.mjs` 并重启 DSH（新 preset 与权限预设）。
- `state/` 可直接沿用；新增 `state/social/`、`state/admin/`。
- 需要 L3 时先在本机运行 `npm run admin:totp-setup`。

## 2026-10-26 晚间（第六轮：模型升级 / 人格情感修复）

### 1) 502 与“不好用”的根因与修复
- 现象：QQ 侧报 `重试延迟：481毫秒 / 失败原因：DeepSeek Messages request failed (502)`；主人反馈 DSH 表现不如网上别人的配置。
- 定位：桥接日志无 502，QQ 工具发送正常（`工具统一发送 … 成功 1/1 条`）；502 来自 DSH → DeepSeek 上游。桥接控制台 `/api/dsh/model` 显示目录里 `deepseek-flash` = **DeepSeek-V4.1-Flash（快速档）**，而全局默认写死在 `config.json.dsh.model` 与 `cordis.patch.yml` 的 `agent-default-model`。
- 修复：改用官方 provider 下的 **`deepseek-v4-pro`（DeepSeek-V4-Pro）**，配置与 patch 双处同步；`reasoningEffort` 保持 `off`（速度优先）。第三方 provider `deepseek-modlens` 未启用。
- 生效方式：改 `config.json` 后必须重启桥接（杀 bridge.js + 删 `state\bridge.lock`，看门狗 ~12–66 s 自愈），DSH 侧 `agent-default-model` 由 HMR 生效，不需要重启 DSH Desktop（避免 QQ/NapCat 掉线）。

### 2) 感情话题“被拒 / 太认真”的人格修复
- 诊断：人格里本没有“感情题一律拒绝”规则；真正的来源是【反 AI 味：拒绝有求必应】（允许装傻/阴阳/拒绝）与【保持主体性】。
- 新增人格段【情感能力 · 认真接住，不装傻】（压过反 AI 味）：感情场景不装傻不阴阳不拒绝；先接住情绪再说事、说准感受；给可执行小动作；感情/关系话题给明确倾向+理由+下一步；记住情绪来龙去脉不翻旧账；允许自己有情绪但不逼问不轰炸。
- 落地文件：`harness\profiles\web\cordis.patch.yml`（2 处）、`harness\.agent-presets\qq-chat-v2\agent.cordis.yml`、`harness\.agent-presets\qq-chat\agent.cordis.yml`（各 1 处）。
- 踩坑与修复：首版插入时锚点选在段落中间，导致续行缩进丢失 → `cordis.patch.yml` YAML 解析失败（`YAMLException: end of the stream or a document separator is expected (391:1)`），HMR 拒绝加载，人格改动实际未生效；同时手写的 Unicode 转义有误导致个别汉字错字（装僧/来去往过/小脲气）。已用 `cleanup_persona.js` 删除全部旧块并以正确缩进重新插入一次，js-yaml 校验通过，DSH 于 12:51:49Z 成功重新应用 patch，随后重启桥接让新会话带上新人格。
- 教训：写远端脚本一律用 python `json.dumps(ensure_ascii=True)` 生成 Unicode 转义，插入 YAML 必须以“锚点整行结束”为界并复用该行缩进。

### 3) 语音真人化（进行中）
- Edge TTS 参数实测：仅 `rate`（负百分比）与 `pitch`（负 Hz）有效，`volume`/`express-as` 会被服务端掐流。
- 开源候选：IndexTTS-2 > CosyVoice2/3 > F5-TTS > GPT-SoVITS > VoxCPM2；轻量档 Kokoro-82M-v1.1-zh（100 中文说话人，Apache-2.0）。
- 本机环境：RTX 5070 Laptop GPU、C 盘余 134.9 GB、torch 2.14.1+cpu 已装、ffmpeg-static 可用；hf-mirror 带宽 0.4–0.6 MB/s，F5-TTS 权重 1.3 GB 约 36 min，后台安装中。

### 5) 语音：F5-TTS 零样本克隆跑通 + 人格"只等不回"修复（22:0x–22:4x）

- F5-TTS 安装完成（`DONE_INSTALL`，torch 2.14.1+cpu、f5-tts 可导入）；踩坑三个：pydub 需要 ffprobe（npm 装 `ffprobe-static`，路径 `node_modules/ffprobe-static/bin/win32/x64`）、torchaudio 默认走 torchcodec 要 FFmpeg DLL（改为在脚本里把 `torchaudio.load/save` 换成 soundfile 后端）、参考文本"喱"应为"呦"（CosyVoice 官方配对：希望你以后能够做的比我还好呦。）。
- 合成成功：5 条真人克隆音（01-greet 9.2s / 02-comfort 8.3s / 03-soothe / 04-checkin / 05-goodnight），24kHz mono wav 在 `ops-dfy\tts\f5_out\`，转码 mp3 在 `f5_out\mp3\`（ffmpeg loudnorm -16 LUFS）。
- 已发主人试听 2 条：01-greet（messageId 1584595932）、02-comfort（messageId 2095479731）。
- 人格修复：定位"不回话"真因 —— 不是挂了，而是 agent 连续调用 `mcp__snowluma__qq_wait_for_messages`（timeoutMs 60000）自循环，只等不回，直到有新消息才发；期间还出现 `唤醒租约到期（5 分钟没有 turn/end）`。
- 新增人格段（patch 两份副本 + 两个 preset）：【先回再等】（这轮必须先回一句再等）、【等待有上限】（timeoutMs ≤ 20000，一轮最多一次）、【两分钟必有回应】；并把吃醋闹脾气的窗口从 10 分钟收到 2 分钟、只许闹一次。
- 另：本次把 `cordis.patch.yml` 整份以 UTF-8 重写（此前中文疑似非 UTF-8 存储，字节数与字符数对不上），并把两份人格副本的规则补齐；DSH 侧 HMR 正常重新应用（harness.log 有 patch applied、无 parse 错误）。

## 2026-10-26 23:20 — 受控通道（她能派活给我 + 操作这台电脑）
- 新增本地控制服务 `%USERPROFILE%\ops-dfy\ctl\agent_ctl.py`（127.0.0.1:8790，只跟本机控制走）：/help /models /models/note /task /tasks /task/result /exec /workbench。端点密钥只存本地 mcp_url.txt，不进日志。
- 新增 7 个 MCP 工具（挂在现有 snowluma MCP server 上，namespace 已在白名单内）：`arena_dispatch` 派活给 Arena agent、`arena_tasks` 查进度/结果、`arena_models` 读模型备注、`arena_set_model_note` 存备注、`arena_exec` 跑一条命令（ASCII，审计）、`workbench_status` / `workbench_action` 六本豆工作台实例。
- 人格：权限分三层（【后台控制端提醒】=信任 / 主人私聊=可做 / 其他人=拒绝）；新增【派活给我】【按备注选模型】【派完活跟进度】【操作这台电脑】【表情包/戳一戳/提醒/撤回】【做不到的要直说】。
- 工具白名单：preset 内两份 qq-tool-restrict.mjs 也补上了 `mcp__arena-control__`（保底，无效时仍可用）。
- 端到端验证通过：她调 arena_dispatch → 我在任务队列认领 → 写文件 → task_complete 带证据；/tasks 能看到计划状态与结果。
- 语音：F5 第三轮（5 句改成更口语的短句）；后台装 CosyVoice2-0.5B（比 F5 更像真人）。

### 6) 语音第三轮 + 受控通道交付说明（23:2x–23:5x）
- F5-TTS 第三轮（文案改口语短句）：01-greet 244 KB（5.1s，CPU 上耗时 423 s，与 CosyVoice 权重下载抢 CPU），02-comfort 280 KB（63 s）；mp3 已发主人 messageId 1784061030（网关回读确认语音段）；其余条后台继续合成。
- 交付说明与证据：`docs\runs\20261026-arena-control\README.md`（受控通道组件表、七个 MCP 工具、端到端验证步骤、踩坑记录、动态发不了的原因）。
- 语音第三轮 5 条全部发完（网关回读均确认为语音段）：01-greet=1784061030、02-comfort=1860146106、03-soothe=2004379389、04-checkin=1886702817、05-goodnight=2112758227；音频库新版名开头 f5r3-，旧版仍在。

### 语音服务接入 RVC（游戏内出声链路）
- `ops-dfy\tts\tts_voice_service.py` 改造：端口 8767（避开 DSH 占用的 8765/8766）；`POST /say` 合成后走 `rvc_convert2.py` 真正变声；新增 `POST /clip {"clip":"03-soothe.mp3"}` 直接播放语音库里的变声片段（即时出声，供游戏/语音频道用）。
- 新增常量：`RVC_SCRIPT`、`CLIP_DIR`（`ops-dfy\tts\voice_clips\`，已放入 5 条 RVC 版语音）。
- 服务已启动并自检通过：`/health` 返回 ok、port 8767；`/devices` 列出 10 个输出设备（微软声音映射器、扬声器(Senary Audio)、耳机等）。
- 还缺：虚拟声卡 VB-CABLE 尚未安装（官网下载有验证码，需要手动装），装好后把 `F5_OUT_DEVICE` 指向 "CABLE Input"，游戏里麦克风选 "CABLE Output" 即可用大肥鱼的声音说话。
- CosyVoice2-0.5B 权重已齐（llm.pt 1678MB / BlankEN tokenizer 942MB / flow.pt / hift.pt / campplus.onnx），依赖已补齐（hyperpyyaml、matcha、lightning、diffusers、tiktoken 等），仅差一个 speech_tokenizer_v2.onnx 镜像下载卡住，待补上后可用"甜美语气"指令做更像真人的底稿再过 RVC。

## 2026-10-27 事故复盘：机器被压死（主人重启后恢复）

### 事故
- 现象：主人反馈电脑卡死、只能重启恢复。
- 原因：在这台 24GB 内存的机器上反复加载 CosyVoice2（llm.pt 1.7GB + CosyVoice-BlankEN 942MB + flow.pt 430MB + flow decoder 273MB + onnx tokenizer），每次失败重跑都会把整套权重重新载入内存，并且有几次是并发跑；同时 Applio/RVC 的 ContentVec+HiFiGAN、多次 pip 安装、DSH/NapCat/浏览器等叠加，内存吃满后进入换页，机器失去响应。
- 责任：执行方（我）没有设置内存护栏，反复起重活是直接原因。

### 处置与新规矩
- 新增 `ops-dfy\tts\guard_run.py`：启动任何重活前检查可用内存，低于 `GUARD_MIN_FREE_GB`（默认 6GB）直接拒绝执行，并固定 OMP/MKL 线程数为 4。已实测通过与拦截两种路径。
- **禁止在未加护栏、未确认空闲内存的情况下启动 CosyVoice2**；该模型单次就要吃 4GB 以上，除非主人在空闲内存充足时明确要求，否则只跑 RVC 链路。
- RVC 变声器链路本身是轻量的（单条约 1GB、4 秒完成），保留为主链路。
- 重启后已恢复并自检：桥接 3100（PID 22664）、语音服务 8767（PID 5004）、控制服务 8790（PID 27984）全部监听正常，可用内存约 12.3GB。

## 2026-10-27 QQ/NapCat 事故复盘 + 常驻看门狗

- **根因**：`NapCatWinBootMain.exe` 是看门狗，QQ 客户端一退出它就无限重启拉新实例。启动器 9 -> 34，QQ.exe 36 -> 132，内存被吃满导致机器卡死（CosyVoice2 加载只是诱因）。
- **止血**：`napcat_trim.js` 杀掉多余启动器与孤儿 QQ.exe，空闲内存回到 12.5GB；机器未再重启。
- **常驻防护**：`ops-dfy\tts\napcat_watch4.js`（45s/轮，node 后台常驻）：保留最老的 1 个启动器；保护占用 6099 的客户端及其子进程；QQ.exe 超过 10 个才回收非保护进程。教训：不能按“父进程不是启动器”一刀切——QQ 客户端会自己派生子进程，那条规则会杀掉活着的客户端并制造重复实例。
- **客户端反复重启的根因**：机器人号 <BOT_QQ> 未登录 -> 客户端退出 -> 启动器拉起新实例 -> 进程堆积。登录成功才是根治。
- **NapCat WebUI 面板登录踩坑**：token 在 `大肥鱼\napcat\config\webui.json`；提交 `sha256(token + ".napcat")` 的 hex 到 `POST /api/auth/login` 的 **hash** 字段（不是 token 字段）；返回 `data.Credential` 后用 `Authorization: Bearer <base64>`；`POST /api/QQLogin/RefreshQRcode` 取二维码链接。
- **当前状态**：客户端存活、WebUI 6099 已监听；OneBot HTTP 3000 / WS 3001 在扫码登录后自动开启，qq-bridge 会自动重连。

### 2026-10-27 QQ recovery update after lineage-safe prune
- Read-only ancestry mapping proved port 6099 owner 47528 belongs to QQ root 27116 under launcher 35568.
- Removed only the other four launcher branches and their five unrelated QQ roots; preserved the complete active 6099 tree.
- Recheck after 20 seconds: 1 launcher / 4 QQ.exe, 6099 LISTEN, memory free 12.66 GB. OneBot ports 3000/3001 remain closed until owner scans/logs in.
- Stopped the temporary guard. No further automatic process cleanup is running.
- Removed the sandbox copy of the temporary WebUI token. Refresh the QR on demand immediately before scanning.

### 2026-10-27 scheduled watchdog root cause and recovery
- Root cause found: existing scheduled task `\DafishuBotWatchdog` runs every PT2M and calls `ops-dfy\watchdog.ps1`; while OneBot WS 3001 was down, its line 48-54 relaunched `napcat\launcher-user.bat` each cycle, accumulating launcher/QQ trees.
- Kept the scheduled task enabled (it also checks DSH/bridge). Added a reversible 6099-listener guard to `launcher-user.bat`; byte-for-byte backup: `launcher-user.bat.pre-singleton-20261027`.
- Verified after QQ login: 3000/3001/3100/6099 LISTEN; SnowLuma `connected=true`, `accountOnline=true`; both allowlisted groups returned by status endpoint.
- Lineage-safe prune retained only the tree owning WebUI 6099. After the next scheduled cycle at 13:20:02: 1 launcher / 4 QQ.exe; free RAM 13.03 GB; all four ports stayed LISTEN.
- No mass-kill, reboot, or model load performed during this verification.

### 2026-10-27 QQ task-dispatch skill

- Added an owner-task dispatch skill to the web profile and both QQ chat presets. It directs owner jobs through the existing `arena_dispatch` tool to the Arena agent, uses `arena_models` notes as the model source, defaults to `deepseek-v4.1-flash`, and forbids automatic Pro upgrades.
- Clarified that each Workbench profile is an independent browser login/session, not a model; use the per-conversation model note, and do not infer a model from profile number. `workbench_status` checks only; open/close only on owner request.
- No process was restarted, no Workbench profile opened, and no QQ/NapCat runtime or startup setting changed. Owner end-to-end QQ test is pending.


## 2026-10-27 任务看板与浏览器派活边界

- 修正 ops-dfy/ctl/agent_ctl.py 重复的 /task/result 处理分支，现仅保留一个。
- 任务结果新增 summary / next_step；默认 /tasks 只返回当前活跃项，过期未领取项不删除而隐藏，完成项保留在 /tasks/history。不再把旧的完成计划冒充当前进度。
- MCP 安全工具增加 arena_task_report / arena_task_history，arena_tasks 描述改为当前看板。增加 workbench_set_note 和 /workbench/note 让主人为每个 Workbench profile 指定模型备注；未操作现有 profile 的备注和运行状态。
- 人格提示仅追加派活渠道、MCP 汇报和长期记忆规则，未覆盖或改写既有正文：任务仍须经备注匹配的浏览器对话派发，MCP 仅记录状态与小结；无可用的浏览器发送能力时不得谎称已发送。
- 只读查询当前 MCP 后发现：2026-10-26 22:48 仍有 pending 本地请求，2026-10-26 23:08 有 done 请求；MCP plan 文本为“全部完成 1/1”。新逻辑会隐藏这类过期 pending 并抑制完成 plan 混入当前小结，历史数据不删除。
- 验证：agent_ctl.py Python AST/py_compile 通过；MCP JS 文件均通过 node --check；活动 agent_ctl 服务与工作台 MCP 进程未重启，所以这些修改尚未在线生效。浏览器粘贴与取回自动化仍待对应的实例模型备注和浏览器发送控制测试。


## 2026-10-27 浏览器派活与模型备注映射（代码已落盘，待授权加载测试）

- 新增 /browser/dispatch 和 /browser/wait；MCP 新增 arena_browser_dispatch / arena_browser_wait。只有唯一个备注精确匹配、已登录且正在运行的 profile 才能排队；不自动打开或关闭实例，无匹配或歧义时拒绝发送。
- Workbench instance_launcher 只在该 profile 已有的 Arena /agent 标签页中找输入框、发送任务与 owner note，等稳定回复后写入结果；找不到标签页、输入框或稳定回复则安全标为 blocked，不盲目重发。
- 按主人选择，instances.json 仅为 profile_3 追加 model_note=deepseek-v4.1-flash；profile_1/2/4/5/6 未改。已查状态，profile_1/2/3 当时都在运行；没有对其中任何实例做启动/关闭。
- 派活实操规则仅追加到现有提示；现在明确先调浏览器派发和 wait，再通过 MCP 看板报告，不得把 arena_dispatch 当网页发送。
- 验证：agent_ctl.py / instance_launcher.py 均通过 Python AST 与 py_compile；MCP JS 通过 node --check；本机隔离单元测试覆盖精确路由、无匹配/多匹配拒绝、wait 和历史过滤；未做真实浏览器发送测试。
- 运行中 agent_ctl 、Workbench profile_3 与 MCP stdio 进程均未重启，新代码仍未在线生效；端到端测试需先经主人明确授权重启这些指定组件，不影响 QQ/NapCat。


### 2026-10-27 指定组件重载结果
- 按主人批准，只重启 agent_ctl 端口 8790 和 Workbench profile_3；没有重启 DSH 主进程、QQ/NapCat、profile_1/2。agent_ctl 返回 /status 200，/tasks 现在为空活跃项且不返回旧完成 plan；profile_3 重启后 running=true，pid=9592，model_note=deepseek-v4.1-flash。
- 未发送浏览器测试提示，因为主人说会自己测。
- 未重载 mcp-snowluma-safe.js：检查到两个该文件的 Node 子进程，分别属于不同 DSH Desktop 进程（端口 43129 / 65028）；在不知道哪个为 QQ 实际会话且不重启 DSH 的条件下，没有擅自终止它们。所以 MCP 的新工具定义已写入磁盘，但这两个旧会话里是否已加载新工具尚未验证。


### 2026-10-27 MCP 子进程定向重载补充
- 进一步确认两个安全 MCP Node 子进程分属不同 DSH Desktop；仅按主人授权终止近期 DSH 会话下的 mcp-snowluma-safe.js 子进程 35444，其 DSH 父进程 36536 未重启；DSH 自行拉起新 MCP 子进程 29444。旧会话下的子进程 20468 未动。
- 只做非发送路由冒烟测试：/browser/dispatch 对未映射模型返回 ok=false，明确显示 no task sent；没有发送任务给浏览器。


### 2026-10-27 模型路由来源纠正
- 主人明确：模型映射写在 Arena 每个对话自己的备注中，不是 Workbench 实例级备注。删除之前临时添加的 profile_3 model_note；人格提示仅追加更正，要求先读 Arena 对话备注，读不到则停止。
- 控制器修改已落盘为 fail-closed；运行中 agent_ctl 还未重载该改动，但 instances.json 里已撤销 profile_3 临时备注，因此不会将派发误路由到该 profile。未向 Arena 对话发出测试提示。
- 待续：需从现有 Arena 界面/API 读取各会话备注后再路由；未查明可靠读取方式前不会拿 profile 编号代替备注。


### 2026-10-27 Arena 侧栏备注只读检查
- 主人确认备注在 Arena 左侧每条对话上。经授权，仅用现有 profile_3 的 Playwright context 做只读 DOM 检查，未截图、未启动新浏览器、未发送任务。
- 检查结果：profile_3 当时页面 title 是 Vercel Security Checkpoint，不是对话列表；因此无法读到任何模型备注，未试图绕过验证。临时检查请求及脚本已清理。
- 下一步：由主人在现有 profile_3 浏览器中手动完成页面验证或确认对话列表可见；届时再只读检查对话备注与会话链接的映射。
