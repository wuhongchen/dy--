@echo off
chcp 65001 >nul
title DyD 智能下载分析系统 - 一键安装

echo.
echo  ========================================
echo   DyD 智能下载分析系统 - 一键安装
echo  ========================================
echo.

set "ROOT=%~dp0"

REM 1. 检查并安装 Python
echo [1/5] 检查 Python...
where python >nul 2>&1
if %errorlevel% equ 0 (
    python --version | findstr /C:"3.1" >nul
    if %errorlevel% equ 0 (
        echo   Python 已安装
        goto :install_deps
    )
)

echo   Python 未安装，正在自动下载安装...
echo   请在弹出的安装窗口中点击 "Install Now"

REM 下载 Python 安装包
set "PYTHON_INSTALLER=%TEMP%\python-installer.exe"
echo   下载 Python 3.11.9...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%PYTHON_INSTALLER%'"

if not exist "%PYTHON_INSTALLER%" (
    echo   [错误] Python 下载失败，请手动安装 Python 3.11+
    echo   下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo   安装 Python...
"%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
timeout /t 30 >nul

REM 刷新环境变量
set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%"

:install_deps
echo.
echo [2/5] 创建虚拟环境...
if not exist "%ROOT%venv" (
    python -m venv "%ROOT%venv"
    echo   虚拟环境创建完成
) else (
    echo   虚拟环境已存在
)

echo.
echo [3/5] 安装依赖（可能需要几分钟）...
"%ROOT%venv\Scripts\pip.exe" install -r "%ROOT%requirements.txt" --no-cache-dir --quiet
echo   基础依赖安装完成

echo.
echo [4/5] 安装语音识别依赖（首次需要几分钟）...
if not exist "%ROOT%venv\Lib\site-packages\faster_whisper" (
    "%ROOT%venv\Scripts\pip.exe" install faster-whisper --no-cache-dir --quiet
    echo   语音识别依赖安装完成
) else (
    echo   语音识别依赖已安装
)

echo.
echo [5/5] 安装完成！
echo.
echo  ========================================
echo   安装完成！正在启动程序...
echo  ========================================
echo.

REM 启动程序
set "PATH=%ROOT%ffmpeg;%PATH%"
set "QTWEBENGINE_DISABLE_SANDBOX=1"
cd /d "%ROOT%"
start http://localhost:8080
"%ROOT%venv\Scripts\python.exe" -m backend.main

pause
