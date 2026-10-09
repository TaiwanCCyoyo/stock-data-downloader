' Hidden-window launcher for run_daily_scheduled.cmd, used only by the
' StockProject-DailyUpdate Task Scheduler action (see setup_scheduled_tasks.ps1).
' Task Scheduler runs the .cmd under the current user's interactive logon
' session, so a bare .cmd action puts a console window on screen for the whole
' run -- and the run is not always short: on 2026-08-14 the Shioaji session died
' 12 seconds in and the window sat there for over three hours until it was
' closed by hand. WScript.Shell.Run's window-style 0 keeps the process fully
' hidden while still waiting (True) for it to finish and forwarding its exit
' code, so Task Scheduler still sees success/failure correctly.
Set objFSO = CreateObject("Scripting.FileSystemObject")
scriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = scriptDir
exitCode = objShell.Run("""" & scriptDir & "\run_daily_scheduled.cmd""", 0, True)
WScript.Quit exitCode
