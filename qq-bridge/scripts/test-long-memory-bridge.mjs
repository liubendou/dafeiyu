// 长期记忆：真实 bridge.js 夹具里的集成测试（不连 DSH、不发真 QQ）。
import assert from 'node:assert/strict';
import http from 'node:http';
import { bridgeHarness } from './audit-bridge-harness.mjs';

const G = 'group:456';
const G2 = 'group:457';
let failures = 0;
const check = async (name, fn) => { try { await fn(); console.log('PASS', name); } catch (e) { failures++; console.error('FAIL', name, '—', e?.stack ?? e); } };

const h = await bridgeHarness({});
const server = h.startConsoleServer();
try {
  if (!server.listening) await new Promise((r) => server.once('listening', r));
  const port = server.address().port;
  const call = (p, body, headers = {}, method = 'POST') => new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: p, method, headers: { 'content-type': 'application/json', 'x-console-token': 'fixture-console-token', ...headers } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; }); res.on('end', () => { let j = null; try { j = JSON.parse(d); } catch {} resolve({ status: res.statusCode, body: j }); });
    });
    req.on('error', reject);
    req.setTimeout(8000, () => req.destroy(new Error('hung')));
    req.end(method === 'GET' ? undefined : JSON.stringify(body));
  });
  await h.initControllerOnce('reserved2');
  h.setMode('reserved2');
  for (const k of [G, G2]) h.getSocialV2State(k).agentToken = 'tok-' + k;
  const as = (k) => ({ 'x-agent-token': 'tok-' + k, 'x-agent-call': '1' });

  await check('存档可用，收到的消息和自己发的都进库', async () => {
    assert.ok(h.getChatArchive(), '存档没打开');
    h.recordLongMemory(G, { userId: '111', name: '小明', text: '我下个月要去成都吃火锅', msgId: 'm1' });
    h.recordLongMemory(G, { userId: '222', name: '小红', text: '/help', msgId: 'm2' });
    h.recordLongMemory(G2, { userId: '111', name: '小明', text: '另一个群的成都火锅' });
    h.recordSentMessagesV2(G, ['成都火锅冲！']);
    const hits = h.getChatArchive().search({ key: G, query: '火锅' });
    assert.equal(hits.length, 2);
    assert.ok(hits.some((x) => x.isSelf));
    assert.equal(h.getChatArchive().search({ key: G, query: 'help' }).length, 0, '斜杠命令不存');
  });

  await check('qq_recall：要令牌、只能查自己会话', async () => {
    assert.equal((await call('/api/socialV2/recall', { key: G, query: '火锅' }, { 'x-agent-call': '1' })).status, 403);
    assert.equal((await call('/api/socialV2/recall', { key: G, query: '火锅' }, as(G2))).status, 403, 'G2 的令牌查不了 G');
    const r = await call('/api/socialV2/recall', { key: G, query: '成都 火锅' }, as(G));
    assert.equal(r.status, 200, JSON.stringify(r.body));
    assert.equal(r.body.count, 2);
    assert.match(r.body.text, /小明\(QQ 111\)：我下个月要去成都吃火锅/);
    assert.doesNotMatch(r.body.text, /另一个群/);
    assert.equal((await call('/api/socialV2/recall', { key: G }, as(G))).status, 400);
  });

  await check('工具开关关掉就拒绝', async () => {
    h.cfg.socialV2 = h.cfg.socialV2 || {};
    h.cfg.socialV2.tools = { ...(h.cfg.socialV2.tools || {}), recall: false };
    assert.equal((await call('/api/socialV2/recall', { key: G, query: '火锅' }, as(G))).status, 403);
    h.cfg.socialV2.tools.recall = true;
  });

  await check('管理接口只给控制台，智能体令牌进不来', async () => {
    const r = await call('/api/long-memory', null, {}, 'GET');
    assert.equal(r.status, 200);
    assert.ok(r.body.stats.messages >= 3);
    assert.equal((await call('/api/long-memory', null, as(G), 'GET')).status, 403);
    assert.equal((await call('/api/long-memory/forget-user', { userId: '111' }, as(G))).status, 403);
  });

  await check('群记忆注入唤醒提示词，只注入本群的', async () => {
    h.getChatArchive().addGroupFact(G, '约好周六晚上八点一起开黑');
    const p = h.buildWakePromptV2(G, 'test');
    const text = typeof p === 'string' ? p : JSON.stringify(p);
    assert.match(text, /【群里的旧事】[\s\S]*周六晚上八点一起开黑/);
    const p2 = h.buildWakePromptV2(G2, 'test');
    assert.doesNotMatch(typeof p2 === 'string' ? p2 : JSON.stringify(p2), /群里的旧事/);
  });

  await check('忘掉我：删本会话本人的印象和存档，不动别的群', async () => {
    h.ext.people.addNote('111', { text: '下个月去成都', scope: G, origin: 'auto' });
    h.ext.people.addNote('111', { text: '别的群的印象', scope: G2, origin: 'auto' });
    const r = h.forgetMeIn(G, '111');
    assert.equal(r.ok, true);
    assert.equal(r.notes, 1);
    assert.equal(r.messages, 1);
    const left = h.ext.people.getPerson('111').notes.map((n) => n.text);
    assert.deepEqual(left, ['别的群的印象']);
    assert.equal(h.getChatArchive().search({ key: G2, query: '火锅' }).length, 1);
  });

  await check('手动整理：DSH 未就绪时安全失败，游标不动', async () => {
    for (let i = 0; i < 3; i++) h.recordLongMemory(G, { userId: '333', name: '阿伟', text: i === 0 ? '我是做前端的，天天写 React' : `闲聊${i}` });
    h.setReady(false);
    const before = h.getChatArchive().pending(G).count;
    const r = await call('/api/long-memory/run', { key: G });
    assert.equal(r.status, 503);
    assert.equal(h.getChatArchive().pending(G).count, before);
    h.setReady(true);
  });
} finally {
  server.close();
  try { h.getChatArchive()?.close(); } catch {}
  await h.close();
}
if (failures) { console.error(`\n${failures} 项失败`); process.exit(1); }
console.log('\n长期记忆桥接集成：全部通过');
