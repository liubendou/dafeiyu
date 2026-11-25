import { useCallback, useEffect, useRef, useState } from "react";
import {
  fmtAgo, getConversations, getControlReceipt, getMessages, getOutbox, getReceipt,
  getToolTrace, sendControl, sendText,
  type ConvItem, type ThreadMsg, type ToolTrace
} from "../api";

const MAX = 1200;

type Pending = {
  key: string;
  id: string | null;
  text: string;
  state: "排队中" | "已送达" | "失败";
  error?: string;
  queuedAt: number;
};

function wake(m: string | null) {
  if (m === "diving") return "潜水";
  if (m === "awake") return "清醒";
  return m || "—";
}

function ToolCard({ t, open }: { t: ToolTrace["items"][number]; open?: boolean }) {
  return (
    <details className="tool-card" open={open}>
      <summary>
        <span className={t.ok ? "tok" : "tfail"}>{t.ok ? "✓" : "✕"}</span>
        <code className="tname">{t.name}</code>
        <span className="muted">{t.args ? `${t.args.length > 54 ? t.args.slice(0, 54) + "…" : t.args}` : "无参数"}</span>
        {t.duration_ms === null || t.duration_ms === undefined ? null : <span>{t.duration_ms} ms</span>}
      </summary>
      <div className="targs">参数：{t.args || "—"}</div>
      <div className="tres">{t.ok ? "结果：" : "失败："}{t.result || "（无返回内容）"}</div>
    </details>
  );
}

function Bubble({ m, unreadStart }: { m: ThreadMsg; unreadStart: boolean }) {
  return (
    <>
      {unreadStart && (
        <div className="unreadline">
          <span>未读起点</span>
        </div>
      )}
      <div className={"bub-row " + (m.from_me ? "me" : "them")}>
        <div className={"bub " + (m.from_me ? "me" : "them")}>
          <div className="meta">
            <b>{m.from_me ? "大肥鱼" : m.sender_name || "对方"}</b>
            <span>{m.iso || "—"}</span>
            {m.seq !== null && <span>#{m.seq}</span>}
            {m.media.map((x) => (
              <span key={x} className="tag">{x}</span>
            ))}
            {m.truncated && <span className="tag">已截断</span>}
          </div>
          <p>{m.text || <i className="muted">（空内容）</i>}</p>
        </div>
      </div>
    </>
  );
}

function PendingBubble({ p, onRetry }: { p: Pending; onRetry: () => void }) {
  const cls = p.state === "失败" ? "danger" : p.state === "已送达" ? "ok" : "warn";
  return (
    <div className="bub-row me">
      <div className={"bub me " + (p.state === "失败" ? "fail" : "")}>
        <div className="meta">
          <b>大肥鱼</b>
          <span className={`pill ${cls}`}>{p.state}</span>
          {p.id && <span className="muted">{p.id}</span>}
          {p.state === "失败" && (
            <button className="ghost tiny" onClick={onRetry}>
              重试
            </button>
          )}
        </div>
        <p>{p.text}</p>
        {p.error && <p className="danger">{p.error}</p>}
      </div>
    </div>
  );
}

export default function MessagesPage() {
  const [convos, setConvos] = useState<ConvItem[]>([]);
  const [umo, setUmo] = useState("");
  const [thread, setThread] = useState<any>(null);
  const [note, setNote] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [queue, setQueue] = useState<any>(null);
  const [trace, setTrace] = useState<ToolTrace | null>(null);
  const [ctrl, setCtrl] = useState<string | null>(null);
  const [ctrlBusy, setCtrlBusy] = useState(false);
  const [gpaused, setGpaused] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);

  const loadConvos = useCallback(async () => {
    const r = await getConversations();
    if (!r) return setNote("桌面桥未就绪");
    if (r.error) return setNote("读不到会话：" + r.error);
    setNote(null);
    setConvos(r.items || []);
    setGpaused(Boolean((r as any).global_paused));
    if (!umo && r.items && r.items.length) setUmo(r.items[0].umo);
  }, [umo]);

  const loadThread = useCallback(async () => {
    if (!umo) return;
    const r = await getMessages(umo);
    if (r) setThread(r);
  }, [umo]);

  useEffect(() => {
    loadConvos();
    const h = setInterval(loadConvos, 6000);
    return () => clearInterval(h);
  }, [loadConvos]);

  useEffect(() => {
    loadThread();
    const h = setInterval(loadThread, 4000);
    return () => clearInterval(h);
  }, [loadThread]);

  // 工具调用轨迹：读 AstrBot 自己的记录，不另起一份日志
  const loadTrace = useCallback(() => {
    if (!umo) {
      setTrace(null);
      return;
    }
    getToolTrace(umo, 20).then((t) => setTrace(t));
  }, [umo]);

  useEffect(() => {
    loadTrace();
    const h = setInterval(loadTrace, 7000);
    return () => clearInterval(h);
  }, [loadTrace]);

  const doControl = async (action: "mark_read" | "pause" | "resume") => {
    setCtrlBusy(true);
    setCtrl(null);
    const r = await sendControl(action, action === "pause" || action === "resume" ? "" : umo);
    let msg = r.error ? `核心拒绝：${r.error}` : `已排入控制队列（${r.id}）`;
    if (!r.error && r.id) {
      await new Promise((res) => setTimeout(res, 2500));
      const rec = await getControlReceipt(r.id);
      if (rec?.state === "sent") {
        const d: any = rec.detail?.result || {};
        msg =
          action === "mark_read"
            ? `已把 ${d.cleared ?? 0} 条未读标记为已读`
            : d.paused
              ? "社交回复已暂停（大肥鱼不再自动回消息，桌面仍可手动发）"
              : "社交回复已恢复";
        if (action === "mark_read") loadThread();
      } else if (rec?.state === "failed") {
        msg = `插件执行失败：${String((rec.detail as any)?.error || "未知")}`;
      } else {
        msg += " · 插件还没处理到，稍后再看";
      }
    }
    setCtrl(msg);
    setCtrlBusy(false);
  };

  // queue depth explains "为什么还没发出去"
  useEffect(() => {
    getOutbox().then(setQueue);
    const h = setInterval(() => getOutbox().then(setQueue), 5000);
    return () => clearInterval(h);
  }, []);

  // poll receipts only while something is in flight
  useEffect(() => {
    const waiting = pending.filter((p) => p.state === "排队中" && p.id);
    if (!waiting.length) return;
    let stop = false;
    const tick = async () => {
      for (const p of waiting) {
        const r = await getReceipt(p.id as string);
        if (stop || !r) continue;
        setPending((cur) =>
          cur.map((x) => {
            if (x.key !== p.key) return x;
            if (r.state === "sent") return { ...x, state: "已送达" as const };
            if (r.state === "failed")
              return { ...x, state: "失败" as const, error: String(r.detail?.error || "插件发送失败") };
            return x;
          })
        );
      }
    };
    tick();
    const h = setInterval(tick, 2000);
    return () => {
      stop = true;
      clearInterval(h);
    };
  }, [pending]);

  // once the real bubble arrives from state.json, the optimistic one is redundant
  useEffect(() => {
    const items: ThreadMsg[] = thread?.items || [];
    if (!items.length) return;
    setPending((cur) =>
      cur.filter((p) => {
        if (p.state !== "已送达") return true;
        const landed = items.some((m) => m.from_me && (m.text || "").trim() === p.text.trim());
        return !landed;
      })
    );
  }, [thread]);

  useEffect(() => {
    if (endRef.current) endRef.current.scrollIntoView({ block: "end" });
  }, [thread, pending]);

  const send = async (text: string, retryOf?: string) => {
    const body = text.trim();
    if (!body || !umo || busy) return;
    const key = String(Date.now()) + Math.random().toString(16).slice(2, 6);
    setBusy(true);
    setPending((cur) => [
      ...cur.filter((p) => p.key !== retryOf),
      { key, id: null, text: body, state: "排队中" as const, queuedAt: Date.now() }
    ]);
    const r = await sendText(umo, body, retryOf ? key + "-retry" : key);
    setPending((cur) =>
      cur.map((p) =>
        p.key === key
          ? r && r.id
            ? { ...p, id: r.id, state: r.state === "sent" ? ("已送达" as const) : ("排队中" as const) }
            : { ...p, state: "失败" as const, error: (r && r.error) || "核心无响应" }
          : p
      )
    );
    setBusy(false);
    getOutbox().then(setQueue);
  };

  const onSend = () => {
    setDraft("");
    void send(draft);
  };

  const items: ThreadMsg[] = thread?.items || [];
  const lrs = Number(thread?.last_read_seq ?? 0);
  const firstUnread = items.findIndex((m) => !m.from_me && lrs && Number(m.seq || 0) > lrs);
  const tooLong = draft.length > MAX;

  return (
    <div className="msgs">
      <aside className="convs">
        <h3>
          会话 <small>{convos.length ? `${convos.length} 个 · 插件 state.json` : ""}</small>
        </h3>
        {note && <p className="muted">{note}</p>}
        {convos.map((c) => (
          <button key={c.umo} className={"convo " + (c.umo === umo ? "on" : "")} onClick={() => setUmo(c.umo)}>
            <span className="lab">{c.label || c.session_id}</span>
            <span className="sub">
              {c.kind === "group" ? "群" : "私聊"} · {wake(c.wake_mode)} · {fmtAgo(c.last_incoming_at)}
            </span>
            {c.unread ? <span className="badge">{c.unread}</span> : null}
          </button>
        ))}
        {!convos.length && !note && <p className="empty">还没有会话记录。</p>}
      </aside>

      <section className="thread">
        <div className="thread-head">
          <b>{thread?.label || "选择会话"}</b>
          <span className="muted">
            共 {thread?.total ?? 0} 条 · 本页 {items.length} 条
            {thread?.unread ? ` · 未读 ${thread.unread}` : ""}
            {queue ? ` · 队列待处理 ${queue.inbox ?? 0}` : ""}
          </span>
          {gpaused ? <span className="pill danger">自动回复已暂停</span> : null}
          <span className="row-actions">
            <button className="ghost tiny" disabled={!umo || ctrlBusy} onClick={() => void doControl("mark_read")}>
              标记已读
            </button>
            <button className="ghost tiny" disabled={ctrlBusy} onClick={() => void doControl("pause")}>
              暂停自动回复
            </button>
            <button className="ghost tiny" disabled={ctrlBusy} onClick={() => void doControl("resume")}>
              恢复自动回复
            </button>
          </span>
        </div>
        {ctrl && <div className="hint">{ctrl}</div>}
        <div className="thread-body">
          {thread?.error && <div className="banner">核心拒绝：{thread.error}</div>}
          {items.map((m, i) => (
            <Bubble key={String(m.seq) + i} m={m} unreadStart={i === firstUnread} />
          ))}
          {pending
            .filter((p) => !items.some((m) => m.from_me && (m.text || "").trim() === p.text.trim()))
            .map((p) => (
              <PendingBubble key={p.key} p={p} onRetry={() => void send(p.text, p.key)} />
            ))}
          {!items.length && !thread?.error && <p className="empty">该会话暂无可显示的消息。</p>}
          <div ref={endRef} />
        </div>
        <div className="composer">
          <textarea
            value={draft}
            placeholder="以主人身份直接发一条（Enter 发送，Shift+Enter 换行）"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!tooLong) onSend();
              }
            }}
          />
          <button className="send" onClick={onSend} disabled={busy || !draft.trim() || tooLong}>
            {busy ? "排队中…" : "发送"}
          </button>
          <p className={"muted" + (tooLong ? " danger" : "")}>
            {draft.length}/{MAX} 字
            {tooLong ? " · 超了，删一点" : ""} · 经 AstrBot 插件队列发出
            {queue && queue.enabled === false ? "（发送已在设置里关闭）" : ""}
            {queue && (queue.failed ?? 0) > 0 ? ` · 失败 ${queue.failed} 条` : ""}
          </p>
        </div>
        <div className="trace">
          <div className="tool-head">
            <b>工具调用轨迹</b>
            <span className="muted">
              {trace?.error
                ? trace.error
                : `${trace?.total_calls ?? 0} 次调用 · 显示最近 ${trace?.returned ?? 0} 条 · ${trace?.note || ""}`}
            </span>
          </div>
          {(trace?.items || []).map((t) => (
            <ToolCard key={String(t.call_id) + t.index} t={t} />
          ))}
          {!(trace?.items || []).length && !trace?.error && (
            <p className="empty">该会话暂无工具调用记录。</p>
          )}
        </div>
      </section>
    </div>
  );
}
