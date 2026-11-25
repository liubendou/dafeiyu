<#
  大肥鱼机器人看门狗（ASCII 路径：C:\Users\<user>\ops-dfy\watchdog.ps1）
  作用：
    1) NapCat/QQ 掉线（OneBot WS 3001 不通）→ 用项目自带 launcher 拉起
    2) 桥接掉线（3100 不通）→ 按同样方式重启桥接
    3) DSH 掉线（43129 不通）→ 启动 DSH，并检测它这次用了哪个端口；
       端口变了就改 config.json 的 dsh.baseUrl 再重启桥接（否则机器人会哑）
  由计划任务每 2 分钟跑一次；开机登录时也跑一次。
#>
$ErrorActionPreference = 'Continue'
$log = 'C:\Users\<user>\ops-dfy\watchdog.log'
$root = 'C:\Users\<user>\大肥鱼'
$lockFile = 'C:\Users\<user>\ops-dfy\watchdog.lock'
$qq = '<BOT_QQ>'

function Log([string]$m) {
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m"
  Add-Content -Path $log -Value $line -Encoding UTF8
}

# 同一时刻只允许一个看门狗实例（避免计划任务重叠导致重复拉起）
try {
  $fs = [System.IO.File]::Open($lockFile, 'OpenOrCreate', 'ReadWrite', 'None')
  $fs.Close()
} catch {
  Log 'another watchdog instance is running, skip'
  exit 0
}
$age = ((Get-Date) - (Get-Item $lockFile).LastWriteTime).TotalMinutes
if ($age -lt 1.2) { Log 'watchdog ran too recently, skip'; exit 0 }
Set-Content -Path $lockFile -Value (Get-Date -Format 'o') -Encoding ASCII

function PortUp([int]$p) {
  $c = New-Object System.Net.Sockets.TcpClient
  try {
    $iar = $c.BeginConnect('127.0.0.1', $p, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(1500)) { return $false }
    $c.EndConnect($iar)
    return $true
  } catch { return $false } finally { try { $c.Close() } catch {} }
}

function DshProcessIds() {
  @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'DSH Desktop.exe' } | ForEach-Object { $_.ProcessId })
}

# 1) NapCat / QQ
if (-not (PortUp 3001)) {
  Log 'NapCat WS 3001 is down -> relaunch QQ (NapCat)'
  $bat = Join-Path $root 'napcat\launcher-user.bat'
  if (Test-Path $bat) {
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', ('"' + $bat + '" -q ' + $qq) -WorkingDirectory (Join-Path $root 'napcat') -WindowStyle Minimized
    Start-Sleep -Seconds 45
    Log ("NapCat after relaunch: 3001=" + (PortUp 3001))
  } else { Log 'launcher-user.bat not found' }
}

# 2) DSH
#   端口以 config.json 里记录的为准；DSH Desktop 自己另有一个端口（桌面端端口），
#   API 端口只认 harness 进程（harness-node-entry）监听的那个，别认错。
$cfgPath = Join-Path $root 'qq-bridge\config.json'
$dshPort = 43129
try {
  $cfg0 = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $m = [regex]::Match([string]$cfg0.dsh.baseUrl, ':(\d+)/?$')
  if ($m.Success) { $dshPort = [int]$m.Groups[1].Value }
} catch {}
function HarnessPort() {
  $hp = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*harness-node-entry*' })
  foreach ($p in $hp) {
    $conn = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $p.ProcessId })
    if ($conn.Count -gt 0) { return [int]$conn[0].LocalPort }
  }
  return 0
}
$dshPortChanged = $false
if (-not (PortUp $dshPort)) {
  Log ("DSH $dshPort is down -> ensure DSH Desktop is running")
  $exe = 'C:\Users\<user>\AppData\Local\Programs\DSH Desktop\DSH Desktop.exe'
  $running = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'DSH Desktop.exe' }).Count
  if ($running -eq 0 -and (Test-Path $exe)) {
    Start-Process $exe
    Start-Sleep -Seconds 45
  }
  $newPort = HarnessPort
  if ($newPort -eq 0) {
    Log 'could not detect DSH harness port'
  } elseif ($newPort -ne $dshPort) {
    Log ("DSH harness port is $newPort (config says $dshPort) -> update config.json")
    try {
      $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
      $cfg.dsh.baseUrl = 'http://127.0.0.1:' + $newPort
      [System.IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 30), (New-Object System.Text.UTF8Encoding($false)))
      Log 'config.json dsh.baseUrl updated'
      $dshPort = $newPort
      $dshPortChanged = $true
    } catch { Log ('failed to update config.json: ' + $_.Exception.Message) }
  } else { Log ("DSH still on $dshPort") }
}

# 3) bridge
$bridgeDir = Join-Path $root 'qq-bridge'
$nodeCmd = "$env:APPDATA\dsh-desktop\harness\.desktop-bin\node.cmd"
if ($dshPortChanged) {
  Log 'DSH port changed -> restart bridge so it picks up the new baseUrl'
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'src[\\/]bridge\.js' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 3
  Remove-Item (Join-Path $bridgeDir 'state\bridge.lock') -Force -ErrorAction SilentlyContinue
}
if ($dshPortChanged -or -not (PortUp 3100)) {
  Log 'relaunch bridge'
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', ('"' + $nodeCmd + '" src/bridge.js') -WorkingDirectory $bridgeDir -WindowStyle Minimized
  Start-Sleep -Seconds 14
  Log ("bridge after relaunch: 3100=" + (PortUp 3100))
}

# 4) long-term memory: WAL checkpoint + rolling daily backups
$memScript = 'C:\Users\<user>\ops-dfy\mem_backup.js'
$nodeCmdMem = "$env:APPDATA\dsh-desktop\harness\.desktop-bin\node.cmd"
if ((Test-Path $memScript) -and (Test-Path $nodeCmdMem)) {
  try {
    $memOut = & cmd.exe /c ('"' + $nodeCmdMem + '" "' + $memScript + '"') 2>&1
    Log ('memory: ' + (($memOut | Out-String).Trim()))
  } catch { Log ('memory maintenance failed: ' + $_.Exception.Message) }
}

Log 'watchdog check done'
