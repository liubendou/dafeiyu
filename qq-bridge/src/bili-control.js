// 主人在 QQ 私聊里遥控「刷B站」插件（dsh-bili-scroll）。
//
// 为什么走桥接、而不是把插件自己的 bili_* 工具放进 QQ 白名单：
//   QQ 会话的工具白名单（qq-tool-restrict）是给群友会话的硬边界。把 bili_* 放进去，
//   群里任何人都能让大肥鱼用主人的 B站 号发评论、发私信、重新扫码换号。
//   这里只开一个 qq_bili 工具，桥接在服务端校验「必须是主人私聊 + 会话令牌」，
//   能改的设置也只有几个无害开关（不能改 pythonPath / dshBin / workspace 这类等于执行代码的项）。
//
// 插件的接口在 DSH 自己的 web 服务上（/api/bili-duty/*），桥接用已有的 DSH 鉴权去调。

/** 允许从 QQ 改的设置：类型 + 范围。 */
export const BILI_SETTABLE = Object.freeze({
  autoComment: { type: 'bool', label: '自动评论' },
  commentPerRound: { type: 'int', min: 1, max: 3, label: '每轮评论条数' },
  replyToComments: { type: 'bool', label: '回复别人回它的评论' },
  replyPerRound: { type: 'int', min: 1, max: 3, label: '每轮回复条数' },
  perDay: { type: 'int', min: 0, max: 20, label: '每天最多刷几轮' },
  noon: { type: 'bool', label: '中午时段自动刷' },
  night: { type: 'bool', label: '夜间时段自动刷' },
  transcribe: { type: 'bool', label: '没字幕时语音转文字' },
  dmWatch: { type: 'bool', label: '私信监听' },
  dmReplyPerDay: { type: 'int', min: 0, max: 30, label: '每天最多回几条私信' },
  keywords: { type: 'text', max: 200, label: '偏好关键词' },
});

/**
 * 只留白名单里的字段，类型不对/越界的报出来。
 * @returns {{ patch: object, rejected: string[] }}
 */
export function sanitizeBiliSettings(input) {
  const patch = {};
  const rejected = [];
  if (!input || typeof input !== 'object' || Array.isArray(input)) return { patch, rejected };
  for (const [k, v] of Object.entries(input)) {
    const spec = BILI_SETTABLE[k];
    if (!spec) { rejected.push(`${k}（不能从 QQ 改）`); continue; }
    if (spec.type === 'bool') {
      if (typeof v === 'boolean') patch[k] = v;
      else if (v === 'true' || v === 'false') patch[k] = v === 'true';
      else rejected.push(`${k}（要 true/false）`);
    } else if (spec.type === 'int') {
      const n = Number(v);
      if (!Number.isFinite(n)) { rejected.push(`${k}（要数字）`); continue; }
      patch[k] = Math.max(spec.min, Math.min(spec.max, Math.round(n)));
    } else if (spec.type === 'text') {
      const s = String(v ?? '').replace(/[\u0000-\u001f\u007f\u200b-\u200f\u2028\u2029]/g, ' ').replace(/\s+/g, ' ').trim();
      if (!s) { rejected.push(`${k}（不能为空）`); continue; }
      patch[k] = s.slice(0, spec.max);
    }
  }
  return { patch, rejected };
}

const onOff = (v) => (v ? '开' : '关');

export function summarizeBiliStatus(st) {
  const c = st?.config ?? {};
  const lines = [
    `B站登录：${st?.loggedIn ? '已登录' : '没登录（用 action=login 出二维码）'}`,
    `今天跑了 ${Number(st?.ranToday) || 0} 轮，挑到 ${Number(st?.pickedToday) || 0} 条${st?.lastRunAt ? `；上次 ${String(st.lastRunAt).replace('T', ' ').slice(0, 16)}` : ''}`,
    `自动评论 ${onOff(c.autoComment)}（每轮 ${c.commentPerRound ?? 1} 条）· 回复评论 ${onOff(c.replyToComments)} · 每天最多 ${c.perDay ?? 0} 轮`,
    `定时：中午 ${onOff(c.noon)} · 夜间 ${onOff(c.night)} · 语音转文字 ${onOff(c.transcribe)} · 私信监听 ${onOff(c.dmWatch)}`,
  ];
  if (st?.venvReady === false || (Array.isArray(st?.missingDeps) && st.missingDeps.length)) lines.push('⚠️ 插件的 Python 环境没装好，去 DSH 设置 → 刷B站 点「建 venv 装依赖」');
  return lines.join('\n');
}

/** 日报：取前面一段给模型看，再数一下挑了几条视频。 */
export function summarizeBiliReport(r, max = 1800) {
  if (!r?.exists || !String(r.text || '').trim()) return `${r?.day ?? '今天'} 还没有日报（还没跑过，或者这一轮还没写完）`;
  const text = String(r.text);
  const picks = (text.match(/^###\s*BV[0-9A-Za-z]+/gm) || []).length;
  const body = text.replace(/\r/g, '').replace(/\n{3,}/g, '\n\n').trim();
  return `${r.day} 日报（挑了 ${picks} 条）：\n${body.length > max ? body.slice(0, max) + '\n…（后面省略）' : body}`;
}

export function dataUrlToBuffer(dataUrl) {
  const m = /^data:image\/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)$/.exec(String(dataUrl ?? ''));
  return m ? Buffer.from(m[2], 'base64') : null;
}

/**
 * @param {object} deps
 * @param {(path: string, init?: {method?: string, body?: object}) => Promise<any>} deps.fetchDsh 调 DSH 上的插件接口，返回 JSON
 * @param {(buffer: Buffer) => Promise<any>} deps.sendQr 把二维码图片发到主人私聊
 * @param {() => number} [deps.now]
 */
export function createBiliControl({ fetchDsh, sendQr, now = () => Date.now(), maxWritesPerHour = 20 } = {}) {
  let pendingLogin = null; // { key, at }
  const writes = [];
  const takeWrite = () => {
    const t = now();
    while (writes.length && t - writes[0] > 3600 * 1000) writes.shift();
    if (writes.length >= maxWritesPerHour) return false;
    writes.push(t);
    return true;
  };

  async function handle(action, body = {}) {
    switch (action) {
      case 'status': {
        const st = await fetchDsh('/api/bili-duty/status');
        return { ok: true, text: summarizeBiliStatus(st), loggedIn: !!st?.loggedIn };
      }
      case 'report': {
        const r = await fetchDsh('/api/bili-duty/report');
        return { ok: true, text: summarizeBiliReport(r) };
      }
      case 'login': {
        if (!takeWrite()) return { ok: false, status: 429, error: '操作太频繁了，过一会儿再试' };
        if (body.force !== true) {
          const st = await fetchDsh('/api/bili-duty/status');
          if (st?.loggedIn) return { ok: true, text: 'B站已经登录了，不用再扫。要换号就带 force=true 再调一次。', loggedIn: true };
        }
        const r = await fetchDsh('/api/bili-duty/login/start', { method: 'POST', body: {} });
        const buf = dataUrlToBuffer(r?.qr);
        if (!r?.ok || !r.key || !buf) return { ok: false, status: 502, error: r?.error || '插件没画出二维码（去 DSH 设置 → 刷B站 点「建 venv 装依赖」）' };
        await sendQr(buf);
        pendingLogin = { key: String(r.key), at: now() };
        return { ok: true, text: '登录二维码已经发到主人私聊了，大约 3 分钟内有效。用手机 B站 扫，确认时选「记住我/长期登录」。主人说扫好了，就调 action=login_check。' };
      }
      case 'login_check': {
        if (!pendingLogin) return { ok: false, status: 409, error: '还没出过二维码，先调 action=login' };
        if (now() - pendingLogin.at > 200 * 1000) { pendingLogin = null; return { ok: false, status: 410, error: '二维码已经过期了，重新 action=login 出一张' }; }
        const r = await fetchDsh('/api/bili-duty/login/poll', { method: 'POST', body: { key: pendingLogin.key } });
        const state = String(r?.state ?? 'unknown');
        if (state === 'ok') { pendingLogin = null; return { ok: true, text: 'B站登录成功，凭证存在插件自己的目录里。', loggedIn: true }; }
        if (state === 'expired') { pendingLogin = null; return { ok: true, text: '二维码过期了，要重新 action=login 出一张。' }; }
        const word = { waiting: '还没扫', scanned: '扫了，等手机上点确认' }[state] ?? `状态：${state}${r?.message ? '（' + r.message + '）' : ''}`;
        return { ok: true, text: `${word}。过一会儿再查。` };
      }
      case 'run': {
        if (!takeWrite()) return { ok: false, status: 429, error: '操作太频繁了，过一会儿再试' };
        const r = await fetchDsh('/api/bili-duty/run', { method: 'POST', body: {} });
        if (r?.busy) return { ok: true, text: '上一轮还在跑，等它跑完再说。' };
        if (r?.started === false || r?.ok === false) return { ok: false, status: 502, error: r?.error || '没跑起来' };
        return { ok: true, text: '开始刷一轮了，通常要几分钟。跑完用 action=report 看日报。' };
      }
      case 'set': {
        const { patch, rejected } = sanitizeBiliSettings(body.settings);
        if (!Object.keys(patch).length) return { ok: false, status: 400, error: `没有能改的设置${rejected.length ? '：' + rejected.join('、') : ''}。能改的有：${Object.entries(BILI_SETTABLE).map(([k, s]) => `${k}（${s.label}）`).join('、')}` };
        if (!takeWrite()) return { ok: false, status: 429, error: '操作太频繁了，过一会儿再试' };
        const r = await fetchDsh('/api/bili-duty/config', { method: 'POST', body: patch });
        if (!r?.ok) return { ok: false, status: 502, error: r?.error || '插件没接受这次设置' };
        const done = Object.entries(patch).map(([k, v]) => `${BILI_SETTABLE[k].label}=${typeof v === 'boolean' ? onOff(v) : v}`).join('，');
        return { ok: true, text: `改好了：${done}${rejected.length ? `；没改：${rejected.join('、')}` : ''}` };
      }
      default:
        return { ok: false, status: 400, error: 'action 只能是 status / login / login_check / run / report / set' };
    }
  }

  return { handle, _pending: () => pendingLogin };
}
