; Script Inno Setup -- genere ClipFarming-Setup.exe a partir du dossier
; dist/ClipFarming produit par PyInstaller (build/clip_farming_gui.spec).
; Compile par .github/workflows/build-windows-gui.yml (ISCC.exe), pas
; localement dans ce depot (Inno Setup est un outil Windows uniquement).
;
; Experience visee (section 18 du cahier des charges) : installation dans
; Program Files, raccourci Bureau (optionnel, coche par defaut) et Menu
; Demarrer, desinstalleur -- comme un logiciel Windows classique.

#define MyAppName "ClipFarming"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "ClipFarming"
#define MyAppExeName "ClipFarming.exe"

[Setup]
AppId={{7B2E9C10-4B1E-4E3B-9B0A-CLIPFARMING01}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ClipFarming-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icons\clipfarming.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Icônes supplémentaires :"; Flags: checkedonce

[Files]
Source: "..\dist\ClipFarming\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Désinstaller {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent
