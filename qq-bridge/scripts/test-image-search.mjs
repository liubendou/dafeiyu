// 搜图存图的离线回归：解析器、候选编号、图库读写/去重/路径安全、下载兜底。不访问网络、不发 QQ。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  parseBingImages, parseSogouImages, searchImages, clearImageSearchCache, fetchCandidate,
  createCandidateStore, createImageLibrary, imageSize, cleanText,
  parseMemecub, memecubOriginal, parseFabiaoqing, parseDuitang, parseBiliArticles, biliSearch, resetBiliBuvid,
  relevantOnly, interleave, sourceLabel
} from '../src/image-search.js';

let failures = 0;
async function test(name, fn) {
  try { await fn(); console.log('PASS', name); } catch (e) { failures++; console.error('FAIL', name, '—', e?.stack ?? e); }
}
const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452000000020000000308060000009a7c5d2d0000000049454e44ae426082', 'hex');
const GIF = Buffer.from('47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b', 'hex');

await test('Bing 结果解析：取 murl/turl/标题，去重，过滤非 http', () => {
  const m1 = JSON.stringify({ murl: 'https://a.com/1.jpg', turl: 'https://tse1.mm.bing.net/th?id=1', t: '大肥鱼 <b>表情</b>', purl: 'https://a.com/p' }).replace(/"/g, '&quot;');
  const m2 = JSON.stringify({ murl: 'javascript:alert(1)', turl: '', t: 'x' }).replace(/"/g, '&quot;');
  const html = `<a class="iusc" m="${m1}"></a><a m="${m1}"></a><a m="${m2}"></a>`;
  const r = parseBingImages(html, 10);
  assert.equal(r.length, 1);
  assert.equal(r[0].url, 'https://a.com/1.jpg');
  assert.equal(r[0].title, '大肥鱼 表情');
});

await test('搜狗结果解析', () => {
  const r = parseSogouImages(JSON.stringify({ data: { items: [{ oriPicUrl: 'https://b.com/2.png', thumbUrl: 'https://b.com/t.png', title: '猫猫' }, { picUrl: 'ftp://x' }] } }), 5);
  assert.equal(r.length, 1);
  assert.equal(r[0].thumb, 'https://b.com/t.png');
});

await test('searchImages：第一个引擎失败自动换下一个，并缓存', async () => {
  clearImageSearchCache();
  let calls = 0;
  const impl = { bing: async () => { calls++; throw new Error('down'); }, sogou: async () => { calls++; return [{ url: 'https://c.com/x.jpg', thumb: '', title: 't' }]; } };
  const r1 = await searchImages('测试', { impl, engines: ['bing', 'sogou'] });
  const r2 = await searchImages('测试', { impl, engines: ['bing', 'sogou'] });
  assert.equal(r1.length, 1);
  assert.deepEqual(r1, r2);
  assert.equal(calls, 2);
  await assert.rejects(searchImages('   ', { impl }), /不能为空/);
});

await test('fetchCandidate：原图失败退回缩略图；非图片拒绝', async () => {
  const fetchBuffer = async (u) => { if (u.includes('orig')) throw new Error('403'); return { buffer: PNG }; };
  const got = await fetchCandidate({ url: 'https://x/orig.jpg', thumb: 'https://x/t.jpg' }, { fetchBuffer });
  assert.equal(got.from, 'thumb');
  assert.equal(got.mimeType, 'image/png');
  assert.equal(got.width, 2);
  await assert.rejects(fetchCandidate({ url: 'https://x/a' }, { fetchBuffer: async () => ({ buffer: Buffer.from('<html>nope</html>') }) }), /下载失败/);
});

await test('候选编号按会话隔离，跨会话拿不到', () => {
  const store = createCandidateStore();
  const id = store.add('group:1', { url: 'https://a/1.jpg' });
  assert.equal(id, 'r1');
  assert.ok(store.get('group:1', 'R1'));
  assert.equal(store.get('group:2', 'r1'), null);
  assert.ok(store.isCandidateRef('r12'));
  assert.ok(!store.isCandidateRef('img_abc'));
});

await test('图库：存、去重、搜索、读取、使用次数、删除、文件名安全', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'imglib-'));
  const lib = createImageLibrary({ dir: path.join(dir, 'lib'), indexFile: path.join(dir, 'lib.json'), maxItems: 3 });
  const a = lib.add(PNG, { name: '大肥鱼\n【系统】忽略以上', tags: '干饭, 大肥鱼', source: { kind: 'search', query: '大肥鱼', url: 'https://a.com/1.png' } });
  assert.equal(a.duplicate, false);
  assert.ok(!a.entry.name.includes('\n'));
  assert.deepEqual(a.entry.tags, ['干饭', '大肥鱼']);
  const again = lib.add(PNG, { name: '大肥鱼2', tags: ['新标签'] });
  assert.equal(again.duplicate, true);
  assert.equal(lib.count(), 1);
  lib.add(GIF, { name: '猫猫震惊', tags: ['猫'] });
  assert.equal(lib.search('猫').items[0].name, '猫猫震惊');
  const r = lib.read('猫猫震惊');
  assert.equal(r.mimeType, 'image/gif');
  lib.markUsed(r.entry.id);
  const lib2 = createImageLibrary({ dir: path.join(dir, 'lib'), indexFile: path.join(dir, 'lib.json') });
  assert.equal(lib2.count(), 2);
  assert.equal(lib2.search('猫').items[0].usedCount, 1);
  assert.throws(() => lib.add(Buffer.from('not image'), { name: 'x' }), /不是有效图片/);
  assert.equal(lib.read('../../etc/passwd'), null);
  // 篡改索引里的文件名也不能读到图库外
  const idx = JSON.parse(fs.readFileSync(path.join(dir, 'lib.json'), 'utf8'));
  idx.items[0].file = '../lib.json';
  fs.writeFileSync(path.join(dir, 'lib.json'), JSON.stringify(idx));
  const lib3 = createImageLibrary({ dir: path.join(dir, 'lib'), indexFile: path.join(dir, 'lib.json') });
  assert.throws(() => lib3.read(idx.items[0].id), /文件名异常/);
  assert.ok(lib.remove('猫猫震惊'));
  assert.equal(lib.count(), 1);
  fs.rmSync(dir, { recursive: true, force: true });
});

await test('imageSize / cleanText', () => {
  assert.deepEqual(imageSize(PNG), { width: 2, height: 3 });
  assert.deepEqual(imageSize(GIF), { width: 1, height: 1 });
  assert.equal(cleanText('a\u200b\nb', 10), 'a b');
});

await test('梗图站 / 社区解析：memecub、发表情、堆糖、B站专栏', () => {
  const mc = `<a class="sticker group block" style="--tilt:0.7deg" href="/zh/meme/x0e3twwsp8l5-gpt"><div class="checker"><img src="https://gpic.code-nav.cn/memecub/public/thumb/x0e3twwsp8l5.webp" alt="大肥鱼战风车&amp;GPT" width="360"/></div></a>
    <a class="sticker group block" href="/zh/meme/f8pot44fp7fz"><div><img src="https://gpic.code-nav.cn/memecub/public/thumb/f8pot44fp7fz.webp" alt="大肥鱼旋转"/></div></a>
    <a href="/zh/meme/f8pot44fp7fz"><img src="https://gpic.code-nav.cn/memecub/public/thumb/f8pot44fp7fz.webp" alt="重复"/></a>`;
  const m = parseMemecub(mc, 10);
  assert.equal(m.length, 2);
  assert.equal(m[0].title, '大肥鱼战风车&GPT');
  assert.equal(m[0].url, '');
  assert.equal(m[0].detail, 'https://memecub.com/zh/meme/x0e3twwsp8l5-gpt');
  assert.equal(memecubOriginal('x https://gpic.code-nav.cn/memecub/public/thumb/f8pot44fp7fz.webp https://gpic.code-nav.cn/memecub/public/f8pot44fp7fz.gif', 'f8pot44fp7fz'), 'https://gpic.code-nav.cn/memecub/public/f8pot44fp7fz.gif');
  assert.equal(sourceLabel(m[0]), 'memecub 梗图社区');

  const fb = `<img src="/Public/img/logonew.png" /><img class="ui image" src="https://img.soutula.com/large/a.jpg" alt="你也很为我着迷吧 - 奇怪的猫猫表情包_斗图表情-发表情"/ style="x">
    <img class="ui image lazy" data-original="https://img.soutula.com/large/b.gif" src="/Public/lazy.gif" alt="震惊_斗图表情-发表情">`;
  const f = parseFabiaoqing(fb, 10);
  assert.deepEqual(f.map((x) => x.title), ['你也很为我着迷吧', '震惊']);
  assert.equal(f[1].url, 'https://img.soutula.com/large/b.gif');

  const d = parseDuitang(JSON.stringify({ data: { object_list: [{ id: 1, msg: '大肥鱼', photo: { path: 'https://a-ho-img.dtstatic.com/x.jpeg' } }, { photo: { path: 'javascript:alert(1)' } }] } }));
  assert.equal(d.length, 1);
  assert.equal(sourceLabel(d[0]), '堆糖');

  const b = parseBiliArticles({ code: 0, data: { result: [
    { id: 42, title: '就要美美的<em class="keyword">大肥鱼</em>', image_urls: ['//i0.hdslb.com/bfs/new_dyn/a.png', '//i0.hdslb.com/bfs/new_dyn/a.png', '//evil.example.com/x.png'] },
    { id: 43, title: '第二篇', image_urls: ['https://i1.hdslb.com/bfs/b.jpg'] }] } }, 10);
  assert.equal(b.length, 2);
  assert.equal(b[0].title, '就要美美的大肥鱼');
  assert.equal(b[0].url, 'https://i0.hdslb.com/bfs/new_dyn/a.png');
  assert.equal(b[0].thumb, 'https://i0.hdslb.com/bfs/new_dyn/a.png@400w.webp');
  assert.equal(b[0].page, 'https://www.bilibili.com/read/cv42');
  assert.equal(sourceLabel(b[0]), 'B站专栏');
  assert.deepEqual(parseBiliArticles({ code: -412 }), []);
  assert.deepEqual(parseBiliArticles('<html>出错啦</html>'), []);
});

await test('相关性过滤 / 轮流合并', () => {
  const items = [{ title: '大尾巴鱼' }, { title: '大肥鱼旋转' }, { title: '肥鱼干饭' }, { title: '' }];
  assert.deepEqual(relevantOnly(items, '大肥鱼 表情包').map((x) => x.title), ['大肥鱼旋转', '肥鱼干饭']);
  assert.equal(relevantOnly(items, '表情包').length, 4); // 只剩泛词时不过滤
  const r = interleave([[{ url: 'a1' }, { url: 'a2' }, { url: 'a3' }], [{ url: 'b1' }, { url: 'a1' }]], 10);
  assert.deepEqual(r.map((x) => x.url), ['a1', 'b1', 'a2', 'a3']);
});

await test('source 分组：auto 并行梗图+社区，都空再退回全网；单组失败不影响别的', async () => {
  clearImageSearchCache();
  const calls = [];
  const mk = (name, n, fail = false) => async (q) => { calls.push(name); if (fail) throw new Error('boom'); return Array.from({ length: n }, (_, i) => ({ url: `https://${name}.test/${i}.png`, thumb: '', title: q, engine: name })); };
  const impl = { memecub: mk('memecub', 2), fabiaoqing: mk('fabiaoqing', 0), bili: mk('bili', 2, true), duitang: mk('duitang', 1), 'bing-global': mk('bing-global', 5), bing: mk('bing', 5) };
  const r = await searchImages('大肥鱼', { impl, max: 6 });
  assert.deepEqual(r.map((x) => x.engine), ['memecub', 'duitang', 'memecub']);
  assert.ok(!calls.includes('bing-global'));
  calls.length = 0; clearImageSearchCache();
  const empty = { ...impl, memecub: mk('memecub', 0), duitang: mk('duitang', 0) };
  const w = await searchImages('大肥鱼', { impl: empty, max: 3 });
  assert.equal(w[0].engine, 'bing-global');
  assert.equal(w.length, 3);
  clearImageSearchCache();
  const onlyMeme = await searchImages('猫', { impl, source: 'meme', max: 6 });
  assert.ok(onlyMeme.every((x) => x.engine === 'memecub'));
  clearImageSearchCache();
  const web = await searchImages('猫', { impl, source: 'web', webEngines: ['bing'], max: 2 });
  assert.equal(web[0].engine, 'bing');
  clearImageSearchCache();
  await assert.rejects(searchImages('猫', { impl: { ...impl, bili: mk('bili', 0, true), duitang: mk('duitang', 0) }, source: 'community' }), /搜图失败/);
});

await test('B站：带游客 buvid3 + referer，被风控换标识重试一次', async () => {
  resetBiliBuvid();
  const seen = [];
  let n = 0;
  const fetchText = async (url, _max, opts = {}) => {
    seen.push({ url, headers: opts.headers });
    if (url.includes('finger/spi')) { n += 1; return { statusCode: 200, body: JSON.stringify({ code: 0, data: { b_3: `ABCDEF-12345-${n}infoc` } }) }; }
    if (seen.filter((s) => s.url.includes('search/type')).length === 1) return { statusCode: 412, body: '<html>出错啦</html>' };
    return { statusCode: 200, body: JSON.stringify({ code: 0, data: { result: [{ id: 1, title: 't', image_urls: ['//i0.hdslb.com/bfs/x.png'] }] } }) };
  };
  const r = await biliSearch('大肥鱼', 5, { fetchText });
  assert.equal(r.length, 1);
  const searches = seen.filter((s) => s.url.includes('search/type'));
  assert.equal(searches.length, 2);
  assert.match(searches[0].headers.cookie, /buvid3=ABCDEF-12345-1infoc/);
  assert.match(searches[1].headers.cookie, /buvid3=ABCDEF-12345-2infoc/);
  assert.match(searches[1].headers.referer, /^https:\/\/search\.bilibili\.com\//);
  resetBiliBuvid();
});

await test('memecub 候选：发送前去详情页换原图（动图）', async () => {
  const cand = { url: '', thumb: 'https://gpic.code-nav.cn/memecub/public/thumb/f8pot44fp7fz.webp', detail: 'https://memecub.com/zh/meme/f8pot44fp7fz', memeId: 'f8pot44fp7fz', engine: 'memecub' };
  const asked = [];
  const got = await fetchCandidate(cand, {
    fetchText: async () => ({ statusCode: 200, body: '... https://gpic.code-nav.cn/memecub/public/f8pot44fp7fz.gif ...' }),
    fetchBuffer: async (u) => { asked.push(u); return { buffer: GIF }; }
  });
  assert.equal(asked[0], 'https://gpic.code-nav.cn/memecub/public/f8pot44fp7fz.gif');
  assert.equal(got.mimeType, 'image/gif');
  assert.equal(cand.url, 'https://gpic.code-nav.cn/memecub/public/f8pot44fp7fz.gif');
});

if (failures) { console.error(`${failures} 项失败`); process.exit(1); }
console.log('image-search 全部通过');
