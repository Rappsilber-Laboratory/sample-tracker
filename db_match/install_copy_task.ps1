<#
  install_copy_task.ps1: registers copy_raw_files.py as a Windows scheduled task.

  Run ONCE on the instrument PC, in an elevated PowerShell (Run as administrator):

      powershell -ExecutionPolicy Bypass -File .\install_copy_task.ps1

  It asks for the account the task should run as. Use an account that can read
  and rename files in the acquisition folder AND write to the server share
  (usually a domain or service account). The task then:

    * starts 2 minutes after every boot, even when nobody logs in
    * runs every 15 minutes, all day, forever
    * never starts a second copy while one is still running (a long copy of a
      big file simply delays the next run)
    * catches up a missed run as soon as possible (e.g. PC was off)
    * restarts up to 3 times if the run crashes

  Remove it again with:   Unregister-ScheduledTask -TaskName "RawFileCopy" -Confirm:$false
  Run it by hand with:    Start-ScheduledTask -TaskName "RawFileCopy"
#>
param(
    [string]$TaskName = "RawFileCopy",
    # Full path to python.exe. Check it with:  where.exe python
    [string]$Python   = "C:\Program Files\Python312\python.exe",
    [string]$Script   = (Join-Path $PSScriptRoot "copy_raw_files.py")
)

if (-not (Test-Path $Python)) { throw "python.exe not found at $Python. Pass -Python <path>." }
if (-not (Test-Path $Script)) { throw "copy_raw_files.py not found at $Script." }

$cred = Get-Credential -Message "Account that runs the copy task (needs access to the acquisition folder and the server share)"

# What to run. The working folder is the script folder.
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory (Split-Path $Script)

# When to run: (1) at boot, (2) every 15 min starting now, never ending.
# (On Windows 10/11 and Server 2016+, leaving out -RepetitionDuration means "indefinitely".)
$atBoot = New-ScheduledTaskTrigger -AtStartup
$atBoot.Delay = "PT2M"   # give the network 2 minutes to come up
$every15 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atBoot, $every15) -Settings $settings `
    -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Highest `
    -Description "Copies finished .raw acquisitions to the server with rsync (db_match/copy_raw_files.py)" -Force

Write-Host "Scheduled task '$TaskName' installed. Starting a first run now..."
Start-ScheduledTask -TaskName $TaskName
