import { useCallback, useEffect, useState } from "react";
import { arena } from "../api";

const SUITES = ["stage1", "send", "stage2", "arena", "stage3", "stage4"];

// 设置：备份策略、界面偏好、加密凭据、模型配置与连接测试、监听诊断、一键自测。
// 核心级开关（发送总开关等）在这里只读——改它要动 config/desktop.json 并重启。
export default function SettingsPage() {
  const [st, setSt] = useState<any>(null);
  const [models, setModels] = useState<any>(null);
  const [cred, setCred] = useState<any>(null);
  const [diag, setDiag] = useState<any>(null);
  const [name, setName] = useState("arena_mcp_url");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [test, setTest] = useState<any>(null);
  const [suite, setSuite] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [rel, setRel] = useState<any>(null);
  const [bundles, setBundles] = useState<any>(null);
  const [relMsg, setRelMsg] = useState<string | null>(null);
  const [relPlan, setRelPlan] = useState<any>(null);
  const [patch, setPatch] = useState<{ source_id: string; api_base: string; timeout: string }>({ source_id: "", api_base: "", timeout: "" });

  const load = useCallback(async () => {
    const [a, b, c, d, e, f] = await Promise.all([
      arena("/v1/settings"), arena("/v1/models"), arena("/v1/credentials"), arena("/v1/diag/ports"),
      arena("/v1/release/status"), arena("/v1/diag/bundles")
    ]);
    setSt(a); setModels(b); setCred(c); setDiag(d); setRel(e); setBundles(f);
  }, []);
  useEffect(() => { void load(); }, [load]);

  const post = async (path: string, body: any) => {
    setBusy(true);
    const r = await arena(path, body);
    setBusy(false);
    void load();
    return r;
  };

  return (
    <section className="card">
      <h3>设置 <small>{st?.note || ""}</small></h3>
      <div className="row2">
        <label className="muted">自动备份（分钟，0＝关）
          <input type="number" defaultValue={st?.backup?.auto_minutes ?? 720}
                 onBlur={async (e) => {
                   const r = await post("/v1/settings", { section: "backup", fields: { auto_minutes: e.target.value } });
                   setMsg(r?.error ? `没改成：${r.error}` : `自动备份 = ${r?.backup?.auto_minutes} 分钟`);
                 }} />
        </label>
        <label className="muted">保留份数
          <input type="number" defaultValue={st?.backup?.keep ?? 20}
                 onBlur={async (e) => {
                   const r = await post("/v1/settings", { section: "backup", fields: { keep: e.target.value } });
                   setMsg(r?.error ? `没改成：${r.error}` : `保留 ${r?.backup?.keep} 份`);
                 }} />
        </label>
        <label className="muted">审计尾部行数
          <input type="number" defaultValue={st?.gui?.audit_tail ?? 60}
                 onBlur={async (e) => {
                   const r = await post("/v1/settings", { section: "gui", fields: { audit_tail: e.target.value } });
                   setMsg(r?.error ? `没改成：${r.error}` : `审计显示 ${r?.gui?.audit_tail} 行`);
                 }} />
        </label>
      </div>
      <div className="row-actions">
        {[["close_to_tray", "关窗口进托盘"], ["autostart", "开机自启"], ["confirm_destructive", "高风险操作二次确认"]].map(([k, t]) => (
          <label key={k} className="muted">
            <input type="checkbox" checked={!!st?.gui?.[k]}
                   onChange={async (e) => { await post("/v1/settings", { section: "gui", fields: { [k]: e.target.checked } }); }} /> {t}
          </label>
        ))}
      </div>
      <p className="muted">
        核心只读项：发送总开关 {st?.core_readonly?.send_enabled ? "开" : "关"} · 正文上限 {st?.core_readonly?.outbox_max_chars} 字 ·
        数据目录 {st?.core_readonly?.runtime_dir}
      </p>
      {msg && <p className={msg.startsWith("没") ? "danger" : "muted"}>{msg}</p>}

      <h4>Arena 上游地址（DPAPI 加密保存）</h4>
      <div className="row2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="凭据名（默认 arena_mcp_url）" />
        <input type="password" value={value} onChange={(e) => setValue(e.target.value)}
               placeholder="https://… 或 http://127.0.0.1:端口/…（其它一律拒）" />
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="备注（可选）" />
      </div>
      <div className="row-actions">
        <button className="ghost tiny" disabled={busy || !value.trim()} onClick={async () => {
          const r = await post("/v1/credentials", { name, value, label });
          setMsg(r?.error ? `没存上：${r.error}` : `已加密保存（${r.chars} 字，指纹 ${r.sha8}）`);
          setValue("");
        }}>加密保存</button>
        <button className="ghost tiny" disabled={busy} onClick={async () => {
          const r = await post("/v1/credentials/clear", { name: name || "arena_mcp_url", confirm: "" });
          if (r?.confirm_required) {
            const yes = await post("/v1/credentials/clear", { name: name || "arena_mcp_url", confirm: "yes" });
            setMsg(yes?.error ? `没删掉：${yes.error}` : "已删除（下一次连接会退回配置文件）");
          } else setMsg(`要确认：${r?.will || r?.error || ""}`);
        }}>清除</button>
      </div>
      <p className="muted">
        保存方式：{cred?.protected_by || "—"}；已存 {(cred?.items || []).map((c: any) => `${c.name}（${c.chars} 字 · ${c.sha8}）`).join("、") || "没有"}
      </p>

      <h4>模型配置 <small>{models?.note || ""}</small></h4>
      {models?.error && <p className="danger">{models.error}</p>}
      {(models?.sources || []).map((s: any) => (
        <div key={s.id} className="mcard">
          <div className="row-actions">
            <b>{s.id}</b>
            <span className="muted">{s.provider} · {s.type} · api_base {s.api_base || "—"} · 超时 {s.timeout ?? "默认"}</span>
            <span className="muted">密钥 {s.has_key ? `有（${s.key_chars} 位）` : "没填"}</span>
            <button className="ghost tiny" onClick={() => setPatch({ ...patch, source_id: s.id, api_base: s.api_base || "", timeout: String(s.timeout ?? "") })}>
              改这条
            </button>
            <button className="ghost tiny" disabled={busy} onClick={async () => {
              setBusy(true);
              const r = await arena("/v1/models/test", { source_id: s.id });
              setBusy(false);
              setTest(r);
            }}>连接测试</button>
          </div>
        </div>
      ))}
      {patch.source_id && (
        <div className="row2">
          <input value={patch.api_base} placeholder="api_base" onChange={(e) => setPatch({ ...patch, api_base: e.target.value })} />
          <input value={patch.timeout} placeholder="超时（秒）" onChange={(e) => setPatch({ ...patch, timeout: e.target.value })} />
          <button className="ghost tiny" disabled={busy} onClick={async () => {
            const body = { source_id: patch.source_id, base_sha8: models?.sha8 || "",
                           patch: { api_base: patch.api_base, timeout: patch.timeout }, confirm: "" };
            let r = await post("/v1/models", body);
            if (r?.confirm_required) r = await post("/v1/models", { ...body, confirm: "yes" });
            setMsg(r?.error ? `没改成：${r.error}` : `已写入（改前快照 ${r.guard}；引擎要重启才重读）`);
            setPatch({ ...patch, source_id: "" });
          }}>保存（先确认）</button>
          <button className="ghost tiny" onClick={() => setPatch({ ...patch, source_id: "" })}>取消</button>
        </div>
      )}
      {test && (
        <p className={test.ok ? "muted" : "danger"}>
          {test.ok ? `通了：HTTP ${test.http}${test.models != null ? ` · 可见 ${test.models} 个模型` : ""}` : `不通：${test.error || `HTTP ${test.http}`}`}
          {test.note ? ` · ${test.note}` : ""}
        </p>
      )}

      <h4>监听诊断 <small>只允许 127.0.0.1</small></h4>
      <p className="muted">
        非回环监听 {diag?.non_loopback_watch_ports?.length ? "有，要处理" : "没有"}
        {diag?.netstat_error ? ` · netstat 失败：${diag.netstat_error}` : ""}
        {diag?.own ? ` · 本核心端口 ${JSON.stringify(diag.own)}` : ""}
      </p>
      {(diag?.non_loopback_watch_ports || []).slice(0, 6).map((r: any, i: number) => (
        <p key={i} className="danger">{r.local} · pid {r.pid} · 端口 {r.port}</p>
      ))}

      <h4>更新与回滚 <small>只动本机应用目录</small></h4>
      <p className="muted">
        当前版本 <b>{rel?.current?.version || rel?.current?.current || "没装（直接跑源码）"}</b>
        {rel?.current?.built_at ? ` · 打包于 ${rel.current.built_at}` : ""}
        {rel?.current?.mode ? ` · ${rel.current.mode}包` : ""}
        {rel?.current?.last_health ? ` · 上次健康检查${rel.current.last_health === "通过" ? "通过" : "失败"}` : ""}
      </p>
      <p className="hint mono">{rel?.current?.install_root || ""} · 已有 {rel?.current?.dirs?.length || 0} 个版本目录</p>
      {(rel?.packages?.items || []).length > 0 ? (
        <table className="meta">
          <thead><tr><th>包</th><th>大小</th><th>时间</th><th></th></tr></thead>
          <tbody>
            {(rel.packages.items || []).slice(0, 6).map((p: any, i: number) => (
              <tr key={i}>
                <td className="mono">{p.name}{p.same_as_current ? "（就是当前版）" : ""}</td>
                <td>{p.bytes} B</td><td>{p.mtime}</td>
                <td>
                  <button className="ghost tiny" disabled={busy} onClick={async () => {
                    setBusy(true);
                    const r = await arena("/v1/release/install", { package: p.file, confirm: "" });
                    setBusy(false);
                    if (r?.confirm_required) setRelPlan({ kind: "install", package: p.file, will: r.will });
                    else setRelMsg(r?.error ? `没装上：${r.error}` : "已装好，下次启动窗口生效");
                  }}>装这个</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">没找到可安装的包：把 <code>dafeiyu-app-&lt;版本&gt;-light.zip</code> 放进
          <span className="mono"> {rel?.install_root || ""}\updates\</span> 或 <span className="mono">desktop\dist\</span>
          ，包里必须自带 SHA256SUMS.txt 和 RELEASE.json，不然核心会拒绝安装。</p>
      )}
      <div className="row-actions">
        <button className="ghost tiny" disabled={busy} onClick={async () => {
          setBusy(true);
          const r = await arena("/v1/release/rollback", { confirm: "" });
          setBusy(false);
          if (r?.confirm_required) setRelPlan({ kind: "rollback", will: r.will });
          else setRelMsg(r?.error ? `回滚不了：${r.error}` : "current 已换回上一版");
        }}>回滚上一版</button>
      </div>
      {relPlan && (
        <p className="banner">
          再确认一次：{relPlan.will}
          <br />
          <button className="send tiny" onClick={async () => {
            setBusy(true);
            const r = relPlan.kind === "install"
              ? await arena("/v1/release/install", { package: relPlan.package, confirm: "yes" })
              : await arena("/v1/release/rollback", { confirm: "yes" });
            setBusy(false);
            setRelPlan(null);
            setRelMsg(r?.error ? `没成：${r.error}` : (r?.note || "好了"));
            void load();
          }}>确认执行</button>
          <button className="ghost tiny" onClick={() => setRelPlan(null)}>算了</button>
        </p>
      )}
      {relMsg && <p className="muted">{relMsg}</p>}
      {(rel?.history?.items || []).length > 0 && (
        <table className="meta">
          <thead><tr><th>时间</th><th>动作</th><th>从</th><th>到</th><th>健康检查</th></tr></thead>
          <tbody>
            {rel.history.items.slice(0, 6).map((h: any, i: number) => (
              <tr key={i}>
                <td>{h.at}</td><td>{h.action}</td><td className="mono">{h.from || "—"}</td>
                <td className="mono">{h.to || "—"}</td>
                <td className={h.health === "通过" ? "" : "danger"}>{h.health}{h.health_error ? `：${h.health_error}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="hint">卸载：{rel?.uninstall_plan?.note || ""}</p>

      <h4>诊断包 <small>给别人排查用，已脱敏</small></h4>
      <p className="muted">
        打包版本号、端口监听、配置结构摘要与审计尾部；token / Cookie / API Key / 二维码 / MCP 地址
        一律被核心洗掉后才落盘。共 {bundles?.count ?? 0} 份。
      </p>
      <div className="row-actions">
        <button className="ghost tiny" disabled={busy} onClick={async () => {
          setBusy(true);
          const r = await arena("/v1/diag/bundle", { note: "手动" });
          setBusy(false);
          setRelMsg(r?.error ? `生成失败：${r.error}` : `诊断包：${r?.file || r?.name || "已生成"}`);
          void load();
        }}>生成诊断包</button>
        <button className="ghost tiny" disabled={busy} onClick={async () => {
          setBusy(true);
          const r = await arena("/v1/setup/reset", { confirm: "yes" });
          setBusy(false);
          setRelMsg(r?.error ? `没清掉：${r.error}` : "向导完成标记已清，下次打开界面就会再弹");
          window.dispatchEvent(new Event("dfy-setup-changed"));
        }}>重新运行向导</button>
      </div>
      {(bundles?.items || []).slice(0, 4).map((b: any, i: number) => (
        <p key={i} className="muted mono">{b.name} · {b.bytes} B · {b.created_at}</p>
      ))}

      <h4>一键自测</h4>
      <div className="row-actions">
        {SUITES.map((s) => (
          <button key={s} className="ghost tiny" disabled={busy} onClick={async () => {
            setBusy(true);
            const r = await arena("/v1/selftest", { suite: s });
            setBusy(false);
            setSuite({ suite: s, ...r });
          }}>跑 {s}</button>
        ))}
      </div>
      {suite && (
        <pre className="muted" style={{ whiteSpace: "pre-wrap" }}>
          {suite.error ? `跑不了：${suite.error}` : `${suite.suite} rc=${suite.rc} · ${suite.ms}ms\n${(suite.lines || []).join("\n")}`}
        </pre>
      )}
    </section>
  );
}
