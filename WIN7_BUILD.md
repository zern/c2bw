# Windows 7 兼容构建

当前发布版由 Python 3.14 打包，而 Python 3.14 仅支持 Windows 10 及更高版本。因此，若要支持 Windows 7，必须使用 Windows 7 兼容的 Python 3.8 环境重新打包，不能仅替换 `.exe` 文件。当前界面采用 Vue 2 + Element UI，并通过 pywebview 的 MSHTML 后端承载；Windows 7 上不会依赖 Edge/EdgeHTML。

## 构建步骤

1. 在 Windows 10 或 Windows 7 SP1 的 64 位环境安装 **CPython 3.8.10 x64**。为获得最可靠的兼容性，建议直接在 Windows 7 SP1 虚拟机中构建并测试。
2. 若 Python 未安装在 `C:\Python38\python.exe`，编辑 `build_win7.bat` 中的 `PYTHON38` 路径。
3. 双击运行 `build_win7.bat`。
4. 构建结果为 `dist\c2bw_win7.exe`。

## Windows 7 目标电脑要求

- Windows 7 SP1，且程序位数必须与系统位数一致（当前配置为 x64）。
- 安装 Microsoft Universal CRT 更新（KB2999226）或 Visual C++ 2015-2022 x64 Redistributable。缺少它时，程序可能在启动时提示找不到运行库。
- 建议安装 Internet Explorer 11（用于 MSHTML 界面渲染）及 .NET Framework 4.7.2 或更高版本（pythonnet/WinForms 后端依赖）。

`requirements-win7.txt` 固定了 Python 3.8 可用的 NumPy、Pillow、pywebview、pythonnet 和 PyInstaller 版本；不要用当前的 Python 3.14 环境构建 Windows 7 版本。构建脚本会把 PyInstaller 缓存放在项目目录，避免受系统缓存目录权限影响。
