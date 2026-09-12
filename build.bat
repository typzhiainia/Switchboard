@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 安装打包工具与依赖...
python -m pip install --upgrade pyinstaller -r requirements.txt || goto :fail
echo [2/3] 打包 Switchboard...
python -m PyInstaller --noconfirm --clean switchboard.spec || goto :fail
if not exist "dist\Switchboard\Switchboard.exe" goto :fail
echo [3/3] 生成在线更新包...
python make_update.py || goto :fail
echo.
echo 打包完成: dist\Switchboard\Switchboard.exe
pause
exit /b 0

:fail
echo.
echo [错误] 打包失败！请把上方窗口中的报错信息截图反馈。
echo 常见原因：杀毒软件拦截、pip 安装失败、Python 版本过低（需 3.10+）。
pause
exit /b 1
