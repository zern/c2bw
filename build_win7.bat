@echo off
setlocal

set "PYTHON38=C:\Program Files\python\python.exe"
if not exist "%PYTHON38%" set "PYTHON38=C:\Python38\python.exe"
if not exist "%PYTHON38%" (
    echo 未找到 Python 3.8: %PYTHON38%
    echo 请安装 CPython 3.8 x64，并按实际路径修改本文件中的 PYTHON38。
    pause
    exit /b 1
)

echo 正在准备 Windows 7 构建依赖...
copy /y "%APPDATA%\Python\Python38\site-packages\pythonnet\runtime\Python.Runtime.dll" . >nul 2>&1
"%PYTHON38%" -m PyInstaller --noconfirm --clean c2bw_win7.spec
set BUILD_ERR=%ERRORLEVEL%
if exist "Python.Runtime.dll" del /f /q "Python.Runtime.dll" >nul 2>&1

if %BUILD_ERR% neq 0 (
    echo Windows 7 兼容构建失败。
    exit /b 1
)

copy /y "dist\智能图像预处理工具 v3.3_Win7.exe" "dist\c2bw_win7.exe" >nul 2>&1
copy /y "dist\智能图像预处理工具 v3.3_Win7.exe" "dist\c2bw_v3.3_win7.exe" >nul 2>&1

echo.
echo Windows 7 兼容构建完成：
echo - dist\智能图像预处理工具 v3.3_Win7.exe
echo - dist\c2bw_win7.exe
echo - dist\c2bw_v3.3_win7.exe
pause
