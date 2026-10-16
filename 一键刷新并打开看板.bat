@echo off
chcp 65001 >nul
title Antigravity Token 看板本地服务 (8765端口)
cd /d "%~dp0"

echo ==============================================================================
echo           Google Antigravity Token 消耗全景仪表盘 - 本地端口服务
echo ==============================================================================
echo.

REM 1. 检查 8765 端口是否已经在运行
set "RUNNING="
for /f "tokens=*" %%a in ('netstat -ano -p tcp ^| findstr ":8765 " ^| findstr "LISTENING"') do (
    set "RUNNING=1"
)

if defined RUNNING (
    echo [状态] Antigravity Token 看板已在运行 (端口 8765)
    echo [操作] 正在打开浏览器看板...
    start "" "http://127.0.0.1:8765"
    timeout /t 1 >nul 2>&1
    exit /b 0
)

REM 2. 检查 Python 环境
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未在系统 PATH 中检测到 Python 环境！
    pause
    exit /b 1
)

REM 3. 启动后台服务进程
echo [状态] 正在启动 Token 看板服务 (server.py)...
start "Antigravity-Token-8765" /min python server.py

REM 4. 轮询等待 8765 端口就绪 (最多等待 8 秒)
echo [状态] 正在等待端口 8765 就绪...
for /l %%i in (1, 1, 8) do (
    timeout /t 1 /nobreak >nul 2>&1
    set "RUNNING="
    for /f "tokens=*" %%a in ('netstat -ano -p tcp ^| findstr ":8765 " ^| findstr "LISTENING"') do (
        set "RUNNING=1"
    )
    if defined RUNNING (
        echo [成功] 看板服务已就绪！正在为您打开浏览器...
        start "" "http://127.0.0.1:8765"
        timeout /t 1 >nul 2>&1
        exit /b 0
    )
)

echo.
echo =======================================================
echo [警告] 端口 8765 启动超时，请检查控制台或 Python 依赖！
echo =======================================================
echo.
pause
