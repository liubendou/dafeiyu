# Start AstrBot in the background with UTF-8 logging.
# Used by the AstrBot watchdog and by the optional logon task.
# Safe to call when AstrBot is already listening: it exits without starting a second copy.

$ErrorActionPreference = 'Continue'
$root = 'C:\Users\<user>\astrbot'
$logDir = Join-Path $root 'logs'
$out = Join-Path $logDir 'astrbot.out.log'
$err = Join-Path $logDir 'astrbot.err.log'
$pidFile = Join-Path $root 'astrbot.pid'
$startLock = Join-Path $root 'astrbot.start.lock'

function PortUp([int]$p) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect('127.0.0.1', $p, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(1200)) { return $false }
        $c.EndConnect($iar)
        return $true
    } catch { return $false } finally { try { $c.Close() } catch {} }
}

if (PortUp 6185) {
    Write-Output 'ALREADY_RUNNING'
    exit 0
}

# Serialize starts. The open handle is held until this script exits, so two
# watchdog/logon runs cannot launch AstrBot at the same time.
$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($startLock, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    Write-Output 'START_LOCKED'
    exit 0
}

try {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Set-Location $root
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    if (-not $env:DEEPSEEK_API_KEY) {
        $userKey = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
        if ($userKey) { $env:DEEPSEEK_API_KEY = $userKey }
    }

    # Keep one previous log generation so a crash is not silently overwritten.
    if (Test-Path $out) { Move-Item -Force $out ($out + '.1') }
    if (Test-Path $err) { Move-Item -Force $err ($err + '.1') }

    $exe = Join-Path $root '.venv\Scripts\astrbot.exe'
    if (-not (Test-Path $exe)) {
        Write-Output 'ASTRBOT_EXE_MISSING'
        exit 2
    }

    $p = Start-Process -FilePath $exe -ArgumentList @('run', '--port', '6185') `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru

    $p.Id | Out-File -FilePath $pidFile -Encoding ASCII
    Write-Output ("STARTED_PID=" + $p.Id)

    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        if (PortUp 6185) {
            Write-Output 'PORT_6185_UP'
            exit 0
        }
    }
    Write-Output 'PORT_6185_NOT_UP'
    exit 3
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
