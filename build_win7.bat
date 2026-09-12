@echo off
chcp 65001 >nul
setlocal

set "PYTHON38=C:\Program Files\python\python.exe"
if not exist "%PYTHON38%" set "PYTHON38=C:\Python38\python.exe"
if not exist "%PYTHON38%" (
    echo [ERROR] Python 3.8 not found: %PYTHON38%
    exit /b 1
)

echo ==========================================================
echo  [1/3] Preparing Windows 7 build dependencies...
echo ==========================================================
copy /y "%APPDATA%\Python\Python38\site-packages\pythonnet\runtime\Python.Runtime.dll" . >nul 2>&1

echo ==========================================================
echo  [2/3] Building Windows 7 standalone executable (c2bw_win7.spec)...
echo ==========================================================
"%PYTHON38%" -m PyInstaller --noconfirm --clean c2bw_win7.spec
set BUILD_ERR=%ERRORLEVEL%
if exist "Python.Runtime.dll" del /f /q "Python.Runtime.dll" >nul 2>&1

if %BUILD_ERR% neq 0 (
    echo [ERROR] Windows 7 build failed with error code: %BUILD_ERR%
    exit /b 1
)

echo ==========================================================
echo  [3/3] Finalizing Windows 7 binaries...
echo ==========================================================
"%PYTHON38%" finalize_build.py win7

echo.
echo ==========================================================
echo  Windows 7 build complete:
echo  - dist\智能图像预处理工具 v3.6_Win7.exe
echo  - dist\c2bw_win7.exe
echo  - dist\c2bw_v3.6_win7.exe
echo ==========================================================
