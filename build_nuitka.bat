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
echo  [2/4] Preparing runtime dependencies...
echo ==========================================================
copy /y "%APPDATA%\Python\Python38\site-packages\pythonnet\runtime\Python.Runtime.dll" . >nul 2>&1

echo ==========================================================
echo  [3/4] Compiling with Nuitka into C++ Native Executable...
echo ==========================================================
set "NO_PROXY=*"
set "no_proxy=*"

"%PYTHON_EXE%" -m nuitka ^
    --standalone ^
    --onefile ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=hanji.ico ^
    --include-data-dir=webui=webui ^
    --include-data-file=hanji.ico=hanji.ico ^
    --include-data-dir="%APPDATA%\Python\Python38\site-packages\pythonnet\runtime"=pythonnet/runtime ^
    --include-data-dir="%APPDATA%\Python\Python38\site-packages\clr_loader\ffi\dlls"=clr_loader/ffi/dlls ^
    --output-dir=dist_nuitka ^
    --output-filename=c2bw_nuitka.exe ^
    --assume-yes-for-downloads ^
    run_c2bw.py

set BUILD_ERR=%ERRORLEVEL%
if exist "Python.Runtime.dll" del /f /q "Python.Runtime.dll" >nul 2>&1

if %BUILD_ERR% neq 0 (
    echo [ERROR] Nuitka build failed with error code: %BUILD_ERR%
    exit /b 1
)

echo ==========================================================
echo  [4/4] Finalizing Nuitka binaries...
echo ==========================================================
if not exist "dist" mkdir dist
"%PYTHON_EXE%" finalize_build.py nuitka

echo.
echo ==========================================================
echo  Nuitka Native C++ build complete:
echo  - dist\智能图像预处理工具 v3.7_Nuitka.exe
echo  - dist\c2bw_nuitka.exe
echo ==========================================================
