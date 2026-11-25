# 阶段 1 · 消息与群组（只读浏览）

## 现在能做什么
- 首页：真实服务状态 + 「立即复查监听」（只判我们自己的进程是否绑到非回环地址）。
- 消息：左栏会话列表（备注名、类型、唤醒状态、未读角标、最后来消息），右栏消息流
  （气泡按 `is_self` 分左右、时间、`seq`、媒体标记、截断提示），未读起点分隔线。
  左栏 6 秒刷新、右栏 4 秒刷新，数据全部来自 `astrbot/data/plugin_data/astrbot_plugin_dafeiyu_social/state.json`。
- 群组：白名单四项核对（主人私聊、插件 owner_qq、两个群启用、旧群 <FORBIDDEN_GROUP> 被排除）
  加群表（备注名、白名单状态、未读、最后来消息）。

## 数据契约（新增）
| 路由 | 说明 |
| --- | --- |
| `GET /v1/conversations` | 会话摘要列表 |
| `GET /v1/messages?umo=<会话key>&limit<=200` | 单个会话正文，单条上限 500 字，经脱敏 |
| `GET /v1/groups` | 群 + 白名单核对 |
| `GET /v1/audit` | 审计尾（返回 `{path,bytes,total,lines}`） |

安全边界：
- 只接受 `state.json` 里存在的会话 key，未知 key 一律回「会话不存在」，绝不把入参当路径或 ID 去试探。
- 被排除的旧群即使存在会话也不返回内容（`denied/excluded` 记审计）。
- 正文只在 `/v1/messages` 出现；`/v1/status` 与审计日志都不含正文（自测断言
  `audit never logs message bodies`、`no 'token'/'password'/'secret'/'Cookie'/'Authorization' anywhere in payload`）。

## 阶段 1b：发送通道（方案 A，已接通）
链路：界面 → 主进程 → 核心 `POST /v1/send` → 写一个请求文件进
`astrbot/data/plugin_data/astrbot_plugin_dafeiyu_social/desktop_outbox/inbox/` →
插件里的 `DesktopOutbox` worker 用 AstrBot 现有的平台连接 `context.send_message()` 发出 →
成功后 `store.record_outgoing()` 记账并把请求移到 `sent/`（失败移 `failed/` 带原因）→
界面用 `GET /v1/receipt?id=` 每 2 秒取回执。

- 核心不连 QQ、不持任何凭据；`/v1/send` 是唯一可写路由，其他 POST 一律 405。
- 发送前的门：会话必须已在 state.json 里存在、非排除群、非空、≤1200 字；`send_enabled=false` 时核心直接拒。
- 真正的目标门在插件里（复用 `_is_allowed_umo`），白名单没放行的群从设计上就发不出去。
- 幂等：同一 idem 在 pending/sent 期间重复提交返回同一个 id，避免手滑重发。
- 原子写入（tmp + `os.replace` + fsync），插件不会读到半个文件；请求 15 分钟没人消费即判 stale 失败。
- 插件暂停（paused）时队列不发但也不丢。

### 已验证（2026-10-30 20:54）
```
POST /v1/send -> 200 {id: 205423-5999e560, state: pending, chars: 49}
3 秒后回执     -> sent {ok: True, chars: 49, umo: dfy_test_qq:FriendMessage:<OWNER_QQ>}
inbox 0 个 / sent 1 个 / failed 0 个
插件 state.json 长出 seq=122、from_me=True 的同文本条目（界面因此看到真实气泡，不是自欺）
AstrBot 重启后日志：desktop outbox started: ...\desktop_outbox；6185/6199 正常，OneBot established=1
SENDTEST OK (pass=26 fail=0)   # 全程跑在 .runtime/selftest-outbox 暂存队列，断言真实 inbox 数量不变
SELFTEST OK (pass=38 fail=0)
生产核对：identical=7 by-design=1 data-delta=1 UNEXPECTED=0 -> CLEAN
```
回滚点：`desktop/.runtime/backups/plugin-main.py.20261030-204728`（改前 sha f7028c25120b7424），
有意的生产改动登记在 `desktop/.runtime/intentional.json`，验收脚本据此区分「有意改动」与「意外改动」。

## 还没做
- 群白名单开关（写 `cmd_config.json`）：与备份/回滚一起在阶段 3 前接入。
- 工具调用卡片：`state.json` 不记录工具调用；候选来源是 AstrBot 的 `data/data_v4.db`
  （有 `parent_message_id / base_checkpoint_id / selected_text` 等字段），阶段 2 先确认可读性。

## 三个候选通道（存档，已选 1）
1. **经 AstrBot 插件外发（已采用）**：不新增端口、桌面侧不碰 token；代价是要改插件并重启 AstrBot。
2. 启用 NapCat OneBot HTTP（127.0.0.1:3000）：`onebot11.json` 里 `enable=false` 要改，且 NapCat 需重启，QQ 会短暂重连。
3. NapCat WebUI 调试接口：实测 `/api/auth/login` 可用（返回 256 字节凭据），但 `/api/<动作>` 全 404，
   只剩 `/api/Debug/schemas`（236 KB 动作目录）+ `/api/Debug/ws` 这套未公开通道，依赖不稳且要在内存里持凭据。
