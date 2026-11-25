import { useCallback, useEffect, useState } from "react";
import { fmtAgo, getGroups, setGroup, type ConvItem, type GroupOpResult } from "../api";

export default function GroupsPage() {
  const [data, setData] = useState<any>(null);
  const [note, setNote] = useState<string | null>(null);
  const [ask, setAsk] = useState<any | null>(null);
  const [opNote, setOpNote] = useState<string | null>(null);
  const [opBusy, setOpBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    const r = await getGroups();
    if (!r) return setNote("桌面桥未就绪");
    setData(r);
    setNote(r.error ? "读不到群信息：" + r.error : null);
  }, []);

  useEffect(() => {
    load();
    const h = setInterval(load, 10000);
    return () => clearInterval(h);
  }, [load]);

  const all: ConvItem[] = data?.items || [];
  const items = showAll ? all : all.filter((g) => g.in_whitelist);
  const checks = data?.checks || [];

  const askToggle = async (g: ConvItem, enable: boolean) => {
    const gid = String(g.session_id || "");
    const r = await setGroup(gid, enable, false);
    setAsk({ gid, enable, ...r });
  };

  const applyToggle = async () => {
    if (!ask) return;
    setOpBusy(true);
    const r: GroupOpResult = await setGroup(ask.gid, ask.enable, true);
    setOpBusy(false);
    setAsk(null);
    setOpNote(
      r.error
        ? `没有改动：${r.error}${(r as any).rolled_back ? "（已回滚到备份）" : ""}`
        : `${ask.enable ? "已启用" : "已停用"}群 ${ask.gid} · 两层配置已一致 · ${r.backup || ""} · ${r.note || ""}`
    );
    load();
  };

  return (
    <>
      <div className="grid2">
        <section className="card">
          <h3>
            白名单核对 <small>cmd_config.json + 插件配置</small>
          </h3>
          {checks.length ? (
            <ul className="checks">
              {checks.map((c: any) => (
                <li key={c.name}>
                  <span className={`pill ${c.ok ? "ok" : "danger"}`}>{c.ok ? "通过" : "不通过"}</span>
                  {c.name}
                  {c.detail ? <em className="muted"> {c.detail}</em> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{note || "读取中…"}</p>
          )}
          <p className="muted">
            旧群 {String(data?.forbidden_group || "—")} 已从列表与内容读取中排除；若它重新出现在白名单里，上面的核对项会转为不通过。
          </p>
        </section>

        <section className="card">
          <h3>
            群 <small>{items.length} 个</small>
          </h3>
          <table>
            <thead>
              <tr>
                <th>群号</th>
                <th>备注名</th>
                <th>白名单</th>
                <th>未读</th>
                <th>最后来消息</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((g) => (
                <tr key={g.session_id || g.umo}>
                  <td><code>{g.session_id}</code></td>
                  <td>{g.label}</td>
                  <td>
                    <span className={`pill ${g.in_whitelist ? "ok" : "warn"}`}>
                      {g.in_whitelist ? "已启用" : "未启用"}
                    </span>
                  </td>
                  <td>{g.unread ?? "—"}</td>
                  <td>{g.last_incoming_iso || fmtAgo(g.last_incoming_at)}</td>
                  <td>
                    <button className="ghost tiny" onClick={() => void askToggle(g, !g.in_whitelist)}>
                      {g.in_whitelist ? "停用" : "启用"}
                    </button>
                  </td>
                </tr>
              ))}
              {!items.length && (
                <tr>
                  <td colSpan={6} className="muted">
                    暂无群会话或白名单群记录。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <label className="sw" title="默认只显示已启用的群">
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
            显示未启用的群（{all.length - items.length} 个）
          </label>
          {ask && (
            <div className="banner">
              <b>确认{ask.enable ? "启用" : "停用"}群 {ask.gid}？</b>
              <p>{ask.will}</p>
              <p className="muted">
                AstrBot 白名单：{ask.in_astrbot_whitelist ? "已含" : "未含"} · 插件群列表：
                {ask.in_plugin_groups ? "已含" : "未含"}。两层一起写、写前备份、校验不一致就回滚。
                {ask.enable ? "启用后大肥鱼会开始回复这个群的新消息。" : "停用只停止回复，历史记录保留、不删除。"}
              </p>
              <span className="row-actions">
                <button className="send tiny" disabled={opBusy} onClick={() => void applyToggle()}>
                  {opBusy ? "写入中…" : "确认写入"}
                </button>
                <button className="ghost tiny" onClick={() => setAsk(null)}>取消</button>
              </span>
            </div>
          )}
          {opNote && <p className="hint">{opNote}</p>}
          <p className="muted">
            开关写的是生产配置（cmd_config.json 的 id_whitelist + 插件 allowed_group_ids），改完需要重启 AstrBot 才对下一条消息生效；
            这里不做批量拉群，也不碰其它白名单项。
          </p>
        </section>
      </div>
    </>
  );
}
