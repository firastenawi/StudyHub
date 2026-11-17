# Removes Study Hub (started from "Apps & features" or by hand).
# Removes: the program folder, shortcuts, Explorer menu, "Send to", autostart, the uninstall entry.
# Keeps:   your data in %APPDATA%\StudyHub (notes, vault, backups) — delete it yourself if you want.
param([switch]$Quiet)
$ErrorActionPreference = "Continue"
$dir = $PSScriptRoot
$exe = Join-Path $dir "StudyHub.exe"
Set-Location $env:TEMP  # don't keep the folder we are deleting busy

if (-not $Quiet) {
    Add-Type -AssemblyName System.Windows.Forms
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "Remove Study Hub?`nYour data in %APPDATA%\StudyHub is kept.`n`nإزالة Study Hub؟ بياناتك تبقى محفوظة.",
        "Study Hub", "YesNo", "Question")
    if ($answer -ne "Yes") { exit 1 }
}

if (Test-Path $exe) {
    Start-Process -FilePath $exe -ArgumentList "--quit" -Wait
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Process StudyHub -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dir*" }) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    Start-Process -FilePath $exe -ArgumentList "--cleanup" -Wait  # Explorer menu, Send to, autostart
}

foreach ($folder in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    Remove-Item (Join-Path $folder "Study Hub.lnk") -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $folder "Study Hub PDF.lnk") -Force -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path ([Environment]::GetFolderPath("Programs")) "Study Hub") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\StudyHub" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
if (-not $Quiet) {
    [System.Windows.Forms.MessageBox]::Show("Study Hub was removed.", "Study Hub") | Out-Null
}
