import { useCallback, useEffect, useState } from "react";
import { arena, fmtAgo } from "../api";

const TABS = [
  { id: "tasks", label: "任务" },
  { id: "models", label: "模型" },
  { id: "tools", label: "工具" },
  { id: "workbench", label: "工作台" }
] as const;

const PERMS: { name: string; perm: string; desc: string; risky: boolean }[] = [
  { name: "查看模型 / 任务 / 工作台", perm: "允许", desc: "只读", risky: false },
  { name: "派活、写模型备注", perm: "主人确认", desc: "写操作", risky: true },
  { name: "浏览器派活", perm: "主人确认", desc: "依赖工作台，当前未接入", risky: true },
  { name: "执行命令", perm: "默认关闭", desc: "高风险，限时授权，到点自动收回", risky: true },
  { name: "开关工作台", perm: "主人确认", desc: "影响本机", risky: true }
];

const statePill = (s?: string | null) =>
  s === "queued" ? "warn" : s === "failed" ? "danger" : s === "done" || s === "sent" ? "ok" : "";

function TaskForm({ onDone }: { onDone: () => void }) {
  const [task, setTask] = useState("");
  const [model, setModel] = useState("");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [ov, setOv] = useState<any>(null);
  const [keys, setKeys] = useState<string[]>([]);
  const [custom, setCustom] = useState(false);

  useEffect(() => {
    arena("/v1/arena/overview").then(setOv);
    arena("/v1/arena/models").then((d: any) => setKeys(d?.note_keys || [])).catch(() => setKeys([]));
  }, []);
  useEffect(() => {
    if (!model.trim()) return setPreview(null);
    const h = setTimeout(() => arena("/v1/arena/route?model=" + encodeURIComponent(model.trim())).then(setPreview), 400);
    return () => clearTimeout(h);
  }, [model]);

  const send = async () => {
    setBusy(true);
    setMsg(null);
    const r = await arena("/v1/arena/dispatch", { task, model: model.trim(), note, mode: "queue" });
    setBusy(false);
    setMsg(r?.error ? `没有派出去：${r.error}` : `已排队（${r?.ms ?? "?"}ms）：${String(r?.reply || "").slice(0, 120)}`);
    if (!r?.error) {
      setTask("");
      setNote("");
    }
    onDone();
  };
  const browserBlocked = !ov?.browser_available;
  const browserWhy =
    ov?.browser_reason || (browserBlocked ? "工作台实例还没起来，浏览器派活暂不可用" : "");

  return (
    <section className="card">
      <h3>
        新任务 <small>队列派活</small>
      </h3>
      {browserWhy && (
        <p className="muted">浏览器派活：{browserWhy}</p>
      )}
      <textarea
        value={task}
        placeholder="要做什么（写清楚验收标准，Agent 按这个自测）"
        onChange={(e) => setTask(e.target.value)}
      />
      <div className="row2">
        <select
          value={custom ? "__custom" : model}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "__custom") { setCustom(true); setModel(""); return; }
            setCustom(false); setModel(v);
          }}
        >
          <option value="">自动（沿用当前默认模型）</option>
          {keys.map((k) => (
            <option key={k} value={k}>备注 · {k}</option>
          ))}
          <option value="__custom">自己填…</option>
        </select>
        {custom && (
          <input value={model} placeholder="填主人备注名，或列表里的模型 ID" onChange={(e) => setModel(e.target.value)} />
        )}
        <input value={note} placeholder="备注：偏好、避免什么" onChange={(e) => setNote(e.target.value)} />
      </div>
      {keys.length === 0 && (
        <p className="muted">还没有可选的模型备注——先在「模型」页写一条，路由只认备注。</p>
      )}
      <p className={"muted" + (preview?.error ? " danger" : "")}>
        {preview?.error
          ? `路由预览：${preview.error}`
          : preview?.route
            ? `路由预览：→ ${preview.route.target}${preview.route.note ? " · 备注 " + preview.route.note : ""}`
            : "路由预览：不指定就用当前默认模型；匹配不上会报错，不会替你猜"}
      </p>
      <div className="row-actions">
        <button className="send" disabled={busy || task.trim().length < 8} onClick={() => void send()}>
          {busy ? "派发中…" : "派活"}
        </button>
        <label className="sw" title={browserBlocked ? ov?.browser_reason : ""}>
          <input type="radio" checked readOnly disabled={!browserBlocked} onChange={() => {}} />
          队列派活
        </label>
        <label className="sw" title={ov?.browser_reason || "还没接入"}>
          <input type="radio" checked={false} disabled onChange={() => {}} />
          浏览器会话（{browserBlocked ? "暂不可用" : "可用"}）
        </label>
      </div>
      <p className="muted">
        走的是「{ov?.mode || "queue"}」：写进 Arena 任务队列，不往浏览器里粘。
        {ov?.browser_available ? "" : ` 浏览器派活不可用的原因：${ov?.browser_reason || "未接入"}。`}
      </p>
      {msg && <div className="hint">{msg}</div>}
    </section>
  );
}

function TasksTab({ ov }: { ov: any }) {
  const [data, setData] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const load = useCallback(async () => setData(await arena("/v1/arena/tasks")), []);
  useEffect(() => {
    load();
    const h = setInterval(load, 15000);
    return () => clearInterval(h);
  }, [load]);
  const items: any[] = data?.items || [];
  const open = items.filter((i) => i.state === "queued" || i.state === "running");
  const done = items.filter((i) => i.state !== "queued" && i.state !== "running");
  return (
    <>
      <TaskForm onDone={load} />
      <section className="card">
        <h3>
          任务台账 <small>{items.length} 条 · 上游状态点「刷新」按号查</small>
        </h3>
        {(open.length ? open : []).map((t) => (
          <button key={t.file} className="taskrow" onClick={() => void arena("/v1/arena/task?file=" + encodeURIComponent(t.file)).then(setDetail)}>
            <span className={`pill ${statePill(t.state)}`}>{t.state}</span>
            <b>{t.task}</b>
            <span className="muted">
              {t.model || "自动"} · {fmtAgo(Date.parse(String(t.created_at || "")) / 1)} · {t.ms != null ? `${t.ms}ms` : ""}
            </span>
          </button>
        ))}
        {!open.length && <p className="empty">没有进行中或排队的任务。</p>}
        {done.length > 0 && <h4>已完成 / 失败</h4>}
        {done.map((t) => (
          <button key={t.file} className="taskrow" onClick={() => void arena("/v1/arena/task?file=" + encodeURIComponent(t.file)).then(setDetail)}>
            <span className={`pill ${statePill(t.state)}`}>{t.state}</span>
            <b>{t.task}</b>
            <span className="muted">{t.model || "自动"} · {String(t.updated_at || t.created_at || "")}</span>
          </button>
        ))}
        {detail && (
          <div className="detail">
            <div className="row-actions">
              <b>{detail.file}</b>
              <button className="ghost tiny" onClick={() => navigator.clipboard?.writeText(String(detail.result || ""))}>
                复制结果
              </button>
              <button className="ghost tiny" onClick={() => setDetail(null)}>收起</button>
            </div>
            <p>{detail.task}</p>
            <p className="muted">
              模型 {detail.model || "自动"} · 状态 {detail.state} · 方式 {detail.mode || "queue"} · 来源 {detail.via || "-"}
            </p>
            <pre>{String(detail.result || "（还没有结果）")}</pre>
          </div>
        )}
      </section>
    </>
  );
}

function ModelsTab({ reloadKey }: { reloadKey: number }) {
  const [data, setData] = useState<any>(null);
  const [edit, setEdit] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => setData(await arena("/v1/arena/models")), []);
  useEffect(() => {
    load();
  }, [load, reloadKey]);
  // 房间不是猜出来的：让每个在跑的实例去读自己的 Arena 侧栏（只读，不发消息）
  const scan = async () => {
    setBusy(true);
    setMsg("正在问每个实例的 Arena 侧栏…");
    const r = await arena("/v1/arena/rooms?refresh=1");
    setBusy(false);
    const got = (r?.items || []).length;
    setMsg(r?.error ? `没扫成：${r.error}` : `扫完：${got} 个房间，来自 ${(r?.targets || []).length} 个实例${r?.errors?.length ? "（" + r.errors.join("；") + "）" : ""}`);
    await load();
  };
  const save = async () => {
    const r = await arena("/v1/arena/note", { model: edit.id, note: edit.note });
    setMsg(r?.error ? `没写成：${r.error}` : `已保存（${r?.backup || "新建文件"}）· ${r?.note_effect || ""}`);
    if (!r?.error) {
      setEdit(null);
      load();
    }
  };
  return (
    <section className="card">
      <h3>
        模型 <small>{data?.note || ""}</small>
      </h3>
      <div className="row-actions">
        <span className="muted">
          {data?.source === "workbench"
            ? `来自 ${(data?.profiles || []).length} 个实例的 Arena 侧栏${data?.rooms?.at ? ` · ${data.rooms.at}` : ""}`
            : "工作台没读到房间，暂用 AstrBot 的 provider 清单撑场面"}
        </span>
        <button className="ghost tiny" disabled={busy} onClick={() => void scan()}>
          {busy ? "正在问实例…" : "扫一遍实例"}
        </button>
      </div>
      {(data?.items || []).map((m: any) => (
        <div key={m.id} className="mcard">
          <div className="row-actions">
            <b>{m.model || m.id}</b>
            {m.provider ? <span className="muted">· {m.provider}</span> : null}
            {m.row_text ? <span className="muted">· {m.row_text}</span> : null}
            <code className="muted">{m.href || m.id}</code>
            {m.note_ambiguous ? <span className="muted">（备注大小写撞车，先清理成一份）</span> : null}
            {m.last_run ? (
              <span className="muted">
                最近 {m.last_run.status} · {m.last_run.tokens} tokens · {m.last_run.duration_s ?? "—"}s
              </span>
            ) : (
              <span className="muted">最近没用过</span>
            )}
          </div>
          <p className="muted">主人备注：{m.note || "（没写）"}</p>
          <button className="ghost tiny" onClick={() => setEdit({ id: m.model || m.id, note: m.note || "" })}>
            {m.note ? "改备注" : "写备注"}
          </button>
        </div>
      ))}
      {!data?.items?.length && <p className="empty">读不到模型列表。</p>}
      {edit && (
        <div className="banner">
          <b>{edit.id}</b>
          <textarea value={edit.note} placeholder="验收标准、偏好、要避免什么——这段就是路由键"
                    onChange={(e) => setEdit({ ...edit, note: e.target.value })} />
          <span className="row-actions">
            <button className="send tiny" onClick={() => void save()}>保存（自动备份原文件）</button>
            <button className="ghost tiny" onClick={() => setEdit(null)}>取消</button>
          </span>
        </div>
      )}
      {msg && <div className="hint">{msg}</div>}
    </section>
  );
}

function ToolsTab({ ov, reload }: { ov: any; reload: () => void }) {
  const [grant, setGrant] = useState<any>(ov?.grant || null);
  const [minutes, setMinutes] = useState(10);
  const [askGrant, setAskGrant] = useState(false);
  const [cmd, setCmd] = useState("");
  const [out, setOut] = useState<string | null>(null);
  const refresh = useCallback(async () => setGrant(await arena("/v1/arena/grant")), []);
  useEffect(() => {
    refresh();
  }, [refresh, ov]);
  const toggle = async () => {
    const r = await arena("/v1/arena/grant", { minutes: grant?.active ? 0 : minutes, confirm: "yes" });
    setAskGrant(false);
    setOut(r?.error ? `没改：${r.error}` : r?.active ? `已开 ${r.minutes} 分钟` : "已收回");
    setGrant(await arena("/v1/arena/grant"));
    reload();
  };
  const run = async () => {
    const r = await arena("/v1/arena/exec", { cmd });
    setOut(r?.error ? `拒绝：${r.error}` : `rc=${r.rc} · ${r.ms}ms · 剩余 ${r.seconds_left ?? 0}s\n${r.output || "（无输出）"}`);
    refresh();
  };
  const test = async (path: string) => {
    const r = await arena(path);
    setOut(`${path} → ${JSON.stringify(r).slice(0, 900)}`);
  };
  return (
    <>
      <section className="card">
        <h3>工具与权限</h3>
        <table className="t">
          <thead>
            <tr><th>工具</th><th>默认权限</th><th>说明</th></tr>
          </thead>
          <tbody>
            {PERMS.map((p) => (
              <tr key={p.name}>
                <td>{p.name}</td>
                <td><span className={`pill ${p.risky ? "warn" : "ok"}`}>{p.perm}</span></td>
                <td className="muted">{p.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">
          网关：{ov?.configured ? `已配置（指纹 ${ov.endpoint_sha8}）` : `未配置（${ov?.error || "?"}）`}
          {ov?.reachable === true ? ` · 上游可达 · ${ov.tools} 个工具` : ov?.reachable === false ? ` · 上游不通：${ov.error}` : ""}
          。 群聊和陌生人一律调不到这些工具；大肥鱼每次调用都进审计。
        </p>
      </section>
      <section className="card">
        <h3>执行命令（本机）</h3>
        <p>
          {grant?.active
            ? `已授权，剩余 ${grant.seconds_left}s（约 ${grant.minutes_left} 分钟），到点自动收回`
            : "默认关闭：不开就谁都执行不了，包括我"}
        </p>
        <span className="row-actions">
          {grant?.active ? (
            <button className="ghost tiny" onClick={() => void toggle()}>立刻收回</button>
          ) : (
            <>
              <input className="num" type="number" min={1} max={30} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} />
              <button className="ghost tiny" onClick={() => setAskGrant(true)}>限时授权…</button>
            </>
          )}
        </span>
        {askGrant && (
          <div className="banner">
            <b>让大肥鱼在你电脑上执行命令 {minutes} 分钟？</b>
            <p className="muted">只在本机、只在这台软件开着的时候有效；递归删除、格式化、改防火墙、登出 QQ 这类命令无论如何都不执行。</p>
            <span className="row-actions">
              <button className="send tiny" onClick={() => void toggle()}>确认开启</button>
              <button className="ghost tiny" onClick={() => setAskGrant(false)}>算了</button>
            </span>
          </div>
        )}
        <input value={cmd} placeholder="授权后才能试：一条命令" onChange={(e) => setCmd(e.target.value)} />
        <span className="row-actions">
          <button className="ghost tiny" disabled={!grant?.active || !cmd.trim()} onClick={() => void run()}>执行</button>
          <button className="ghost tiny" onClick={() => void test("/v1/arena/models")}>测试台：看模型</button>
          <button className="ghost tiny" onClick={() => void test("/v1/arena/workbench")}>测试台：看工作台</button>
        </span>
        {out && <pre>{out}</pre>}
      </section>
    </>
  );
}

function WorkbenchTab({ reloadKey }: { reloadKey: number }) {
  const [st, setSt] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [askClose, setAskClose] = useState(false);
  const load = useCallback(async () => setSt(await arena("/v1/arena/workbench")), []);
  useEffect(() => {
    load();
  }, [load, reloadKey]);
  const act = async (action: string, confirm = "") => {
    const r = await arena("/v1/arena/workbench", { action, instance: st?.scope, confirm });
    setAskClose(false);
    setMsg(r?.error ? `${action} 没成：${r.error}` : r?.note || r?.ok ? `${action} 已提交` : JSON.stringify(r).slice(0, 200));
    load();
  };
  const row = (st?.items || [])[0];
  return (
    <section className="card">
      <h3>
        工作台 <small>只检查 {st?.scope || "profile_3"}</small>
      </h3>
      {row ? (
        <div className="mcard">
          <div className="row-actions">
            <b>{row.name}</b>
            <span className={`pill ${row.alive ? "ok" : "warn"}`}>{row.alive ? "运行中" : "没在跑"}</span>
            {row.logged_in !== undefined && <span className="muted">登录：{String(row.logged_in)}</span>}
            {row.pid ? <code className="muted">pid {row.pid}</code> : null}
          </div>
          <p className="muted">模型备注：{row.model_note || row.note || "（没写）"}</p>
          <span className="row-actions">
            <button className="ghost tiny" onClick={() => void act("open")}>打开</button>
            <button className="ghost tiny" disabled={!row.alive} onClick={() => setAskClose(true)}>关闭</button>
            <button className="ghost tiny" onClick={() => void act("status")}>刷新状态</button>
          </span>
          {askClose && (
            <div className="banner">
              <b>关闭 {row.name}？</b>
              <p className="muted">正在跑的浏览器任务会被打断。历史与登录状态不会丢。</p>
              <span className="row-actions">
                <button className="send tiny" onClick={() => void act("close", "yes")}>确认关闭</button>
                <button className="ghost tiny" onClick={() => setAskClose(false)}>取消</button>
              </span>
            </div>
          )}
        </div>
      ) : (
        <p className="empty">{st?.error || "看不到那个实例（未安装或路径不对）——不猜、不启动别的东西。"}</p>
      )}
      {msg && <div className="hint">{msg}</div>}
      <p className="muted">浏览器派活要用哪个会话，是按 Arena 左侧会话上的「模型备注」精确匹配的；匹配不到或有多个就报错。</p>
    </section>
  );
}

export default function ArenaPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("tasks");
  const [ov, setOv] = useState<any>(null);
  const [tick, setTick] = useState(0);
  const load = useCallback(async () => setOv(await arena("/v1/arena/overview")), []);
  useEffect(() => {
    load();
    const h = setInterval(load, 20000);
    return () => clearInterval(h);
  }, [load]);
  return (
    <div className="arena">
      <div className="gbar">
        <span className={`pill ${ov?.reachable ? "ok" : ov?.configured ? "warn" : "danger"}`}>
          {ov?.reachable ? "网关就绪" : ov?.configured ? "网关未连通" : "网关未配置"}
        </span>
        <span className="muted">
          {ov?.configured ? `上游指纹 ${ov.endpoint_sha8}` : ov?.error || "—"} · 派活方式：{ov?.mode || "queue"} · 台账 {ov?.requests ?? 0} 条
        </span>
        <span className="row-actions">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "seg on" : "seg"} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
          <button className="ghost tiny" onClick={() => { load(); setTick((x) => x + 1); }}>刷新</button>
        </span>
      </div>
      {tab === "tasks" && <TasksTab ov={ov} />}
      {tab === "models" && <ModelsTab reloadKey={tick} />}
      {tab === "tools" && <ToolsTab ov={ov} reload={load} />}
      {tab === "workbench" && <WorkbenchTab reloadKey={tick} />}
    </div>
  );
}
