# -*- coding: utf-8 -*-
"""Build a portable release package for the desktop app (design §8.1、§8.2、§7.4).

    python packaging/build_release.py --version 0.4.0 [--with-runtime] [--out dist]

Produces  dist/dafeiyu-<mode>-<version>.zip  containing the app payload plus
RELEASE.json, SHA256SUMS.txt, BUILD-INFO.txt, install.ps1 and uninstall.ps1.

Light mode (default) is the §8.2 「轻量版」: it takes over the machine that already runs
the bot engine and keeps node_modules outside the package. --with-runtime is the
「完整版」 and zips node_modules in too (slow, big).

Nothing here signs anything — §7.4 wants a code signature and Defender trust; that needs
a real certificate, which this machine doesn't have. The hash manifest is the integrity
piece we *can* do honestly; the gap is called out in BUILD-INFO rather than hidden.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# src/ 必须在包里：package.json 的 main 指的是 src/main/index.mjs，只装 out/ 会是个起不来的窗口
DEFAULT_INCLUDE = ["out", "src", "core", "scripts", "packaging", "docs", "config", "plugin",
                   "package.json", "assets"]
RUNTIME_INCLUDE = ["node_modules"]
EXCLUDE_PARTS = (".runtime", "__pycache__", ".git", ".cache")
# 顶层 dist 是打包产物目录，不能进包；但 node_modules/electron/dist 是 Electron 本体，
# 以前按子串匹配把它整包排掉，导致“完整版”里没有 electron.exe（2026-10-31 发现）。
EXCLUDE_TOP = ("dist",)
SECRETISH = re.compile(r"(?i)(mcp_url|credential|\.key$|\.pem$|token|secret|\.db$|\.db-wal|\.db-shm)")
# 源码后缀不是秘密：SECRETISH 本来只该拦“数据文件”，结果把 core/dfy_core/credentials.py
# 这个模块也拦掉了，打出来的包起不来（2026-10-31 真机冒烟发现）。代码一律放行。
CODE_SUFFIXES = (".py", ".mjs", ".cjs", ".js", ".jsx", ".ts", ".tsx")
# 打包别带上 *.bak/*.orig/~ 这类编辑残留
BACKUPISH = re.compile(r"(?i)(\.bak|\.orig|\.tmp$|~$)")
SUMS = "SHA256SUMS.txt"

RUN_CMD = r"""@echo off
rem Dafeiyu desktop launcher (keep this file ASCII: cmd.exe reads .cmd in the OEM
rem codepage, so UTF-8 Chinese comments would be garbage on a 936 machine).
rem Order: Electron bundled in this package (full), then the machine-level copy at
rem %LOCALAPPDATA%\Dafeiyu\electron (shared by light packages). Never opens a blank window.
setlocal
set "APPDIR=%~dp0"
if exist "%APPDIR%node_modules\.bin\electron.cmd" (
  "%APPDIR%node_modules\.bin\electron.cmd" "%APPDIR%." %*
  exit /b
)
if exist "%LOCALAPPDATA%\Dafeiyu\electron\node_modules\.bin\electron.cmd" (
  "%LOCALAPPDATA%\Dafeiyu\electron\node_modules\.bin\electron.cmd" "%APPDIR%." %*
  exit /b
)
echo Missing Electron runtime. The light package does not ship node_modules:
echo   use the full package dafeiyu-app-<version>-full.zip, or
echo   run "npm install" once inside this folder (about 200MB).
exit /b 3
"""


INSTALL_PS = r'''# 大肥鱼桌面 App 安装脚本（便携版）。默认只装程序，不碰 QQ 与机器人引擎。
param(
  [string]$Root = (Join-Path $env:LOCALAPPDATA "Dafeiyu"),
  [switch]$WithShortcuts,
  [switch]$Autostart,
  [string]$Package = ""
)
$ErrorActionPreference = "Stop"
$zip = if ($Package) { (Resolve-Path $Package).Path } else {
  (Get-ChildItem -Path $PSScriptRoot -Filter "dafeiyu-*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $zip) { throw "旁边没有 dafeiyu-*.zip" }
$tmp = Join-Path $env:TEMP ("dfy-install-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $tmp
$rel = Get-Content (Join-Path $tmp "RELEASE.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = $rel.version
# 校验清单：算一遍哈希，对不上就别装
$bad = 0
foreach ($line in Get-Content (Join-Path $tmp "SHA256SUMS.txt") -Encoding UTF8) {
  if (-not $line.Trim()) { continue }
  $h, $n = $line.Trim().TrimStart('*') -split " ", 2
  $p = Join-Path $tmp ($n -replace "/", "\")
  if (-not (Test-Path $p)) { continue }
  if ($n -eq "SHA256SUMS.txt" -or $n -like "node_modules/*") { continue }
  $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower()
  if ($got -ne $h) { Write-Warning "哈希不一致：$n"; $bad++ }
}
if ($bad -gt 0) { Remove-Item $tmp -Recurse -Force; throw "有 $bad 个文件校验不过，已中止" }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
if (Test-Path (Join-Path $Root "current.txt")) {
  $cur = (Get-Content (Join-Path $Root "current.txt") -Raw).Trim()
  if ($cur -and (Test-Path (Join-Path $Root "app-$cur"))) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    # "--" 分隔：版本号自己可能带横杠（1.0.1-beta.2），别用单横杠拆
    Copy-Item (Join-Path $Root "app-$cur") (Join-Path $Root "rollback\$stamp--$cur") -Recurse -Force
  }
}
$dst = Join-Path $Root "app-$ver"
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Get-ChildItem $tmp | Where-Object { $_.Name -notin @("SHA256SUMS.txt","RELEASE.json","BUILD-INFO.txt","install.ps1","uninstall.ps1") } |
  ForEach-Object { Copy-Item $_.FullName (Join-Path $dst $_.Name) -Recurse -Force }
foreach ($f in @("SHA256SUMS.txt", "RELEASE.json", "BUILD-INFO.txt")) { Copy-Item (Join-Path $tmp $f) (Join-Path $dst $f) -Force }
# 把上一版里用户改过的配置带到新版本目录（core 读的是 <app 目录>\config\desktop.json）
if ($cur -and $cur -ne $ver -and (Test-Path (Join-Path $Root "app-$cur\config\desktop.json"))) {
  New-Item -ItemType Directory -Force -Path (Join-Path $dst "config") | Out-Null
  Copy-Item (Join-Path $Root "app-$cur\config\desktop.json") (Join-Path $dst "config\desktop.json") -Force
  Write-Host "配置已从 app-$cur 带到 app-$ver"
}
Set-Content -Path (Join-Path $Root "current.txt") -Value $ver -Encoding utf8
Remove-Item $tmp -Recurse -Force
if ($WithShortcuts) {
  $sh = New-Object -ComObject WScript.Shell
  $lnk = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "大肥鱼.lnk"))
  # 走 run.cmd：它自己找包里的或本机的 Electron，找不到会明说
  $lnk.TargetPath = (Join-Path $dst "run.cmd")
  $lnk.Arguments = '--user-data-dir="' + (Join-Path $Root "userdata") + '"'
  $lnk.WorkingDirectory = $dst
  $lnk.Save()
}
if ($Autostart) {
  $run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
  Set-ItemProperty -Path $run -Name "DafeiyuDesktop" -Value ('"' + (Join-Path $dst "run.cmd") + '"')
}
Write-Host "已安装 $ver 到 $dst"
'''

UNINSTALL_PS = r'''# 卸载：默认只删程序目录，配置与本地数据保留（§8.4）
param(
  [string]$Root = (Join-Path $env:LOCALAPPDATA "Dafeiyu"),
  [switch]$PurgeData
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Root)) { Write-Host "没有安装目录：$Root"; exit 0 }
$keep = @(
  (Join-Path $Root "config"),
  (Join-Path $Root "userdata"),
  (Join-Path $Root "rollback")
)
if ($PurgeData) {
  Write-Host "彻底删除：$Root（配置与本地数据一起走）" -ForegroundColor Yellow
  $ans = Read-Host "最后一次确认，输入 yes"
  if ($ans -ne "yes") { Write-Host "已取消，什么都没删"; exit 1 }
  Remove-Item $Root -Recurse -Force
  Write-Host "已删除"
  exit 0
}
foreach ($d in Get-ChildItem $Root -Directory -Filter "app-*") { Remove-Item $d.FullName -Recurse -Force }
foreach ($f in @("current.txt", "release.json")) { Remove-Item (Join-Path $Root $f) -Force -ErrorAction SilentlyContinue }
Write-Host "程序目录已删；保留：$($keep -join '、')。要连数据一起删就再加 -PurgeData"
'''


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_files(base, includes):
    out = []
    for rel in includes:
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            out.append(rel.replace("\\", "/"))
            continue
        if not os.path.isdir(p):
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_PARTS and not SECRETISH.search(d)]
            for f in files:
                rp = os.path.relpath(os.path.join(root, f), base).replace("\\", "/")
                parts = rp.split("/")
                if any(x in EXCLUDE_PARTS for x in parts) or parts[0] in EXCLUDE_TOP:
                    continue
                if BACKUPISH.search(f):
                    continue
                if SECRETISH.search(f) and not f.lower().endswith(CODE_SUFFIXES):
                    continue
                out.append(rp)
    return sorted(set(out))


def win_text(text, bom=False):
    """Batch and PowerShell files need Windows line endings before they leave the zip.

    2026-10-31: run.cmd was packaged LF-only, so cmd.exe mis-split the `if exist (...)`
    block and the launcher died with "not recognized as an internal or external command";
    install.ps1/uninstall.ps1 had no BOM, so Windows PowerShell read the Chinese as ANSI.
    """
    t = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return ("\ufeff" + t) if bom else t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="")
    ap.add_argument("--mode", default="light", choices=["light", "full"])
    ap.add_argument("--with-runtime", action="store_true")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--channel", default="portable")
    a = ap.parse_args()
    ver = a.version
    if not ver:
        pkg = os.path.join(ROOT, "package.json")
        if os.path.isfile(pkg):
            ver = str(json.load(open(pkg, encoding="utf-8")).get("version") or "")
    if not re.match(r"^\d+\.\d+\.\d{1,4}(-[a-z0-9.]{1,20})?$", ver or ""):
        sys.stderr.write("版本号不合法（%r），用 --version 0.4.0 这种写法\n" % ver)
        return 2
    mode = "full" if a.with_runtime else a.mode
    includes = DEFAULT_INCLUDE + (RUNTIME_INCLUDE if mode == "full" else [])
    files = walk_files(ROOT, includes)
    missing = [i for i in ("out", "core") if not os.path.isdir(os.path.join(ROOT, i))]
    if missing:
        sys.stderr.write("缺 %s；先跑 vite build（out/）再打包\n" % "、".join(missing))
        return 2
    os.makedirs(os.path.join(ROOT, a.out), exist_ok=True)
    # 命名约定和 core/dfy_core/release.py 一致：dafeiyu-app-<version>-<mode>.zip
    name = "dafeiyu-app-%s-%s.zip" % (ver, mode)
    zpath = os.path.join(ROOT, a.out, name)
    release = {"product": "大肥鱼 desktop", "version": ver, "mode": mode,
               "channel": a.channel, "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "built_by": "packaging/build_release.py", "files": len(files),
               "signature": None,
               "note": "未做代码签名（本机无证书）；完整性靠 SHA256SUMS 清单"}
    runtime_note = ("轻量版不含 node_modules：需要本机已有 Electron（装一次完整版包即可）或在安装目录 npm install。\n"
                    if mode == "light" else "完整版含 node_modules，双击 run.cmd 就能起。\n")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        extra = {"RELEASE.json": json.dumps(release, ensure_ascii=False, indent=1) + "\n",
                 "BUILD-INFO.txt": ("大肥鱼桌面 App %s（%s）\n构建时间：%s\n文件数：%d\n"
                                    "安装：powershell -File install.ps1\n启动：双击 run.cmd（它会自己找 Electron）\n"
                                    "卸载：powershell -File uninstall.ps1（保留数据）\n"
                                    "未签名的原因：本机没有代码证书；§7.4 要求的验签在拿到证书前用哈希清单代替。\n"
                                    % (ver, mode, release["built_at"], len(files))) + runtime_note,
                 "run.cmd": win_text(RUN_CMD), "install.ps1": win_text(INSTALL_PS, bom=True),
                 "uninstall.ps1": win_text(UNINSTALL_PS, bom=True)}
        sums = []
        for rel in files:
            z.write(os.path.join(ROOT, rel), rel)
            sums.append("%s  %s" % (sha256(os.path.join(ROOT, rel)), rel))
        for k, v in extra.items():
            z.writestr(k, v)
            # 元数据也进清单：改一个 RELEASE.json 也要能被 verify 抓到
            sums.append("%s  %s" % (hashlib.sha256(v.encode("utf-8")).hexdigest(), k))
        z.writestr(SUMS, "\n".join(sums) + "\n")
    print(json.dumps({"ok": True, "package": zpath, "bytes": os.path.getsize(zpath),
                      "files": len(files), "version": ver, "mode": mode,
                      "package_sha256": sha256(zpath)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
