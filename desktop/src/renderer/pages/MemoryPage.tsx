import { useCallback, useEffect, useState } from "react";
import { arena } from "../api";

// 长期记忆：只读。事实源是插件的 long-memory.db，我们不双写、不猜内容。
export default function MemoryPage() {
  const [info, setInfo] = useState<any>(null);
  const [q, setQ] = useState("");
  const [table, setTable] = useState("");
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    const d = await arena(`/v1/memory?limit=20${table ? "&table=" + encodeURIComponent(table) : ""}${q ? "&q=" + encodeURIComponent(q) : ""}`);
    setBusy(false);
    setData(d);
  }, [q, table]);

  useEffect(() => { void arena("/v1/memory/info").then(setInfo); }, []);
  useEffect(() => { void load(); }, [load]);

  const tables: any[] = info?.tables || [];
  return (
    <section className="card">
      <h3>
        记忆 <small>{info?.available ? `读的是 ${info.path}（${info.bytes} B）` : info?.error || "读不到"}</small>
      </h3>
      <div className="row2">
        <input value={q} placeholder="搜一个词、一个人名、一句黑话"
               onChange={(e) => setQ(e.target.value)} />
        <select value={table} onChange={(e) => setTable(e.target.value)}>
          <option value="">自动（挑有正文的那张表）</option>
          {tables.map((t) => (
            <option key={t.name} value={t.name}>{t.name} · {t.rows} 行{t.text_col ? "" : "（没找到正文列）"}</option>
          ))}
        </select>
        <button className="ghost tiny" disabled={busy} onClick={() => void load()}>{busy ? "查中…" : "查"}</button>
      </div>
      {info && !info.available && <p className="danger">{info.error}{info.looked_in ? `（找过：${info.looked_in}）` : ""}</p>}
      {data?.error && <p className="danger">{data.error}</p>}
      {info?.available && (
        <p className="muted">
          {data?.mode === "search" ? `命中 ${data.count} 条${data.total != null ? `（库里匹配 ${data.total} 条）` : ""}` : `最近 ${data?.count ?? 0} 条`} · 表 {data?.table || "—"} · 只读
        </p>
      )}
      {(data?.items || []).length === 0 && info?.available && !data?.error && (
        <p className="muted">没有可显示的内容——通常是还没积累到，或这张表没有文本列</p>
      )}
      {(data?.items || []).map((it: any, i: number) => (
        <div key={i} className="mcard">
          {Object.entries(it).map(([k, v]) => (
            <p key={k} className="muted">
              <b>{k}</b> {String(v).slice(0, 400)}
            </p>
          ))}
        </div>
      ))}
      <p className="muted">{info?.note || ""}</p>
    </section>
  );
}
