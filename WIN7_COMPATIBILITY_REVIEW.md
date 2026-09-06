# Windows 7 兼容性复核（2026-09-05）

结论：当前产物的应用代码与本机功能测试通过，但 Windows 7 兼容性尚未得到验证。不能把“使用 Python 3.8 构建成功”作为“Windows 7 实机测试通过”。此前对产物的兼容性表述应以本报告为准。

## 检查对象与环境

- 产物：`dist/c2bw_win7.exe`，当前版本 3.0.0.0（文件大小和构建时间以实际生成文件为准）。
- EXE SHA-256：`18ec78b4dd5d126d7c8604a98fa0dadd69c7aeb5e74e25c63285b375742e6efc`。
- 源码 SHA-256：`ebfbe2092f4f24e48b5b4d9b2d073a8ea09025a3bda0956686c93ae743fe5e22`。
- 检查主机：Windows build 26100（Windows 11；Python 的 platform 字符串显示 Windows-10-10.0.26100-SP0）。本次没有可用的 Windows 7 测试环境。
- 实际构建环境：CPython 3.8.6 x64、NumPy 1.24.4、Pillow 9.5.0、pypdf 3.17.4、PyInstaller 4.10、Tcl 8.6.9。不是构建文档推荐的 CPython 3.8.10。

## 已完成的检查

| 项目 | 结果及边界 |
| --- | --- |
| Python 3.8 语法 | 通过解析检查；EXE 内的应用字节码与当前源码编译结果一致。 |
| 可执行文件与原生依赖 | 检查了 EXE 和内嵌的 85 个 DLL/PYD，共 86 个 PE 文件；均为 x64。当前 EXE 不能用于 32 位 Windows 7。 |
| EXE 清单 | 包含 Windows 7 supportedOS 声明，执行权限为 asInvoker。声明本身不能证明依赖兼容。 |
| 运行库与接口 | 检查了 PE 头、导入表、延迟导入及转发 DLL；抽查的较新 API 名称没有出现在静态导入中。这不是完整的 Windows 7 导出表比对，也不覆盖动态加载和实际运行行为。 |
| 图像与 PDF 功能 | 在本机 Python 3.8.6 环境加载 EXE 内的应用字节码，使用本地构建环境的依赖运行：8 线程处理 8 个输入文件、7 种扩展名（JPG/JPEG/PNG/TIF/TIFF/BMP/JP2），包含中文目录；得到 16 个 600×700、1 位、Group 4 TIFF 页面和 2 个各 8 页的 PDF，无 `.part` 残留。 |
| PDF 属性 | 两个 PDF 的 Creator/Producer 均为 SHUGE.ORG，单页布局，打开适合页面。 |
| JPEG 仅裁切 | 使用质量 85、subsampling=2 的样本；两个输出页的量化表与色度抽样均与源图一致。 |
| 完成后打开目录 | 验证了完成回调与 os.startfile 调用的参数，系统打开操作使用模拟对象；未实际启动资源管理器。 |

上述功能检查没有运行完整冻结 EXE 的启动器，也没有在 Windows 7 上执行。界面显示、Win7 DLL 加载、运行库缺失时的行为仍待目标系统实测。

## 需要处理的兼容性风险

### 1. 打包运行库来源未受到限制

包内 `ucrtbase.dll` 及 43 个 `api-ms-win-*.dll` 为 10.0.26100.4654。APISet 文件的 PE 操作系统版本和子系统版本字段为 10.0；`ucrtbase.dll` 的操作系统版本字段为 10.0、子系统版本字段为 5.2。

`build/c2bw_win7/Analysis-00.toc` 的第 1426–1427、1443–1444、1488–1489 行分别记录了部分 APISet DLL 和 UCRT 的来源：

`C:\Users\zern\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\libheif\libheif\bin\`

抽样比对包内 `ucrtbase.dll`、`api-ms-win-crt-runtime-l1-1-0.dll`、`api-ms-win-core-sysinfo-l1-2-0.dll` 与上述来源文件的 SHA-256，均完全一致。因此不是仅凭构建主机版本推测：构建确实收集到了项目外部工具缓存中的运行库。

这证明当前构建未隔离运行库来源，但仅凭 DLL 版本号或 PE 字段，不能断言该 EXE 在所有 Windows 7 环境中必然启动失败。当前没有这些 DLL 在 Win7 上完整加载成功的证据。

微软说明 Windows 10/11 优先使用系统 UCRT，即便应用附带本地副本；旧系统的加载方式与限制不同。因此本机运行成功不能验证包内 UCRT 在 Win7 上可用。应采用明确支持目标系统的运行库部署方式，并核验重新构建后的依赖来源。[微软 UCRT 部署说明](https://learn.microsoft.com/en-us/cpp/windows/universal-crt-deployment?view=msvc-170)

### 2. 安装前提和构建文档需要收紧

`WIN7_BUILD.md` 中笼统推荐“Visual C++ 2015–2022 x64 Redistributable”，缺少具体版本边界。不能引导 Win7 用户随意安装最新运行库；微软目前的最新 v14 下载页列出的系统支持范围不包括 Win7。应明确选择支持 Win7 的安装包版本。[微软运行库支持说明](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170)

微软的 UCRT 更新要求 Windows 7 SP1，不能以未安装 SP1 的 Windows 7 为兼容目标。当前产物还是 x64 专用，不能仅更改文件名就兼容 32 位系统。[微软 UCRT 部署说明](https://learn.microsoft.com/en-us/cpp/windows/universal-crt-deployment?view=msvc-170)

`build_win7.bat` 也没有逐步检查依赖安装和打包的退出码，失败后仍可能显示构建完成。这属于构建结果报告的缺陷，不能证明生成了正确的新产物。

### 3. 缺少目标系统验收

Python 3.8 的选择符合 Python 官方对 Win7 的版本建议，但 Python 兼容不等于所有打包依赖兼容。[Python Windows 支持说明](https://docs.python.org/3/using/windows.html#supported-windows-versions)

本机依赖导入和编码测试通过，不应据此单独断言 NumPy 1.24.4 或 Pillow 9.5.0 已在 Win7 实测通过，也没有充分证据要求本次立即更换这些版本。

## 建议的下一步验收

1. 在隔离的构建环境使用 Python 3.8 x64，固定依赖来源与运行库部署方式，避免从外部工具的目录收集 DLL；检查每一步退出状态及最终产物哈希。
2. 在 Windows 7 SP1 x64 真机或虚拟机上，对同一哈希的完整 EXE 做首次启动与功能测试；PyInstaller 官方也将直接在 Win7 构建列为处理 Windows 运行库兼容性的方案之一。[PyInstaller 4.10 Windows 部署说明](https://pyinstaller.org/en/v4.10/usage.html#windows)
3. 验收范围包括中文路径、8 线程裁切和二值化、JPEG 保持品质、按子目录生成 PDF、暂停与取消、完成后打开目录、低分辨率和高 DPI 下的滚动区域。
4. 单独验证长路径：当前临时文件名额外加入线程标识和 `.part` 后缀；EXE 的 longPathAware 声明不能视为 Windows 7 长路径问题已解决。

本次仅增加此复核报告；应用源码、依赖版本和 EXE 均未更改。
