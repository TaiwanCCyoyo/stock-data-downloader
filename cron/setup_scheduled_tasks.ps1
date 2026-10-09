# Register new Windows tasks, or migrate existing actions after pulling a release.
# Run only from the production checkout. This script never starts a task.
# -UpdateActionsOnly preserves triggers, principal, settings and enabled state.
[CmdletBinding()]
param([switch]$UpdateActionsOnly)

$ErrorActionPreference = "Stop"

$repoDir = Split-Path -Parent $PSScriptRoot
$cronDir = $PSScriptRoot

# Keep both launcher chains hidden while retaining their exit codes.
$dailyAction = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "`"$cronDir\run_daily_scheduled.vbs`"" -WorkingDirectory $repoDir
$backfillAction = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "`"$cronDir\backfill_official_history_scheduled.vbs`"" -WorkingDirectory $repoDir

if ($UpdateActionsOnly) {
    # Resolve both tasks before changing either, so missing tasks fail early.
    $dailyTask = Get-ScheduledTask -TaskName "StockProject-DailyUpdate" -TaskPath "\"
    $backfillTask = Get-ScheduledTask -TaskName "StockProject-OfficialBackfill" -TaskPath "\"
    Set-ScheduledTask -TaskName $dailyTask.TaskName -TaskPath $dailyTask.TaskPath -Action $dailyAction | Out-Null
    Set-ScheduledTask -TaskName $backfillTask.TaskName -TaskPath $backfillTask.TaskPath -Action $backfillAction | Out-Null
    Write-Host "Updated task actions only. Existing triggers, settings and enabled state are preserved."
    return
}

$dailyTrigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 18:00

$backfillTrigger = New-ScheduledTaskTrigger -Daily -At 18:25

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

# The crawler has its own 23h budget; this is the scheduler hard-stop backstop.
# Missed triggers and long daily runs can still cause the two tasks to overlap.
$backfillSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 23 -Minutes 30)


Register-ScheduledTask -TaskName "StockProject-DailyUpdate" `
    -Action $dailyAction -Trigger $dailyTrigger -Settings $settings `
    -Description "Shioaji + official TWSE/TPEx daily download and health check, Mon-Fri only (StockProject)" `
    -Force

Register-ScheduledTask -TaskName "StockProject-OfficialBackfill" `
    -Action $backfillAction -Trigger $backfillTrigger -Settings $backfillSettings `
    -Description "Official TWSE historical daily-price backfill crawler, one ~23h hidden-window slice per day at 18:25, may overlap a delayed DailyUpdate run (StockProject)" `
    -Force

Write-Host "Registered StockProject-DailyUpdate (18:00 Mon-Fri) and StockProject-OfficialBackfill (daily, 18:25, ~23h hidden run)."
Write-Host "View/manage: taskschd.msc, or 'Get-ScheduledTask -TaskName StockProject-*'"
