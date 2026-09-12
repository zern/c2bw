@echo off
chcp 65001 >nul
setlocal

set "PYTHON_EXE=C:\Program Files\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Python38\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python environment not found: %PYTHON_EXE%
    exit /b 1
)

echo ==========================================================
echo  [1/4] Building Vue 3 + Element Plus Frontend (Vite)...
echo ==========================================================
if exist "frontend\package.json" (
    cd frontend
    call npm run build
    if errorlevel 1 (
        echo [ERROR] Vite frontend build failed!
        cd ..
        exit /b 1
    )
    cd ..
)

echo ==========================================================
echo  [2/4] Preparing Windows 11 build dependencies...
echo ==========================================================
copy /y "%APPDATA%\Python\Python38\site-packages\pythonnet\runtime\Python.Runtime.dll" . >nul 2>&1

echo ==========================================================
echo  [3/4] Building Windows 11 standalone executable (c2bw.spec)...
echo ==========================================================
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean c2bw.spec
set BUILD_ERR=%ERRORLEVEL%
if exist "Python.Runtime.dll" del /f /q "Python.Runtime.dll" >nul 2>&1

if %BUILD_ERR% neq 0 (
    echo [ERROR] Windows 11 build failed with error code: %BUILD_ERR%
    exit /b 1
)

echo ==========================================================
echo  [4/4] Finalizing Windows 11 binaries...
echo ==========================================================
if not exist "dist" mkdir dist
"%PYTHON_EXE%" finalize_build.py win11

echo.
echo ==========================================================
echo  Windows 11 build complete:
echo  - dist\智能图像预处理工具 v3.6.exe
echo  - dist\智能图像预处理工具 v3.6_Win11.exe
echo  - dist\c2bw_win11.exe
echo  - dist\c2bw-windows-x64.exe
echo ==========================================================
