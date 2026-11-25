import { useCallback, useEffect, useState } from "react";
import { arena } from "../api";

type Arm = { id: string; name: string } | null;

// 备份：一次写入一份快照，恢复前先自保，sqlite 只快照不整库覆盖（引擎在跑）。
export default function BackupPage() {
  const [data, setData] = useState<any>(null);
  const [arm, setArm] = useState<Arm>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [label, setLabel] = useState("");
  const [importPath, setImportPath] = useState("");
  const [armImport, setArmImport] = useState(false);

  const load = useCallback(async () => setData(await arena("/v1/backups")), []);
  useEffect(() => { void load(); }, [load]);

  const act = async (path: string, body: any) => {
    setBusy(true);
    const r = await arena(path, body);
    setBusy(false);
    void load();
    return r;
  };

  return (
    <section className="card">
      <h3>
        备份 <small>每 {data?.auto_minutes || "—"} 分钟自动一份，留 {data?.keep ?? "—"} 份；最近自动：{data?.last_auto || "还没跑过"}</small>
      </h3>
      <p className="muted">
        覆盖的东西：{(data?.targets || []).map((t: any) => `${t.name}（${t.bytes} B）`).join("、") || "读不到"}
      </p>
      <p className="muted">{data?.note}</p>
      <div className="row2">
        <input value={label} placeholder="给这份快照起个短标签（字母数字-_）" onChange={(e) => setLabel(e.target.value)} />
        <button disabled={busy} onClick={async () => {
          const r = await act("/v1/backups", { label: label || "manual" });
          setMsg(r?.error ? `没备份成：${r.error}` : `已存快照 ${r.id}（${r.files?.length ?? 0} 个文件）`);
          setLabel("");
        }}>立刻备份</button>
        <button className="ghost tiny" disabled={busy} onClick={async () => {
          const r = await act("/v1/backups/export", { note: "gui" });
          setMsg(r?.error ? `没导出来：${r.error}` : `加密导出包：${r.file}（${r.bytes} B，只有当前 Windows 用户解得开）`);
        }}>加密导出</button>
      </div>
      {msg && <p className={msg.startsWith("没") ? "danger" : "muted"}>{msg}</p>}
      {(data?.items || []).map((b: any) => (
        <div key={b.id} className="mcard">
          <div className="row-actions">
            <b>{b.id}</b>
            <span className="muted">{b.created_at} · {b.files} 个文件 · {b.bytes} B{b.errors ? ` · ${b.errors} 个没读成` : ""}</span>
            {arm && arm.id === b.id && arm.name === "" ? (
              <button className="tiny" disabled={busy} onClick={async () => {
                const r = await act("/v1/backups/restore", { id: b.id, confirm: "yes" });
                setArm(null);
                setMsg(r?.error ? `没恢复成：${r.error}` : `已还原 ${r.restored?.length ?? 0} 个文件（还原前又存了 ${r.guard}）`);
              }}>确认：全部写回</button>
            ) : (
              <button className="ghost tiny" disabled={busy} onClick={() => setArm({ id: b.id, name: "" })}>恢复全部</button>
            )}
          </div>
          {(b.items || []).map((f: any) => (
            <div key={f.name} className="row-actions">
              <span className="muted">
                {f.name} · {f.kind} · {f.bytes} B · {f.sha8}{f.error ? ` · 读失败 ${f.error}` : ""}
              </span>
              {f.kind !== "sqlite" && (
                arm && arm.id === b.id && arm.name === f.name ? (
                  <button className="tiny" disabled={busy} onClick={async () => {
                    const r = await act("/v1/backups/restore", { id: b.id, name: f.name, confirm: "yes" });
                    setArm(null);
                    setMsg(r?.error ? `没恢复成：${r.error}` : `已还原 ${f.name}（${r.restored?.length ?? 0} 个文件）`);
                  }}>确认写回</button>
                ) : (
                  <button className="ghost tiny" disabled={busy} onClick={() => setArm({ id: b.id, name: f.name })}>只恢复这个</button>
                )
              )}
              {f.kind === "sqlite" && <span className="muted">（只快照；整库覆盖要停引擎后手工做）</span>}
            </div>
          ))}
        </div>
      ))}
      <h4>从导出包导入</h4>
      <div className="row2">
        <input value={importPath} placeholder={data?.exports?.[0]?.file ? `例如 ${data.exports[0].file}` : "导出文件的完整路径"}
               onChange={(e) => setImportPath(e.target.value)} />
        <button className="ghost tiny" disabled={busy} onClick={async () => {
          if (!armImport) {
            const r = await act("/v1/backups/import", { file: importPath });
            if (r?.confirm_required) { setArmImport(true); setMsg(`要确认：${r.will} —— 再点一次就写回`); }
            else setMsg(r?.error ? `读不成：${r.error}` : "这个包不需要确认（不太对劲，先不写）");
            return;
          }
          setArmImport(false);
          const r = await act("/v1/backups/import", { file: importPath, confirm: "yes" });
          setMsg(r?.error ? `没导入成：${r.error}` : `已写回 ${r.restored?.length ?? 0} 个文件（之前的状态在 ${r.guard}）`);
        }}>{armImport ? "确认写回" : "检查并确认"}</button>
      </div>
      {(data?.exports || []).length > 0 && (
        <p className="muted">
          现有导出包：{data.exports.map((e: any) => `${e.file}（${e.bytes} B）`).join("、")}
        </p>
      )}
      {arm && (
        <p className="muted">
          点「确认」才会真写回，写回前我会先把现在这份再存一次。
          <button className="ghost tiny" onClick={() => setArm(null)}>取消</button>
        </p>
      )}
    </section>
  );
}
