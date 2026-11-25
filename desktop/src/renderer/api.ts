export interface Status {
  ok?: boolean;
  generated_at?: number;
  duration_ms?: number;
  core?: { pid: number; version: string; mode: string };
  blocks?: Record<string, { label: string; state: string; detail: string }>;
  whitelist?: {
    all_ok?: boolean;
    checks?: { name: string; ok: boolean; detail?: string }[];
    plugin_flags?: Record<string, unknown>;
    errors?: string[];
  };
  plugin?: {
    state?: {
      session_count?: number;
      state_bytes?: number;
      state_age_seconds?: number;
      sessions?: {
        key: string;
        kind: string;
        message_count: number;
        unread: number | null;
        paused: boolean;
        last_message_at: number | string | null;
      }[];
      error?: string;
    };
    persona?: { available: boolean; bytes?: number; lines?: number; name?: string };
  };
  services?: Record<string, any>;
  network?: {
    verdict?: string;
    listeners?: Record<string, { port: number; listening: boolean; loopback_only: boolean; pids: string[] }>;
    established?: Record<string, number>;
    non_loopback_watch_ports?: { port: number; local: string; pid: string }[];
    netstat_error?: string | null;
  };
  production?: Record<string, unknown>;
  error?: string;
  detail?: string;
}

export async function getStatus(): Promise<Status | null> {
  const w = window as any;
  if (!w.dafeiyu) return null;
  const r = await w.dafeiyu.core("/v1/status");
  return (r || null) as Status;
}

export async function getAudit(): Promise<any> {
  const w = window as any;
  if (!w.dafeiyu) return { lines: [] };
  return await w.dafeiyu.core("/v1/audit");
}

export function fmtAgo(ms?: number | null): string {
  if (ms === null || ms === undefined) return "—";
  const s = ms > 1e11 ? Math.max(0, Math.round((Date.now() - ms) / 1000)) : Math.round(ms);
  if (s < 60) return `${s} 秒前`;
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`;
  return `${Math.round(s / 86400)} 天前`;
}

export interface ConvItem {
  umo: string;
  kind: "group" | "private";
  label: string | null;
  session_id: string | null;
  platform?: string | null;
  message_count: number;
  unread: number | null;
  last_read_seq: number | null;
  seq?: number | null;
  paused: boolean;
  wake_mode: string | null;
  last_incoming_at: number | null;
  last_incoming_iso: string | null;
  last_ai_reply_at: number | null;
  in_whitelist: boolean;
  excluded: boolean;
}

export interface ThreadMsg {
  seq: number | null;
  at: number | null;
  iso: string | null;
  from_me: boolean;
  is_owner: boolean;
  sender_name: string | null;
  text: string;
  truncated: boolean;
  media: string[];
  message_id: string | null;
}

async function core(path: string): Promise<any | null> {
  const w = window as any;
  if (!w.dafeiyu) return null;
  return (await w.dafeiyu.core(path)) || null;
}

export async function getConversations() {
  return (await core("/v1/conversations")) as { items: ConvItem[]; total?: number; error?: string } | null;
}

export async function getMessages(umo: string) {
  return (await core("/v1/messages?umo=" + encodeURIComponent(umo))) as {
    items: ThreadMsg[]; total?: number; returned?: number; unread?: number | null;
    last_read_seq?: number | null; label?: string | null; error?: string;
  } | null;
}

export async function getDiagPorts(): Promise<any | null> {
  return await core("/v1/diag/ports");
}

export async function getGroups() {
  return (await core("/v1/groups")) as {
    items: ConvItem[]; expected_groups?: string[]; forbidden_group?: string;
    checks?: { name: string; ok: boolean; detail?: string }[]; all_ok?: boolean; error?: string;
  } | null;
}

export interface SendResult {
  id?: string;
  state?: "pending" | "sent" | "failed" | string;
  deduplicated?: boolean;
  chars?: number;
  error?: string;
}

export async function sendText(umo: string, text: string, idem: string): Promise<SendResult> {
  const w = window as any;
  if (!w.dafeiyu) return { error: "桌面桥未就绪" };
  const r = await w.dafeiyu.core("/v1/send", {
    method: "POST",
    body: JSON.stringify({ umo, text, idem })
  });
  return (r || { error: "核心无响应" }) as SendResult;
}

export async function getReceipt(id: string) {
  return (await core("/v1/receipt?id=" + encodeURIComponent(id))) as
    { id: string; state: string; detail?: Record<string, any> } | null;
}

export async function getOutbox() {
  return (await core("/v1/outbox")) as {
    enabled?: boolean; max_chars?: number; inbox?: number | null;
    sent?: number | null; failed?: number | null; note?: string;
  } | null;
}

export interface ToolItem {
  index: number;
  name: string;
  args: string | null;
  ok: boolean;
  result: string | null;
  duration_ms: number | null;
  call_id?: string | null;
}

export interface ToolTrace {
  items: ToolItem[];
  total_calls?: number;
  returned?: number;
  by_name?: Record<string, number>;
  conversation_id?: string | null;
  note?: string;
  error?: string;
  available?: boolean;
}

async function corePost(path: string, body: unknown) {
  const w = window as any;
  if (!w.dafeiyu) return { error: "桌面桥未就绪" };
  const r = await w.dafeiyu.core(path, { method: "POST", body: JSON.stringify(body) });
  return (r || { error: "核心无响应" }) as any;
}

export async function getToolTrace(umo: string, limit = 20) {
  return (await core("/v1/tools?umo=" + encodeURIComponent(umo) + "&limit=" + limit)) as ToolTrace | null;
}

export interface ControlResult {
  id?: string;
  state?: string;
  action?: string;
  error?: string;
  detail?: Record<string, any>;
}

export async function sendControl(
  action: "mark_read" | "pause" | "resume" | "wake" | "sleep",
  umo = "",
  args: Record<string, number> = {}
): Promise<ControlResult> {
  return corePost("/v1/control", { action, umo, args }) as Promise<ControlResult>;
}

export async function getControlReceipt(id: string) {
  return (await core("/v1/control/receipt?id=" + encodeURIComponent(id))) as
    { id: string; state: string; detail?: Record<string, any> } | null;
}

export interface GroupOpResult {
  ok?: boolean;
  changed?: boolean;
  confirm_required?: boolean;
  will?: string;
  in_astrbot_whitelist?: boolean;
  in_plugin_groups?: boolean;
  backup?: string;
  restart_required?: boolean;
  note?: string;
  error?: string;
}

/** Two-step by design: the first call returns what would change, the second carries confirm. */
/** Arena gateway: thin passthrough; every value here is already redacted by the core. */
export async function arena(path: string, body?: unknown) {
  const w = window as any;
  if (!w.dafeiyu) return { error: "桌面桥未就绪" };
  if (body === undefined) return ((await w.dafeiyu.core(path)) || { error: "核心无响应" }) as any;
  return (await w.dafeiyu.core(path, { method: "POST", body: JSON.stringify(body) })) as any;
}

export async function setGroup(groupId: string, enable: boolean, confirm = false): Promise<GroupOpResult> {
  return corePost("/v1/groups/" + (enable ? "enable" : "disable"), {
    group_id: groupId,
    confirm: confirm ? "yes" : ""
  }) as Promise<GroupOpResult>;
}
