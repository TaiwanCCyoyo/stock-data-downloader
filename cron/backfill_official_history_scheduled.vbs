' Hidden-window launcher for backfill_official_history_scheduled.cmd, used only
' by the StockProject-OfficialBackfill Task Scheduler action (see
' setup_scheduled_tasks.ps1). Task Scheduler runs the .cmd under the current
' user's interactive logon session (same as before), so a bare .cmd action
' would flash a console window on screen for the whole ~23h run -- disruptive
' to fullscreen apps far beyond the old hourly popups. WScript.Shell.Run's
' window-style 0 keeps the process fully hidden while still waiting (True) for
' it to finish and forwarding its exit code, so Task Scheduler still sees
' success/failure correctly.
Set objFSO = CreateObject("Scripting.FileSystemObject")
scriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = scriptDir
exitCode = objShell.Run("""" & scriptDir & "\backfill_official_history_scheduled.cmd""", 0, True)
WScript.Quit exitCode
