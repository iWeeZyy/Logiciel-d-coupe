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
; Le desinstalleur d'Inno ne supprime que ce que l'installateur a POSE, et un
; dossier seulement s'il est vide. Tout ce qui apparait APRES l'installation
; lui est inconnu : caches Python et numba ecrits a cote des bibliotheques,
; journal de plantage, fichiers de reglages reecrits par la page Parametres,
; restes d'une version precedente. Chacun de ces fichiers empeche la
; suppression de son dossier parent, et de proche en proche le dossier
; d'installation reste debout au complet -- constate deux fois en usage reel :
; d'abord ClipFarming.exe et ses DLL, puis les dossiers de dependances
; (numpy, PySide6, scipy, av...) apres une desinstallation.
;
; Nommer les restes un par un ne marche pas : c'est une liste sans fin. On
; supprime donc TOUT le dossier d'installation, ce qui est sans risque ici :
; depuis la version qui accompagne ce script, projets, reglages et caches
; vivent sous Documents et %LOCALAPPDATA%, hors du dossier d'installation.
; Ces deux emplacements ne sont volontairement nommes NULLE PART dans ce
; fichier : desinstaller un logiciel ne doit pas emporter le travail fait avec.
;
; Conseil qui decoule de la meme regle : ne rangez rien de personnel dans le
; dossier d'installation, il est fait pour disparaitre entierement.
Type: filesandordirs; Name: "{app}"
