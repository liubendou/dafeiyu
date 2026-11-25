@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Stopping old guard windows...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -like '*start.bat*' -and $_.CommandLine -notlike '*restart.bat*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Stopping old bridge processes...
rem 桥接不一定跑在 node.exe 上（当前是用 DSH Desktop.exe + ELECTRON_RUN_AS_NODE 起的），
rem 所以这里按**命令行**匹配 bridge.js，而不是按进程名；cmd 包装进程也一起收掉，
rem 否则会起双实例互相抢消息、互相覆盖 state（见本文件上方的历史事故说明）。
powershell -NoProfile -Command "$lock = Join-Path '%~dp0' 'state\bridge.lock'; $pids = @(); if (Test-Path $lock) { $raw = (Get-Content $lock -Raw).Trim(); if ($raw -match '^\d+$') { $pids += [int]$raw } }; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bridge.js*' -and $_.CommandLine -notlike '*mcp-*' -and $_.CommandLine -notlike '*node_modules*' -and $_.CommandLine -notlike '*restart.bat*' } | ForEach-Object { $pids += $_.ProcessId }; $pids | Sort-Object -Unique | ForEach-Object { Write-Host ('  kill PID ' + $_); Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
rem 等旧实例真的退出（并放开控制台端口）再删锁；否则「锁删了但进程还活着」就是双实例。
timeout /t 3 /nobreak >nul
powershell -NoProfile -Command "$live = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bridge.js*' -and $_.CommandLine -notlike '*mcp-*' -and $_.CommandLine -notlike '*node_modules*' -and $_.CommandLine -notlike '*restart.bat*' }; if ($live) { Write-Host 'still alive:'; $live | ForEach-Object { Write-Host ('  PID ' + $_.ProcessId + '  ' + $_.CommandLine) }; exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo.
    echo restart aborted: avoid two instances fighting over messages and state.
    echo Please end the PIDs listed above and retry.
    pause
    exit /b 1
)
del state\bridge.lock 2>nul
start "" cmd /c start.bat
echo Guard window started.
