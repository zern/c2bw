@echo off
chcp 65001 >nul
setlocal

echo ==========================================================
echo  Building Standalone Executables (v4.0):
echo   1. Nuitka Native C++ Build (Win10 / Win11)
echo   2. Windows 7 Compatible Build (Win7 SP1)
echo ==========================================================
echo.

call build_nuitka.bat
if errorlevel 1 (
    echo [ERROR] Nuitka build failed!
    exit /b 1
)

echo.
call build_win7.bat
if errorlevel 1 (
    echo [ERROR] Windows 7 build failed!
    exit /b 1
)

echo.
echo ==========================================================
echo  Build complete! Output binaries:
echo   - dist\智能图像预处理工具 v4.0.exe
echo   - dist\智能图像预处理工具 v4.0_Win7.exe
echo   - dist\c2bw-v4.0.exe
echo   - dist\c2bw-v4.0_Win7.exe
echo ==========================================================
