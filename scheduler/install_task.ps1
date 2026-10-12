<#
  Registers the "HRSU-Blog-To-Video" scheduled task (daily, runs only while the current user is logged on,
  because Ollama cloud and Claude auth are user-level).

  Usage:  powershell -ExecutionPolicy Bypass -File scheduler\install_task.ps1 [-At 11:00]
#>
param(
    [string]$At = "11:00",
    [string]$TaskName = "HRSU-Blog-To-Video"
)

$ErrorActionPreference = "Stop"
$bat = Join-Path $PSScriptRoot "run_scheduled.bat"
$repo = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path $bat)) { throw "run_scheduled.bat not found next to this script" }

# Bake in the absolute python interpreter: the task may see a different PATH than your shell.
$py = (Get-Command python -ErrorAction Stop).Source
$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"set HRSU_PYTHON=$py&& `"$bat`"`"" -WorkingDirectory $repo
$trigger   = New-ScheduledTaskTrigger -Daily -At $At
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
              -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
# Interactive logon + Limited: runs only when this user is logged on, no stored password, no elevation.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "HRSU: new blog post -> explainer video -> public YouTube upload" -Force | Out-Null
Write-Host "Registered '$TaskName' daily at $At (user logged on only). Test now with: Start-ScheduledTask -TaskName $TaskName"
