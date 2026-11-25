# 阶段 4：首次启动向导、便携包、更新与回滚、全量验收

日期 2026-10-31 · 状态 **已上线并通过门禁**（七套自检 38 / 26 / 57 / 123 / 112 / **158** / 看门狗 10，`tsc --noEmit` 0 错，`vite build` ✓，`build_release.py` 出包 429 414 B / 67 个文件并通过核心自己的校验；现场验收 52 PASS · 0 FAIL · 4 NA）

对照设计文档 §8.1（安装与首次启动）、§8.2（轻量/完整两档便携包）、§8.3（更新与回滚）、§8.4（卸载保留数据）、§7.2（凭据与脱敏）、§7.3（二次确认＋自动备份）、§10 验收 11/12/14/15。

## 新增内核模块（仍全 stdlib，无新依赖）

| 模块 | 负责 | 关键约定 |
| --- | --- | --- |
| `core/dfy_core/setup.py` | 体检、只读盘点、接管/新建标记 | `adopt` 只写 `.runtime/setup.json` ＋一份自动快照；**不改任何生产文件**（验收用 sha256 逐字节比对证明）；`checks()` 的阻塞级只留给「运行时目录写不了」这种真阻塞，构建产物缺失只警告 |
| `core/dfy_core/release.py` | 校验→解包→健康检查→切 `current.txt`→失败自动回滚 | 安装根目录 `%LOCALAPPDATA%\Dafeiyu`（`DFY_INSTALL_ROOT` 可覆盖，自测就是靠它在临时目录里跑全流程）；`SHA256SUMS.txt`/`RELEASE.json`/结构完整性任一不过直接拒；护栏快照目录名 `<时间戳>--<版本>`，版本号自己带横杠也不会解析错 |
| `core/dfy_core/diag.py` | 一键诊断包 | 只打包**投影**（status/settings/models/arena/backups/ports/persona/memory/audit/env），先按结构脱敏（字段名是可靠信号）再扫一遍文本，最后再补一道 `sk-` / `token=` 兜底；`cmd_config`、凭据目录、数据库、MCP 地址一律不进包 |
| `packaging/build_release.py` | 出包 | 默认轻量版（不含 `node_modules`），`--mode full` 才打运行时；包里带 `RELEASE.json`＋`SHA256SUMS.txt`＋`BUILD-INFO.txt`＋`install.ps1`/`uninstall.ps1`/`run.cmd`；清单连元数据文件一起哈希 |

路由：`GET /v1/setup/state|checks|inventory`、`GET /v1/release/status`、`GET /v1/diag/bundles`；
`POST /v1/setup/adopt|reset`、`POST /v1/release/install|rollback`、`POST /v1/diag/bundle`。
写入面从 21 条长到 26 条，`selftest_stage2.py` / `selftest_arena.py` 里那份**逐字面量钉死**的白名单同步改掉了（就是要逼一次有意识的确认）；`src/main/index.mjs` 的 POST 白名单、`src/main/supervisor.mjs` 的 GET 清单与核心侧等值，由自测断言三者完全一致。

界面：新页 `FirstRunPage.tsx`（看体检 → 看盘点 → 选接管/新建 → 收尾，第二步全程只读，第三步要二次确认，随时可跳过），`App.tsx` 在 `wizard_needed` 时弹出；`SettingsPage.tsx` 加「更新与回滚」（当前版本、可安装包、装/回滚都先给 `will` 预览）与「诊断包」（生成＋列表），另加「重新运行向导」。

## 这一轮被门禁抓出来的真问题（都是现场才暴露的）

1. **轻量包漏了 `src/`**：`package.json` 的 `main` 是 `src/main/index.mjs`，vite 只产 `out/renderer`。原来的 `DEFAULT_INCLUDE` 没打 `src/`，装完就是个起不来的空窗口。补进去，并在 `release.verify()` 加了结构校验（缺 `package.json`/`src/main/index.mjs`/`out/renderer/index.html`/`core/dfy_core/app.py` 任一项，验包阶段就拒，不等健康检查）。
2. **体检按不存在的产物判死刑**：`setup.checks()` 查 `out/main/index.js`（这个工程根本不产它），把好机器报成硬阻塞，连带 `adopt` 直接拒绝执行。改成读 `package.json` 的 `main` 再查那个文件，并把这一项降为警告——接管配置不需要前端产物。
3. **同秒两次安装会把护栏目录撞名**：`rollback/<时间戳>-<版本>` 相同 → `copytree` 抛 `FileExistsError`。改成撞名就在**时间戳**一侧加后缀（`.1`、`.2`），版本号留在 `--` 右侧原样可解析；自测里专门验了「护栏名还能反解出版本」。
4. **卸载计划对没装过的机器短路成 error**：`%LOCALAPPDATA%\Dafeiyu` 不存在时直接 `{"error": ...}`，设置页那张卡就死在那儿。改成照样给出「会删什么/会留什么」并标 `exists:false`；同时把确认门槛改成绝对规则——**破坏性动作先要 `confirm`，空操作也不例外**。
5. **`install.ps1` 里用了 `$using:`**：那是 `Invoke-Command` 专用语法，写在脚本文件里会解析失败。改成字面量 `SHA256SUMS.txt`，并把「带上一版用户改过的 `config/desktop.json`」也写进脚本（与 `release.install()` 的行为一致，否则升级一次配置被打回出厂值）。
6. **脱敏有洞**：`security.redact()` 只认 `Bearer`/`authorization`/32 位十六进制，`token=xxx`、`{"api_key": "sk-…"}`、裸 `sk-…` 都能穿过审计与诊断文本。补了三条模式（`key=value` 形状、`sk-` 形状、JSON 字段形状），`api_base` 这类要给用户看的字段仍原样保留。
7. **验收脚本自己量错过两次**：把 `own_listeners()` 的 dict 当 list 索引；把「配置里提到 8790」当成「本 App 占了 8790」。前者改了形状判断，后者改成按可执行文件路径/镜像名认人——顺带查清机上 `127.0.0.1:8790` 是**旧的 uvicorn `services.engine.main:app`**（pid 起于 10-30 22:44，属上一代常驻服务），不是桌面 App 起的，已在验收输出里如实列出。
8. **强制杀掉窗口会把核心变成孤儿**：`before-quit` 根本不来，python 核心继续占着一个回环端口，下次启动就再起一个。第一次重启界面时实测到 `CORE_CHILDREN=1620 24772`（一父一孤）坐实了这件事。修法：supervisor 给核心的 `stdio[0]` 从 `ignore` 改成 `pipe` 并置 `DFY_CORE_WATCH_STDIN=1`，核心起一个守护线程 `sys.stdin.read()`——父进程一没，管道就 EOF，核心自己 `shutdown(); os._exit(0)`；那条 stdin 从不写任何东西，写坏了还得吞掉 error。新增 `scripts/selftest_watchdog.py`（已进 `app._SUITES` 与 `gate.py`）在真机上跑两组：**开标志**关 stdin → 自己退出且端口释放；**不开标志**同样关 stdin → 照样活着（对照组，证明不是"崩了"）。生产 runtime 的审计里也确认没有这次测试的痕迹。
9. **"哪份快照最新"被名字排出来，是个假绿**：护栏目录名 `<时间戳>--<版本>`，同秒撞名时在时间戳一侧加 `.1`/`.2`（见问题 3）。`rollback()` 原先按名字倒序取第一个，于是回滚目标取决于"这两次安装有没有跨秒"——自测里写死的 `1.0.0` 期望在快机器上对、在慢机器上错，今天就是这样在真机上炸出 `147/2`。改成：新增 `release._snapshots()` 按**目录 mtime**（真实创建顺序）排序，名字只作同刻的收尾；`rollback(cfg, confirm, to=)` 支持指名回滚到哪一份快照（目录名或裸版本号都行），预览里把 `options` / `target` / `snapshot_versions` 一起给出，指错就带着可选项拒绝并且不动 `current.txt`。自测相应改成从磁盘实际快照算期望，另加一条 mtime 反转用例（名字更大但 mtime 更早的快照必须排后面）。

### 还有一条要记在生产账上

- **自测起的核心以前直接写生产 `.runtime`**：`selftest_stage1.py` / `selftest_send.py` 起核心时没设 `DFY_RUNTIME_DIR`，于是这几天生产审计里混进了测试流量（这也是审计行数一路涨的原因之一）；2b-ii 上线 `core-link.json` 之后，同一件事还会留下**指向死端口的僵尸指针文件**——现场就是这样读到过一次（`port=54252 pid=19328 connect=False`）。两条老自测现在都把 runtime 指到临时目录，accept 与门禁那轮都会先认出僵尸文件并清掉。写生产审计的行为没有变：真正跑生产的核心照旧记账。

### 度量工具的两次自我更正

- **审计泄漏只能用 python 数**：PowerShell `Select-String` 在同一份 `audit-20261030.jsonl` 上报过 `token=` 1 条、`sk-` 1 条，两次独立的 python 扫描（区分大小写与不区分）都是 **0**，全量 8 322 行（含 10-30 的 2 389 行与 10-31 的 5 933 行）也是 **0**。结论以脚本里的 `$_ -cmatch 'token=' -or 'sk-' -or 'Set-Cookie'` 为准；那个 PowerShell 数字不再引用。
- **界面重启的两次是我的操作问题，不是产品的**：第一次是修复前的已知孤儿；第二次是我把同一个 kill 任务提交了两次（取 job id 时用了 `tail -1`，把第一行 `已启动，job_id=` 截掉了），两个 kill+重启循环交错，界面一度被带走。已经恢复并核对：GUI 4 个进程、核心 1 个（`core 22144 ppid 4524`，只听 `127.0.0.1`）、审计里 `/v1/status` 每 5 秒一跳。教训写进脚本约定：**动进程的任务一次只提一个，job id 一律 `head -1` 取**。


## 门禁与现场验收

门禁 `scripts/gate.py all`：哈希核对（本地镜像 ↔ 远端逐文件 sha256 前 16 位全等）→ `py_compile` → 七套自测 → `tsc --noEmit` → `vite build` → 出包 + `release.verify` → 现场验收。

现场验收 `scripts/accept_stage4.py`（在生产配置上跑，写操作只碰 `.runtime/setup.json` 与一份快照，跑完 `reset` 把向导留给机主）：

七套自测在 Windows 目标机上全绿：`stage1 38/0 · send 26/0 · stage2 57/0 · arena 123/0 · stage3 112/0 · **stage4 158/0** · watchdog 10/0`（本地 Linux 跑同一套 stage4 是 140/0，差的 18 项正是只在有构建产物时才跑的打包检查）。`tsc --noEmit` rc=0，`vite build` rc=0，`build_release.py --version 0.4.0` 出 `dafeiyu-app-0.4.0-light.zip` 429 414 B / 67 个文件，`release.verify()` 判 ok=True files=72。

现场验收 **52 PASS · 0 FAIL · 4 NA**，要点：

| 项 | 现场证据 |
| --- | --- |
| 体检/盘点只读 | 生产 4 个文件 sha256 逐字节全等；盘点认得 3 份配置、人格 2 行、记忆 559 条（与直查 `long-memory.db` 一致） |
| 接管两步 + 自动备份 | 先给 `will` 不落盘；确认后只多出 `.runtime/setup.json` 与快照 `20261031-103211-handover`（备份目录 21→22），跑完 `reset` 把向导留给机主 |
| 包完整性 | 轻量包 405 KB、不含 `node_modules`、`run.cmd`/`install.ps1`/`uninstall.ps1` 齐；**改一个字节即被拒**（`core/dfy_core/__init__.py 哈希不一致`） |
| 更新与回滚 | `install` 不给 confirm 只解释，`current.txt` 未变；未装过的机器 `rollback` 明确说「没有可回滚的快照」；`rollback` 不给 confirm 时把 `options`/`target`/`snapshot_versions` 一起给出，指到不存在的目标就带着可选项拒绝且不动 `current.txt`；`uninstall_plan` 默认保留 2 项，`purge` 空操作也要确认 |
| 健康检查 | 用**生产 core** 起一次并拿到 `/v1/status`（keys 含 blocks/core/network） |
| 诊断包 | `dfy-diag-20261031-103829.zip` 8 706 B；扫 48 349 字节，`token=` / `sk-` / Cookie / 二维码 / `mcp_url.txt` / Bearer 六条模式 **0 命中**；包内无凭据目录、无 `.db`、无原始 `cmd_config` |
| 监听面（验收 12） | 本 App 无非回环监听；`ours=[]`，系统其它非回环监听 22 个都与本 App 无关；所有 electron/python 进程监听（9 条）全在 127.0.0.1；配置里的端口没有一个跑到非回环（netstat 204 行） |
| 审计（验收 11） | 阶段 4 每次写都留痕，全量 8 322 行（两天）里 `token=` / `sk-` / `Set-Cookie` / `mcp_url` / `Bearer ` / `password` 六条模式 **0 命中**（python 逐行数） |
| 实起核心 + HTTP 打新路由 | 随机端口 64514 起来后：`GET /v1/setup/state`=200 `wizard_needed:true`、`/v1/setup/checks`=200 阻塞=[]、`/v1/setup/inventory`=200（messages 559、outbox inbox0/sent1/failed0）、`/v1/release/status`=200、`/v1/diag/bundles`=200；`POST /v1/setup/reset` 无 confirm 只回预览；`POST` 只读路由 → **405**；`POST /v1/release/install` 空包名 → **400 包文件不存在** |

顺带量到一件机器现状：`127.0.0.1:8790` 上有旧的 `uvicorn services.engine.main:app`（pid 3436，起于 10-30 22:44，来自 `AppData\Local\Programs\Python\Python312`）——不是本 App 起的，本 App 也没占它；要不要停掉请机主定。

## 已知边界（不假装做完）

- **没在真机上装过一轮** `app-<版本>` 目录（要落盘并重启窗口）。机制在自测里用「真核心起得来」「桩核心起不来就自动回滚」「改一字节被拒」「越界路径被拒」四条路径分别跑过。
- **完整版包**（`--mode full`，打 `node_modules`）没跑：几百 MB，留给真正发布那次。轻量包缺 Electron 时 `run.cmd` 会明确说缺什么，不会静默起空窗口。
- **孤儿核心已在真机干净实测过一次**（2b-ii 那轮补做的）：只杀窗口那 4 个进程（`N_E0=4 N_C0=1`，核心 22144 挂在 4524 下、听 `127.0.0.1:50683`），6 秒与 10 秒后核心数都是 **0** → `WATCHDOG_OK`，端口随之释放、`ORPHANS_FINAL=(none)`；重启后 GUI 19780 + 核心 18832（ppid 19780）。更早那次被我交错提交弄脏的测量作废，现在以这次为准。随时可重跑 `python scripts\selftest_watchdog.py`（只用它自己起的子进程，不碰界面）。
- **`rollback` 的 `to=` 参数没做进界面**：设置页只有「回滚到最新那份快照」一个按钮，指定快照目前只能走 API（`POST /v1/release/rollback {"to": "<目录名或版本>"}`）。要做成下拉的话，得先决定它出现在确认对话框里还是版本列表里。
- **没有代码签名**：本机无证书。§7.4 要的验签暂时用 `SHA256SUMS.txt` 哈希清单代替，这一点写在包内 `BUILD-INFO.txt` 里，不藏着。
- 真实 QQ 侧（扫码、发消息）仍按红线不做；一次性授权已用完。
- 「重启 AstrBot」这一条已经在 2b-ii 装机那一轮做过一次（机主点头）：插件侧 7 个工具由 AstrBot 日志确认注册。本节 NA 里剩下的「装 `app-<版本>` → 重启窗口 → 回滚」是安装器自己的路径，仍然没在真机走过。详见 `STAGE2B.md` 末尾的安装记录。
- 上一代常驻服务（`services.engine.main:app` 听 127.0.0.1:8790）仍在跑，它不属于这个 App 的进程树；要不要收掉请机主定。
