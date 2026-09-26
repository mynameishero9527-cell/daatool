@echo off
rem 公共环境：由 start.bat / update.bat / check_logs.bat 调用，不要单独运行。
rem 用法: call scripts\env.bat :venv          准备项目内虚拟环境，设置 PY
rem       call scripts\env.bat :deps [force]  requirements.txt 有变化或 force 时安装依赖
rem 虚拟环境、pip 缓存、临时文件、数据库、日志、报告全部落在项目目录内，不写 C 盘用户目录。

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "DATA=%ROOT%\data"
set "LOGDIR=%DATA%\logs"
set "REPORTDIR=%DATA%\reports"
set "VENV=%ROOT%\.venv"
set "TEMP=%DATA%\tmp"
set "TMP=%DATA%\tmp"
set "PIP_CACHE_DIR=%DATA%\cache\pip"
set "PYTHONPYCACHEPREFIX=%DATA%\cache\pycache"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
if not defined QUANT_PORT set "QUANT_PORT=8888"
rem 国内下载依赖慢时，去掉下一行开头的 rem 改用清华镜像
rem if not defined PIP_INDEX_URL set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"

for %%D in ("%DATA%" "%LOGDIR%" "%REPORTDIR%" "%TEMP%" "%PIP_CACHE_DIR%") do if not exist "%%~D" mkdir "%%~D"

set "PY="
if exist "%VENV%\Scripts\python.exe" set "PY=%VENV%\Scripts\python.exe"

if "%~1"=="" exit /b 0
goto %~1


:venv
if not defined PY goto :venv_create
"%PY%" -c "import sys" >nul 2>&1
if not errorlevel 1 exit /b 0
echo 虚拟环境不可用（可能 Python 被卸载或移动），正在重建...
rmdir /s /q "%VENV%"
set "PY="
:venv_create
call :find_base
if errorlevel 1 exit /b 1
echo 正在项目内创建虚拟环境 .venv ...
%BASEPY% -m venv "%VENV%"
if errorlevel 1 goto :venv_fail
set "PY=%VENV%\Scripts\python.exe"
exit /b 0
:venv_fail
echo [错误] 创建虚拟环境失败，请确认使用的是完整版 Python（不是 embeddable 压缩包）
exit /b 1


:find_base
set "BASEPY="
if exist "%ROOT%\runtime\python\python.exe" set BASEPY="%ROOT%\runtime\python\python.exe"
if defined BASEPY goto :check_base
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "BASEPY=py -3"
if defined BASEPY goto :check_base
python -c "import sys" >nul 2>&1
if not errorlevel 1 set "BASEPY=python"
if defined BASEPY goto :check_base
echo [错误] 未找到 Python 3.10 及以上版本。可任选一种方式：
echo   1. 安装 Python 到非 C 盘，例如 D:\Python312，安装时勾选 Add python.exe to PATH
echo   2. 把完整版 Python 目录放到本项目 runtime\python\ 下，确保存在 runtime\python\python.exe
exit /b 1
:check_base
%BASEPY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 exit /b 0
echo [错误] Python 版本低于 3.10：%BASEPY%
exit /b 1


:deps
set "REQ=%ROOT%\requirements.txt"
set "STAMP=%DATA%\cache\requirements.installed"
if /i "%~2"=="force" goto :deps_install
if not exist "%STAMP%" goto :deps_install
fc /b "%REQ%" "%STAMP%" >nul 2>&1
if not errorlevel 1 exit /b 0
:deps_install
echo 正在安装依赖，下载缓存在 data\cache\pip ...
"%PY%" -m pip install -r "%REQ%"
if errorlevel 1 goto :deps_fail
copy /y "%REQ%" "%STAMP%" >nul
exit /b 0
:deps_fail
echo [错误] 依赖安装失败，请检查网络；国内网络可在 scripts\env.bat 中启用清华镜像
exit /b 1
