@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 安装打包工具与依赖...
python -m pip install --upgrade pyinstaller -r requirements.txt
echo [2/2] 打包 LLMGateway...
python -m PyInstaller --noconfirm --clean gateway.spec
echo.
echo 打包完成: dist\LLMGateway\LLMGateway.exe
pause
