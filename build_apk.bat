@echo off
setlocal
echo ==========================================================
echo 正在调用 WSL2 (Ubuntu 24.04) 执行 c2bw Android APK 编译...
echo ==========================================================
wsl -d Ubuntu-24.04 -- bash -c "dos2unix /mnt/d/codex/c2bw-main/build_apk_wsl.sh 2>/dev/null; bash /mnt/d/codex/c2bw-main/build_apk_wsl.sh"
if %ERRORLEVEL% equ 0 (
    echo.
    echo ==========================================================
    echo 构建成功！生成的 APK 已自动输出至 bin\ 目录。
    echo ==========================================================
) else (
    echo.
    echo ==========================================================
    echo 构建遇到异常，请根据上方详细日志进行排查。
    echo ==========================================================
)
pause
