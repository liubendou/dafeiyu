// Electron 预加载：沙箱预加载脚本按 CommonJS 语义加载（sandbox:true 下 import 语法会失败），
// 所以这个文件是 .cjs 而不是 .mjs。
// 只暴露一条受控通道，绝不把 fs / child_process / token 递到页面里。
const { contextBridge, ipcRenderer } = require("electron");

const INVOKE = "dfy:invoke";

contextBridge.exposeInMainWorld("dafeiyu", {
  version: 1,

  core(path, opts) {
    const o = opts || {};
    return ipcRenderer.invoke(INVOKE, {
      kind: "core",
      path,
      method: o.method === "POST" ? "POST" : "GET",
      body: typeof o.body === "string" ? o.body : undefined
    });
  },
  app(method, params) {
    return ipcRenderer.invoke(INVOKE, { kind: "app", method, params });
  },
  onCoreState(cb) {
    const listener = (_e, payload) => cb(payload);
    ipcRenderer.on("dfy:core-state", listener);
    return () => ipcRenderer.removeListener("dfy:core-state", listener);
  },
  info() {
    return ipcRenderer.invoke("dfy:app-info");
  }
});
