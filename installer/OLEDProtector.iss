; Display Control Inno Setup Script
; This script creates an installer with options for desktop shortcut and Task Scheduler background auto-start

[Setup]
AppName=Display Control
AppVersion=1.0.5
DefaultDirName={commonpf64}\Display Control
DefaultGroupName=Display Control
UninstallDisplayIcon={app}\DisplayControl.exe
OutputDir=..
OutputBaseFilename=DisplayControlSetup_v1.0.5
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Files]
Source: "..\dist\DisplayControl.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\tray.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\overlay_bg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Display Control+ Logo.png"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\KnightLogicsLogo.png"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{commondesktop}\Display Control"; Filename: "{app}\DisplayControl.exe"; Tasks: desktopicon
Name: "{group}\Display Control"; Filename: "{app}\DisplayControl.exe"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce
Name: "trayicon"; Description: "Start in &Task Tray on login"; GroupDescription: "Startup options:"; Flags: checkedonce

[Run]
; Register Task Scheduler entry for tray startup (preferred startup model)
Filename: "cmd.exe"; \
  Parameters: "/C schtasks /Create /F /TN DisplayControlBackground /TR ""\""{app}\tray.exe\"""" /SC ONLOGON"; \
  StatusMsg: "Registering startup task..."; \
  Flags: runhidden; Check: WizardIsTaskSelected('trayicon')

; Start tray immediately after install (manual/wizard installs)
Filename: "{app}\tray.exe"; Description: "Start Display Control+ tray"; Flags: nowait postinstall skipifsilent

; Optional: open dashboard after install (manual/wizard installs)
Filename: "{app}\DisplayControl.exe"; Description: "Open Display Control+ dashboard"; Flags: nowait postinstall skipifsilent unchecked

; Silent update: re-register startup task, relaunch tray, then reopen dashboard
Filename: "cmd.exe"; \
  Parameters: "/C schtasks /Create /F /TN DisplayControlBackground /TR ""\""{app}\tray.exe\"""" /SC ONLOGON"; \
  Flags: runhidden; Check: WizardSilent()
Filename: "{app}\tray.exe"; Flags: nowait; Check: WizardSilent()
Filename: "{app}\DisplayControl.exe"; Flags: nowait; Check: WizardSilent()