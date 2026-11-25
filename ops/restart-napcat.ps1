# Restart NapCat/QQ for the AstrBot production cutover.
# ASCII-only on purpose so Windows PowerShell 5.1 reads it correctly.
param(
    [int]$WaitSeconds = 90
)

$ErrorActionPreference = 'Continue'
$ops = 'C:\Users\<user>\ops-dfy'
$log = Join-Path $ops 'restart-napcat.log'
$qq = '<BOT_QQ>'
$dfyName = [string]::Concat([char]0x5927, [char]0x80A5, [char]0x9C7C)
$dfyRoot = Join-Path 'C:\Users\<user>' $dfyName
$napcatDir = Join-Path $dfyRoot 'napcat'
$launcher = Join-Path $napcatDir 'launcher-user.bat'
$killer = Join-Path $napcatDir 'KillQQ.bat'

function Log([string]$m) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m"
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Output $line
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

if (-not (Test-Path $launcher)) {
    Log 'launcher-user.bat not found'
    exit 1
}

Log 'restart requested'
if (Test-Path $killer) {
    & cmd.exe /c $killer 2>&1 | Out-Null
    Log ("kill exit=" + $LASTEXITCODE)
} else {
    Get-Process -Name QQ -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Log 'kill fallback used'
}
Get-Process -Name NapCatWinBootMain -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# Wait for 6099 to close; the launcher refuses to start while it is still up.
for ($i = 0; $i -lt 20; $i++) {
    if (-not (PortUp 6099)) { break }
    Start-Sleep -Seconds 1
}
if (PortUp 6099) {
    Log '6099 is still listening after kill; aborting restart'
    exit 1
}

$arg = '/c "' + $launcher + '" -q ' + $qq
Start-Process -FilePath 'cmd.exe' -ArgumentList $arg -WorkingDirectory $napcatDir -WindowStyle Minimized
Log 'launcher started'

# Wait for the WebUI and the OneBot WS client to both come back.
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$lastLog = Get-Date
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $up = PortUp 6099
    $est = Established6199
    if ($up -and ($est -gt 0)) {
        Log ("restart ok: 6099=True established=$est")
        exit 0
    }
    if (((Get-Date) - $lastLog).TotalSeconds -ge 15) {
        Log ("waiting: 6099=$up established=$est")
        $lastLog = Get-Date
    }
}

Log ("restart incomplete: 6099=" + (PortUp 6099) + " established=" + (Established6199))
exit 1
