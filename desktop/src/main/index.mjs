// 大肥鱼 desktop — main process.
// One window, one tray icon, background core. Stage 0: read-only status.
import { app, BrowserWindow, Menu, Tray, ipcMain, nativeImage, shell } from "electron";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { coreRequest, coreState, startCore, stopCore } from "./supervisor.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", ".."); // <desktop>
const RENDERER = path.join(ROOT, "out", "renderer", "index.html");
const ASSETS = path.join(ROOT, "assets");

// 图标是资源文件（assets/icon.png / tray.png）；缺了才退回内置小圆点。
function assetPath(name) {
  const p = path.join(ASSETS, name);
  try {
    return fs.existsSync(p) ? p : null;
  } catch {
    return null;
  }
}

// 16x16 dot, status colours; no binary asset needed in the repo.
function dot(rgba) {
  const png =
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAP0lEQVR42u3QsQ2AMAxE0X8oFbAB" +
    "CzABG7ABG7ABG7ABG7ABG7ABG7AB7gopkeA4EukKnp7LOZBLQQoUFigoUFCgoEBBgcIJBQoKFBQo" +
    "KFBQoKBA4YSCAgUFCgoUFChQUKCgQOGEAgUFCgoUFChoUFAgP8UdV0rgc/YAAAAASUVORK5CYII=";
  return nativeImage.createFromDataURL(`data:image/png;base64,${png}`);
}

let win = null;
let tray = null;
let quitting = false;
let prefs = {};

function prefsFile() {
  return path.join(app.getPath("userData"), "prefs.json");
}
function loadPrefs() {
  try {
    prefs = JSON.parse(fs.readFileSync(prefsFile(), "utf8")) || {};
  } catch {
    prefs = {};
  }
}
function savePrefs() {
  try {
    fs.mkdirSync(path.dirname(prefsFile()), { recursive: true });
    fs.writeFileSync(prefsFile(), JSON.stringify(prefs, null, 1));
  } catch {
    /* 偏好存不下不算致命 */
  }
}
// 任务栏图标可以整个藏起来（只留托盘），窗口重建后要重新应用一次。
function applyTaskbar() {
  try {
    if (win && !win.isDestroyed()) win.setSkipTaskbar(!!prefs.hideTaskbar);
  } catch {
    /* ignore */
  }
}

function sendState() {
  if (win && !win.isDestroyed()) win.webContents.send("dfy:core-state", coreState());
}

// Core up/down must reach the page without polling: the page paints the
// supervisor badge from this push.
let stateTimer = null;
function startStatePush() {
  if (stateTimer) return;
  stateTimer = setInterval(sendState, 4000);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 960,
    minHeight: 640,
    show: false,
    icon: assetPath("icon.png") || undefined,
    title: "大肥鱼",
    backgroundColor: "#101418",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(HERE, "..", "preload", "index.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false
    }
  });

  applyTaskbar();
  win.once("ready-to-show", () => win.show());
  win.loadFile(RENDERER);

  // External links must not open inside the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });

  // Close goes to tray; the core keeps running until the tray says quit.
  win.on("close", (e) => {
    if (!quitting && tray) {
      e.preventDefault();
      win.hide();
    }
  });
  win.on("closed", () => {
    win = null;
  });
}

function createTray() {
  try {
    const trayIcon = assetPath("tray.png");
    tray = new Tray(trayIcon ? nativeImage.createFromPath(trayIcon) : dot());
    tray.setToolTip("大肥鱼");
    tray.setContextMenu(
      Menu.buildFromTemplate([
        {
          label: "打开大肥鱼",
          click: () => {
            if (!win) createWindow();
            else {
              win.show();
              win.focus();
            }
          }
        },
        { type: "separator" },
        {
          label: "复制诊断状态",
          click: async () => {
            const { clipboard } = await import("electron");
            clipboard.writeText(JSON.stringify(await coreRequest("/v1/status"), null, 2));
          }
        },
        {
          label: "隐藏任务栏图标（只留托盘）",
          type: "checkbox",
          checked: !!prefs.hideTaskbar,
          click: (item) => {
            prefs.hideTaskbar = !!item.checked;
            savePrefs();
            applyTaskbar();
          }
        },
        { label: "本地核心：" + (coreState().state || "stopped"), enabled: false },
        { type: "separator" },
        {
          label: "退出",
          click: () => {
            quitting = true;
            app.quit();
          }
        }
      ])
    );
    tray.on("click", () => {
      if (!win) createWindow();
      else if (win.isVisible()) win.focus();
      else win.show();
    });
  } catch (err) {
    console.error("tray unavailable:", err.message);
  }
}

// `electron . --smoke` : start the core, pull /v1/status, print, exit.
// Lets the whole chain be verified without a human looking at a window.
const SMOKE = process.argv.includes("--smoke");
const DIAG = process.argv.includes("--diag");

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      win.show();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    app.setAppUserModelId("Dafeiyu.Desktop");
    loadPrefs();
    // One channel, an explicit envelope. Anything outside the /v1/ prefix is
    // refused here, before the core is ever touched.
    ipcMain.handle("dfy:invoke", (_e, msg) => {
      if (!msg || typeof msg !== "object") return { error: "bad message" };
      if (msg.kind === "core") {
        if (typeof msg.path !== "string" || !msg.path.startsWith("/v1/")) {
          return { error: "bad route" };
        }
        // POST is only ever forwarded to the routes the core declares writable.
        const WRITABLE = new Set(["/v1/send", "/v1/control", "/v1/groups/enable", "/v1/groups/disable",
          "/v1/arena/dispatch", "/v1/arena/note", "/v1/arena/workbench", "/v1/arena/grant", "/v1/arena/exec",
          "/v1/backups", "/v1/backups/restore", "/v1/backups/export", "/v1/backups/import",
          "/v1/persona", "/v1/persona/restore", "/v1/models", "/v1/models/test",
          "/v1/settings", "/v1/credentials", "/v1/credentials/clear", "/v1/selftest",
          "/v1/setup/adopt", "/v1/setup/reset", "/v1/release/install", "/v1/release/rollback",
          "/v1/diag/bundle"]);
        const method = msg.method === "POST" && WRITABLE.has(msg.path.split("?")[0]) ? "POST" : "GET";
        return coreRequest(msg.path, { method, body: msg.body });
      }
      if (msg.kind === "app") {
        if (msg.method === "info") return { error: "use dfy:app-info" };
        // 只允许打开本机服务（AstrBot 面板之类），别把页面变成万能开网页的工具
        if (msg.method === "open-external") {
          const url = String((msg.params && msg.params.url) || "");
          if (!/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?(\/|$)/i.test(url)) {
            return { error: "只允许打开本机地址" };
          }
          shell.openExternal(url);
          return { ok: true, url };
        }
        return { error: "unknown app method" };
      }
      return { error: "unknown kind" };
    });
    ipcMain.handle("dfy:core-state", () => coreState());
    ipcMain.handle("dfy:app-info", () => ({
      version: app.getVersion(),
      electron: process.versions.electron,
      mode: "read-only",
      root: ROOT,
      rendererBuilt: fs.existsSync(RENDERER)
    }));

    await startCore(ROOT);
    startStatePush();
    if (DIAG) {
      const say = (m) => process.stdout.write("DIAG " + m + "\n");
      const withTimeout = (pr, ms, tag) =>
        Promise.race([pr, new Promise((r) => setTimeout(() => r("<timeout " + tag + ">"), ms))]);
      let w = null;
      try {
        say("begin");
        w = new BrowserWindow({
          show: true,
          backgroundThrottling: false,
          webPreferences: {
            preload: path.join(HERE, "..", "preload", "index.cjs"),
            contextIsolation: true, nodeIntegration: false, sandbox: true
          }
        });
        const ev = [];
        w.webContents.on("did-start-loading", () => ev.push("did-start-loading"));
        w.webContents.on("dom-ready", () => ev.push("dom-ready"));
        w.webContents.on("did-finish-load", () => ev.push("did-finish-load"));
        w.webContents.on("did-fail-load", (_e, code, desc, url) =>
          ev.push(`did-fail-load ${code} ${desc} ${url}`));
        w.webContents.on("render-process-gone", (_e, d) =>
          ev.push("render-process-gone " + JSON.stringify(d)));
        const READ = '"read"';
        const TABS = { status: "\u9996\u9875", messages: "\u6d88\u606f", groups: "\u7fa4\u7ec4" };
        const tkey = (process.argv.find((a2) => a2.startsWith("--tab=")) || "--tab=").slice(6);
        const want = TABS[tkey] || "";
        say("loading " + RENDERER);
        w.loadFile(RENDERER);
        await withTimeout(new Promise((r) => w.webContents.once("did-finish-load", r)), 12000, "load");
        say("loaded events=" + JSON.stringify(ev));
        if (want) {
          const js =
            "(function(){var bs=[].slice.call(document.querySelectorAll('button.nav'));" +
            "var b=bs.find(function(x){return x.textContent.indexOf(" + JSON.stringify(want) + ")>=0;});" +
            "if(!b)return 'nav-missing:'+(document.title||'?');b.click();return 'clicked:'+b.textContent;})()";
          say("tab=" + await withTimeout(w.webContents.executeJavaScript(js), 8000, "click"));
          await new Promise((r) => setTimeout(r, 3000));
        }
        const probeJs =
          "(function(){var d=window.dafeiyu;return JSON.stringify({bridge:typeof d," +
          "keys:d?Object.keys(d):[],banner:(document.querySelector('.banner')||{}).textContent||''," +
          "body:(document.body.innerText||'').slice(0,420)});})()";
        say("probe=" + await withTimeout(w.webContents.executeJavaScript(probeJs), 8000, "probe"));
        const audit = await withTimeout(coreRequest("/v1/audit"), 9000, "audit");
        const lines = (audit && audit.lines) || [];
        say("audit_lines=" + lines.length + " reads=" +
          lines.filter((l) => l.indexOf(READ) >= 0).length);
        say("done");
      } catch (err) {
        say("ERROR " + (err && err.message ? err.message : String(err)));
      }
      try {
        if (w && !w.isDestroyed()) w.close();
      } catch (e) {}
      await withTimeout(stopCore(), 4000, "stopCore").catch(() => {});
      app.exit(0);
      return;
    }
    if (SMOKE) {
      const st = await coreRequest("/v1/status");
      const good = st && !st.error;
      const line = good
        ? `SMOKE_OK core=${st.core?.pid} blocks=${Object.values(st.blocks || {}).map((b) => b.state).join("/")} ` +
          `sessions=${st.plugin?.state?.session_count} whitelist=${st.whitelist?.all_ok} ` +
          `net=${st.network?.verdict} touched=${st.production?.touched}`
        : `SMOKE_FAIL ${JSON.stringify(st).slice(0, 300)}`;
      process.stdout.write(line + "\n");
      await stopCore();
      app.exit(good ? 0 : 1);
      return;
    }
    createWindow();
    createTray();
    sendState();
    setInterval(sendState, 5000);

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("before-quit", async () => {
    quitting = true;
    try {
      await stopCore();
    } catch {
      /* ignore */
    }
  });

  app.on("window-all-closed", () => {
    if (!tray) app.quit();
  });
}
