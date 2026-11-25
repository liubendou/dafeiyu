# 模型列表改成「按实例读工作台」而不是抄 AstrBot 清单（2026-10-31 18:20）

## 为什么要改

原来的 `arena.models()` 读的是 AstrBot 的 `provider_sources`（cmd_config 里那几条），
所以界面上永远是那几个 provider 名字。主人要的语义不是这个：

> 每个实例打开 Arena 之后，左侧栏每一行就是一个会话，行里写着模型名。

也就是说：**模型 = 每个实例里真实开着的会话**，要按实例去读，不能从别的配置文件里猜。

## 怎么读的（只读，不碰消息）

六本豆工作台本来就有一条任务通道，启动器会消费它：

```
~/ops-dfy/ctl/requests/<name>.json
  {"profile": "profile_3", "kind": "inspect_arena_notes", "state": "pending"}
```

`instance_launcher.py` 看到自己 profile 的 pending 巡检任务，就用 Playwright 读**已有的 Arena 标签页**，
从左侧栏 `[class*="group/menu-item"]` 行里取出 `model_note` / `href` / `row_text`，写回同一个文件。
不点按钮、不输入、不发消息。

新增 `core/dfy_core/rooms.py`：

| 函数 | 做什么 |
|---|---|
| `instances(cfg)` | 读工作台的 `instances.json` + `run/profile_N.json`（pid/端口/标签页），一次 tasklist 判断在跑没有 |
| `inspect(cfg, profile)` | 发一个只读巡检任务并等回话（默认 25 s，超时可解释） |
| `snapshot(cfg, refresh)` | 每个在跑的实例读一遍，合并成房间清单，落 `runtime/arena/rooms.json`，10 分钟内复用缓存 |
| `cached(cfg)` | 只读缓存，给要秒回的接口用，绝不自己触发巡检 |

路由：`GET /v1/arena/rooms`（`?refresh=1` 现场扫）、`GET /v1/arena/instances`。
`arena.models()` 改成以房间为准（`source: "workbench"`）；一台工作台都没读到时才退回 AstrBot 清单（`source: "astrbot"`），
两个来源都没列的备注仍会单独列出（`source: "note"`）。

界面：模型页每张卡显示「实例 · 模型名 · 会话链接」，右上角「扫一遍实例」按钮走 `?refresh=1`。
备注仍按**模型名**存（`_note_lookup` 的语义没变），所以路由依旧只认模型名，不认房间 ID。

## 真机实测（2026-10-31 18:18）

```
在跑：profile_1/2/3（pid 31116 / 22012 / 17208），profile_4/5 未登录、profile_6 未初始化
profile_1  10 个房间  claude-haiku-5.5、deepseek-v4.1-flash-1017、glm-5.3-flashx、gpt-6-luna、qwen3.8-flash、qwen3.8-max-0902
profile_2  12 个房间  claude-haiku-5.5、deepseek-v4-flash、deepseek-v4.1-flash-1017、glm-5.3-flash、gpt-4o、gpt-6-luna、qwen3.8-flash
profile_3   7 个房间  claude-haiku-5.5、deepseek-v4-flash、gpt-6-luna、qwen3.8-max-0902
合计 29 个房间；arena.models() → source=workbench，35 张卡（29 房间 + 6 条只有备注的）
```

同一个实例里同一个模型名出现多次（例如 profile_1 有两个 `claude-haiku-5.5`），
所以**路由不能只按模型名**：派发时还要带上会话链接 `href`，在工作台侧做「模型名 + href」双唯一匹配。

## 还没做（下一步）

* 派发改走工作台的 `kind: "browser"` 任务（profile + model + conversation_href，双唯一匹配才发），
  而不是现在的 MCP `plan_create`；这条会真的往 Arena 会话里打字，需要一次实机验证并授权。
* 插件 8 个工具里 `dfy_arena_models` 已经跟着变成「按实例的房间」；`dfy_workbench_status` 仍按 `profile_3` 单实例投影，
  要放开成全部实例得连同 `link.PLUGIN_ROUTES` 一起改（还要动安装器与插件 main.py 的托管块）。

## 门禁

`python scripts/gate.py compile suites typecheck accept` → **GATE OK**：arena 自测 135 pass / 0 fail（新增 7 条 rooms 断言）、accept 67 PASS / 0 FAIL / 6 NA。
