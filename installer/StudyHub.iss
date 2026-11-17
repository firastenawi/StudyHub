; Study Hub installer (Inno Setup 6).
; Build dist\StudyHub first (build.bat), then: ISCC installer\StudyHub.iss
;
; The setup unpacks the app to a temp folder and runs install.ps1 from there, so Setup.exe and Install.bat
; do exactly the same thing: per-user install into %LOCALAPPDATA%\Programs\StudyHub, Start menu shortcuts,
; an "Apps & features" entry (its uninstaller is uninstall.ps1). User data in %APPDATA%\StudyHub is never touched.
;
; Extra arguments for install.ps1 can be passed with /PSARGS="...", e.g. for a test install:
;   StudyHub-Setup.exe /VERYSILENT /PSARGS="-Dest C:\temp\sh -NoShortcuts -NoRegistry -NoLaunch"

#define AppExe "..\dist\StudyHub\StudyHub.exe"
#define AppVersion GetStringFileInfo(AppExe, "ProductVersion")

[Setup]
AppId={{6F1D2B7C-4E3A-4C59-9C1E-5A7B3D2E8F10}
AppName=Study Hub
AppVersion={#AppVersion}
AppVerName=Study Hub {#AppVersion}
AppPublisher=Study Hub
DefaultDirName={localappdata}\Programs\StudyHub
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
; install.ps1 registers its own uninstaller
Uninstallable=no
CreateUninstallRegKey=no
OutputDir=Output
OutputBaseFilename=StudyHub-Setup-{#AppVersion}
SetupIconFile=studyhub.ico
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription=Study Hub Setup

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ar"; MessagesFile: "compiler:Languages\Arabic.isl"

[Files]
Source: "..\dist\StudyHub\*"; DestDir: "{tmp}\StudyHub"; Flags: recursesubdirs createallsubdirs ignoreversion deleteafterinstall

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{tmp}\StudyHub\install.ps1"" {param:PSARGS|}"; \
  StatusMsg: "Installing Study Hub..."; Flags: runhidden waituntilterminated
