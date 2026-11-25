// 大肥鱼「自己的手」：改 QQ 昵称/签名/头像、联网搜图、发图。
// 挂在 qq-bridge 的 snowluma-host MCP 上（工具名前缀 mcp__snowluma-host__，已在 qq-tool-restrict 白名单内）。
// 安全：改资料类工具只认「主人私聊会话」——key 必须是 private:<ownerQQ>，且会话令牌要经桥接校验通过。
import fs from 'node:fs';
import path from 'node:path';
import { z } from 'zod';

const COOL = { nickname: 600e3, signature: 300e3, avatar: 1800e3, search: 10e3, image: 10e3 };
const last = {};
const cool = (k) => { const now = Date.now(); const w = (last[k] || 0) + COOL[k] - now; if (w > 0) return Math.ceil(w / 1000); last[k] = now; return 0; };
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36';
const txt = (s, err = false) => ({ content: [{ type: 'text', text: typeof s === 'string' ? s : JSON.stringify(s) }], ...(err ? { isError: true } : {}) });

export function registerDafeiyuTools(server, { root, getConfig }) {
  const readJson = (f) => { try { return JSON.parse(fs.readFileSync(f, 'utf8').replace(/^\uFEFF/, '')); } catch { return null; } };
  const consoleToken = () => { try { return fs.readFileSync(path.join(root, 'state', 'console-token'), 'utf8').trim(); } catch { return ''; } };
  const consolePort = () => Number(getConfig().consolePort) || 3100;

  // NapCat OneBot HTTP：从控制中心记下的 NapCat 目录里找已启用的 HTTP 服务端
  function onebot() {
    const cc = readJson(path.join(root, 'state', 'control-center.json')) || {};
    const home = cc.napcatHome || process.env.DAFEIYU_NAPCAT_HOME || '';
    const dir = path.join(home, 'config');
    let files = [];
    try { files = fs.readdirSync(dir).filter((f) => /^onebot11_\d+\.json$/.test(f)); } catch {}
    for (const f of files) {
      const c = readJson(path.join(dir, f));
      const h = (c?.network?.httpServers || []).find((s) => s.enable);
      if (h) return { url: `http://${h.host === '0.0.0.0' || !h.host ? '127.0.0.1' : h.host}:${h.port}`, token: h.token || '' };
    }
    return null;
  }
  async function call(action, params) {
    const ob = onebot();
    if (!ob) throw new Error('NapCat 没开 OneBot HTTP 服务端（网络配置里新建 HTTP 服务端 127.0.0.1:3000 后重试）');
    const r = await fetch(`${ob.url}/${action}`, { method: 'POST', headers: { 'content-type': 'application/json', ...(ob.token ? { authorization: `Bearer ${ob.token}` } : {}) }, body: JSON.stringify(params), signal: AbortSignal.timeout(30000) });
    const b = await r.json().catch(() => ({}));
    if (b.status !== 'ok' && b.retcode !== 0) throw new Error(`${action} 失败：${b.wording || b.message || r.status}`);
    return b.data;
  }
  // 会话校验：令牌必须属于这个 key（由桥接判定）
  async function sessionOk(key, token) {
    try {
      const r = await fetch(`http://127.0.0.1:${consolePort()}/api/socialV2/state?key=${encodeURIComponent(key)}`, {
        headers: { 'x-console-token': consoleToken(), 'x-agent-call': '1', 'x-agent-token': token }, signal: AbortSignal.timeout(10000) });
      const b = await r.json().catch(() => null);
      return r.ok && b && b.ok !== false;
    } catch { return false; }
  }
  async function ownerOnly(key, token) {
    const owner = String(getConfig().ownerQQ || '');
    if (!owner) return '还没设置主人（config.json 的 ownerQQ）';
    if (key !== `private:${owner}`) return '只有主人私聊吩咐才能改资料。礼貌告诉对方：这得主人点头。';
    if (!(await sessionOk(key, token))) return '会话令牌无效（用唤醒提示里的【会话令牌】）';
    return '';
  }
  async function fetchImage(src) {
    if (/^https?:\/\//i.test(src)) {
      const r = await fetch(src, { headers: { 'user-agent': UA, referer: 'https://cn.bing.com/' }, signal: AbortSignal.timeout(20000) });
      if (!r.ok) throw new Error(`图片下载失败 HTTP ${r.status}`);
      const buf = Buffer.from(await r.arrayBuffer());
      if (buf.length > 5 * 1024 * 1024) throw new Error('图片超过 5MB');
      if (!/^image\//.test(r.headers.get('content-type') || 'image/')) throw new Error('链接不是图片');
      return buf;
    }
    throw new Error('只支持 http(s) 图片链接（可以先用 dafeiyu_image_search 搜）');
  }
  async function bingImages(q, n) {
    const r = await fetch(`https://cn.bing.com/images/async?q=${encodeURIComponent(q)}&first=1&count=35&mmasync=1`, { headers: { 'user-agent': UA, 'accept-language': 'zh-CN,zh;q=0.9' }, signal: AbortSignal.timeout(15000) });
    const html = await r.text();
    const out = [];
    for (const m of html.matchAll(/m="(\{[^"]+\})"/g)) {
      try {
        const j = JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&'));
        if (j.murl) out.push({ url: j.murl, title: (j.t || '').slice(0, 60), thumb: j.turl });
      } catch {}
      if (out.length >= n) break;
    }
    return out;
  }
  const KEY = z.string().describe('会话 key，格式 private:QQ号 或 group:群号');
  const TOKEN = z.string().describe('会话令牌（见唤醒提示中的【会话令牌】）');

  server.tool('dafeiyu_set_nickname', '改自己（机器人小号）的 QQ 昵称。仅主人私聊吩咐时可用；冷却 10 分钟。改完用人设语气汇报一句。', { key: KEY, token: TOKEN, nickname: z.string().min(1).max(24) }, async ({ key, token, nickname }) => {
    const deny = await ownerOnly(key, token); if (deny) return txt(deny, true);
    const w = cool('nickname'); if (w) return txt(`昵称刚改过，${w} 秒后再试`, true);
    try { await call('set_qq_profile', { nickname }); return txt({ ok: true, nickname }); } catch (e) { last.nickname = 0; return txt(String(e.message || e), true); }
  });
  server.tool('dafeiyu_set_signature', '改自己的 QQ 个性签名。仅主人私聊吩咐时可用；冷却 5 分钟。', { key: KEY, token: TOKEN, text: z.string().min(1).max(80) }, async ({ key, token, text }) => {
    const deny = await ownerOnly(key, token); if (deny) return txt(deny, true);
    const w = cool('signature'); if (w) return txt(`签名刚改过，${w} 秒后再试`, true);
    try { await call('set_self_longnick', { longNick: text }); return txt({ ok: true, signature: text }); } catch (e) { last.signature = 0; return txt(String(e.message || e), true); }
  });
  server.tool('dafeiyu_set_avatar', '换自己的 QQ 头像。imageUrl 填图片直链（可先用 dafeiyu_image_search 搜，或用主人发来的图片链接）。仅主人私聊吩咐时可用；冷却 30 分钟。换之前最好先把候选图发给主人确认。', { key: KEY, token: TOKEN, imageUrl: z.string().url() }, async ({ key, token, imageUrl }) => {
    const deny = await ownerOnly(key, token); if (deny) return txt(deny, true);
    const w = cool('avatar'); if (w) return txt(`头像刚换过，${w} 秒后再试`, true);
    try { const buf = await fetchImage(imageUrl); await call('set_qq_avatar', { file: 'base64://' + buf.toString('base64') }); return txt({ ok: true, bytes: buf.length }); } catch (e) { last.avatar = 0; return txt(String(e.message || e), true); }
  });
  server.tool('dafeiyu_image_search', '联网搜图（必应图片），返回图片直链列表。用于找头像候选、找梗图、回答“X 长什么样”。不要搜色情、血腥、真人隐私照片。', { query: z.string().min(1).max(60), count: z.number().int().min(1).max(8).optional() }, async ({ query, count }) => {
    const w = cool('search'); if (w) return txt(`搜太快了，${w} 秒后再试`, true);
    try { const r = await bingImages(query, count || 5); return txt(r.length ? { ok: true, results: r } : { ok: false, error: '没搜到' }); } catch (e) { return txt(String(e.message || e), true); }
  });
  server.tool('dafeiyu_send_image', '把一张网络图片（直链）发到当前会话（私聊或群）。适合发搜到的图、头像候选给主人确认。每条一张，不能附文字；冷却 10 秒。', { key: KEY, token: TOKEN, imageUrl: z.string().url() }, async ({ key, token, imageUrl }) => {
    if (!(await sessionOk(key, token))) return txt('会话令牌无效', true);
    const w = cool('image'); if (w) return txt(`发太快了，${w} 秒后再试`, true);
    const [kind, id] = key.split(':');
    try {
      const buf = await fetchImage(imageUrl);
      const msg = [{ type: 'image', data: { file: 'base64://' + buf.toString('base64') } }];
      const d = kind === 'group' ? await call('send_group_msg', { group_id: Number(id), message: msg }) : await call('send_private_msg', { user_id: Number(id), message: msg });
      return txt({ ok: true, message_id: d?.message_id });
    } catch (e) { return txt(String(e.message || e), true); }
  });
}
