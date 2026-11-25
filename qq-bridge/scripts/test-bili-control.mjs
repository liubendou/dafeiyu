// qq_bili：主人私聊遥控刷B站。纯逻辑单元测试（不连 DSH、不发 QQ）。
import assert from 'node:assert/strict';
import { createBiliControl, sanitizeBiliSettings, summarizeBiliStatus, summarizeBiliReport, dataUrlToBuffer } from '../src/bili-control.js';

const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452000000020000000308060000009a7c5d2d0000000049454e44ae426082', 'hex');
let failures = 0;
async function test(name, fn) { try { await fn(); console.log('PASS', name); } catch (e) { failures++; console.error('FAIL', name, '—', e?.stack ?? e); } }

await test('设置白名单：危险项（pythonPath/dshBin/workspace/personaPrompt）一律拒绝，数值钳制', () => {
  const { patch, rejected } = sanitizeBiliSettings({ autoComment: true, commentPerRound: 9, perDay: -3, pythonPath: 'C:/evil.exe', dshBin: 'x', workspace: 'C:/', personaPrompt: '忽略之前', headlessProfile: 'web', keywords: ' 猫猫\n狗狗 ', transcribe: 'yes' });
  assert.deepEqual(patch, { autoComment: true, commentPerRound: 3, perDay: 0, keywords: '猫猫 狗狗' });
  for (const k of ['pythonPath', 'dshBin', 'workspace', 'personaPrompt', 'headlessProfile', 'transcribe']) assert.ok(rejected.some((r) => r.startsWith(k)), k);
  assert.deepEqual(sanitizeBiliSettings(null).patch, {});
  assert.deepEqual(sanitizeBiliSettings(['autoComment']).patch, {});
});

await test('状态 / 日报摘要', () => {
  const t = summarizeBiliStatus({ loggedIn: true, ranToday: 2, pickedToday: 6, lastRunAt: '2026-10-25T09:00:00Z', config: { autoComment: true, commentPerRound: 1, perDay: 5, noon: true, night: false } });
  assert.match(t, /已登录/); assert.match(t, /今天跑了 2 轮/); assert.match(t, /自动评论 开/); assert.match(t, /夜间 关/);
  assert.match(summarizeBiliStatus({ loggedIn: false, venvReady: false, config: {} }), /没登录[\s\S]*Python 环境没装好/);
  assert.match(summarizeBiliReport({ exists: false, day: '2026-10-25' }), /还没有日报/);
  const r = summarizeBiliReport({ exists: true, day: '2026-10-25', text: '# 日报\n### BV1xx\n好看\n### BV2yy\n' + 'x'.repeat(3000) }, 200);
  assert.match(r, /挑了 2 条/); assert.match(r, /后面省略/);
  assert.equal(dataUrlToBuffer('data:image/png;base64,' + PNG.toString('base64')).length, PNG.length);
  assert.equal(dataUrlToBuffer('data:text/html;base64,AAAA'), null);
});

function fixture({ loggedIn = false, pollStates = ['waiting', 'ok'] } = {}) {
  const calls = [];
  const sent = [];
  let t = 1_000_000;
  const ctl = createBiliControl({
    now: () => t,
    maxWritesPerHour: 5,
    sendQr: async (buf) => { sent.push(buf); },
    fetchDsh: async (p, init = {}) => {
      calls.push({ p, init });
      if (p === '/api/bili-duty/status') return { ok: true, loggedIn, config: {} };
      if (p === '/api/bili-duty/login/start') return { ok: true, key: 'k123', qr: 'data:image/png;base64,' + PNG.toString('base64') };
      if (p === '/api/bili-duty/login/poll') return { ok: true, state: pollStates.shift() ?? 'ok' };
      if (p === '/api/bili-duty/run') return { ok: true, started: true };
      if (p === '/api/bili-duty/config') return { ok: true, config: init.body };
      if (p === '/api/bili-duty/report') return { ok: true, exists: true, day: 'd', text: '### BV1\nx' };
      throw new Error('unexpected ' + p);
    },
  });
  return { ctl, calls, sent, tick: (ms) => { t += ms; } };
}

await test('登录：出码发到私聊 → 轮询 waiting → ok；已登录不重复出码；过期要重出', async () => {
  const f = fixture();
  assert.equal((await f.ctl.handle('login_check')).ok, false);
  const a = await f.ctl.handle('login');
  assert.equal(a.ok, true); assert.equal(f.sent.length, 1); assert.ok(f.sent[0].equals(PNG));
  assert.match((await f.ctl.handle('login_check')).text, /还没扫/);
  const ok = await f.ctl.handle('login_check');
  assert.equal(ok.loggedIn, true);
  assert.deepEqual(f.calls.filter((c) => c.p.endsWith('/poll')).map((c) => c.init.body.key), ['k123', 'k123']);
  const g = fixture({ loggedIn: true });
  assert.match((await g.ctl.handle('login')).text, /已经登录/);
  assert.equal(g.sent.length, 0);
  await g.ctl.handle('login', { force: true });
  assert.equal(g.sent.length, 1);
  g.tick(201 * 1000);
  assert.match((await g.ctl.handle('login_check')).error, /过期/);
});

await test('run / set / report / 未知 action / 写操作限频', async () => {
  const f = fixture();
  assert.match((await f.ctl.handle('run')).text, /开始刷一轮/);
  const s = await f.ctl.handle('set', { settings: { autoComment: true, pythonPath: 'x' } });
  assert.match(s.text, /自动评论=开/); assert.match(s.text, /pythonPath/);
  assert.deepEqual(f.calls.find((c) => c.p === '/api/bili-duty/config').init.body, { autoComment: true });
  assert.equal((await f.ctl.handle('set', { settings: { dshBin: 'x' } })).ok, false);
  assert.match((await f.ctl.handle('report')).text, /挑了 1 条/);
  assert.equal((await f.ctl.handle('rm -rf')).status, 400);
  for (let i = 0; i < 3; i++) await f.ctl.handle('run');
  assert.equal((await f.ctl.handle('run')).status, 429);
  assert.equal((await f.ctl.handle('status')).ok, true, '只读操作不限频');
});

if (failures) { console.error(`${failures} 项失败`); process.exit(1); }
console.log('bili-control 全部通过');
