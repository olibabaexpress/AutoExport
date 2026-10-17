#ifndef AppVersion
  #define AppVersion "0.1.2"
#endif
#ifndef AppIdentifier
  #define AppIdentifier "{{B2D3CE76-514F-40CF-B24B-ADFFFA6DB995}"
#endif

[Setup]
AppId={#AppIdentifier}
AppName=AutoExport
AppVersion={#AppVersion}
AppPublisher=AutoExport
DefaultDirName={localappdata}\AutoExport
DefaultGroupName=AutoExport
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
DisableProgramGroupPage=yes
WizardStyle=modern
OutputDir=..\dist\installer
OutputBaseFilename=AutoExport-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\AutoExport.exe
SetupLogging=yes
SetupIconFile=autoexport.ico
WizardImageFile=..\build\wizard\large.bmp
WizardSmallImageFile=..\build\wizard\small.bmp

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\LOGS"
Type: filesandordirs; Name: "{app}\debug"
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: checkedonce

[Files]
Source: "..\dist\AutoExport\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AutoExport"; Filename: "{app}\AutoExport.exe"; WorkingDir: "{app}"
Name: "{group}\Configure AutoExport"; Filename: "{app}\AutoExport.exe"; Parameters: "--configure"; WorkingDir: "{app}"
Name: "{autodesktop}\AutoExport"; Filename: "{app}\AutoExport.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\Directory\shell\AutoExport"; ValueType: string; ValueName: ""; ValueData: "AutoExport"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\AutoExport"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\AutoExport.exe"""
Root: HKCU; Subkey: "Software\Classes\Directory\shell\AutoExport"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"
Root: HKCU; Subkey: "Software\Classes\Directory\shell\AutoExport\command"; ValueType: string; ValueName: ""; ValueData: """{app}\AutoExport.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\AutoExport"; ValueType: string; ValueName: ""; ValueData: "AutoExport"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\AutoExport"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\AutoExport.exe"""
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\AutoExport\command"; ValueType: string; ValueName: ""; ValueData: """{app}\AutoExport.exe"" ""%V"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.als\shell\AutoExport"; ValueType: string; ValueName: ""; ValueData: "AutoExport"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.als\shell\AutoExport"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\AutoExport.exe"""
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.als\shell\AutoExport"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.als\shell\AutoExport\command"; ValueType: string; ValueName: ""; ValueData: """{app}\AutoExport.exe"" ""%1"""

[Run]
Filename: "{app}\AutoExport.exe"; Description: "Launch AutoExport"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
procedure ThemeControls(Parent: TWinControl);
var
  I: Integer;
  Control: TControl;
begin
  for I := 0 to Parent.ControlCount - 1 do
  begin
    Control := Parent.Controls[I];
    if Control is TNewNotebookPage then
      TNewNotebookPage(Control).Color := $1E1E1E
    else if Control is TNewStaticText then
      TNewStaticText(Control).Font.Color := $E0E0E0
    else if Control is TLabel then
      TLabel(Control).Font.Color := $E0E0E0
    else if Control is TPanel then
    begin
      TPanel(Control).Color := $1E1E1E;
      TPanel(Control).Font.Color := $E0E0E0;
    end
    else if Control is TEdit then
    begin
      TEdit(Control).Color := $1A1A1A;
      TEdit(Control).Font.Color := $E0E0E0;
    end
    else if Control is TMemo then
    begin
      TMemo(Control).Color := $1A1A1A;
      TMemo(Control).Font.Color := $E0E0E0;
    end
    else if Control is TNewCheckListBox then
    begin
      TNewCheckListBox(Control).Color := $1E1E1E;
      TNewCheckListBox(Control).Font.Color := $E0E0E0;
    end
    else if Control is TNewRadioButton then
      TNewRadioButton(Control).Font.Color := $E0E0E0
    else if Control is TRadioButton then
      TRadioButton(Control).Font.Color := $E0E0E0
    else if Control is TNewCheckBox then
      TNewCheckBox(Control).Font.Color := $E0E0E0
    else if Control is TCheckBox then
      TCheckBox(Control).Font.Color := $E0E0E0;
    if Control is TWinControl then
      ThemeControls(TWinControl(Control));
  end;
end;

procedure InitializeWizard;
begin
  WizardForm.Color := $1E1E1E;
  WizardForm.Font.Name := 'Segoe UI';
  ThemeControls(WizardForm);
  WizardForm.InnerPage.Color := $1E1E1E;
  WizardForm.PageNameLabel.Font.Color := $5FBF5F;
  WizardForm.WelcomeLabel1.Font.Color := $5FBF5F;
  WizardForm.FinishedHeadingLabel.Font.Color := $5FBF5F;
  WizardForm.WelcomeLabel2.Caption :=
    'This installs AutoExport, Python, its packages, Tesseract OCR and AbletonOSC.' + #13#10#13#10 +
    'No separate Python installation or internet connection is needed.' + #13#10#13#10 +
    'You must already have Ableton Live 12 installed with the English interface. ' +
    'Close Live before continuing. After copying the files, a configuration window ' +
    'will detect your User Library and offer Browse buttons for any missing paths.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if (CurStep = ssPostInstall) and not WizardSilent then
  begin
    if not Exec(ExpandConstant('{app}\AutoExport.exe'), '--configure',
      ExpandConstant('{app}'), SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then
      RaiseException('Could not open AutoExport configuration. Use Configure AutoExport from the Start menu.')
    else if ResultCode <> 0 then
      MsgBox('Application files were installed, but configuration was cancelled or failed. ' +
        'AutoExport will ask for configuration when first launched. ' +
        'You can also use Configure AutoExport from the Start menu.', mbInformation, MB_OK);
  end;
end;
