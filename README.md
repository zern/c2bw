# c2bw 智能图像预处理工具

![c2bw 智能图像预处理工具](webui.jpg)

## 一、项目简介

`c2bw`（Color to Black & White）是一款高效、专业的批量图像与扫描文档预处理工具，主要用于书籍扫描、文献归档中的图片双页裁切、黑白二值化、无损原图提取以及高清紧凑 PDF 汇总。

当前版本：**v3.7**

> 📖 **[点击查看：单文件版本详细用户操作使用说明 (简体中文)](使用说明.md) | [點擊查看：單文件版本詳細用戶操作使用說明 (繁體中文)](使用說明.md)**

### 核心特性

- **双工作模式支持**：
  - **从图片目录开始处理**：支持单目录或递归遍历子文件夹，保持原有目录层级结构；
  - **从 PDF 文件开始处理**：直接读取图片打包型 PDF（扫描件、古籍、插画等），无损提取原始分页图片，流水线式执行预处理与重构。
- **丰富的图像格式兼容**：
  - 批量读取与处理 JPG、JPEG、PNG、TIF、TIFF、BMP、JP2 等主流格式；
- **智能分页与裁切**：
  - 双页跨页扫描图自动分割为单页，支持自定义分割中线比例（1%~100%）；
  - 支持从右至左（古籍/竖排版）与从左至右（现代横排版）阅读顺序；
  - 具备宽高比智能识别，自动跳过封面、单页插图等无需裁切的页面；
  - **直观动态线框视觉**：实时展示单页判定盒形，双页裁切精准展示左右各裁切 $P\%$ 宽与中间重复重叠区域（保护装订中缝内容）。
- **高质量黑白二值化**：
  - 提供 OTSU 自适应阈值算法与 0~100% 自定义阈值二值化；
  - 二值化结果输出为标准 1 位 Group 4 TIFF，极佳保留笔画细节。
- **高效 PDF 流封装（极小体积、拒绝反相）**：
  - 黑白二值图打包 PDF 采用底层 CCITT Group 4（`/CCITTFaxDecode`，2-D）直接压缩封装，体积较常规 PDF 打包缩减 99% 以上；
  - 严格校准极性参数（`/BlackIs1`），彻底修复主流阅读器（Adobe Acrobat、Chrome、Edge、SumatraPDF 等）中的黑白反相问题，呈现完美白底黑字；
  - 彩色/灰度图片智能采用 DCT/JPEG 直通压缩流，不损失画质；
  - 输出 PDF 自动设置适合窗口单页视图（`/Fit` + `/SinglePage`）。
- **灵活的生成与清理控制**：
  - **PDF 模式 - 不转换为PDF**：仅输出处理后的图片文件夹，不合成新 PDF；
  - **PDF 模式 - 纯提取模式**：勾选「不转换为PDF」且未勾选裁切与二值化时，自动生成与 PDF 同名的目录，直接无损提取内部所有原始分页图片；
  - **图片目录模式 - 清理控制**：合成 PDF 后默认自动清理处理后的中间图片（仅保留最终 PDF）；勾选「合成PDF后保留处理后的图片」可同时保留图片和 PDF。
- **结构化任务日志报告**：
  - 任务结束后弹窗呈现完整汇总，并自动在输出目录导出 UTF-8 `task_report.txt`；
  - 完整记录设定参数、原始图片/分页数、转换后图片总量（清晰标注排除单页数与裁切双页数）。
- **全系统多语言（i18n）国际化**：
  - 原生支持 **简体中文**、**繁體中文**、**日本語**、**English** 四种语言；
  - 启动时根据操作系统内核语言环境自动适配；未匹配时默认使用简体中文；
  - 界面右上角提供一键语言切换菜单，支持记忆用户自选偏好；
  - 任务运行实时日志、状态提示与输出目录生成的任务日志报告（`task_report.txt`）均自动适配对应语言。
- **色彩处理关闭时的文件大小优化体系**：
  - **原大图片（无优化）**：保留原始文件格式与图像尺寸，未裁切图片完整保留原文件；
  - **精简尺寸（适合手机）**：在裁切前使用最优算法（Lanczos）将图片等比缩小至 2160px 宽（宽度 ≤2160px 不放大），输出为质量 75 的渐进式（Progressive）JPEG，兼顾高清晰度与极佳的文件压缩率；
  - **自定义参数**：自由设置图片缩放比例（80%、60%、50%、40%、30%、20%）及渐进式 JPEG 质量（默认 80）。
- **现代化双界面与高可用容灾**：
  - 基于 Vite + Vue 3 + Element Plus 的现代化响应式本地图形界面；
  - 具备多线程并发、实时进度反馈、任务暂停、继续与安全取消机制；
  - **老系统自动降级保护**：Windows 7 下自动匹配 IE11/MSHTML；若缺少 .NET 4.0 或浏览器组件受损，无感自动降级启动原生 Tkinter 桌面窗口；支持命令行 `--tk` 直接进入原生界面。

---

## 二、项目结构

```text
c2bw.py                 主程序（图像处理核心算法、PDF 底层流封装与服务调度）
run_c2bw.py             单文件打包启动入口（freeze_support 与主进程防重复保护）
finalize_build.py       多版本编译产物命名分发与归档脚本
使用说明.md             单文件版本用户操作使用说明（简体中文）
使用說明.md             單文件版本用戶操作使用說明（繁體中文）
frontend/               Vite + Vue 3 + Element Plus 前端项目源码
webui/                  前端编译产物静态资源（打包时内嵌到可执行程序中）
c2bw.spec               Windows 10/11 标准版打包配置
c2bw_win7.spec          Windows 7 专用独立版打包配置
c2bw_macos.spec         macOS (Intel & Apple Silicon) 单文件打包配置
c2bw_linux.spec         Linux (x64) 单文件打包配置
build_win11.bat         Windows 10/11 默认版一键自动化构建脚本
build_win7.bat          Windows 7 专用独立版一键自动化构建脚本
requirements.txt        标准运行依赖
requirements-win7.txt   Windows 7 兼容构建依赖
version_info.txt        Windows 可执行文件版本元数据
.github/workflows/      GitHub Actions 自动化构建与发布工作流
  build-and-release.yml 多平台单文件 CI/CD 工作流
```

---

## 三、直接运行源码

建议使用 Python 3.8 及以上版本（Windows 7 请使用 CPython 3.8.x x64）。

进入项目根目录：

### Windows：

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python c2bw.py
```

若希望在旧系统上直接以原生 Tkinter 界面运行，可附带 `--tk` 参数：

```bat
python c2bw.py --tk
```

### Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python c2bw.py
```

---

## 四、Windows 编译可执行程序

### 1. Windows 10 / 11 默认版（推荐）

直接双击运行自动化构建脚本：

```bat
build_win11.bat
```

脚本将自动执行前端 Vite 打包、准备运行时依赖、调用 PyInstaller 封装并在 `dist\` 生成以下文件：
- `dist\智能图像预处理工具 v3.7.exe`
- `dist\智能图像预处理工具 v3.7_Win11.exe`
- `dist\c2bw_win11.exe`
- `dist\c2bw-windows-x64.exe`

### 2. Windows 7 专用独立版（兼容老旧系统）

Windows 7 环境建议使用 CPython 3.8.x x64，直接双击运行自动化脚本：

```bat
build_win7.bat
```

生成产物位于 `dist\`：
- `dist\智能图像预处理工具 v3.7_Win7.exe`
- `dist\c2bw_win7.exe`
- `dist\c2bw_v3.7_win7.exe`

---

## 五、跨平台单文件构建与 GitHub Actions CI/CD

本项目配置了完整的 GitHub Actions 自动化持续集成与发布流水线（`.github/workflows/build-and-release.yml`），原生支持跨平台自动化构建无依赖单文件：

### 1. 预编译单文件下载 (GitHub Releases)
- **Windows 10 / 11 (x64)**：`c2bw-windows-x64.exe`
- **Windows 7 独立版 (x64)**：`c2bw-windows7-x64.exe` (内嵌 .NET 运行时)
- **macOS Intel (x86_64)**：`c2bw-macos-x86_64` (适配 Intel 处理器 Mac)
- **macOS Apple Silicon (arm64)**：`c2bw-macos-arm64` (适配 M1/M2/M3/M4 芯片 Mac)
- **Linux (x64)**：`c2bw-linux-x64` (适配主流 Linux 发行版，glibc >= 2.35)

> **触发机制**：
> - **自动发布**：向仓库推送版本标签（如 `git tag v3.7 && git push origin v3.7`）时，自动触发全平台并行编译并直接创建 GitHub Release 附带全部二进制文件及 `SHA256SUMS.txt` 校验清单；
> - **手动触发**：亦可在 GitHub 仓库的 `Actions` 页面选择 `Build and Release Multi-Platform Binaries` 手动一键运行测试。

### 2. 本地手工构建命令

#### Linux：
```bash
sudo apt-get install -y libgirepository1.0-dev libcairo2-dev gir1.2-gtk-3.0 gir1.2-webkit2-4.0
python3 -m venv .venv-build && source .venv-build/bin/activate
python -m pip install PyGObject -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean c2bw_linux.spec
```

#### macOS (Intel / Apple Silicon)：
```bash
python3 -m venv .venv-build && source .venv-build/bin/activate
python -m pip install -r requirements.txt pyinstaller
python -m pip install pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-WebKit
python -m PyInstaller --noconfirm --clean c2bw_macos.spec
```

---

## 六、注意事项与常见问题

1. **二值化 PDF 体积与黑白反相说明**：
   - 本工具使用底层 `pypdf` 直接封装 1 位 CCITT Group 4 传真压缩数据流，生成的二值化 PDF 体积极其轻巧（百页仅数百 KB）；
   - 彻底解决传统工具将黑白二值图转为 RGB/灰度再封装导致的体积暴增和反相问题。
2. **Windows 7 运行说明**：
   - Windows 7 需安装 SP1 及补丁（KB2533623 或 KB3063858）；
   - Win7 独立版自带运行时，优先通过 MSHTML 启动现代界面；若系统环境不支持，将自动启动原生 Tkinter 界面，无需额外配置。
3. **输出目录安全限制**：
   - 输出目录不能与输入目录相同，也不能是输入目录的父目录，避免覆盖或误删输入原图。
4. **清理规则**：
   - PDF 模式下，完成新 PDF 生成后会自动清理临时提取的分页图片；若勾选「不转换为PDF」，则会保留处理好的图片文件夹并自动删除中间临时原图。
