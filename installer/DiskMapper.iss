; DiskMapper installer script for Inno Setup 6 (https://jrsoftware.org/isdl.php)
;
; Build dist\DiskMapper.exe first (run build.bat), then either open this file
; in the Inno Setup Compiler and press F9, or run:
;     iscc installer\DiskMapper.iss
;
; Output: installer\Output\DiskMapper-Setup-1.0.0.exe

#define AppName        "DiskMapper"
#define AppVersion     "1.0.0"
#define AppPublisher   "Rohan Rathod"
#define AppExeName     "DiskMapper.exe"

[Setup]
AppId={{7C3A9E14-5B2D-4F86-9E31-DA5C7F0B1A42}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=Output
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile=..\assets\diskmapper.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user install by default: no admin prompt, friendlier first run.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";          DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE";            DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";           Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";     Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the local scan-history database on uninstall.
Type: filesandordirs; Name: "{localappdata}\DiskMapper"
