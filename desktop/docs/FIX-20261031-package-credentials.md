# 2026-10-31 打包三连坑：0.4.x 轻量包装不上、完整版没有 Electron、启动脚本在 cmd 里炸

## 结论先说

`dafeiyu-app-0.4.0-light.zip` 这个包**装不上**：安装时健康检查必然失败。
同一天接着查出另外两个更隐蔽的问题：`--mode full` 打出来的包**不含 electron.exe**；包里的 `run.cmd`
是 LF 换行 + UTF-8 中文注释，`cmd.exe` 解析直接崩。

三处都修了，最终产物 **0.4.3**，真机跑通：`校验 → 安装 → 健康检查 → 升级 → 回滚 → 启动窗口 → 关门后核心自退`。

## 坑 1：包缺 `credentials.py`（0.4.0 / 0.4.1）

* 现象：`release.install` 返回
  `健康检查没过…没有打印 READY 行（核心没起来）`
* 根因：手动按同样方式起包内核心，stderr 直接给出
  `ImportError: cannot import name 'credentials' from 'dfy_core'`
  —— `SECRETISH` 正则里有 `credential`，把**模块** `core/dfy_core/credentials.py` 当成密钥数据文件排除了。
* 修法：源码后缀（`.py/.mjs/.cjs/.js/.jsx/.ts/.tsx`）不受 `SECRETISH` 约束；顺手加 `BACKUPISH`，
  `*.bak/*.orig/*.tmp/~` 不再进包（0.4.0 里把 `arena.py.bak-2b1` 打进去了）。

## 坑 2：完整版里没有 Electron（0.4.1-full）

* 现象：`--mode full` 出来的包只有 **21MB / 2,848 文件**，`node_modules/electron/` 只有 10 个元数据文件，
  `electron.exe` 不存在。`run.cmd` 双击后必然报「缺 Electron 运行时」。
* 根因：`EXCLUDE_PARTS` 里带 `"dist"`，而排除逻辑是 **子串匹配**：`node_modules/electron/dist/**` 整棵被端掉。
* 修法：改成**按路径分段**判断——`EXCLUDE_PARTS = (".runtime","__pycache__",".git",".cache")` 逐段比，
  外加 `EXCLUDE_TOP = ("dist",)` 只排顶层 `dist/`（打包产物目录）。
  之后完整包 = **180,846,372 B / 3,052 清单项**，`electron.exe` 在位。

## 坑 3：`run.cmd` 在 cmd.exe 里被解析错（0.4.0–0.4.2 全部）

* 现象：双击 `run.cmd`，屏幕上蹦
  `'），' 不是内部或外部命令`、`'\electron.cmd" (' 不是内部或外部命令`、`'--user-data-dir' 不是内部或外部命令`。
* 根因两条叠加：
  1. 包里的 `run.cmd` 是 **LF 换行**（Python 写字符串原样落盘），`cmd.exe` 对 LF-only 批处理里的
     `if exist (...) (` 块会解析错位；
  2. 批处理里写了**中文注释**，而 `cmd.exe` 按 OEM 代码页（本机 936）读文件，UTF-8 字节变乱码。
  同理 `install.ps1` / `uninstall.ps1` 是 **无 BOM 的 UTF-8 + LF**，Windows PowerShell 5.1 会按 ANSI 读，
  中文全乱——这条本来就在自己的工程规矩里（"覆盖 .ps1 要补 UTF-8 BOM"），只是打包器没遵守。
* 修法：新增 `win_text(text, bom=False)`——统一转 CRLF；`.ps1` 加 UTF-8 BOM；
  `run.cmd` 全文改 **ASCII**（注释改英文），不再依赖代码页。

## 0.4.3 实机验收（2026-10-31 17:29–17:47 +0800）

| 步骤 | 结果 |
|---|---|
| `verify(0.4.3-light)` | ok，77 项，sha256 `77b7c818…` |
| `verify(0.4.3-full)` | ok，3,052 项，sha256 `b5a8a0f7…`，包 180,846,372 B |
| 装轻量版到 `%LOCALAPPDATA%\Dafeiyu` | ok（0.4.1→0.4.2→0.4.3，每次都留 `rollback/` 快照） |
| 装完整版到真机根 | ok，`current.txt=0.4.3`、`mode=full`、健康检查 HTTP 200 |
| 装完目录检查 | `run.cmd`（CRLF/ASCII）、`install.ps1`（CRLF+BOM）、`uninstall.ps1`（CRLF+BOM）、`node_modules\electron\dist\electron.exe` 全在位 |
| **双击级启动冒烟** | 临时根里 `run.cmd` → 4 个 electron 进程 → 核心起在随机端口（63195）→ `/v1/arena/workbench` 200，stderr 干净 |
| 关门后看门狗 | 结束 electron 后 9 秒内核心自己退出，无孤儿 |
| 回滚 | 临时根 0.4.2→0.4.3 后 `rollback` 回到上一版，切前重跑健康检查，通过 |
| 诊断包 | 10 个 parts、0 错误；`b4ae1032`/`sk-`/`token=`/`2N51Y-` 命中数全 0 |

## 运维提醒（今天踩到的）

* **别在这台机器上同时开两个实例**。完整版冒烟时另开了一个实例（带独立 `--user-data-dir`），
  期间原窗口在 17:42:09 停止轮询（日志里没有崩溃记录，像是窗口被关/被顶掉），
  之后按原方式从源码目录重启恢复（新核心 17364 / 57611，心跳正常）。要做双实例测试，先关掉主窗口。
* `dist` 里留着历史包：`0.4.0-light`、`0.4.1-full` 已确认有问题，文件名带 `.BROKEN` 标记；
  **只有 `0.4.3-*` 是可用版本**。

## 还没做的

* 代码签名（本机无证书），`RELEASE.json.signature` 仍是 null；
* `rollback to=<指定快照>` 的界面入口、多版本选择器；
* 完整包没做增量更新，每次都是整包 180MB。
