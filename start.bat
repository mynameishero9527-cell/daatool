@echo off
chcp 65001 >nul
setlocal
title A股量化工具
cd /d "%~dp0"

call "%~dp0scripts\env.bat" :venv
if errorlevel 1 goto :fail
call "%~dp0scripts\env.bat" :deps
if errorlevel 1 goto :fail

"%PY%" scripts\port_in_use.py %QUANT_PORT%
if errorlevel 1 goto :launch
echo 服务已在运行，直接打开浏览器：http://127.0.0.1:%QUANT_PORT%
start "" "http://127.0.0.1:%QUANT_PORT%"
goto :end

:launch
"%PY%" scripts\launcher_log.py INFO 启动服务 port=%QUANT_PORT%
"%PY%" scripts\startup_report.py --port %QUANT_PORT%
echo.
echo ============================================================
echo  服务地址: http://127.0.0.1:%QUANT_PORT%
echo  日志目录: data\logs    报告目录: data\reports
echo  停止服务: 关闭本窗口，或按 Ctrl+C
echo ============================================================
echo.
set "QUANT_OPEN_BROWSER=1"
"%PY%" run.py
set "RC=%errorlevel%"
if "%RC%"=="0" goto :stopped

"%PY%" scripts\launcher_log.py ERROR 服务异常退出 exit_code=%RC%
echo.
echo [错误] 服务异常退出，退出码 %RC%，正在生成异常日志报告...
"%PY%" scripts\check_logs.py --days 1 --port %QUANT_PORT%
pause
exit /b %RC%

:stopped
"%PY%" scripts\launcher_log.py INFO 服务已停止
goto :end

:fail
echo.
echo 启动失败，请根据上方提示处理后重新运行 start.bat
pause
exit /b 1

:end
endlocal
