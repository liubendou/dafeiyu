import { useCallback, useEffect, useState } from "react";
import { arena } from "../api";

// 首次启动向导（设计文档 §8.1、阶段 4）。只做四步：体检 → 只读盘点 → 选接管/新建 → 记录。
// 刻意不做的事：不自动发好友申请、不动群白名单、不重启 AstrBot、不替你发任何消息。
// 「接管」只写一个运行时状态文件 + 一次自动备份快照，生产配置一个字节都不改。
const STEPS = ["看体检", "看盘点", "选方式", "收尾"];

export default function FirstRunPage({ onDone, onSkip }: { onDone: () => void; onSkip: () => void }) {
  const [step, setStep] = useState(0);
  const [ck, setCk] = useState<any>(null);
  const [inv, setInv] = useState<any>(null);
  const [mode, setMode] = useState("adopt");
  const [plan, setPlan] = useState<any>(null);
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [a, b] = await Promise.all([arena("/v1/setup/checks"), arena("/v1/setup/inventory")]);
    setCk(a);
    setInv(b);
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const adopt = async (confirm: boolean) => {
    setBusy(true);
    const r = await arena("/v1/setup/adopt", { mode, confirm: confirm ? "yes" : "" });
    setBusy(false);
    if (r?.confirm_required) setPlan(r);
    else {
      setRes(r);
      setStep(3);
    }
  };

  const checks = ck?.items || [];
  const files = inv?.files || [];

  return (
    <div className="wizard-mask">
      <section className="wizard card">
        <h3>
          首次启动向导 <small>共 {STEPS.length} 步 · 全程不改生产配置</small>
        </h3>
        <div className="steps">
          {STEPS.map((s, i) => (
            <span key={s} className={i === step ? "on" : i < step ? "done" : ""}>
              {i + 1}. {s}
            </span>
          ))}
        </div>

        {step === 0 && (
          <>
            <p className="muted">
              先确认这台机器上的东西还在不在、能不能读。{ck?.note || ""}
            </p>
            <ul className="checks">
              {checks.map((c: any, i: number) => (
                <li key={i}>
                  <span className={c.ok ? "pill" : c.level === "warn" ? "pill warn" : "pill danger"}>
                    {c.ok ? "通过" : c.level === "warn" ? "警告" : "不行"}
                  </span>{" "}
                  <b>{c.name}</b> — {c.detail}
                </li>
              ))}
            </ul>
            {!ck?.ok && (
              <p className="danger">
                有硬性问题（多半是 AstrBot 目录或配置文件路径不对）。可以先跳过向导，去设置里改
                <code>config/desktop.json</code> 再回来。
              </p>
            )}
            <div className="row-actions">
              <button className="ghost tiny" onClick={() => void load()} disabled={busy}>
                重跑体检
              </button>
              <button className="send tiny" onClick={() => setStep(1)}>下一步</button>
            </div>
          </>
        )}

        {step === 1 && (
          <>
            <p className="muted">{inv?.note || ""}</p>
            {files.length === 0 ? (
              <p className="muted">没盘到现成配置文件——那就走「本机新建」，向导只写自己的状态文件。</p>
            ) : (
              <table className="meta">
                <thead>
                  <tr>
                    <th>项</th>
                    <th>文件</th>
                    <th>大小</th>
                    <th>改动时间</th>
                  </tr>
                </thead>
                <tbody>
                  {files.map((f: any, i: number) => (
                    <tr key={i}>
                      <td>{f.label}</td>
                      <td className="mono">{f.name}</td>
                      <td>{f.bytes} B</td>
                      <td>
                        {f.mtime} <span className="muted">#{f.sha8_head}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="hint">
              盘点只读。计数：消息 {inv?.counts?.messages ?? "?"} 条 · 群 {inv?.counts?.groups ?? "?"} 个
              {inv?.counts?.persona_chars ? ` · 人格 ${inv.counts.persona_chars} 字` : ""}
            </p>
            <div className="row-actions">
              <button className="ghost tiny" onClick={() => setStep(0)}>
                上一步
              </button>
              <button className="send tiny" onClick={() => setStep(2)}>
                下一步
              </button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <label className="muted">
              <input type="radio" checked={mode === "adopt"} onChange={() => setMode("adopt")} /> 接管现有数据
              （推荐）：认可这台机器上已经跑着的 AstrBot + 插件 + 记忆，GUI 直接读它们
            </label>
            <br />
            <label className="muted">
              <input type="radio" checked={mode === "fresh"} onChange={() => setMode("fresh")} /> 本机新建：
              只记下「这台机器从零开始」，之后自己按界面提示配
            </label>
            <p className="hint">
              点「就这么定」会先做一次自动备份，然后把向导完成状态写进运行时目录。<b>不会</b>改
              <code>cmd_config.json</code>、<b>不会</b>改插件配置、<b>不会</b>重启引擎。
            </p>
            {plan && !res && (
              <p className="banner">
                请再确认一次：{plan.will}
                <br />
                <button className="send tiny" onClick={() => void adopt(true)} disabled={busy}>
                  确认执行
                </button>
                <button className="ghost tiny" onClick={() => setPlan(null)}>
                  算了
                </button>
              </p>
            )}
            <div className="row-actions">
              <button className="ghost tiny" onClick={() => setStep(1)}>
                上一步
              </button>
              <button className="send tiny" onClick={() => void adopt(false)} disabled={busy}>
                就这么定
              </button>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            {res?.error ? (
              <p className="danger">没记成：{res.error}</p>
            ) : (
              <p className="muted">
                记好了：模式 <b>{res?.mode}</b>，快照 <span className="mono">{res?.guard}</span>
                ，检查项 {res?.checks_ok ? "全通过" : "有警告（可以在设置里再看）"}。
              </p>
            )}
            <p className="hint">以后想重看向导：设置 → 首次启动向导 → 重新运行。</p>
            <div className="row-actions">
              <button className="send tiny" onClick={onDone}>
                开始用
              </button>
            </div>
          </>
        )}

        {step < 3 && (
          <div className="foot">
            <button className="ghost tiny" onClick={onSkip}>
              先跳过
            </button>
            <span className="muted">跳过后界面不再自动弹，随时能在设置里打开</span>
          </div>
        )}
      </section>
    </div>
  );
}
