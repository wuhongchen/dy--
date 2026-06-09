@echo off
chcp 65001 >nul
title DyD 智能下载分析系统

echo ========================================
echo   DyD 智能下载分析系统
echo ========================================
echo.

REM 设置路径
set "ROOT=%~dp0"
set "PYTHON_DIR=%ROOT%python"
set "CHROME_DIR=%ROOT%chrome"
set "FFMPEG_DIR=%ROOT%ffmpeg"
set "APP_DIR=%ROOT%app"

REM 设置环境变量
set "PATH=%PYTHON_DIR%;%FFMPEG_DIR%;%CHROME_DIR%;%PATH%"
set "QTWEBENGINE_DISABLE_SANDBOX=1"

REM 检查 Python
if not exist "%PYTHON_DIR%\python.exe" (
    echo [错误] 未找到嵌入式 Python，请先运行 install.bat
    pause
    exit /b 1
)

echo [1/3] 启动服务...
echo 访问地址: http://localhost:8080
echo.

REM 启动 FastAPI 服务
cd /d "%ROOT%"
"%PYTHON_DIR%\python.exe" -m backend.main

pause
