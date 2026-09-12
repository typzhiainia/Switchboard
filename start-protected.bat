@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Switchboard (守护模式-异常退出自动重启)
echo 守护模式：进程异常退出将自动重启。
echo 关闭本窗口即停止守护与服务。
echo.
:loop
echo [%date% %time%] 启动 Switchboard...
Switchboard.exe
echo.
echo [%date% %time%] 进程已退出，3 秒后自动重启... (要停止请直接关闭本窗口)
timeout /t 3 >nul
goto loop
