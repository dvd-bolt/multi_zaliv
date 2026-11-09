#ifndef Payload
  #error Payload must name the prepared FrameFlow folder
#endif
#ifndef Version
  #define Version "0.2.0"
#endif
#ifndef ReleaseDir
  #define ReleaseDir "..\releases"
#endif

[Setup]
AppId={code:AppIdentity}
AppName=FrameFlow
AppVersion={#Version}
AppPublisher=FrameFlow
DefaultDirName={localappdata}\Programs\FrameFlow
DefaultGroupName=FrameFlow
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#ReleaseDir}
OutputBaseFilename=FrameFlow-Setup-{#Version}-x64
SetupIconFile={#Payload}\assets\icons\survivalflow-v1.ico
UninstallDisplayIcon={app}\FrameFlow.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
UsePreviousLanguage=no
UninstallFilesDir={app}\uninstall
VersionInfoVersion={#Version}.0
VersionInfoDescription=FrameFlow Setup

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; Flags: unchecked

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\data\tools\build\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: not WebView2Installed

[Icons]
Name: "{group}\FrameFlow"; Filename: "{app}\FrameFlow.exe"; Check: not TestInstall
Name: "{group}\Остановить FrameFlow полностью"; Filename: "{app}\FrameFlow.exe"; Parameters: "--stop"; Check: not TestInstall
Name: "{autodesktop}\FrameFlow"; Filename: "{app}\FrameFlow.exe"; Tasks: desktopicon; Check: not TestInstall

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Установка Microsoft Edge WebView2…"; Flags: waituntilterminated runhidden; Check: not WebView2Installed
Filename: "{app}\FrameFlow.exe"; Description: "Запустить FrameFlow"; Flags: nowait postinstall skipifsilent; Check: not TestInstall

[Code]
function TestInstall: Boolean;
begin
  Result := ExpandConstant('{param:TESTINSTALL|0}') = '1';
end;

function AppIdentity(Param: String): String;
begin
  if TestInstall then
    Result := '{75890FA0-AE2C-4DD6-B956-3B1E37C1E639}'
  else
    Result := '{6A4DC840-6E3A-4E5D-81C7-6AE597F1DD78}';
end;

function WebView2Installed: Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(HKLM32,
    'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  if not Result then
    Result := RegQueryStringValue(HKCU,
      'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  Result := Result and (Version <> '') and (Version <> '0.0.0.0');
end;
