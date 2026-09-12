@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在安装依赖...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo 安装完成，运行 run.bat 启动网关。
pause
