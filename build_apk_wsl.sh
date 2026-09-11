#!/usr/bin/env bash
# ==============================================================================
# c2bw - Android APK 构建脚本 (运行于 WSL2 Ubuntu 24.04 环境)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$HOME/c2bw_build"

echo "=========================================================="
echo " [1/4] 同步项目源码至 WSL2 本地 ext4 文件系统..."
echo "       (避免 Windows NTFS 挂载点下的权限与符号链接异常)"
echo "=========================================================="
mkdir -p "$BUILD_DIR"
rsync -av --delete \
    --exclude '.venv*' \
    --exclude '.git' \
    --exclude 'dist' \
    --exclude 'build' \
    --exclude '.buildozer' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '*.part' \
    --exclude 'bin' \
    "$SCRIPT_DIR/" "$BUILD_DIR/"

cd "$BUILD_DIR"

echo "=========================================================="
echo " [2/4] 配置 Buildozer 构建环境变量..."
echo "=========================================================="
export PATH="$HOME/.local/bin:$PATH"
export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-amd64"

# 自动探测并配置 Windows 宿主机代理（解决国内访问 dl.google.com / github 下载 SDK/NDK 失败问题）
HOST_IP=$(ip route show default 2>/dev/null | awk '{print $3}')
if [ -z "$HOST_IP" ]; then
    HOST_IP="172.20.64.1"
fi

for port in 1080 7890 10808 10809; do
    if curl -x "http://$HOST_IP:$port" -I -s --connect-timeout 2 "https://dl.google.com" >/dev/null 2>&1; then
        echo "检测到宿主机网络代理可用 ($HOST_IP:$port)，正在配置环境变量加速 SDK 与 NDK 下载..."
        export http_proxy="http://$HOST_IP:$port"
        export https_proxy="http://$HOST_IP:$port"
        export all_proxy="socks5://$HOST_IP:$port"
        export HTTP_PROXY="http://$HOST_IP:$port"
        export HTTPS_PROXY="http://$HOST_IP:$port"
        break
    fi
done

echo "Buildozer 版本: $(buildozer version)"
echo "Java 版本:"
java -version

# 确保 numpy recipe 使用 release tarball 并注入 prebuild_arch 补丁（修复 Android NDK libc++ 缺失 unordered_map 头文件报错）
P4A_NUMPY_RECIPE="$BUILD_DIR/.buildozer/android/platform/python-for-android/pythonforandroid/recipes/numpy/__init__.py"
python3 -c "
import os
p = '$P4A_NUMPY_RECIPE'
if os.path.exists(p):
    t = open(p).read()
    t = t.replace('version = \"v2.3.0\"', 'version = \"2.3.0\"')
    t = t.replace('url = \"git+https://github.com/numpy/numpy\"', 'url = \"https://github.com/numpy/numpy/releases/download/v{version}/numpy-{version}.tar.gz\"')
    if 'def prebuild_arch' not in t:
        method = '''    def prebuild_arch(self, arch):
        super().prebuild_arch(arch)
        import os
        unique_cpp = os.path.join(self.get_build_dir(arch.arch), \"numpy\", \"_core\", \"src\", \"multiarray\", \"unique.cpp\")
        if os.path.exists(unique_cpp):
            with open(unique_cpp, \"r\", encoding=\"utf-8\") as f:
                c = f.read()
            if \"#include <unordered_map>\" not in c:
                c = c.replace(\"#include <unordered_set>\", \"#include <unordered_set>\\\\n#include <unordered_map>\")
                with open(unique_cpp, \"w\", encoding=\"utf-8\") as f:
                    f.write(c)

    def get_hostrecipe_env'''
        t = t.replace('    def get_hostrecipe_env', method)
    open(p, 'w').write(t)
" 2>/dev/null || true

find "$BUILD_DIR/.buildozer" -name "unique.cpp" -exec sed -i 's|#include <unordered_set>|#include <unordered_set>\n#include <unordered_map>|' {} + 2>/dev/null || true


# 修复 python-for-android build.py 中对 venv pip 进行自更新导致 pip 损坏的缺陷
P4A_BUILD_PY="$BUILD_DIR/.buildozer/android/platform/python-for-android/pythonforandroid/build.py"
python3 -c "
import os
p = '$P4A_BUILD_PY'
if os.path.exists(p):
    t = open(p).read()
    s = '\"source venv/bin/activate && pip install -U pip\"'
    if s in t:
        open(p, 'w').write(t.replace(s, '\"true\"'))
" 2>/dev/null || true



echo "=========================================================="
echo " [3/4] 开始执行 Buildozer 编译构建 APK..."
echo "       (首次编译会自动下载并解压 Android SDK 与 NDK)"
echo "=========================================================="
buildozer -v android debug

echo "=========================================================="
echo " [4/4] 复制生成的 APK 至 Windows 项目目录 (bin/)..."
echo "=========================================================="
mkdir -p "$SCRIPT_DIR/bin"
cp -vf "$BUILD_DIR/bin/"*.apk "$SCRIPT_DIR/bin/"

echo "=========================================================="
echo " 构建成功！APK 文件已保存至 Windows 目录:"
ls -lh "$SCRIPT_DIR/bin/"*.apk
echo "=========================================================="
