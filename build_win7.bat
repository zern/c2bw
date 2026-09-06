@echo off
setlocal

set "PYTHON38=C:\Python38\python.exe"
if not exist "%PYTHON38%" (
    echo 未找到 %PYTHON38%
    echo 请安装 CPython 3.8.10 x64，并按实际路径修改本文件中的 PYTHON38。
    pause
    exit /b 1
)

"%PYTHON38%" -m venv .venv-win7
call .venv-win7\Scripts\activate.bat
set "PYINSTALLER_CONFIG_DIR=%CD%\.pyinstaller-cache-win7"
python -m pip install --upgrade "pip<24.1" "setuptools<60.7" "wheel<0.46"
if errorlevel 1 exit /b 1
python -m pip install -r requirements-win7.txt
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean c2bw_win7.spec
if errorlevel 1 (
    echo Windows 7 兼容构建失败。
    exit /b 1
)

echo.
echo Windows 7 兼容构建完成：dist\c2bw_win7.exe
pause
