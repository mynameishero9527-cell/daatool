@echo off
chcp 65001 >nul
setlocal
title A股量化工具 - 异常日志检查
cd /d "%~dp0"
rem 用法: check_logs.bat [天数]，默认统计最近 7 天

call "%~dp0scripts\env.bat" :venv
if errorlevel 1 goto :fail

set "DAYS=%~1"
if "%DAYS%"=="" set "DAYS=7"

"%PY%" scripts\check_logs.py --days %DAYS%
if exist "%REPORTDIR%\log_report_latest.txt" start "" notepad "%REPORTDIR%\log_report_latest.txt"
pause
exit /b 0

:fail
pause
exit /b 1
