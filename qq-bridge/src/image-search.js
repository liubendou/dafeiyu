// 联网搜图 + 本地图库（二代仿真：qq_search_images / qq_save_image / qq_list_saved_images / qq_send_image）。
//
// 职责：
// - 搜图：分三类来源，并行搜、轮流取、去重
//     meme      梗图/表情包站：memecub（AI 梗图社区，大肥鱼/Claude/GPT 梗很全）、发表情 fabiaoqing
//     community 社区：B站专栏（配图）、堆糖
//     web       全网：Bing 图片（安全搜索 strict）
//   auto = meme + community，一张都没有再退回 web。只解析结果，不执行任何脚本
// - 候选缓存：每个会话最近一批搜索结果，编号 r1、r2…（30 分钟有效），供“看图 → 存图/发图”引用
// - 本地图库：state/image-lib/ 存图片字节，state/image-lib.json 存索引（名字/标签/来源/使用次数）
//
// 安全边界：
// - 所有下载都走 safe-fetch（DNS 固定 + 逐跳校验 + 大小上限 + 图片签名校验），不把 URL 交给 OneBot 去抓
// - 图库里的名字/标签是跨会话共享的自由文本：统一单行化、截断，只当资料展示，不当指令
// - 图库文件名由桥接生成（img_ + 随机），AI 传入的任何字符串都不会变成路径

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { safeFetch, safeFetchBuffer, looksLikeImageBuffer } from './safe-fetch.js';

// ── 小工具 ────────────────────────────────────────────────────────────────
export function cleanText(value, max = 60) {
  const s = String(value ?? '')
    .replace(/[\s\u00a0\u3000]+/g, ' ')
    .replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028\u2029\ufeff]/g, '')
    .trim();
  const points = Array.from(s);
  return points.length > max ? points.slice(0, max).join('') : s;
}

export function decodeEntities(s) {
  return String(s ?? '')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&#x27;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&amp;/g, '&');
}

function stripTags(s) {
  return decodeEntities(String(s ?? '').replace(/<[^>]*>/g, ''));
}

function httpUrl(u) {
  try {
    const url = new URL(String(u ?? '').trim());
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : '';
  } catch {
    return '';
  }
}

export function hostOf(u) {
  try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return ''; }
}

export function mimeOf(buf) {
  if (!buf || buf.length < 12) return 'image/jpeg';
  if (buf[0] === 0x89 && buf[1] === 0x50) return 'image/png';
  if (buf[0] === 0xff && buf[1] === 0xd8) return 'image/jpeg';
  if (buf.toString('ascii', 0, 3) === 'GIF') return 'image/gif';
  if (buf.toString('ascii', 0, 4) === 'RIFF' && buf.toString('ascii', 8, 12) === 'WEBP') return 'image/webp';
  return 'image/jpeg';
}

const EXT = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp' };

/** PNG/JPEG/GIF/WebP 宽高（解析失败返回 null）。用于拦截“图片炸弹”和给 AI 报尺寸。 */
export function imageSize(buf) {
  try {
    if (!buf || buf.length < 24) return null;
    if (buf[0] === 0x89 && buf[1] === 0x50) return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
    if (buf.toString('ascii', 0, 3) === 'GIF') return { width: buf.readUInt16LE(6), height: buf.readUInt16LE(8) };
    if (buf.toString('ascii', 0, 4) === 'RIFF' && buf.toString('ascii', 8, 12) === 'WEBP') {
      const chunk = buf.toString('ascii', 12, 16);
      if (chunk === 'VP8X') return { width: 1 + buf.readUIntLE(24, 3), height: 1 + buf.readUIntLE(27, 3) };
      if (chunk === 'VP8 ') return { width: buf.readUInt16LE(26) & 0x3fff, height: buf.readUInt16LE(28) & 0x3fff };
      if (chunk === 'VP8L') {
        const b = buf.readUInt32LE(21);
        return { width: (b & 0x3fff) + 1, height: ((b >> 14) & 0x3fff) + 1 };
      }
      return null;
    }
    if (buf[0] === 0xff && buf[1] === 0xd8) {
      let i = 2;
      while (i + 9 < buf.length) {
        if (buf[i] !== 0xff) { i += 1; continue; }
        const marker = buf[i + 1];
        const len = buf.readUInt16BE(i + 2);
        if ((marker >= 0xc0 && marker <= 0xc3) || (marker >= 0xc5 && marker <= 0xc7) || (marker >= 0xc9 && marker <= 0xcb) || (marker >= 0xcd && marker <= 0xcf)) {
          return { width: buf.readUInt16BE(i + 7), height: buf.readUInt16BE(i + 5) };
        }
        i += 2 + len;
      }
    }
  } catch {}
  return null;
}

// ── 搜图 ──────────────────────────────────────────────────────────────────
/** 解析 Bing 图片异步结果页：每个结果的 a.iusc 上有 m="{murl,turl,t,purl}"。 */
export function parseBingImages(html, max = 12) {
  const out = [];
  const seen = new Set();
  for (const m of String(html ?? '').matchAll(/\bm="(\{[^"]*?\})"/g)) {
    let meta;
    try { meta = JSON.parse(decodeEntities(m[1])); } catch { continue; }
    const url = httpUrl(meta.murl);
    const thumb = httpUrl(meta.turl);
    if (!url && !thumb) continue;
    const dedupe = url || thumb;
    if (seen.has(dedupe)) continue;
    seen.add(dedupe);
    out.push({ url, thumb, page: httpUrl(meta.purl), title: cleanText(stripTags(meta.t || meta.desc || ''), 60), engine: 'bing' });
    if (out.length >= max) break;
  }
  return out;
}

/** 解析搜狗图片 JSON（napi/pc/searchList）。 */
export function parseSogouImages(body, max = 12) {
  let data;
  try { data = typeof body === 'string' ? JSON.parse(body) : body; } catch { return []; }
  const items = data?.data?.items ?? data?.items ?? [];
  const out = [];
  const seen = new Set();
  for (const it of Array.isArray(items) ? items : []) {
    const url = httpUrl(it.oriPicUrl || it.picUrl || it.pic_url);
    const thumb = httpUrl(it.thumbUrl || it.locImageLink || it.thumb_url);
    if (!url && !thumb) continue;
    const dedupe = url || thumb;
    if (seen.has(dedupe)) continue;
    seen.add(dedupe);
    out.push({ url, thumb, page: httpUrl(it.url || it.page_url), title: cleanText(stripTags(it.title || it.name || ''), 60), engine: 'sogou' });
    if (out.length >= max) break;
  }
  return out;
}

export async function bingImageSearch(query, max = 12, host = 'cn.bing.com') {
  const u = new URL(`https://${host}/images/async`);
  u.searchParams.set('q', query);
  u.searchParams.set('first', '0');
  u.searchParams.set('count', '35');
  u.searchParams.set('mmasync', '1');
  u.searchParams.set('adlt', 'strict');
  u.searchParams.set('setlang', 'zh-hans');
  u.searchParams.set('cc', 'CN');
  const res = await safeFetch(u.toString(), 1500000);
  if (res.statusCode < 200 || res.statusCode >= 300) throw new Error(`Bing 图片 HTTP ${res.statusCode}`);
  return parseBingImages(res.body, max);
}

export async function sogouImageSearch(query, max = 12) {
  const u = new URL('https://pic.sogou.com/napi/pc/searchList');
  u.searchParams.set('mode', '1');
  u.searchParams.set('start', '0');
  u.searchParams.set('xml_len', '48');
  u.searchParams.set('query', query);
  const res = await safeFetch(u.toString(), 1500000);
  if (res.statusCode < 200 || res.statusCode >= 300) throw new Error(`搜狗图片 HTTP ${res.statusCode}`);
  return parseSogouImages(res.body, max);
}

// ── 梗图 / 社区来源 ───────────────────────────────────────────────────────
const MEMECUB = 'https://memecub.com';

/** memecub 搜索页：每个结果是 <a href="/zh/meme/<id>-slug"> 里包一张 thumb/<id>.webp。原图要到详情页取。 */
export function parseMemecub(html, max = 12) {
  const out = [];
  const seen = new Set();
  const body = String(html ?? '');
  for (const m of body.matchAll(/href="(\/(?:[a-z]{2}\/)?meme\/([a-z0-9]{6,20})[^"]*)"/g)) {
    const [, href, id] = m;
    if (seen.has(id)) continue;
    const near = body.slice(m.index, m.index + 900);
    const img = near.match(/<img[^>]*src="(https:\/\/gpic\.code-nav\.cn\/memecub\/public\/thumb\/[a-z0-9]+\.(?:webp|png|jpe?g|gif))"[^>]*>/);
    if (!img) continue;
    const alt = img[0].match(/alt="([^"]*)"/);
    seen.add(id);
    out.push({ url: '', thumb: img[1], detail: MEMECUB + href, page: MEMECUB + href, memeId: id, title: cleanText(decodeEntities(alt?.[1] ?? ''), 60), engine: 'memecub' });
    if (out.length >= max) break;
  }
  return out;
}

/** memecub 详情页里找这张梗图的原图（gif/png/jpg/webp，非 thumb）。 */
export function memecubOriginal(html, id) {
  const re = new RegExp(`https://gpic\\.code-nav\\.cn/memecub/public/${id}\\.(?:gif|png|jpe?g|webp)`, 'i');
  return String(html ?? '').match(re)?.[0] ?? '';
}

export async function memecubSearch(query, max = 12) {
  const u = new URL(`${MEMECUB}/search`);
  u.searchParams.set('q', query);
  const res = await safeFetch(u.toString(), 1500000);
  if (res.statusCode < 200 || res.statusCode >= 300) throw new Error(`memecub HTTP ${res.statusCode}`);
  return parseMemecub(res.body, max);
}

/** 发表情（fabiaoqing.com）搜索页：<img class="ui image" src="…" alt="标题 - 表情包名_斗图表情-发表情">。 */
export function parseFabiaoqing(html, max = 12) {
  const out = [];
  const seen = new Set();
  for (const m of String(html ?? '').matchAll(/<img[^>]*class="ui image[^"]*"[^>]*>/g)) {
    const tag = m[0];
    const src = httpUrl(decodeEntities((tag.match(/data-original="([^"]+)"/) || tag.match(/\bsrc="([^"]+)"/) || [])[1] || ''));
    if (!src || /\/Public\//.test(src) || seen.has(src)) continue;
    seen.add(src);
    const alt = decodeEntities((tag.match(/alt="([^"]*)"/) || [])[1] || '').split(/ - |_斗图/)[0];
    out.push({ url: src, thumb: src, page: '', title: cleanText(alt, 60), engine: 'fabiaoqing' });
    if (out.length >= max) break;
  }
  return out;
}

export async function fabiaoqingSearch(query, max = 12) {
  const res = await safeFetch(`https://fabiaoqing.com/search/bqb/keyword/${encodeURIComponent(query)}/type/bq/page/1.html`, 1500000);
  if (res.statusCode < 200 || res.statusCode >= 300) throw new Error(`发表情 HTTP ${res.statusCode}`);
  return parseFabiaoqing(res.body, max);
}

/** 堆糖（duitang）搜索接口 JSON。 */
export function parseDuitang(body, max = 12) {
  let data;
  try { data = typeof body === 'string' ? JSON.parse(body) : body; } catch { return []; }
  const out = [];
  for (const it of data?.data?.object_list ?? []) {
    const url = httpUrl(it?.photo?.path);
    if (!url) continue;
    out.push({ url, thumb: url, page: it.id ? `https://www.duitang.com/blog/?id=${it.id}` : '', title: cleanText(it.msg || '', 60), engine: 'duitang' });
    if (out.length >= max) break;
  }
  return out;
}

export async function duitangSearch(query, max = 12) {
  const u = new URL('https://www.duitang.com/napi/blog/list/by_search/');
  u.searchParams.set('kw', query);
  u.searchParams.set('type', 'feed');
  u.searchParams.set('start', '0');
  u.searchParams.set('limit', '24');
  const res = await safeFetch(u.toString(), 1500000);
  if (res.statusCode < 200 || res.statusCode >= 300) throw new Error(`堆糖 HTTP ${res.statusCode}`);
  return parseDuitang(res.body, max);
}

// ── B站专栏 ────────────────────────────────────────────────────────────────
// 公开搜索接口不用登录，但要带一个游客 buvid3 cookie + 搜索页 referer，否则很快 412。
// buvid3 从 finger/spi 拿，缓存 6 小时；被风控时换一个新的再试一次。
let biliBuvid = { value: '', at: 0 };
async function biliBuvid3(force = false, fetchText = safeFetch) {
  if (!force && biliBuvid.value && Date.now() - biliBuvid.at < 6 * 3600 * 1000) return biliBuvid.value;
  const r = await fetchText('https://api.bilibili.com/x/frontend/finger/spi', 20000);
  const v = JSON.parse(r.body)?.data?.b_3;
  if (!v || !/^[A-Za-z0-9-]{10,80}$/.test(v)) throw new Error('B站游客标识获取失败');
  biliBuvid = { value: v, at: Date.now() };
  return v;
}
export function resetBiliBuvid() { biliBuvid = { value: '', at: 0 }; }

/** B站专栏搜索结果：每篇文章带 0~3 张 image_urls（协议相对地址）。缩略图用 B站自带的 @400w.webp 变体。 */
export function parseBiliArticles(body, max = 12) {
  let data;
  try { data = typeof body === 'string' ? JSON.parse(body) : body; } catch { return []; }
  if (data?.code !== 0) return [];
  const out = [];
  const seen = new Set();
  for (const it of data?.data?.result ?? []) {
    const title = cleanText(stripTags(it.title || ''), 60);
    for (const raw of Array.isArray(it.image_urls) ? it.image_urls : []) {
      const u = httpUrl(String(raw).startsWith('//') ? 'https:' + raw : raw);
      if (!u || seen.has(u) || !/\.hdslb\.com$/.test(new URL(u).hostname)) continue;
      seen.add(u);
      out.push({ url: u, thumb: `${u}@400w.webp`, page: it.id ? `https://www.bilibili.com/read/cv${it.id}` : '', title, engine: 'bili' });
      if (out.length >= max) return out;
    }
  }
  return out;
}

export async function biliSearch(query, max = 12, { fetchText = safeFetch } = {}) {
  const api = `https://api.bilibili.com/x/web-interface/search/type?search_type=article&keyword=${encodeURIComponent(query)}`;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const buvid = await biliBuvid3(attempt > 0, fetchText);
    const r = await fetchText(api, 1500000, {
      headers: { cookie: `buvid3=${buvid}`, referer: `https://search.bilibili.com/article?keyword=${encodeURIComponent(query)}`, origin: 'https://search.bilibili.com', accept: 'application/json, text/plain, */*' }
    });
    if (r.statusCode >= 200 && r.statusCode < 300 && String(r.body).trimStart().startsWith('{')) {
      const items = parseBiliArticles(r.body, max);
      if (items.length || JSON.parse(r.body)?.code === 0) return items;
    }
    if (attempt === 0) await new Promise((res) => setTimeout(res, 800));
  }
  throw new Error('B站搜索被风控拦了，稍后再试');
}

/**
 * 相关性过滤：有的站（发表情、堆糖）搜不到时会塞“最新/热门”凑数。
 * 标题里一个查询词片段（中文按两字切）都不含的结果丢掉。
 */
export function relevantOnly(items, query) {
  const words = cleanText(query, 80).toLowerCase().split(' ').filter(Boolean)
    .filter((w) => !/^(表情包?|梗图?|图片?|搞笑|沙雕|gif|动图)$/.test(w));
  const pieces = new Set();
  for (const w of words) {
    if (/^[\x00-\x7f]+$/.test(w)) { if (w.length >= 2) pieces.add(w); continue; }
    const chars = Array.from(w);
    if (chars.length === 1) pieces.add(w);
    for (let i = 0; i + 1 < chars.length; i += 1) pieces.add(chars[i] + chars[i + 1]);
  }
  if (!pieces.size) return items;
  return items.filter((it) => { const t = String(it.title || '').toLowerCase(); for (const p of pieces) if (t.includes(p)) return true; return false; });
}

const SITE_LABEL = {
  memecub: 'memecub 梗图社区', fabiaoqing: '发表情', duitang: '堆糖', bili: 'B站专栏',
  'tieba.baidu.com': '贴吧', 'bilibili.com': 'B站', 'weibo.com': '微博', 'weibo.cn': '微博', 'zhihu.com': '知乎',
  'xiaohongshu.com': '小红书', 'douyin.com': '抖音', 'nga.cn': 'NGA', 'hupu.com': '虎扑', 'douban.com': '豆瓣'
};
/** 给 AI 看的来源名：社区结果按页面域名归到「贴吧 / B站 …」。 */
export function sourceLabel(cand) {
  if (SITE_LABEL[cand.engine]) return SITE_LABEL[cand.engine];
  const host = hostOf(cand.page || cand.url || '');
  for (const [domain, label] of Object.entries(SITE_LABEL)) if (host === domain || host.endsWith('.' + domain)) return label;
  return host;
}

export const SOURCE_GROUPS = {
  meme: ['memecub', 'fabiaoqing'],
  community: ['bili', 'duitang'],
  web: ['bing-global', 'bing'],
};
// 国内网络实测（2026-10）：memecub / 发表情 / 堆糖 / B站专栏（带游客 buvid3）/ Bing 可直接搜；
// 搜狗、百度图片接口返回 forbid；贴吧搜索 403；微博、小红书、知乎要登录或签名；
// Bing 图片的 site: 过滤实测不生效（照样出图库站），所以没用它冒充“社区”。
export const DEFAULT_ENGINES = SOURCE_GROUPS.web;

const ENGINE_FNS = {
  bing: bingImageSearch,
  'bing-global': (q, m) => bingImageSearch(q, m, 'www.bing.com'),
  sogou: sogouImageSearch,
  memecub: memecubSearch,
  fabiaoqing: async (q, m) => relevantOnly(await fabiaoqingSearch(q, m), q),
  duitang: async (q, m) => relevantOnly(await duitangSearch(q, m), q),
  bili: biliSearch,
};

const searchCache = new Map(); // `${source}|${query}` -> { at, items }

/** 轮流从各引擎结果里取一张（a1 b1 c1 a2 b2 …），去重。 */
export function interleave(lists, max) {
  const out = [];
  const seen = new Set();
  const longest = Math.max(0, ...lists.map((l) => l.length));
  for (let i = 0; i < longest && out.length < max; i += 1) {
    for (const l of lists) {
      const it = l[i];
      if (!it) continue;
      const k = it.url || it.thumb;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(it);
      if (out.length >= max) break;
    }
  }
  return out;
}

/**
 * 搜图。source：auto（梗图站 + 社区，都没有再全网）/ meme / community / web。
 * 同组引擎并行，任一成功即可；全部失败才报错。
 */
export async function searchImages(query, { max = 12, source = 'auto', engines, webEngines, ttlMs = 10 * 60 * 1000, impl } = {}) {
  const q = cleanText(query, 80);
  if (!q) throw new Error('搜索词不能为空');
  const src = ['auto', 'meme', 'community', 'web'].includes(source) ? source : 'auto';
  const fns = { ...ENGINE_FNS, ...(impl ?? {}) };
  const web = Array.isArray(webEngines) && webEngines.length ? webEngines : SOURCE_GROUPS.web;
  // engines 显式传入时（旧调用方/测试）按顺序逐个试；否则按 source 分组
  const plan = Array.isArray(engines) && engines.length
    ? [{ names: engines, sequential: true }]
    : src === 'auto' ? [{ names: [...SOURCE_GROUPS.meme, ...SOURCE_GROUPS.community] }, { names: web, sequential: true }]
      : src === 'web' ? [{ names: web, sequential: true }]
        : [{ names: SOURCE_GROUPS[src] }];
  const cacheKey = `${src}|${(engines || []).join(',')}|${q}`;
  const hit = searchCache.get(cacheKey);
  if (hit && Date.now() - hit.at < ttlMs) return hit.items.slice(0, max);
  const errors = [];
  for (const step of plan) {
    let lists = [];
    if (step.sequential) {
      for (const name of step.names) {
        const fn = fns[name];
        if (!fn) continue;
        try {
          const items = await fn(q, Math.max(max, 12));
          if (items.length) { lists = [items]; break; }
          errors.push(`${name}: 没有结果`);
        } catch (error) { errors.push(`${name}: ${error?.message ?? error}`); }
      }
    } else {
      const settled = await Promise.allSettled(step.names.map((name) => (fns[name] ? fns[name](q, Math.max(max, 12)) : Promise.resolve([]))));
      settled.forEach((r, i) => {
        if (r.status === 'fulfilled' && r.value.length) lists.push(r.value);
        else errors.push(`${step.names[i]}: ${r.status === 'rejected' ? (r.reason?.message ?? r.reason) : '没有结果'}`);
      });
    }
    const items = interleave(lists, Math.max(max, 24));
    if (items.length) {
      searchCache.set(cacheKey, { at: Date.now(), items });
      if (searchCache.size > 200) searchCache.delete(searchCache.keys().next().value);
      return items.slice(0, max);
    }
  }
  throw new Error(`搜图失败（${errors.join('；') || '没有可用引擎'}）`);
}

export function clearImageSearchCache() { searchCache.clear(); }

/**
 * 下载一张候选图。preferThumb=true 先拿缩略图（给 AI 看，快且稳），否则先拿原图（存图/发图）。
 * 原图常有防盗链/失效，失败时自动退回另一路。返回 { buffer, mimeType, width, height, from }。
 */
export async function fetchCandidate(cand, { preferThumb = false, maxBytes = 5 * 1024 * 1024, maxPixels = 40e6, fetchBuffer = safeFetchBuffer, fetchText = safeFetch } = {}) {
  // memecub 搜索页只有缩略图；要原图（动图）时去详情页取一次并记在候选上
  if (!preferThumb && !cand.url && cand.engine === 'memecub' && cand.detail && cand.memeId) {
    try {
      const page = await fetchText(cand.detail, 800000);
      cand.url = memecubOriginal(page?.body, cand.memeId);
    } catch {}
  }
  const order = preferThumb ? [['thumb', cand.thumb], ['url', cand.url]] : [['url', cand.url], ['thumb', cand.thumb]];
  const errors = [];
  for (const [from, u] of order) {
    if (!u) continue;
    try {
      const got = await fetchBuffer(u, maxBytes);
      const buffer = got.buffer;
      if (!looksLikeImageBuffer(buffer)) throw new Error('不是有效图片');
      const dims = imageSize(buffer);
      if (dims && dims.width * dims.height > maxPixels) throw new Error(`像素超限 ${dims.width}x${dims.height}`);
      return { buffer, mimeType: mimeOf(buffer), width: dims?.width ?? null, height: dims?.height ?? null, from };
    } catch (error) {
      errors.push(`${from}: ${error?.message ?? error}`);
    }
  }
  throw new Error(`图片下载失败（${errors.join('；') || '没有地址'}）`);
}

// ── 会话候选缓存（r1、r2…） ───────────────────────────────────────────────
export function createCandidateStore({ ttlMs = 30 * 60 * 1000, perKey = 24 } = {}) {
  const byKey = new Map(); // key -> { seq, items: Map(id -> cand) }
  function bucket(key) {
    let b = byKey.get(key);
    if (!b) { b = { seq: 0, items: new Map() }; byKey.set(key, b); }
    const now = Date.now();
    for (const [id, c] of b.items) if (now - c.at > ttlMs) b.items.delete(id);
    return b;
  }
  return {
    add(key, cand) {
      const b = bucket(key);
      b.seq += 1;
      const id = `r${b.seq}`;
      b.items.set(id, { ...cand, id, at: Date.now() });
      while (b.items.size > perKey) b.items.delete(b.items.keys().next().value);
      return id;
    },
    get(key, id) {
      const b = bucket(key);
      return b.items.get(String(id ?? '').trim().toLowerCase()) ?? null;
    },
    isCandidateRef(ref) { return /^r\d{1,4}$/i.test(String(ref ?? '').trim()); },
    clear() { byKey.clear(); }
  };
}

// ── 本地图库 ──────────────────────────────────────────────────────────────
export function createImageLibrary({ dir, indexFile, maxItems = 500, maxTotalBytes = 300 * 1024 * 1024, log = () => {} }) {
  let items = [];
  try {
    const raw = JSON.parse(fs.readFileSync(indexFile, 'utf8'));
    items = (Array.isArray(raw?.items) ? raw.items : []).filter((e) => e && /^img_[a-z0-9]{6,}$/.test(e.id) && e.file);
  } catch (error) {
    if (error?.code !== 'ENOENT') log(`[image-lib] 索引读取失败，按空库处理：${error?.message ?? error}`);
  }

  function persist() {
    fs.mkdirSync(path.dirname(indexFile), { recursive: true });
    const tmp = `${indexFile}.tmp-${process.pid}`;
    fs.writeFileSync(tmp, JSON.stringify({ version: 1, items }, null, 2));
    fs.renameSync(tmp, indexFile);
  }

  function fileOf(entry) {
    // 只用桥接生成的文件名，防路径穿越
    const name = path.basename(String(entry.file));
    if (!/^img_[a-z0-9]+\.(png|jpg|gif|webp)$/.test(name)) throw new Error('图库文件名异常');
    return path.join(dir, name);
  }

  function publicView(e) {
    return {
      id: e.id,
      name: e.name,
      tags: e.tags,
      size: e.width && e.height ? `${e.width}x${e.height}` : '',
      kb: Math.round((e.bytes || 0) / 1024),
      source: e.source?.kind === 'search' ? `搜图「${e.source.query}」· ${hostOf(e.source.url || e.source.page || '')}` : e.source?.kind === 'chat' ? '聊天里存的' : '',
      usedCount: e.usedCount || 0,
      savedAt: e.savedAt
    };
  }

  function find(ref) {
    const r = cleanText(ref, 60);
    if (!r) return null;
    return items.find((e) => e.id === r) || items.find((e) => e.name === r) || items.find((e) => e.name && e.name.includes(r)) || null;
  }

  function search(query, count = 20) {
    const q = cleanText(query, 40).toLowerCase();
    const words = q ? q.split(' ').filter(Boolean) : [];
    const scored = items.map((e) => {
      const hay = `${e.name} ${(e.tags || []).join(' ')} ${e.source?.query || ''}`.toLowerCase();
      const score = words.length ? words.reduce((s, w) => s + (hay.includes(w) ? 1 : 0), 0) : 1;
      return { e, score };
    }).filter((x) => x.score > 0);
    scored.sort((a, b) => b.score - a.score || (b.e.usedCount || 0) - (a.e.usedCount || 0) || String(b.e.savedAt).localeCompare(String(a.e.savedAt)));
    return { total: items.length, matched: scored.length, items: scored.slice(0, Math.max(1, Math.min(50, count))).map((x) => publicView(x.e)) };
  }

  function add(buffer, { name, tags, source }) {
    if (!looksLikeImageBuffer(buffer)) throw new Error('不是有效图片，未保存');
    const md5 = crypto.createHash('md5').update(buffer).digest('hex');
    const cleanName = cleanText(name, 30) || '未命名';
    const cleanTags = (Array.isArray(tags) ? tags : String(tags ?? '').split(/[,，、\s]+/))
      .map((t) => cleanText(t, 12)).filter(Boolean).slice(0, 8);
    const dup = items.find((e) => e.md5 === md5);
    if (dup) {
      dup.name = cleanName || dup.name;
      dup.tags = [...new Set([...(dup.tags || []), ...cleanTags])].slice(0, 8);
      persist();
      return { entry: publicView(dup), duplicate: true };
    }
    if (items.length >= maxItems) throw new Error(`图库已满（${maxItems} 张），请管理员清理 state/image-lib 后再存`);
    const total = items.reduce((s, e) => s + (e.bytes || 0), 0);
    if (total + buffer.length > maxTotalBytes) throw new Error('图库容量已满，请管理员清理 state/image-lib 后再存');
    const mimeType = mimeOf(buffer);
    const id = `img_${crypto.randomBytes(5).toString('hex')}`;
    const file = `${id}.${EXT[mimeType] || 'jpg'}`;
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, file), buffer);
    const dims = imageSize(buffer);
    const entry = {
      id, name: cleanName, tags: cleanTags, file, mimeType, md5, bytes: buffer.length,
      width: dims?.width ?? null, height: dims?.height ?? null,
      source: source ?? null, savedAt: new Date().toISOString(), usedCount: 0, lastUsedAt: null
    };
    items.push(entry);
    persist();
    return { entry: publicView(entry), duplicate: false };
  }

  function read(ref) {
    const e = find(ref);
    if (!e) return null;
    const buffer = fs.readFileSync(fileOf(e));
    return { entry: e, view: publicView(e), buffer, mimeType: e.mimeType || mimeOf(buffer) };
  }

  function markUsed(id) {
    const e = items.find((x) => x.id === id);
    if (!e) return;
    e.usedCount = (e.usedCount || 0) + 1;
    e.lastUsedAt = new Date().toISOString();
    try { persist(); } catch (error) { log(`[image-lib] 使用次数写入失败：${error?.message ?? error}`); }
  }

  function remove(ref) {
    const e = find(ref);
    if (!e) return null;
    items = items.filter((x) => x.id !== e.id);
    try { fs.unlinkSync(fileOf(e)); } catch {}
    persist();
    return publicView(e);
  }

  return { add, find, read, search, markUsed, remove, count: () => items.length, publicView };
}
