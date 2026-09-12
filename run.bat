@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Switchboard
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH。
    pause
    exit /b 1
)
python -m app.main %*
if errorlevel 1 pause
