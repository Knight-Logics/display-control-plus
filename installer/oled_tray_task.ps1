# PowerShell script to register OLED Protector with Windows Task Scheduler for tray auto-start
param(
    [string]$ExePath = "$PSScriptRoot\..\dist\OLEDProtector.exe"
)
$Action = New-ScheduledTaskAction -Execute $ExePath -Argument '/tray'
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName 'OLEDProtectorTray' -Action $Action -Trigger $Trigger -Principal $Principal -Description 'Start OLED Protector in tray mode at logon' -Force
