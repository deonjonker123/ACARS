; installer/tailwind.iss
;
; Inno Setup 6.3+ script for the Tailwind ACARS installer. build.py runs it
; automatically after the PyInstaller build if Inno Setup is installed
; (https://jrsoftware.org/isdl.php), passing the values below with /D:
;
;   ISCC /DAppVersion=1.0.3 /DProjectDir=<project> /DSourceDir=<project>\dist\Tailwind
;        /DOutputDir=<project>\dist installer\tailwind.iss
;
; Result: dist\Tailwind-Setup-<version>.exe
;
; - Installs per user in %LOCALAPPDATA%\Programs\Tailwind: no admin prompt.
; - Updating = running a newer setup over the top. Tailwind is closed first
;   and the old program files are replaced cleanly.
; - The pilot's data (%LOCALAPPDATA%\Tailwind ACARS - logbook, fleet,
;   photos, log) is never part of the install. On uninstall the pilot is
;   asked whether to delete it too (default: keep).
;
; AppId must never change - it's how Windows knows a new version is an
; update of the same app.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef ProjectDir
  #define ProjectDir ".."
#endif
#ifndef SourceDir
  #define SourceDir ProjectDir + "\dist\Tailwind"
#endif
#ifndef OutputDir
  #define OutputDir ProjectDir + "\dist"
#endif

#define AppName "Tailwind"
#define AppExe "Tailwind.exe"
#define AppUrl "https://github.com/deonjonker123/ACARS"
#define DataFolder "Tailwind ACARS"

[Setup]
AppId={{329C4B96-E876-4E0A-89D0-9EA38D425EB3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=deonjonker123
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} ACARS installer

PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} ACARS

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

LicenseFile={#ProjectDir}\LICENSE
SetupIconFile={#ProjectDir}\build\app_icon.ico
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup-{#AppVersion}

CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Clean replace on update - no leftovers from an older build
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ProjectDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectDir}\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; AppUserModelID matches the app's own taskbar ID (core/paths.py), so the
; Start-menu shortcut, the running window and a pinned icon all group as one
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "Tailwind.ACARS"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "Tailwind.ACARS"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if (CurUninstallStep <> usPostUninstall) or UninstallSilent then
    Exit;

  DataDir := ExpandConstant('{localappdata}\{#DataFolder}');
  if not DirExists(DataDir) then
    Exit;

  if MsgBox('Also delete your Tailwind pilot data?' + #13#10#13#10 +
            'This is your logbook, fleet, aircraft photos, settings and log in:' + #13#10 +
            DataDir + #13#10#13#10 +
            'Choose No to keep it - reinstalling Tailwind later picks it up again.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
  begin
    DelTree(DataDir, True, True, True);
    { The app's small preferences (e.g. last network used) }
    RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\Tailwind\ACARS');
    RegDeleteKeyIfEmpty(HKEY_CURRENT_USER, 'Software\Tailwind');
  end;
end;