# Arena 页为什么一直显示「网关未连通」（2026-10-31）

## 现象

桌面端顶栏 pill 写「网关未连通」，Arena 页写「工作台暂不可用」，但：

* 导航里 Arena 是 `ready: true`，页面本身没灰；
* `.runtime/audit-*.jsonl` 里近 400 条记录中 `/v1/arena/overview` 66 次、`/v1/arena/tasks` 70 次——人确实一直在看这一页；
* 上游 MCP 直连 `http://127.0.0.1:8765/mcp/<token>` 是 200，69 个工具都在。

## 根因

`core/dfy_core/mcp.py` 的 `_post()` 用同一个常量当两件事：

```python
MAX_REPLY = 20000
raw = resp.read(MAX_REPLY + 1)      # ← 拿「还给界面的正文上限」当「读响应上限」
```

`tools/list` 一次吐出 69 个工具带描述，几十 KB。正文被读到 20001 字节就截断，剩下半截 JSON，
`json.loads` 必然失败，于是抛 `响应不是合法 JSON（HTTP 200）`。
`arena.overview()` 捕获后写 `reachable: false` + `error`，界面据此把 pill 判成未连通。

用装好的 0.4.3 核心复算（`DFY_RUNTIME_DIR` 指到临时目录，不碰线上）：

```
{"configured": true, "error": "响应不是合法 JSON（HTTP 200）", "reachable": false}
```

这就是「明明网关是通的，界面说没通」的全部原因。错误信息还把锅甩给了服务器。

## 修复

读响应和交付正文分成两个上限，`call()` 的截断行为不变：

| 常量 | 值 | 用在哪 |
|---|---|---|
| `MAX_BODY` | 1,000,000 | `resp.read(MAX_BODY + 1)`；超过就报 `响应过大（1000000 字节上限）` |
| `MAX_REPLY` | 20,000 | `call()` 把交付给界面的文本截到 20 KB，末尾补「…（已截断）」 |
| `MAX_REPLY` | 20,000 | `arena._call()` 的错误/回复截断（未改） |

`core/dfy_core/mcp.py` 6,224 B，sha256 `1c58a82994df811fa2c605f61e978b6be059fc6e7c229cb02f2276ec0a140874`（修前 `119fd8e3…`）。

修复后同一份配置复算：`{"configured": true, "error": null, "reachable": true, "tools": 69}`，`plan_create` 在 `tool_names` 里。

## 自测契约跟着改了（scripts/selftest_arena.py，24,398 B `94784b6e…`）

旧断言 `oversized reply refused` 建立在「读上限 = 20 KB」上：stub 回 3 万字符正文，期望报错。
读上限放宽后这条路变成了「读到、按 MAX_REPLY 截断、照常交付」，旧断言必然红（gate 里 arena 套 123 pass / 1 fail）。

现在按新契约拆成三条：

1. `long reply truncated, not dropped`——3 万字符正文，`call()` 回来 `ok` 且以「…（已截断）」结尾、长度 = 20000 + 标记；
2. `long reply still dispatches`——长正文照样能派单并记账（`state == queued`）；
3. `body over the read cap refused`——stub 回 1.1 MB 正文，必须报错且错误里含「过大」。

arena 套：**126 pass / 0 fail**；整套门禁 `python scripts/gate.py compile suites typecheck accept` → **GATE OK**（stage1 38 / send 26 / stage2 57 / arena 126 / stage3 112 / stage4 158 / watchdog 10 / arena_tools 58；accept 66 PASS 6 NA）。

## 顺带查到的第二件「灰」

`.runtime` 里首次向导标记一直是空的 → `setup.state.wizard_needed = true` →
`App.tsx` 每次启动都会渲染 `<FirstRunPage>`，那层 `.wizard-mask` 是半透明遮罩，**整个界面都是暗的**。

已按向导自己的「接管现有」路径落标记（`setup.adopt(mode=adopt)`）：只写完成标记 + 一份 `handover` 快照（guard `20261031-175957-handover`），**一个字节都没改生产配置**。
结果 `wizard_needed = false`，重启后不再弹遮罩。想重看：设置页「重新运行向导」，或 `setup.reset` 清标记。

## 版本

| 版本 | 内容 |
|---|---|
| 0.4.4 | 只换 `mcp.py`（light 461,282 B / full 180,846,898 B） |
| **0.4.5** | 0.4.4 + 修好的 `selftest_arena.py`（light 461,832 B `7d4d2935…` / full 180,847,443 B `f397c2fa…`），**推荐用这个** |

真机：`release.verify` + `release.install` 装 0.4.5-full，
`current=0.4.5`、`mode=full`、`last_health=通过`（health http 200），回滚快照 `rollback\20261031-180059--0.4.4`。
用装好的 0.4.5 核心复算 Arena：`reachable: true, tools: 69`。

## 教训

* 别把一个「长度上限」常量同时用在读响应和交付正文上——大响应会被静默截成非法 JSON，而且错误信息看着像服务器坏了。
* 排查时看清 `sys.path` 指向哪份代码：用 `app-0.4.3` 的核心复算是复现旧 bug，用源码目录的核心才是新行为。
* 门禁输出别用 grep 过滤：`arena 123 fail=1` 只剩汇总，FAIL 明细被滤掉了，白跑一轮。

## 附：0.4.5 / 0.4.6 为什么又多出两个版本

* **0.4.5**：把修好的 `scripts/selftest_arena.py` 一起打进包（0.4.4 的包里还是旧断言，谁从安装目录跑 gate 都会看到 1 条红）。
* **0.4.6**：`scripts/accept_stage4.py` 也有一个「假设机器全新」的坑——它断言「没确认时 `setup.state_path()` 不存在」，收尾还无条件 `setup.reset("yes")`，
  等于把我们 17:59 落的向导完成标记当成测试残留清掉，于是在跑过向导的机器上必红一条，且每次 `gate accept` 都会把机主的标记抹掉。
  改成：先记标记原样 → 断言「不带 confirm 的 adopt 不改一个字节」→ 收尾按原字节还原，并补一条还原断言。
  结果 accept 从 65 PASS / 1 FAIL 变成 **67 PASS / 0 FAIL**，且跑完标记还在（已用 `setup.state` 复核：`wizard_needed: false`）。

最终状态：

| 项 | 值 |
|---|---|
| 包 | `dafeiyu-app-0.4.6-light.zip` 465,197 B `7f980145…` / `-full.zip` 180,850,811 B `bd9feb07…` |
| 装机 | `current=0.4.6`、`mode=full`、`last_health=通过`、回滚快照 `rollback\20261031-180509--0.4.5` |
| 门禁 | `GATE OK`：compile ok / 8 套自测全绿（arena 126）/ tsc+vite ok / accept 67 PASS 0 FAIL 6 NA |
| 装好的 0.4.6 核心 | `{"reachable": true, "tools": 69, "error": null}` |
