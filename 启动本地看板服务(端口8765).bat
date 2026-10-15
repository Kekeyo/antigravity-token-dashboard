@echo off
chcp 65001 >nul
title Antigravity Token 看板本地服务 (8765端口)
echo ==============================================================================
echo           Google Antigravity Token 消耗全景仪表盘 - 本地端口服务
echo ==============================================================================
echo 正在启动本地 Web 服务 (http://127.0.0.1:8765)...
echo 特性: 彻底解决浏览器缓存 · 支持网页端一键无刷新重新扫描 · DOpus 本地穿透
echo.

python "%~dp0server.py"

if %errorlevel% neq 0 (
    echo.
    echo [提示] 启动失败，请检查 Python 环境或端口占用。
    pause
    exit /b %errorlevel%
)
pause
