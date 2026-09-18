@echo off
title CAN & CANopen Studio - Installer
setlocal EnableDelayedExpansion

echo ============================================================
echo    CAN ^& CANopen Studio - Setup ^& Launcher Installation
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Installation encountered an issue (Exit code: %ERRORLEVEL%).
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo Press any key to exit this installer...
pause >nul
exit /b 0
