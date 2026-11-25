# 阶段 3：人格、记忆、备份、设置

日期 2026-10-30 · 状态 **已上线并通过门禁**（五套自检 38 / 26 / 57 / 123 / 112，`tsc --noEmit` 0 错，`vite build` ✓）

对照设计文档 §6.6 数据归属、§7.1 网络、§7.2 凭据、§7.3 权限、§8.3 更新回滚、§9 阶段 3、§10 验收 10/11/12。
接口名按 §12.1：`GET/POST /v1/persona`、`GET/POST /v1/models`、`GET /v1/memory/search`（本项目内叫 `/v1/memory`）、`GET/POST /v1/backups`、`POST /v1/backups/restore`。
文档写的是 `PUT`，我们的桥只转发 GET/POST，所以用 POST 落在同一路径上，语义不变。

## 新增内核模块（全 stdlib，无新依赖）

| 模块 | 干什么 |
| --- | --- |
| `credentials.py` | DPAPI（当前用户）加密保存密钥/上游地址；只存密文 + 指纹，**没有任何路由能读回明文** |
| `backup.py` | 快照 / 列表 / 单文件恢复 / 加密导出与导入 / 自动备份与保留数；sqlite 用备份 API 取一致副本 |
| `persona.py` | 人格读写 + 版本历史（乐观锁 sha8、短文拒绝、历史退回、写完自校验，不一致自动倒回去） |
| `memories.py` | 长期记忆只读搜索/浏览（按插件自己的 `legacy_state_dir` 找库，不猜目录） |
| `modelcfg.py` | provider 配置的读 / 改（白名单字段）/ 连接测试（`GET /models`，不花 token，密钥永不出界） |
| `settings.py` | 备份策略 + 界面偏好；核心级开关只读展示 |

GUI 新增四页：**人格**、**记忆**、**备份**、**设置**（含凭据、模型配置与连接测试、监听诊断、一键自测）。

## 三条安全边界（都是"宁可拒绝也不假装成功"）

1. **恢复只碰 json/text**。`instances.json` 那类 sqlite 库**只快照不整库覆盖**——引擎在跑，整库盖回去会吃掉它自己的并发写入；想按条退回去用历史。
2. **写回必须自校验**。备份恢复、人格写入、provider 保存全部：先给自己存一份 → 写 → 读回比对 sha → 不一致立刻倒回，并把 guard 编号告诉你。
3. **DPAPI 只保机密，不保完整**。实测：把 blob 中间一个字节翻掉，`CryptUnprotectData` 照样返回字节（甚至是原值——翻在头部时）。所以 `open_secret()` 自己校验 sha8，对不上就当读不到。这也说明它挡的是"文件坏了/写了一半"，不是"能读你账户的本地攻击者"——那种人本来就能解，任何本地方案都挡不住，文档里不吹。

## 现场校正（我原来的假设错了两处）

* **AstrBot 根目录是 `C:\Users\<user>\astrbot`**，不在 `大肥鱼\` 下。
* 人格的**事实源是 `data_v4.db` 的 `personas` 表**（id=1 `default` 41 字，id=2 `病娇大肥鱼` 1593 字，都停在 10-28）；`personas\dafeiyu_system_prompt.md` 是 1594 字的近似副本。界面把两条都列出来并标清哪条才生效，改哪条就说改哪条。
* 长期记忆库不在 `data/plugin_data/…`，而在**插件配置里 `legacy_state_dir` 指的目录**（278528 B，`messages` 559 行 + FTS5 + `group_facts`）。现在按这条真实解析路径找，找不到就把"找过哪里"一起报出来。

## 阶段 3 期间被自检抓出来的真 bug（4 个）

1. `time.strftime("...%f")` 在 Windows 上直接 `ValueError`（Linux 上更阴——把 `%f` 原样写进字符串）→ 人格写入全废。改用 `datetime.now()`。
2. `import_archive` 拿**未解析**的入参路径去读导出包，确认那一步必然 `None.get` 崩 → 改成解析一次并复用，并把"包里没有本机认得的项"变成人话。
3. `persona.restore` 被 `MIN_CHARS` 拦住：恢复一份本来就短的历史版本不可能 → 加 `from_history` 豁免（GUI 路由拿不到这个参数）。
4. `modelcfg` 对越界超时**悄悄夹紧**（99999 → 600）而不是拒绝 → 改成明说"我不替你改成别的数"。

另外把 `selftest_stage2` / `selftest_arena` 里"可写路由必须恰好等于这张清单"的断言按阶段 3 新增的 12 条钉住：以后再加写路由，必须在测试里有意识地改一次。

## 真实验收（只读，不动生产）

* 一次性核心 + 生产配置：`/v1/settings`、`/v1/credentials`、`/v1/models`、`/v1/persona/sources`、`/v1/persona?source=db|md`、`/v1/memory/info`、`/v1/memory`、`/v1/backups` 全部正常返回；`/v1/memory` 读出真实条目。
* `/v1/models` 里 provider 的 `api_base` 是**要给你编辑的**配置项，因此会显示；密钥只显示"有/多少位"。审计与自检输出里 `sk-` 与 `token=` 均为 0 命中。
* 自动备份已生效：生产 `.runtime/backups/20261030-222629-auto` 是阶段 3 上线后 GUI/核心启动时自动产生的第一份。
* 自检全程只用临时目录：生产 `personas` 表两条记录的长度与 `updated_at` 在跑完之后一字未变（41 / 1593，10-28）。

## 还没做（各需你一句新的"好"）

真实派单一次、真改一条群白名单、2b-ii（插件侧 7 个 `dfy_arena_*` + `browser_*`，要重启 AstrBot）、以及阶段 4 的安装包/首启动向导。
