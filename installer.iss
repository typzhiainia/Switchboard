; Inno Setup 安装包脚本（需先执行 build.bat 生成 dist\Switchboard）
; 使用 Inno Setup 6+ 编译本文件，生成 Setup.exe

#define MyAppName "Switchboard"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Local"
#define MyAppExe "Switchboard.exe"

[Setup]
AppId={{A91F3B22-7C4D-4E5A-8B1C-D2E3F4A50607}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Switchboard
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer-output
OutputBaseFilename=Switchboard-Setup-1.0.0
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile=
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked
Name: "autostart"; Description: "开机自动启动 Switchboard"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "dist\Switchboard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "立即启动 Switchboard"; Flags: nowait postinstall skipifsilent
