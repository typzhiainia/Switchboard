; Inno Setup 安装包脚本（需先执行 build.bat 生成 dist\LLMGateway）
; 使用 Inno Setup 6+ 编译本文件，生成 Setup.exe

#define MyAppName "LLM Gateway"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Local"
#define MyAppExe "LLMGateway.exe"

[Setup]
AppId={{8F3C2A10-9E7B-4C2A-A1B2-C3D4E5F60708}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\LLMGateway
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer-output
OutputBaseFilename=LLMGateway-Setup-1.0.0
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
Name: "autostart"; Description: "开机自动启动 LLM Gateway"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "dist\LLMGateway\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "立即启动 LLM Gateway"; Flags: nowait postinstall skipifsilent
