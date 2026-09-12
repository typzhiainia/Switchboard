@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在安装依赖（清华镜像）...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
echo 安装完成，运行 run.bat 启动网关。
pause
