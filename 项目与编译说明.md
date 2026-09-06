# c2bw 项目简要说明与跨平台编译指南

## 一、项目简介

`c2bw` 是一款批量图像预处理工具，主要用于扫描图片的分页裁切、黑白二值化和 PDF 汇总。

当前版本：**3.0**

主要功能：

- 批量处理 JPG、JPEG、PNG、TIF、TIFF、BMP、JP2 图片
- 双页图片自动裁切为左右两个页面
- OTSU 自适应或自定义阈值黑白二值化
- 多线程处理、暂停、继续和取消任务
- 支持处理子文件夹并保持目录结构
- 自动生成单个或按子文件夹分别生成 PDF
- PDF 默认单页布局、适合页面显示
- 任务完成后打开输出目录
- 从服务器读取版本更新信息并显示更新提醒
- Vue 2 + Element UI 界面，通过 pywebview 调用 Python 本地处理服务

## 二、项目结构

```text
c2bw.py                 Python 图像处理、PDF 生成和本地服务
webui/                  Vue 2 + Element UI 前端资源
  index.html            界面结构
  app.js                Vue 业务逻辑和 pywebview 调用
  style.css             界面样式
  vendor/               Vue、Element UI 和字体等离线资源
c2bw.spec               Windows 10/11 等新系统打包配置
c2bw_win7.spec         Windows 7 兼容打包配置
build_win7.bat         Windows 7 自动构建脚本
requirements.txt       常规环境依赖
requirements-win7.txt  Windows 7 构建依赖
version_info.txt       Windows 文件版本信息
使用说明.md             用户操作说明
```

## 三、直接运行源码

建议使用 Python 3.8 及以上版本。进入项目目录后执行：

```bash
python -m venv .venv
```

Windows：

```bat
.venv\Scripts\activate
python -m pip install -r requirements.txt
python c2bw.py
```

Linux/macOS：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python c2bw.py
```

## 四、Windows 编译单文件程序

### Windows 10/11

```bat
python -m venv .venv-build
.venv-build\Scripts\activate
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean c2bw.spec
```

生成文件通常位于：

```text
dist\c2bw.exe
```

### Windows 7

Windows 7 必须使用 CPython 3.8 x64，并建议在 Windows 7 SP1 环境中构建：

```bat
build_win7.bat
```

或手动执行：

```bat
python -m venv .venv-win7
.venv-win7\Scripts\activate
python -m pip install -r requirements-win7.txt
python -m PyInstaller --noconfirm --clean c2bw_win7.spec
```

生成文件：`dist\c2bw_win7.exe`。

## 五、Linux 编译单文件程序

PyInstaller 需要在 Linux 系统中构建 Linux 程序，不能直接在 Windows 上交叉编译。

```bash
python3 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name c2bw \
  --add-data "webui:webui" \
  c2bw.py
```

生成文件：`dist/c2bw`。

运行：

```bash
chmod +x dist/c2bw
./dist/c2bw
```

Linux 还需要安装 pywebview 所需的 GTK/WebKit 运行库，具体包名按发行版安装。

## 六、macOS 编译单文件程序

PyInstaller 需要在 macOS 系统中构建 macOS 程序。建议使用与目标电脑相同的 CPU 架构构建（Intel 或 Apple Silicon）。

```bash
python3 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name c2bw \
  --add-data "webui:webui" \
  c2bw.py
```

生成文件：`dist/c2bw`。

如需生成 macOS 应用包，可去掉 `--onefile` 并使用：

```bash
python -m PyInstaller --noconfirm --clean --windowed \
  --name c2bw \
  --add-data "webui:webui" \
  c2bw.py
```

生成：`dist/c2bw.app`。

## 七、跨平台编译注意事项

- PyInstaller 不能把 Windows 程序直接编译成 Linux 或 macOS 程序，三类系统应分别构建。
- `--add-data` 的分隔符不同：Linux/macOS 使用冒号 `:`，Windows 使用分号 `;`。
- 首次启动时，pywebview 需要系统可用的网页渲染后端。
- 服务器版本检查失败时会静默跳过，不影响图像处理。
- 发布前应在目标系统测试目录选择、图片处理、PDF 输出和打开输出目录功能。
- 发布单文件前建议删除旧的 `build`、`dist` 目录，避免误用旧产物。
