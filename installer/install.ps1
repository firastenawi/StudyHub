# Installs / updates Study Hub for the current user (no admin rights, nothing downloaded).
#   - closes the running Study Hub (exe or run.bat copy) so its files can be replaced
#   - copies this folder to %LOCALAPPDATA%\Programs\StudyHub
#   - Start Menu + Desktop shortcuts, "Apps & features" entry (uninstall)
#   - starts the app; it re-points the Explorer menu and autostart at the installed exe
# User data stays in %APPDATA%\StudyHub and is never touched.
param(
    [string]$Dest = (Join-Path $env:LOCALAPPDATA "Programs\StudyHub"),
    [switch]$NoShortcuts,
    [switch]$NoRegistry,
    [switch]$NoLaunch
)
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$exeName = "StudyHub.exe"
if (-not (Test-Path (Join-Path $src $exeName))) { throw "$exeName not found next to install.ps1" }

# 1. close the running copy (asks it politely through its pipe, then waits)
$closer = if (Test-Path (Join-Path $Dest $exeName)) { Join-Path $Dest $exeName } else { Join-Path $src $exeName }
Start-Process -FilePath $closer -ArgumentList "--quit" -Wait
function Test-Running { [bool](Get-Process StudyHub -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Dest*" }) }
$deadline = (Get-Date).AddSeconds(15)
while ((Test-Running) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
if (Test-Running) {  # busy (a dialog open, saving...): ask once more and give it longer
    Start-Process -FilePath $closer -ArgumentList "--quit" -Wait
    $deadline = (Get-Date).AddSeconds(30)
    while ((Test-Running) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
}
# never copy over a running copy: that would leave old and new files mixed
if (Test-Running) { throw "Study Hub is still open. Close it (tray icon > Quit) and run the installer again." }

# 2. copy the files (mirror: removes files a previous version had and this one doesn't)
if ((Resolve-Path $src).Path.TrimEnd('\') -ne [IO.Path]::GetFullPath($Dest).TrimEnd('\')) {
    robocopy $src $Dest /MIR /XF install_source.txt /R:3 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copy failed (robocopy $LASTEXITCODE): is Study Hub still running?" }
    # where updates come from (Settings › Updates) — not when this is a temporary downloaded update
    if ($src -notlike '*\StudyHub\updates\*') {
        Set-Content -Path (Join-Path $Dest "install_source.txt") -Value (Resolve-Path $src).Path -Encoding UTF8
    }
}
$exe = Join-Path $Dest $exeName
$version = (Get-Item $exe).VersionInfo.ProductVersion

# 3. shortcuts
if (-not $NoShortcuts) {
    $shell = New-Object -ComObject WScript.Shell
    $places = @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))
    foreach ($dir in $places) {
        $lnk = $shell.CreateShortcut((Join-Path $dir "Study Hub.lnk"))
        $lnk.TargetPath = $exe
        $lnk.WorkingDirectory = $Dest
        $lnk.IconLocation = "$exe,0"
        $lnk.Description = "Study Hub"
        $lnk.Save()
    }
    $pdf = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Programs")) "Study Hub PDF.lnk"))
    $pdf.TargetPath = $exe
    $pdf.Arguments = "--pdf"
    $pdf.WorkingDirectory = $Dest
    $pdf.IconLocation = "$exe,0"
    $pdf.Description = "Study Hub PDF reader"
    $pdfIco = Join-Path $Dest "icons\pdf.ico"
    if (Test-Path $pdfIco) { $pdf.IconLocation = "$pdfIco,0" }
    $pdf.Save()

    # every app gets its own Start-menu shortcut (Start › Study Hub › …) with its own icon. The shortcut carries the
    # same AppUserModelID as the app's window, so a pinned shortcut and the open window share one taskbar button.
    $appsJson = Join-Path $Dest "apps.json"
    if (Test-Path $appsJson) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class ShortcutAppId {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PROPERTYKEY { public Guid fmtid; public uint pid; }
    [StructLayout(LayoutKind.Explicit)]
    public struct PROPVARIANT { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        void GetCount(out uint c); void GetAt(uint i, out PROPERTYKEY k); void GetValue(ref PROPERTYKEY k, out PROPVARIANT v);
        void SetValue(ref PROPERTYKEY k, ref PROPVARIANT v); void Commit();
    }
    [ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPersistFile {
        void GetClassID(out Guid g); [PreserveSig] int IsDirty(); void Load([MarshalAs(UnmanagedType.LPWStr)] string f, uint m);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string f, bool remember); void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f);
    }
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")] class ShellLink { }
    public static void Set(string lnk, string appId) {
        object link = new ShellLink();
        ((IPersistFile)link).Load(lnk, 2);
        var key = new PROPERTYKEY { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
        var value = new PROPVARIANT { vt = 31, p = Marshal.StringToCoTaskMemUni(appId) };
        try { var store = (IPropertyStore)link; store.SetValue(ref key, ref value); store.Commit(); }
        finally { Marshal.FreeCoTaskMem(value.p); }
        ((IPersistFile)link).Save(lnk, true);
        Marshal.ReleaseComObject(link);
    }
}
"@ -ErrorAction SilentlyContinue
        $folder = Join-Path ([Environment]::GetFolderPath("Programs")) "Study Hub"
        if (Test-Path $folder) { Get-ChildItem $folder -Filter *.lnk | Remove-Item -Force }  # apps that are gone
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        $apps = Get-Content $appsJson -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($app in $apps) {
            $path = Join-Path $folder ("{0}.lnk" -f ($app.name -replace '[\\/:*?"<>|]', '-'))
            $lnk = $shell.CreateShortcut($path)
            $lnk.TargetPath = $exe
            $lnk.Arguments = "--app $($app.key)"
            $lnk.WorkingDirectory = $Dest
            $ico = Join-Path $Dest "icons\$($app.key).ico"
            $lnk.IconLocation = if (Test-Path $ico) { "$ico,0" } else { "$exe,0" }
            $lnk.Description = $app.description
            $lnk.Save()
            try { [ShortcutAppId]::Set($path, $app.app_id) } catch { Write-Host "app id not set on $($app.key): $_" }
        }
    }
}

# 4. "Apps & features" entry
if (-not $NoRegistry) {
    $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\StudyHub"
    New-Item -Path $key -Force | Out-Null
    $size = [int]((Get-ChildItem $Dest -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
    $uninstall = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Dest\uninstall.ps1`""
    $values = @{
        DisplayName = "Study Hub"; DisplayVersion = $version; Publisher = "Study Hub"
        DisplayIcon = "$exe,0"; InstallLocation = $Dest; UninstallString = $uninstall
    }
    foreach ($name in $values.Keys) { Set-ItemProperty -Path $key -Name $name -Value $values[$name] }
    Set-ItemProperty -Path $key -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty -Path $key -Name NoRepair -Value 1 -Type DWord
    Set-ItemProperty -Path $key -Name EstimatedSize -Value $size -Type DWord
}

Write-Host "Study Hub $version installed in $Dest"
if (-not $NoLaunch) { Start-Process -FilePath $exe -WorkingDirectory $Dest }
