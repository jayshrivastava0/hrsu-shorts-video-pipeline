<#
  Removes the "HRSU-Blog-To-Video" scheduled task.
  Usage:  powershell -ExecutionPolicy Bypass -File scheduler\uninstall_task.ps1
#>
param([string]$TaskName = "HRSU-Blog-To-Video")

$ErrorActionPreference = "Stop"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'."
} else {
    Write-Host "Task '$TaskName' is not registered."
}
