### 2026-10-27 scheduled watchdog root cause and recovery
- Root cause found: existing scheduled task `\DafishuBotWatchdog` runs every PT2M and calls `ops-dfy\watchdog.ps1`; while OneBot WS 3001 was down, its line 48-54 relaunched `napcat\launcher-user.bat` each cycle, accumulating launcher/QQ trees.
- Kept the scheduled task enabled (it also checks DSH/bridge). Added a reversible 6099-listener guard to `launcher-user.bat`; byte-for-byte backup: `launcher-user.bat.pre-singleton-20261027`.
- Verified after QQ login: 3000/3001/3100/6099 LISTEN; SnowLuma `connected=true`, `accountOnline=true`; both allowlisted groups returned by status endpoint.
- Lineage-safe prune retained only the tree owning WebUI 6099. After the next scheduled cycle at 13:20:02: 1 launcher / 4 QQ.exe; free RAM 13.03 GB; all four ports stayed LISTEN.
- No mass-kill, reboot, or model load performed during this verification.
