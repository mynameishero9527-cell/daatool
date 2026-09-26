@echo off
chcp 65001 >nul
setlocal
title A股量化工具 - 更新
rem git pull 可能改写本文件，cmd 边读边执行会错位，所以先复制到 data\tmp 再从副本运行
if /i "%~1"=="--from-copy" goto :from_copy
if not exist "%~dp0data\tmp" mkdir "%~dp0data\tmp"
copy /y "%~f0" "%~dp0data\tmp\update_run.bat" >nul
"%~dp0data\tmp\update_run.bat" --from-copy "%~dp0."

:from_copy
cd /d "%~f2"
call "%CD%\scripts\env.bat" :venv
if errorlevel 1 goto :fail
set "ULOG=%LOGDIR%\update.log"
set "OUT=%TEMP%\update_step.out"

"%PY%" scripts\port_in_use.py %QUANT_PORT%
if errorlevel 1 goto :begin
echo [提示] 检测到服务正在运行（端口 %QUANT_PORT%），请先关闭 start.bat 窗口再更新。
goto :fail

:begin
"%PY%" scripts\launcher_log.py INFO 开始更新
>>"%ULOG%" echo ==== 更新开始 %date% %time% ====

echo [1/3] 备份数据库到 data\backup ...
"%PY%" scripts\backup_db.py
if errorlevel 1 goto :fail_logged

echo [2/3] 拉取最新代码 ...
if not exist "%ROOT%\.git" goto :no_git
git --version >nul 2>&1 || goto :no_git
for /f "delims=" %%V in ('git log -1 --format^=%%h') do set "OLDREV=%%V"
git pull --ff-only >"%OUT%" 2>&1
set "RC=%errorlevel%"
type "%OUT%"
type "%OUT%" >>"%ULOG%"
if not "%RC%"=="0" goto :git_fail
for /f "delims=" %%V in ('git log -1 --format^=%%h') do set "NEWREV=%%V"
echo 代码版本: %OLDREV% -^> %NEWREV%
"%PY%" scripts\launcher_log.py INFO 代码更新 %OLDREV% -^> %NEWREV%
goto :deps

:no_git
echo 未检测到 git 仓库或未安装 git，跳过代码拉取；如需更新请手动覆盖代码后再运行本脚本
"%PY%" scripts\launcher_log.py WARNING 跳过 git pull：无 git 仓库或未安装 git
goto :deps

:git_fail
echo [错误] 拉取代码失败（可能有本地修改冲突或网络问题），详情见 data\logs\update.log
"%PY%" scripts\launcher_log.py ERROR git pull 失败 exit_code=%RC%
goto :fail

:deps
echo [3/3] 更新依赖 ...
call "%ROOT%\scripts\env.bat" :deps force >"%OUT%" 2>&1
set "RC=%errorlevel%"
type "%OUT%"
type "%OUT%" >>"%ULOG%"
if not "%RC%"=="0" goto :fail_logged

"%PY%" scripts\launcher_log.py INFO 更新完成
>>"%ULOG%" echo ==== 更新完成 %date% %time% ====
echo.
echo 更新完成，双击 start.bat 启动。
pause
exit /b 0

:fail_logged
"%PY%" scripts\launcher_log.py ERROR 更新失败
:fail
echo.
echo 更新未完成，可运行 check_logs.bat 查看异常日志报告
pause
exit /b 1
