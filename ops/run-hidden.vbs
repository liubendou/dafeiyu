' Run a PowerShell script with a completely hidden window (no console/terminal flash).
' Usage: wscript.exe //B //Nologo run-hidden.vbs "C:\path\to\script.ps1"
Option Explicit
Dim sh, cmd, script
If WScript.Arguments.Count = 0 Then WScript.Quit 1
script = WScript.Arguments(0)
Set sh = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & script & """"
sh.Run cmd, 0, True
