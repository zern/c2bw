[app]

# (str) 应用标题
title = 智能图像预处理工具

# (str) 包名（小写字母，无空格与特殊符号）
package.name = c2bw

# (str) 包域名（反向域名规范）
package.domain = org.shuge

# (str) 源代码根目录
source.dir = .

# (list) 包含的文件扩展名
source.include_exts = py,png,jpg,jpeg,ico,html,js,css,json,ttf,woff,woff2

# (list) 包含的子目录与模式
source.include_patterns = c2bw/*,webui/*,webui/**/*

# (str) 应用版本号
version = 3.5

# (list) Python 运行依赖库
# 注意：pillow 和 numpy 在 p4a 中有官方 C/C++ 交叉编译 recipe，必须保留以支持 ARM64
requirements = python3,flask,pillow,numpy,pypdf,pyjnius

# (str) 应用图标
icon.filename = %(source.dir)s/hanji.ico

# (str) 屏幕方向 (portrait, landscape, sensorLandscape, all)
orientation = all

# (list) Android 系统权限
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE

# (int) 目标 Android API 级别
android.api = 33

# (int) 最小支持 Android API 级别 (Android 7.0+)
android.minapi = 24

# (str) NDK 版本 (推荐 25b)
android.ndk = 25b

# (bool) 保持屏幕常亮以防批处理大图时息屏休眠
android.wakelock = True

# (list) 支持的 CPU 架构 (目前绝大多数现代安卓机为主流 arm64-v8a)
android.archs = arm64-v8a, armeabi-v7a

# (str) python-for-android 启动器模板：webview
p4a.bootstrap = webview

# (str) webview 加载的起始 URL（连接到本地 Flask 服务）
p4a.port = 5000

# (bool) 是否允许备份
android.allow_backup = True

# (bool) 是否支持全屏
fullscreen = 0

[buildozer]

# (int) 日志输出级别 (0 = 仅错误, 1 = 信息, 2 = 调试)
log_level = 2

# (int) 警告级别
warn_on_root = 1
