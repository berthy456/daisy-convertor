; The Windows download: one exe that unpacks the app into the user's own folder.
; build_binary.py compiles this with Inno Setup's ISCC.exe. Relative paths below
; are relative to this file, which sits at the root of the repository.

#ifndef AppVersion
  #define AppVersion "0.0"
#endif
#ifndef PythonInstaller
  #define PythonInstaller "dist\python-installer.exe"
#endif

[Setup]
AppId={{3D197B4F-344A-49A5-9146-DA4D0FCE8486}
AppName=Daisy Convertor
AppVersion={#AppVersion}
AppPublisher=berthy456
AppPublisherURL=https://github.com/berthy456/daisy-convertor
AppSupportURL=https://github.com/berthy456/daisy-convertor/issues
DefaultDirName={localappdata}\daisy2telmi\app
DefaultGroupName=Daisy Convertor
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=daisy-convertor-{#AppVersion}-setup
SetupIconFile=assets\icon.ico
UninstallDisplayName=Daisy Convertor
UninstallDisplayIcon={app}\daisy-convertor.exe
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
Compression=lzma2/max
SolidCompression=yes

[Tasks]
Name: "Python"; Description: "Install Python 3.11 (the app needs one to install a speech engine)"
Name: "DesktopIcon"; Description: "Add a shortcut on the desktop"

[Files]
Source: "dist\daisy-convertor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PythonInstaller}"; DestDir: "{tmp}"; DestName: "python-installer.exe"; Flags: deleteafterinstall

[Icons]
Name: "{autoprograms}\Daisy Convertor"; Filename: "{app}\daisy-convertor.exe"
Name: "{autodesktop}\Daisy Convertor"; Filename: "{app}\daisy-convertor.exe"; Tasks: DesktopIcon

[Run]
Filename: "{tmp}\python-installer.exe"; Parameters: "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 AssociateFiles=0 Include_test=0"; Tasks: Python; Check: PythonAlreadyThere; StatusMsg: "Installing Python..."
Filename: "{app}\daisy-convertor.exe"; Description: "Start Daisy Convertor"; Flags: nowait postinstall skipifsilent

[Code]
function PythonIn(const Root: string): Boolean;
var
  Find: TFindRec;
begin
  Result := False;
  if DirExists(Root) and FindFirst(Root + '\*', Find) then
  begin
    try
      repeat
        if ((Find.Attributes and $10) <> 0) and (Copy(Find.Name, 1, 7) = 'Python3') and
           FileExists(Root + '\' + Find.Name + '\python.exe') then
        begin
          Result := True;
          Break;
        end;
      until not FindNext(Find);
    finally
      FindClose(Find);
    end;
  end;
end;

{ The per-user folders the python.org installer fills, which is also where the }
{ app looks for a Python of its own. }
function PythonAlreadyThere(): Boolean;
begin
  Result := PythonIn(ExpandConstant('{localappdata}\Programs\Python')) or
            PythonIn(ExpandConstant('{autopf}'));
end;
