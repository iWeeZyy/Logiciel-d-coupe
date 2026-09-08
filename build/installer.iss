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
Source: "..\dist\ClipFarming\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Désinstaller {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Le desinstalleur ne supprime que ce que l'installateur a POSE, et un dossier
; seulement s'il est vide. Tout ce que l'application ecrit ensuite a cote de
; l'exe -- caches Python, journal de plantage, fichiers restes d'une version
; precedente -- lui est donc inconnu : il survit, empeche la suppression des
; dossiers parents, et le dossier d'installation reste debout au complet
; (constate en usage : ClipFarming.exe et toutes ses DLL encore la apres
; desinstallation). On nomme donc explicitement ces restes.
;
; Rien de tout cela n'est une donnee de l'utilisateur : depuis la version qui
; accompagne ce script, projets, reglages et caches vivent sous Documents et
; %LOCALAPPDATA%, hors du dossier d'installation. Ce sont les emplacements que
; le desinstalleur ne touche PAS, volontairement : desinstaller un logiciel ne
; doit pas emporter le travail fait avec.
Type: filesandordirs; Name: "{app}\.cache"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\crash_log.txt"
Type: files; Name: "{app}\youtube_api_key.txt"
Type: files; Name: "{app}\config\gui_settings.json"
Type: dirifempty; Name: "{app}\config"
Type: dirifempty; Name: "{app}"
