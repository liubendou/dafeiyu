import { useCallback, useEffect, useState } from "react";
import { arena } from "../api";

// AstrBot 插件市场。清单是 AstrBot 官方注册表（由本地核心代拉 + 缓存），
// 安装/更新仍然在 AstrBot 面板里点：装完要重启 AstrBot 才生效，这一步不替主人按。
export default function PluginsPage() {
  const [data, setData] = useState<any>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    let r = await arena(`/v1/plugins${refresh ? "?refresh=1" : ""}`);
    setData(r);
    setBusy(false);
    setMsg(r?.market?.error || null);
    // 有缓存时刷新走后台线程：这里每 3 秒问一次，最多一分钟，拉到就自己长出来
    for (let i = 0; refresh && r?.market?.refreshing && i < 20; i += 1) {
      await new Promise((ok) => setTimeout(ok, 3000));
      r = await arena("/v1/plugins");
      setData(r);
      if (!r?.market?.refreshing) {
        setMsg(r?.market?.error || `注册表已更新：${r?.market?.at || "-"}`);
        break;
      }
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const openPanel = async () => {
    const w = window as any;
    if (!w.dafeiyu?.app) return setMsg("桌面桥没起来");
    const r = await w.dafeiyu.app("open-external", { url: "http://127.0.0.1:6185/extension-marketplace" });
    if (r?.error) setMsg(`打不开 AstrBot 面板：${r.error}`);
  };

  const refreshing = Boolean(data?.market?.refreshing);
  const items = (data?.items || []).filter((m: any) =>
    !q.trim() || `${m.id} ${m.name} ${m.author} ${m.desc}`.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <section className="card">
      <h3>
        插件市场{" "}
        <small>
          {data
            ? `注册表 ${data.market?.at || "未同步"}（${data.market?.source || "-"}）· 已装 ${data.installed_count} 个${refreshing ? " · 正在后台拉取…" : ""}`
            : "读取中…"}
        </small>
      </h3>
      <div className="row-actions">
        <input value={q} placeholder="搜插件名 / 作者 / 说明" onChange={(e) => setQ(e.target.value)} />
        <button className="ghost tiny" disabled={busy || refreshing} onClick={() => void load(true)}>
          {busy || refreshing ? "拉取中…" : "刷新注册表"}
        </button>
        <button className="ghost tiny" onClick={() => void openPanel()}>
          打开 AstrBot 面板装
        </button>
      </div>
      {msg && <div className="hint">{msg}</div>}
      {items.map((m: any) => (
        <div key={m.id} className="mcard">
          <div className="row-actions">
            <b>{m.name}</b>
            <code className="muted">{m.id}</code>
            {m.author ? <span className="muted">· {m.author}</span> : null}
            {m.installed ? <span className="muted">· 已装{m.installed_version ? " " + m.installed_version : ""}</span> : null}
          </div>
          <p className="muted">{m.desc || "（没写说明）"}</p>
          {m.repo ? <p className="muted">{m.repo}</p> : null}
        </div>
      ))}
      {data && !items.length && <p className="empty">{data.market?.error || "没有匹配的插件。"}</p>}
      <p className="hint">
        装/更新请在 AstrBot 面板里点：装完要重启 AstrBot 才生效，这步我不会自己按。已装清单读的是
        <code>{data?.installed_dir || "%LOCALAPPDATA%…\\astrbot\\data\\plugins"}</code>。
      </p>
    </section>
  );
}
