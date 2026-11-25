// 搜图存图：真实 bridge.js 夹具里的端点集成测试。搜图与下载全部打桩，不访问网络、不发 QQ。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { bridgeHarness } from './audit-bridge-harness.mjs';

const GIF = Buffer.from('47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b', 'hex');
const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452000000020000000308060000009a7c5d2d0000000049454e44ae426082', 'hex');
const KEY = 'group:456';
const TOKEN = 'fixture-agent-token-imagesearch';
let failures = 0;
const check = async (name, fn) => { try { await fn(); console.log('PASS', name); } catch (e) { failures++; console.error('FAIL', name, '—', e?.stack ?? e); } };

const searched = [];
const h = await bridgeHarness({
  globals: {
    safeFetchBuffer: async () => ({ buffer: GIF }),
    searchImages: async (q, opts) => { searched.push({ q, opts }); return [
      { url: 'https://img.invalid/broken.jpg', thumb: 'https://img.invalid/broken-t.jpg', title: '坏图', page: 'https://site.invalid/0' },
      { url: 'https://img.invalid/1.png', thumb: 'https://img.invalid/1t.png', title: '大肥鱼一号', page: 'https://site.invalid/1' },
      { url: 'https://img.invalid/2.png', thumb: 'https://img.invalid/2t.png', title: '大肥鱼二号', page: 'https://site.invalid/2' },
    ]; },
    fetchCandidate: async (cand) => {
      if (String(cand.url).includes('broken')) throw new Error('403');
      return { buffer: PNG, mimeType: 'image/png', width: 2, height: 3, from: 'url' };
    },
  },
});
const server = h.startConsoleServer();
try {
  if (!server.listening) await new Promise((r) => server.once('listening', r));
  const port = server.address().port;
  const call = (p, body, headers = {}) => new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: p, method: 'POST', headers: { 'content-type': 'application/json', 'x-console-token': 'fixture-console-token', 'x-agent-call': '1', ...headers } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; }); res.on('end', () => { let j = null; try { j = JSON.parse(d); } catch {} resolve({ status: res.statusCode, body: j }); });
    });
    req.on('error', reject);
    req.setTimeout(8000, () => req.destroy(new Error('hung')));
    req.end(JSON.stringify(body));
  });
  await h.initControllerOnce('reserved2');
  h.setMode('reserved2');
  const st = h.getSocialV2State(KEY);
  st.agentToken = TOKEN;
  const agent = { 'x-agent-token': TOKEN };

  await check('没令牌 / 错令牌一律拒绝', async () => {
    assert.equal((await call('/api/socialV2/image-search', { key: KEY, query: '大肥鱼' })).status, 403);
    assert.equal((await call('/api/socialV2/image-search', { key: KEY, query: '大肥鱼' }, { 'x-agent-token': 'wrong' })).status, 403);
  });

  let first;
  await check('搜图：下载失败的候选被跳过，编号连续，返回缩略图', async () => {
    const r = await call('/api/socialV2/image-search', { key: KEY, query: '大肥鱼', count: 2 }, agent);
    assert.equal(r.status, 200, JSON.stringify(r.body));
    assert.deepEqual(r.body.results.map((x) => x.id), ['r1', 'r2']);
    assert.deepEqual(r.body.results.map((x) => x.title), ['大肥鱼一号', '大肥鱼二号']);
    assert.equal(r.body.images.length, 2);
    assert.equal(r.body.images[0].mimeType, 'image/png');
    first = r.body.results[0].id;
  });

  let savedId;
  await check('存图：按编号存进图库，文件落在 state/image-lib', async () => {
    const r = await call('/api/socialV2/image-save', { key: KEY, ref: first, name: '大肥鱼一号', tags: ['大肥鱼', '测试'] }, agent);
    assert.equal(r.status, 200, JSON.stringify(r.body));
    savedId = r.body.image.id;
    assert.match(savedId, /^img_[0-9a-f]{10}$/);
    const files = fs.readdirSync(path.join(h.temp, 'state', 'image-lib'));
    assert.equal(files.length, 1);
    assert.ok(fs.existsSync(path.join(h.temp, 'state', 'image-lib.json')));
    const missing = await call('/api/socialV2/image-save', { key: KEY, ref: 'r99', name: 'x' }, agent);
    assert.equal(missing.status, 500);
    const noName = await call('/api/socialV2/image-save', { key: KEY, ref: first }, agent);
    assert.equal(noName.status, 400);
  });

  await check('存图：聊天里别人发的图（messageId）', async () => {
    h.appendSocialV2Message(KEY, '甲', '[图片]', '[图片]', false, false, '777', [{ kind: 'image', url: 'https://public.invalid/a.png' }], '20001');
    const r = await call('/api/socialV2/image-save', { key: KEY, messageId: '777', name: '群友的图' }, agent);
    assert.equal(r.status, 200, JSON.stringify(r.body));
    // 聊天图走桥接的安全下载（这里打桩成 1x1 GIF，和上一张字节不同），所以是新图
    assert.equal(r.body.duplicate, false);
    assert.equal(r.body.total, 2);
  });

  await check('查图库：按名字搜，preview 带图', async () => {
    const r = await call('/api/socialV2/image-list', { key: KEY, query: '大肥鱼', preview: true }, agent);
    assert.equal(r.status, 200);
    assert.equal(r.body.items[0].id, savedId);
    assert.equal(r.body.images.length >= 1, true);
  });

  await check('发图：图库 id 与搜图编号都能发，走 OneBot send_group_msg 的 base64 图片段', async () => {
    h.calls.http.length = 0;
    const a = await call('/api/socialV2/send-image', { key: KEY, ref: savedId }, agent);
    assert.equal(a.status, 200, JSON.stringify(a.body));
    const b = await call('/api/socialV2/send-image', { key: KEY, ref: 'r2' }, agent);
    assert.equal(b.status, 200, JSON.stringify(b.body));
    const sends = h.calls.http.filter((x) => x.url.endsWith('/send_group_msg'));
    assert.equal(sends.length, 2);
    const seg = sends[0].body.message.find((m) => m.type === 'image');
    assert.match(seg.data.file, /^base64:\/\//);
    assert.ok(!JSON.stringify(sends).includes('img.invalid'), 'URL 不交给网关去抓');
    const list = await call('/api/socialV2/image-list', { key: KEY, query: '大肥鱼一号' }, agent);
    assert.equal(list.body.items[0].usedCount, 1);
    assert.ok(h.getSocialV2State(KEY).recentMessages.some((m) => m.isSelf && /\[图片:大肥鱼一号\]/.test(m.text)));
  });

  await check('其他会话拿不到本会话的搜图编号', async () => {
    const other = h.getSocialV2State('private:123');
    other.agentToken = TOKEN + '-other';
    const r = await call('/api/socialV2/send-image', { key: 'private:123', ref: 'r1' }, { 'x-agent-token': TOKEN + '-other' });
    assert.equal(r.status, 500);
    assert.match(r.body.error, /找不到搜图结果/);
  });

  await check('工具开关关掉后 403', async () => {
    h.cfg.socialV2.tools.searchImages = false;
    const r = await call('/api/socialV2/image-search', { key: KEY, query: '大肥鱼' }, agent);
    assert.equal(r.status, 403);
    h.cfg.socialV2.tools.searchImages = true;
  });

  await check('搜图限频', async () => {
    h.cfg.socialV2.imageSearch.maxPerMinute = 1;
    h.getSocialV2State(KEY).imageSearchTimes = [];
    assert.equal((await call('/api/socialV2/image-search', { key: KEY, query: '猫' }, agent)).status, 200);
    assert.equal((await call('/api/socialV2/image-search', { key: KEY, query: '狗' }, agent)).status, 429);
  });
} finally {
  h.ext?.controller?.stop?.();
  await new Promise((r) => server.close(r));
  await h.close();
}
if (failures) { console.error(`${failures} 项失败`); process.exit(1); }
console.log('image bridge 集成测试全部通过');
process.exit(0);
