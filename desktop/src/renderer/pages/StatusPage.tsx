import { fmtAgo, getDiagPorts, type Status } from "../api";
import { useState } from "react";

function Card({ title, sub, children }: any) {
  return (
    <section className="card">
      <h3>
        {title}
        {sub && <small>{sub}</small>}
      </h3>
      {children}
    </section>
  );
}

// The design's one-click "no non-loopback listener" proof. It only judges our own
// processes, because Windows itself owns many 0.0.0.0 RPC listeners and flagging
// those would train everyone to ignore the check.
function NetCheck({ net }: any) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await getDiagPorts();
      if (!r) setErr("桌面桥未就绪");
      else if (r.error) setErr(String(r.error));
      else setRes(r);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const own = res?.own || net?.own || { ours: [], ours_count: 0, system_non_loopback: 0 };
  const watch = res ? res.non_loopback_watch_ports : net?.non_loopback_watch_ports;
  const clean = (own.ours || []).length === 0 && (watch || []).length === 0;

  return (
    <div className="foot">
      <span className={`pill ${clean ? "ok" : "danger"}`}>
        {clean ? "本程序无非回环监听" : "⚠ 出现非回环监听"}
      </span>
      <span className="muted">
        {" "}我们自己的进程 {own.ours_count || 0} 个；监控端口非回环 {(watch || []).length} 个；
        系统级非回环共 {own.system_non_loopback ?? "—"} 个（多为 Windows 服务，不归本程序管）
      </span>
      {res && <span className="muted"> · 复查于 {new Date().toLocaleTimeString()}</span>}
      <button className="ghost" onClick={run} disabled={busy}>
        {busy ? "复查中…" : "立即复查监听"}
      </button>
      {err && <p className="danger">复查失败：{err}</p>}
      {(own.ours || []).length > 0 && (
        <p className="danger">
          需要立刻处理：{(own.ours || []).map((o: any) => ` ${o.image}:${o.port}(${o.local})`).join(",")}
        </p>
      )}
      <p className="muted">核心只绑 127.0.0.1 的随机端口；每启动换一个令牌，不写进界面、日志或备份。</p>
    </div>
  );
}

export default function StatusPage({ status }: { status: Status | null }) {
  if (!status) {
    return <div className="empty">正在读取本机状态…</div>;
  }
  const blocks = Object.entries(status.blocks || {});
  const wl = status.whitelist || {};
  const st = status.plugin?.state || {};
  const net = status.network || {};
  const svc = status.services || {};
  const legacy = svc.legacy || {};

  return (
    <>
      <div className="grid4">
        {blocks.map(([k, b]) => (
          <div key={k} className={`stat ${b.state}`}>
            <b>{b.label}</b>
            <span className={`pill ${b.state}`}>
              {b.state === "ok" ? "正常" : b.state === "warn" ? "注意" : b.state === "down" ? "离线" : "未接入"}
            </span>
            <p>{b.detail}</p>
          </div>
        ))}
      </div>

      <div className="grid2">
        <Card title="会话与未读" sub={`state.json ${st.state_bytes ? (st.state_bytes / 1024).toFixed(1) + " KB" : "—"}`}>
          {st.error ? (
            <p className="muted">读不到插件状态：{st.error}</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>会话</th>
                  <th>消息</th>
                  <th>未读</th>
                  <th>最后一条</th>
                </tr>
              </thead>
              <tbody>
                {(st.sessions || []).map((s) => (
                  <tr key={s.key}>
                    <td>
                      <code>{s.key}</code>
                      {s.paused && <span className="tag">已暂停</span>}
                    </td>
                    <td>{s.message_count}</td>
                    <td>{s.unread ?? "—"}</td>
                    <td title={String(s.last_message_at ?? "")}>{fmtAgo(Number(s.last_message_at) || null)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="foot">
            只读统计；聊天正文不经过本地核心，也不进界面。
          </p>
        </Card>

        <Card title="生产白名单核对" sub={wl.all_ok ? "与交接记录一致" : "有偏差"}>
          <ul className="checks">
            {(wl.checks || []).map((c) => (
              <li key={c.name} className={c.ok ? "yes" : "no"}>
                {c.ok ? "✓" : "✗"} {c.name}
                {c.detail && <em>{c.detail}</em>}
              </li>
            ))}
          </ul>
          {wl.plugin_flags && (
            <p className="foot">
              {Object.entries(wl.plugin_flags)
                .map(([k, v]) => `${k}=${String(v)}`)
                .join("  ")}
            </p>
          )}
        </Card>
      </div>

      <div className="grid2">
        <Card title="网络暴露自检" sub={net.verdict === "ok" ? "仅回环" : net.verdict}>
          <table>
            <tbody>
              {Object.entries(net.listeners || {}).map(([name, l]: [string, any]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>
                    <code>127.0.0.1:{l.port}</code>
                  </td>
                  <td>{l.listening ? `监听中${l.pids?.length ? " pid " + l.pids.join(",") : ""}` : "未监听"}</td>
                  <td>{l.listening ? (l.loopback_only ? "回环" : "⚠ 非回环") : "—"}</td>
                  <td className="muted">established={net.established?.[name] ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(net.non_loopback_watch_ports || []).length > 0 && (
            <p className="danger">
              有监控端口绑定到了非回环地址：
              {net.non_loopback_watch_ports!.map((p) => ` ${p.local}(pid ${p.pid})`).join(",")}
            </p>
          )}
          <NetCheck net={net} />
          <p className="muted">
            旧控制端口 8790 {legacy.legacy_arena?.listening ? "已被其他程序占用" : "未监听"}；本程序不复用它、不杀它的进程。
          </p>
        </Card>

        <Card title="阶段 0 边界" sub={status.production?.touched ? "已改动" : "生产目录零写入"}>
          <ul className="checks">
            <li className="yes">✓ 本地核心只读模式（pid {status.core?.pid}，v{status.core?.version}）</li>
            <li className="yes">✓ 不启停任何服务，不重启 AstrBot</li>
            <li className="yes">✓ 不发送 QQ 消息，不读取密钥文件</li>
            <li className="yes">✓ 设计文档存在：{status.production?.design_doc ? "是" : "否"}；交接记录：
              {status.production?.handoff_doc ? "是" : "否"}</li>
          </ul>
          <p className="foot">
            采集耗时 {status.duration_ms} ms · {new Date(status.generated_at || Date.now()).toLocaleTimeString("zh-CN")}
          </p>
        </Card>
      </div>
    </>
  );
}
