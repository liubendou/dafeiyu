# AstrBot production watchdog for the B-mode cutover.
# - NapCat WebUI 6099: start the NapCat launcher if it is down.
# - AstrBot WebUI 6185: start AstrBot through start-astrbot-background.ps1.
# - OneBot WS 6199: if 6185 is up but 6199 stays down, restart AstrBot with a cooldown.
# - If 6199 is listening but has no established NapCat connection for 3 consecutive
#   checks, restart NapCat through restart-napcat.ps1 with a 30-minute cooldown.
# - Do not manage qq-bridge/DSH in this mode; the legacy watchdog is kept for rollback.
# The script is ASCII-only on purpose so Windows PowerShell 5.1 reads it correctly.

$ErrorActionPreference = 'Continue'
$ops = 'C:\Users\<user>\ops-dfy'
$log = Join-Path $ops 'watchdog-astrbot.log'
$lockFile = Join-Path $ops 'watchdog-astrbot.lock'
$lastRun = Join-Path $ops 'watchdog-astrbot.last'
$startScript = Join-Path $ops 'start-astrbot-background.ps1'
$restartMarker = Join-Path $ops 'astrbot-last-restart.txt'
$restartNapcat = Join-Path $ops 'restart-napcat.ps1'
$napcatRestartMarker = Join-Path $ops 'napcat-last-restart.txt'
$wsMissFile = Join-Path $ops 'watchdog-ws-miss.txt'
$memMarker = Join-Path $ops 'memory-last-backup.txt'
$astrbotRoot = 'C:\Users\<user>\astrbot'
$qq = '<BOT_QQ>'

# Build the Chinese project directory without putting non-ASCII bytes in this
# script; Windows PowerShell 5.1 may otherwise read the file as ANSI.
$dfyName = [string]::Concat([char]0x5927, [char]0x80A5, [char]0x9C7C)
$dfyRoot = Join-Path 'C:\Users\<user>' $dfyName
$napcatDir = Join-Path $dfyRoot 'napcat'
$launcher = Join-Path $napcatDir 'launcher-user.bat'
$bridgeDir = Join-Path $dfyRoot 'qq-bridge'

function Log([string]$m) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m"
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function PortUp([int]$p) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect('127.0.0.1', $p, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(1200)) { return $false }
        $c.EndConnect($iar)
        return $true
    } catch { return $false } finally { try { $c.Close() } catch {} }
}

function Established6199() {
    @(Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -eq 6199 }).Count
}

function StopAstrBot() {
    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'astrbot\.exe"?\s+run\s+--port\s+6185' })
    foreach ($p in ($procs | Sort-Object ProcessId -Descending)) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
}

function StartAstrBotHelper() {
    if (-not (Test-Path $startScript)) {
        Log 'start helper is missing'
        return
    }
    $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript 2>&1
    foreach ($line in $out) { Log ('start: ' + [string]$line) }
}

# Keep a bounded log.
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 2MB)) {
    Move-Item -Force $log ($log + '.1')
}

# Only one watchdog instance may run. The open handle is held for the whole run.
$lockExisted = Test-Path $lastRun
$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($lockFile, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    Log 'another watchdog instance is running, skip'
    exit 0
}

try {
    if ($lockExisted -and (((Get-Date) - (Get-Item $lastRun).LastWriteTime).TotalSeconds -lt 90)) {
        Log 'watchdog ran too recently, skip'
        exit 0
    }
    Set-Content -Path $lastRun -Value (Get-Date -Format 'o') -Encoding ASCII

    Log 'check start'
    $napcatOk = PortUp 6099
    $webOk = PortUp 6185
    $wsOk = PortUp 6199
    $est = Established6199
    Log ("before: napcat6099=$napcatOk astrbot6185=$webOk ws6199=$wsOk established=$est")

    # 1) NapCat process / WebUI.
    if (-not $napcatOk) {
        Log 'NapCat 6099 is down -> start launcher-user.bat'
        if (Test-Path $launcher) {
            Start-Process -FilePath 'cmd.exe' `
                -ArgumentList '/c', ('"' + $launcher + '" -q ' + $qq) `
                -WorkingDirectory $napcatDir -WindowStyle Minimized
            Start-Sleep -Seconds 45
            Log ("NapCat after start: 6099=" + (PortUp 6099))
        } else {
            Log 'launcher-user.bat not found'
        }
    }

    # 2) AstrBot WebUI / process.
    if (-not (PortUp 6185)) {
        Log 'AstrBot 6185 is down -> start helper'
        StartAstrBotHelper
        Start-Sleep -Seconds 5
    }

    # 3) OneBot WS. Give a fresh process time, then restart with a 30-minute cooldown.
    if ((PortUp 6185) -and -not (PortUp 6199)) {
        Log 'AstrBot 6199 is down; waiting 20 seconds before deciding'
        Start-Sleep -Seconds 20
        if (-not (PortUp 6199)) {
            $canRestart = $true
            if (Test-Path $restartMarker) {
                $age = ((Get-Date) - (Get-Item $restartMarker).LastWriteTime).TotalMinutes
                if ($age -lt 30) {
                    $canRestart = $false
                    Log ("AstrBot restart cooldown active ({0:N1} min)" -f $age)
                }
            }
            if ($canRestart) {
                Log 'restarting AstrBot because 6199 is down'
                StopAstrBot
                StartAstrBotHelper
                Set-Content -Path $restartMarker -Value (Get-Date -Format 'o') -Encoding ASCII
                for ($i = 0; $i -lt 30; $i++) {
                    Start-Sleep -Seconds 1
                    if (PortUp 6199) { break }
                }
                Log ("after restart: 6185=" + (PortUp 6185) + " 6199=" + (PortUp 6199))
            }
        }
    }

    $est = Established6199
    if ((PortUp 6199) -and ($est -eq 0)) {
        $miss = 0
        if (Test-Path $wsMissFile) {
            $raw = Get-Content -Path $wsMissFile -Raw -ErrorAction SilentlyContinue
            if ([string]$raw -match '^\s*\d+\s*$') { $miss = [int]$raw }
        }
        $miss = $miss + 1
        Set-Content -Path $wsMissFile -Value $miss -Encoding ASCII
        Log ("WARN: 6199 is listening but there is no established NapCat connection (miss=$miss)")

        if ($miss -ge 3) {
            $canRestartNapcat = $true
            if (Test-Path $napcatRestartMarker) {
                $age = ((Get-Date) - (Get-Item $napcatRestartMarker).LastWriteTime).TotalMinutes
                if ($age -lt 30) {
                    $canRestartNapcat = $false
                    Log ("NapCat restart cooldown active ({0:N1} min)" -f $age)
                }
            }
            if ($canRestartNapcat) {
                if (Test-Path $restartNapcat) {
                    Log 'restarting NapCat because 6199 has no established connection'
                    $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $restartNapcat -WaitSeconds 90 2>&1
                    foreach ($line in $out) { Log ('napcat-restart: ' + [string]$line) }
                    Set-Content -Path $napcatRestartMarker -Value (Get-Date -Format 'o') -Encoding ASCII
                    Set-Content -Path $wsMissFile -Value 0 -Encoding ASCII
                    Start-Sleep -Seconds 5
                    Log ("after NapCat restart: 6099=" + (PortUp 6099) + " established=" + (Established6199))
                } else {
                    Log 'restart-napcat.ps1 is missing; cannot recover NapCat'
                }
            }
        }
    } else {
        if (Test-Path $wsMissFile) { Set-Content -Path $wsMissFile -Value 0 -Encoding ASCII }
        Log ("OneBot WS established=$est")
    }

    # qq-bridge is intentionally not restarted in B mode. It is kept only for rollback.
    if (PortUp 3100) {
        Log 'note: qq-bridge 3100 is running; not managed in AstrBot mode'
    }

    # 4) Memory maintenance for the old long-memory DB, at most every 10 minutes.
    $doMem = $true
    if (Test-Path $memMarker) {
        if (((Get-Date) - (Get-Item $memMarker).LastWriteTime).TotalMinutes -lt 9) {
            $doMem = $false
        }
    }
    if ($doMem) {
        $memScript = Join-Path $ops 'mem_backup.js'
        $nodeCmd = Join-Path $env:APPDATA 'dsh-desktop\harness\.desktop-bin\node.cmd'
        if ((Test-Path $memScript) -and (Test-Path $nodeCmd)) {
            try {
                $out = & cmd.exe /c ('"' + $nodeCmd + '" "' + $memScript + '"') 2>&1
                Log ('memory: ' + (($out | Out-String).Trim()))
                Set-Content -Path $memMarker -Value (Get-Date -Format 'o') -Encoding ASCII
            } catch {
                Log ('memory maintenance failed: ' + $_.Exception.Message)
            }
        }
    }

    Log 'check done'
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
