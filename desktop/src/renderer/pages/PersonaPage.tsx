import { useCallback, useEffect, useState } from "react";
import { arena } from "../api";

// 人格：事实源是 AstrBot 的 personas 表；personas/*.md 是另一份旧稿。两份都能改，
// 但界面上永远说清改的是哪一份，不拿旧稿冒充生效配置。
export default function PersonaPage() {
  const [src, setSrc] = useState<any>(null);
  const [target, setTarget] = useState<{ source: string; persona_id: string }>({ source: "db", persona_id: "病娇大肥鱼" });
  const [cur, setCur] = useState<any>(null);
  const [text, setText] = useState("");
  const [hist, setHist] = useState<any[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [armed, setArmed] = useState<string | null>(null);

  const load = useCallback(async () => {
    setSrc(await arena("/v1/persona/sources"));
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    let dead = false;
    void arena(`/v1/persona?source=${target.source}&persona_id=${encodeURIComponent(target.persona_id)}`).then((d) => {
      if (dead) return;
      setCur(d); setText(d?.text || "");
    });
    void arena(`/v1/persona/history?source=${target.source}&persona_id=${encodeURIComponent(target.persona_id)}`)
      .then((d) => !dead && setHist(d?.items || []));
    return () => { dead = true; };
  }, [target]);

  const save = async (confirm: boolean) => {
    setBusy(true);
    const r = await arena("/v1/persona", {
      source: target.source, persona_id: target.persona_id, text,
      base_sha8: cur?.sha8 || "", confirm: confirm ? "yes" : ""
    });
    setBusy(false);
    if (r?.confirm_required) { setConfirming(true); setMsg(`要确认：${r.will}`); return; }
    setConfirming(false);
    setMsg(r?.error ? `没改成：${r.error}` : `已写入 ${r.chars} 字${r.restart_required ? "（引擎要重启才生效，我没动它）" : ""}`);
    void load();
  };

  const rows = src?.db?.rows || [];
  return (
    <section className="card">
      <h3>
        人格 <small>{src?.note || ""}</small>
      </h3>
      <div className="row-actions">
        {rows.map((r: any) => (
          <button key={"db" + r.persona_id}
                  className={"ghost tiny" + (target.source === "db" && target.persona_id === r.persona_id ? " on" : "")}
                  onClick={() => setTarget({ source: "db", persona_id: r.persona_id })}>
            生效中 · {r.persona_id}（{r.chars} 字）
          </button>
        ))}
        {src?.md?.available && (
          <button className={"ghost tiny" + (target.source === "md" ? " on" : "")}
                  onClick={() => setTarget({ source: "md", persona_id: "" })}>
            旧稿 md（{src.md.bytes} B）
          </button>
        )}
        {!src?.db?.available && <span className="danger">读不到 personas 表：{src?.db?.error || "未知"}</span>}
      </div>
      <p className="muted">
        当前 sha8 {cur?.sha8 || "—"} · {cur?.chars ?? 0} 字 · 改于 {cur?.updated_at || "—"}
        {target.source === "md" ? " · 这份只是存档，引擎不读" : " · 写完要重启 AstrBot 才会重读"}
      </p>
      <textarea className="mono" style={{ minHeight: 300 }} value={text}
                onChange={(e) => setText(e.target.value)} />
      <div className="row-actions">
        <button disabled={busy} onClick={() => void save(!confirming)}>
          {confirming ? "再点一次确认写入" : "保存（先确认）"}
        </button>
        {confirming && <button className="ghost tiny" onClick={() => { setConfirming(false); setMsg(null); }}>取消</button>}
        <button className="ghost tiny" disabled={busy} onClick={() => void load()}>重新载入</button>
        <span className="muted">{text.length} 字</span>
      </div>
      {msg && <p className={msg.startsWith("没改成") ? "danger" : "muted"}>{msg}</p>}
      <h4>版本历史</h4>
      {hist.length === 0 && <p className="muted">还没有历史（第一次改动时会自动留下改前那一版）</p>}
      {hist.map((h: any) => (
        <div key={h.id} className="row-actions">
          <span className="muted">{h.ts} · {h.chars} 字 · {h.sha8}{h.note ? ` · ${h.note}` : ""}</span>
          <button className="ghost tiny"
                  onClick={() => void (async () => {
                    if (armed !== h.id) { setArmed(h.id); setMsg(`要确认：${await arena("/v1/persona/restore", {
                      source: target.source, persona_id: target.persona_id, entry: h.id, confirm: ""
                    }).then((r: any) => r?.will || r?.error || "")} —— 再点一次就退回`); return; }
                    setArmed(null);
                    const r = await arena("/v1/persona/restore", {
                      source: target.source, persona_id: target.persona_id, entry: h.id, confirm: "yes"
                    });
                    setMsg(r?.error ? `没退回去：${r.error}` : `已退回 ${h.ts} 那一版`);
                    void load();
                  })()}>
            {armed === h.id ? "确认退回这一版" : "退回这一版"}
          </button>
        </div>
      ))}
    </section>
  );
}
