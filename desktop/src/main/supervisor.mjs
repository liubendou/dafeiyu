// Spawns and supervises dfy-core. The token lives only here, never in argv,
// never in the renderer, never in a log line.
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import path from "node:path";

const ALLOWED = new Set([
  "/v1/status",
  "/v1/audit",
  "/v1/diag/ports",
  "/v1/logs",
  "/v1/conversations",
  "/v1/messages",
  "/v1/groups",
  "/v1/outbox",
  "/v1/receipt",
  "/v1/send",
  "/v1/tools",
  "/v1/control",
  "/v1/control/receipt",
  "/v1/arena/overview",
  "/v1/arena/tasks",
  "/v1/arena/task",
  "/v1/arena/models",
  "/v1/arena/route",
  "/v1/arena/workbench",
  "/v1/arena/grant",
  "/v1/backups",
  "/v1/persona",
  "/v1/persona/history",
  "/v1/persona/sources",
  "/v1/memory",
  "/v1/memory/info",
  "/v1/models",
  "/v1/settings",
  "/v1/credentials",
  "/v1/setup/state",
  "/v1/setup/checks",
  "/v1/setup/inventory",
  "/v1/release/status",
  "/v1/diag/bundles",
  "/v1/groups/enable",
  "/v1/groups/disable"
]);

// Query strings are allowed (e.g. /v1/messages?umo=...), but the route is matched
// on the path alone, so nothing can smuggle a different endpoint past the gate.
const isAllowed = (subPath) => ALLOWED.has(String(subPath || "").split("?")[0].replace(/\/$/, ""));

let child = null;
let port = 0;
let token = "";
let state = "stopped";
let attempts = 0;
let lastError = null;
let stoppingByUser = false;
let backoffMs = 800;
let readyResolve = null;

function scrub(text) {
  if (!text) return text;
  return String(text).split(token).join("***");
}

export function coreState() {
  return {
    state,
    endpoint: port ? `127.0.0.1:${port}` : null,
    restarts: attempts,
    lastError: scrub(lastError),
    pid: child && !child.killed ? child.pid : null
  };
}

function note(kind, text) {
  if (globalThis.process?.env?.DFY_DEBUG) console.error(`[core ${kind}] ${scrub(text)}`);
}

export function startCore(rootDir) {
  if (child && !child.killed) return Promise.resolve(true);
  stoppingByUser = false;
  token = crypto.randomBytes(36).toString("base64url");
  const python = process.env.DFY_PYTHON || "python";
  const coreDir = path.join(rootDir, "core");

  let settled = false;
  const ready = new Promise((resolve) => {
    readyResolve = (v) => {
      if (!settled) {
        settled = true;
        resolve(v);
      }
    };
  });

  try {
    child = spawn(python, ["-m", "dfy_core.app"], {
      cwd: coreDir,
      env: {
        ...process.env,
        DFY_CORE_TOKEN: token,
        DFY_CORE_PORT: "0",
        PYTHONPATH: coreDir,
        PYTHONIOENCODING: "utf-8",
        // 窗口被强杀时 before-quit 不会跑；stdin 一断核心就自己退出，不留孤儿
        DFY_CORE_WATCH_STDIN: "1"
      },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true
    });
  } catch (err) {
    state = "error";
    lastError = String(err && err.message ? err.message : err);
    readyResolve(false);
    return ready;
  }
  state = "starting";
  // 这根管道只是「父进程还活着」的信号，永远不往里写；关掉它之前别报 EPIPE
  child.stdin.on("error", () => {});

  let buf = "";
  child.stdout.on("data", (chunk) => {
    buf += chunk.toString("utf8");
    const m = buf.match(/READY\s+(\d+)\s+(\d+)/);
    if (m && !port) {
      port = parseInt(m[1], 10);
      state = "running";
      attempts = 0;
      backoffMs = 800;
      lastError = null;
      note("info", `ready on 127.0.0.1:${port} pid=${m[2]}`);
      readyResolve(true);
    }
  });
  child.stderr.on("data", (c) => {
    lastError = scrub(c.toString("utf8")).slice(-400);
    note("stderr", lastError);
  });
  child.on("error", (err) => {
    lastError = scrub(err.message);
    state = "error";
    readyResolve(false);
  });
  child.on("exit", (code) => {
    port = 0;
    if (state !== "error") state = "stopped";
    if (!stoppingByUser) {
      attempts += 1;
      const wait = backoffMs;
      backoffMs = Math.min(backoffMs * 2, 15000);
      note("exit", `code=${code}; respawn in ${wait}ms`);
      setTimeout(() => {
        if (!stoppingByUser) startCore(rootDir);
      }, wait);
    }
    readyResolve(false);
  });

  setTimeout(() => readyResolve(false), 15000);
  return ready;
}

export async function stopCore() {
  stoppingByUser = true;
  state = "stopping";
  if (child) {
    try {
      child.kill("SIGTERM");
    } catch {
      /* already gone */
    }
    child = null;
  }
  port = 0;
  state = "stopped";
  return true;
}

export async function coreRequest(subPath, opts = {}) {
  if (!isAllowed(subPath)) return { error: "route not allowed", subPath };
  if (!port) return { error: "core not ready", state, detail: scrub(lastError) };
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), opts.method === "POST" ? 12000 : 8000);
  const method = opts.method === "POST" ? "POST" : "GET";
  try {
    const res = await fetch(`http://127.0.0.1:${port}${subPath}`, {
      method,
      headers: method === "POST"
        ? { "X-Dfy-Token": token, "Content-Type": "application/json" }
        : { "X-Dfy-Token": token },
      body: method === "POST" ? String(opts.body ?? "") : undefined,
      signal: ctl.signal
    });
    const body = await res.json().catch(() => ({ error: "bad json from core" }));
    if (!res.ok && res.status === 403) {
      lastError = "core rejected our own token";
      return { error: "core auth failure" };
    }
    return body;
  } catch (err) {
    lastError = scrub(err.message);
    return { error: "core unreachable", detail: lastError, state };
  } finally {
    clearTimeout(t);
  }
}
