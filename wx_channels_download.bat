@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "SCRIPT=%~dp0wx_channels_download.py"
if not exist "%SCRIPT%" (
    echo 找不到 wx_channels_download.py，请把它和本文件放在同一目录。
    goto :end
)

set "PY="

where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)

if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=python"
    )
)

if not defined PY (
    where python3 >nul 2>&1
    if not errorlevel 1 (
        python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=python3"
    )
)

if not defined PY (
    echo 未找到 Python 3.8 或更高版本。
    echo 请先安装 Python：https://www.python.org/downloads/
    echo 安装时请勾选 "Add python.exe to PATH"。
    echo 本工具不会自动安装任何软件包。
    goto :end
)

%PY% "%SCRIPT%" menu
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
    echo.
    echo 程序退出代码：%ERR%
)

:end
echo.
pause
endlocal
