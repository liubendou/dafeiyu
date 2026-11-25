# Stage-0 verification: electron binary, typecheck, renderer build. ASCII only.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Output ("cwd=" + (Get-Location).Path)
if (Test-Path node_modules/electron/dist/electron.exe) {
  $v = (Get-Content node_modules/electron/dist/version.txt -ErrorAction SilentlyContinue)
  Write-Output "ELECTRON_DIST_OK version=$v"
} else {
  Write-Output "ELECTRON_DIST_MISSING"
}
Write-Output "--- tsc --noEmit ---"
& node_modules/.bin/tsc.CMD --noEmit 2>&1 | Select-Object -First 40
Write-Output ("TYPECHECK_EXIT=" + $LASTEXITCODE)
Write-Output "--- vite build ---"
& node_modules/.bin/vite.CMD build 2>&1 | Select-Object -Last 20
Write-Output ("BUILD_EXIT=" + $LASTEXITCODE)
if (Test-Path out/renderer/index.html) {
  Write-Output ("RENDERER_BUILT bytes=" + (Get-Item out/renderer/index.html).Length)
  Get-ChildItem -Recurse out/renderer | ForEach-Object { Write-Output ("   " + $_.Name + " " + $_.Length) }
} else { Write-Output "RENDERER_MISSING" }
Write-Output "--- py_compile (core) ---"
$py = @(Get-ChildItem -Recurse core/dfy_core -Filter *.py | ForEach-Object { $_.FullName })
& python -m py_compile @py 2>&1 | Select-Object -First 20
Write-Output ("PYCOMPILE_EXIT=" + $LASTEXITCODE + " files=" + $py.Count)
Write-Output "--- smoke (main -> core -> /v1/status) ---"
if (Get-Process electron -ErrorAction SilentlyContinue) {
  Write-Output "SMOKE_SKIPPED reason=electron_running_single_instance"
} else {
  & node_modules/.bin/electron.CMD . --smoke 2>&1 |
    ForEach-Object { if ($_ -match "SMOKE_") { Write-Output $_ } }
}
Write-Output "--- stage self-tests ---"
if (Test-Path scripts/selftest_stage1.py) {
  & python scripts/selftest_stage1.py 2>&1 |
    ForEach-Object { if ($_ -match "FAIL|SELFTEST") { Write-Output $_ } }
  Write-Output ("SELFTEST_EXIT=" + $LASTEXITCODE)
}
Write-Output "--- production untouched (vs baseline) ---"
if (Test-Path scripts/accept_stage0.py) {
  & python scripts/accept_stage0.py 2>&1 |
    ForEach-Object { if ($_ -match "identical=|deny-list enforced|OURS non-loopback|verdict:|loopback ports|ACCEPT_DONE") { Write-Output $_ } }
}
