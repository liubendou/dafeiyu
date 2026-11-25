import { useCallback, useEffect, useState } from "react";
import { arena, getStatus, type Status } from "./api";
import FirstRunPage from "./pages/FirstRunPage";
import StatusPage from "./pages/StatusPage";
import MessagesPage from "./pages/MessagesPage";
import GroupsPage from "./pages/GroupsPage";
import ArenaPage from "./pages/ArenaPage";
import PluginsPage from "./pages/PluginsPage";
import PersonaPage from "./pages/PersonaPage";
import MemoryPage from "./pages/MemoryPage";
import BackupPage from "./pages/BackupPage";
import SettingsPage from "./pages/SettingsPage";

type Nav = "status" | "messages" | "groups" | "arena" | "plugins" | "persona" | "memory" | "backup" | "settings";

// Stage 0 ships one page. The nav shell is real so that later stages drop in
// without restructuring: each stage adds a page here, nothing else.
const NAV: { id: string; label: string; ready: boolean }[] = [
  { id: "status", label: "首页", ready: true },
  { id: "messages", label: "消息", ready: true },
  { id: "groups", label: "群组", ready: true },
  { id: "arena", label: "Arena", ready: true },
  { id: "plugins", label: "插件", ready: true },
  { id: "persona", label: "人格", ready: true },
  { id: "memory", label: "记忆", ready: true },
  { id: "logs", label: "日志", ready: false },
  { id: "backup", label: "备份", ready: true },
  { id: "settings", label: "设置", ready: true }
];

export default function App() {
  const [tab, setTab] = useState<Nav>("status");
  const [status, setStatus] = useState<Status | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [setup, setSetup] = useState<any>(null);
  const [skipWizard, setSkipWizard] = useState(false);
  const [msgNote, setMsgNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await getStatus();
      if (s && !s.error) {
        setStatus(s);
        setErr(null);
      } else {
        setErr(s?.error ? `${s.error}${s.detail ? "：" + s.detail : ""}` : "界面桥接未就绪");
      }
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setTick((t) => t + 1);
    }
  }, []);

  useEffect(() => {
    refresh();
    const h = setInterval(refresh, 5000);
    return () => clearInterval(h);
  }, [refresh]);

  // 向导只在启动时查一次；设置页里点「重新运行向导」会广播事件让它再查
  const checkSetup = useCallback(async () => {
    try {
      const s = await arena("/v1/setup/state");
      if (s && !s.error) setSetup(s);
    } catch {
      /* 核心还没起来就算了，别把界面卡住 */
    }
  }, []);
  useEffect(() => {
    void checkSetup();
    const on = () => {
      setSkipWizard(false);
      void checkSetup();
    };
    window.addEventListener("dfy-setup-changed", on);
    return () => window.removeEventListener("dfy-setup-changed", on);
  }, [checkSetup]);

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">大肥鱼</span>
        <span className="dots">
          {Object.entries(status?.blocks || {}).map(([k, b]) => (
            <span key={k} className={`dot ${b.state}`} title={`${b.label}：${b.detail}`}>
              {b.label}
            </span>
          ))}
          {!status && <span className="dot unknown">连接中</span>}
        </span>
        <button className="ghost" onClick={refresh} title="立即刷新">
          刷新
        </button>
      </header>

      <div className="body">
        <nav className="side">
          {NAV.map((n) => (
            <button
              key={n.id}
              className={tab === n.id ? "nav on" : "nav"}
              disabled={!n.ready}
              onClick={() => setTab(n.id as Nav)}
              title={n.ready ? "" : "后续阶段开放"}
            >
              {n.label}
              {!n.ready && <em>·</em>}
            </button>
          ))}
          <div className="side-foot">
            <span className="stage">阶段 4 · 打包与自举</span>
            <span className="tick">#{tick}</span>
          </div>
        </nav>
        <main className="page">
          {err && (
            <div className="banner">
              本地核心没响应：<code>{err}</code>
            </div>
          )}
          {!err && msgNote && (
            <div className="banner">
              {msgNote}
              <button className="ghost tiny" onClick={() => setMsgNote(null)}>
                知道
              </button>
            </div>
          )}
          {tab === "status" && <StatusPage status={status} />}
          {tab === "messages" && <MessagesPage />}
          {tab === "groups" && <GroupsPage />}
          {tab === "arena" && <ArenaPage />}
          {tab === "plugins" && <PluginsPage />}
          {tab === "persona" && <PersonaPage />}
          {tab === "memory" && <MemoryPage />}
          {tab === "backup" && <BackupPage />}
          {tab === "settings" && <SettingsPage />}
        </main>
      </div>

      {setup?.wizard_needed && !skipWizard && (
        <FirstRunPage
          onDone={() => setSkipWizard(true)}
          onSkip={() => {
            setSkipWizard(true);
            setMsgNote("向导先跳过了；设置页里能重新打开");
          }}
        />
      )}
    </div>
  );
}
