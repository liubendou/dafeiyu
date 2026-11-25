// qq_bili：真实 bridge.js 夹具里的端点集成测试。DSH 插件接口打桩，不连 DSH、不发真 QQ。
import assert from 'node:assert/strict';
import http from 'node:http';
import { bridgeHarness } from './audit-bridge-harness.mjs';
import { createBiliControl as realCreate } from '../src/bili-control.js';

const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452000000020000000308060000009a7c5d2d0000000049454e44ae426082', 'hex');
const OWNER = 'private:123';      // 夹具里 ownerQQ = 123
const GROUP = 'group:456';
const STRANGER = 'private:789';
let failures = 0;
const check = async (name, fn) => { try { await fn(); console.log('PASS', name); } catch (e) { failures++; console.error('FAIL', name, '—', e?.stack ?? e); } };

const dshCalls = [];
const h = await bridgeHarness({
  globals: {
    createBiliControl: (deps) => realCreate({
      ...deps,
      fetchDsh: async (p, init = {}) => {
        dshCalls.push({ p, body: init.body });
        if (p === '/api/bili-duty/status') return { ok: true, loggedIn: false, config: { autoComment: false } };
        if (p === '/api/bili-duty/login/start') return { ok: true, key: 'kk', qr: 'data:image/png;base64,' + PNG.toString('base64') };
        if (p === '/api/bili-duty/config') return { ok: true };
        if (p === '/api/bili-duty/run') return { ok: true, started: true };
        return { ok: true };
      },
    }),
  },
});
const server = h.startConsoleServer();
try {
  if (!server.listening) await new Promise((r) => server.once('listening', r));
  const port = server.address().port;
  const call = (body, headers = {}) => new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: '/api/socialV2/bili', method: 'POST', headers: { 'content-type': 'application/json', 'x-console-token': 'fixture-console-token', 'x-agent-call': '1', ...headers } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; }); res.on('end', () => { let j = null; try { j = JSON.parse(d); } catch {} resolve({ status: res.statusCode, body: j }); });
    });
    req.on('error', reject);
    req.setTimeout(8000, () => req.destroy(new Error('hung')));
    req.end(JSON.stringify(body));
  });
  await h.initControllerOnce('reserved2');
  h.setMode('reserved2');
  for (const k of [OWNER, GROUP, STRANGER]) h.getSocialV2State(k).agentToken = 'tok-' + k;
  const as = (k) => ({ 'x-agent-token': 'tok-' + k });

  await check('没令牌 / 错令牌拒绝', async () => {
    assert.equal((await call({ key: OWNER, action: 'status' })).status, 403);
    assert.equal((await call({ key: OWNER, action: 'status' }, { 'x-agent-token': 'bad' })).status, 403);
  });

  await check('群里、陌生人私聊一律拒绝，且不碰 DSH', async () => {
    dshCalls.length = 0;
    for (const k of [GROUP, STRANGER]) {
      for (const action of ['status', 'login', 'run', 'set']) {
        const r = await call({ key: k, action, settings: { autoComment: true } }, as(k));
        assert.equal(r.status, 403, `${k} ${action}`);
        assert.match(r.body.error, /只听主人/);
      }
    }
    assert.equal(dshCalls.length, 0);
  });

  await check('主人私聊：status / set / run 走 DSH 插件接口，危险设置被挡', async () => {
    const s = await call({ key: OWNER, action: 'status' }, as(OWNER));
    assert.equal(s.status, 200, JSON.stringify(s.body));
    assert.match(s.body.text, /没登录/);
    const set = await call({ key: OWNER, action: 'set', settings: { autoComment: true, pythonPath: 'C:/evil.exe' } }, as(OWNER));
    assert.equal(set.status, 200);
    assert.deepEqual(dshCalls.find((c) => c.p === '/api/bili-duty/config').body, { autoComment: true });
    const run = await call({ key: OWNER, action: 'run' }, as(OWNER));
    assert.equal(run.status, 200);
  });

  await check('主人私聊 login：二维码以 base64 图片段发到主人私聊', async () => {
    h.calls.http.length = 0;
    const r = await call({ key: OWNER, action: 'login' }, as(OWNER));
    assert.equal(r.status, 200, JSON.stringify(r.body));
    const sends = h.calls.http.filter((x) => x.url.endsWith('/send_private_msg'));
    assert.equal(sends.length, 1);
    assert.equal(sends[0].body.user_id, 123);
    const seg = sends[0].body.message.find((m) => m.type === 'image');
    assert.equal(seg.data.file, 'base64://' + PNG.toString('base64'));
  });

  await check('工具开关关掉后 403', async () => {
    h.cfg.socialV2.tools.biliControl = false;
    assert.equal((await call({ key: OWNER, action: 'status' }, as(OWNER))).status, 403);
    h.cfg.socialV2.tools.biliControl = true;
  });
} finally {
  h.ext?.controller?.stop?.();
  await new Promise((r) => server.close(r));
  await h.close();
}
if (failures) { console.error(`${failures} 项失败`); process.exit(1); }
console.log('bili bridge 集成测试全部通过');
process.exit(0);
