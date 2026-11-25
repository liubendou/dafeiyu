const fs = require('fs');
const path = require('path');
const os = require('os');
const ROOT = path.join('C:/Users/<user>', '\u5927\u80a5\u9c7c', 'qq-bridge');
const st = path.join(ROOT, 'state');
const db = path.join(st, 'long-memory.db');
const bakDir = 'C:\\Users\\Administrator\\ops-dfy\\memory-backups';
try { fs.mkdirSync(bakDir, { recursive: true }); } catch {}
function stamp() { const d = new Date(); const p = (n) => String(n).padStart(2, '0'); return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes()); }
function day() { const d = new Date(); const p = (n) => String(n).padStart(2, '0'); return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()); }
let ck = 'skip';
try {
  const { DatabaseSync } = require('node:sqlite');
  const d = new DatabaseSync(db);
  const r = d.prepare('PRAGMA wal_checkpoint(TRUNCATE)').get();
  d.close();
  ck = 'checkpointed=' + (r && r.checkpointed);
} catch (e) { ck = 'ckpt-failed:' + e.message; }
const dest = path.join(bakDir, 'long-memory-' + stamp() + '.db');
try {
  for (const suffix of ['', '-wal', '-shm']) {
    const src = db + suffix;
    if (fs.existsSync(src)) fs.copyFileSync(src, dest + suffix);
  }
  // keep the last 7 daily sets
  const files = fs.readdirSync(bakDir).filter((f) => f.endsWith('.db')).sort();
  while (files.length > 14) { try { fs.unlinkSync(path.join(bakDir, files.shift())); } catch {} }
  console.log('mem-backup ok ' + ck + ' -> ' + path.basename(dest));
} catch (e) { console.log('mem-backup failed: ' + e.message); }
