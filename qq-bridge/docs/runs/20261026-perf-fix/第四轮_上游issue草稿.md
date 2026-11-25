# Issue 草稿（未提交：机器上没有 GitHub 凭证，需要你贴到 github.com/deepseek-ai/deepseek-harness/issues）

**Title**: `dsh-fs-local`: `mode: Number(info.mode & 511n)` throws when the path is inside an asar (skill provider silently skipped)

**Body**:

`@deepseek-ai/dsh-fs-local/lib/index.js` computes the file mode with a BigInt literal:

- L239 / L254: `stat(path, { bigint: true })` / `lstat(path, { bigint: true })`
- **L243 / L258: `mode: Number(info.mode & 511n)`**
- L270: the thrown error is wrapped in `FsError`

When the path resolves inside an Electron **asar** archive, the `fs` shim returns a plain
`Stats` object whose `mode` is a regular number. `number & 511n` then throws
`TypeError: Cannot mix BigInt and other types`, and the wrapper turns it into an
`FsError`, so the whole skill provider is skipped.

Observed in DSH Desktop 0.12.1 (harness 0.2.1-rc.2, app.asar):
`warn skill-registry: skill provider "filesystem" skipped: FsError: cannot list "C:\Users\...\resources\app.asar\..."`
repeated 10991 times in one day, and the user's own skills never load.

I checked the published `0.2.1-rc.3` tarball (2026-10-23): that file is byte-identical to
the shipped one (41311 bytes), so the bug is still present upstream.

Suggested fix — only use the BigInt mask when the value really is a BigInt, e.g.:

```js
const rawMode = info.mode;
const mode = typeof rawMode === 'bigint' ? Number(rawMode & 511n) : rawMode & 511;
```

(Or normalize `info.mode` to BigInt before masking.) A one-line guard would also make the
asar case work instead of failing the whole provider.
