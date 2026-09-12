"""
c2bw.desktop - 桌面端宿主模块
包含：Pywebview 窗口管理、Windows COM 拖拽路径解析、Tkinter 容灾回退 UI 及桌面启动入口 main()
"""

import os
import sys
import io
import shutil
import tempfile
import threading
import queue
import re
import json
import webbrowser
import socket
import webview

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont



from c2bw.core import (
    PDF_APPLICATION_NAME,
    PDF_SPEC_VERSION,
    extract_images_from_pdf,
    get_task_suffix,
    BACKEND_LOGS,
    REPORT_TEXTS,
    get_backend_text,
    get_system_language,
    calculate_otsu_threshold,
    parse_jp2_dpi,
    get_normalized_dpi,
    get_jpeg_save_options,
    save_image_atomically,
    copy_file_atomically,
    save_image,
    remove_outputs,
    make_result,
    process_single_image,
    is_same_or_parent,
    natural_sort_key,
    assign_output_stems,
    completion_text,
    build_task_report,
    add_image_page_to_pdf_writer,
    build_single_pdf,
    set_pdf_open_to_fit_page,
    clean_cancelled_output,
)
from c2bw.service import (
    APP_TITLES,
    QUIT_CONFIRMATIONS,
    UPDATE_INFO_URL,
    get_config_dir,
    get_config_path,
    load_user_config,
    save_user_config,
    get_saved_language,
    save_user_language,
    ImageProcessorService,
    WebImageProcessorService,
)


def _resolve_system_dropped_path(raw_input):
    """若仅拖入文件名或受沙箱保护的相对标识，尝试在系统资源管理器、桌面或常用目录中解析绝对路径。"""
    if not raw_input:
        return ''
    raw_input = str(raw_input).strip().strip('"').strip("'")
    if not raw_input:
        return ''
    if os.path.exists(raw_input):
        return os.path.abspath(raw_input)

    name = os.path.basename(raw_input) if ('/' in raw_input or '\\' in raw_input) else raw_input
    name_lower = name.lower()

    # 1. Windows 环境：遍历系统资源管理器窗口，寻找选中项或同名文件/目录
    if sys.platform == 'win32':
        com_initialized = False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            com_initialized = True
        except Exception:
            pass

        try:
            import win32com.client
            shell = win32com.client.Dispatch("Shell.Application")
            windows = shell.Windows()
            # 优先匹配资源管理器窗口当前选中的项目
            for w in windows:
                try:
                    doc = w.Document
                    sel = doc.SelectedItems()
                    for i in range(sel.Count):
                        item_path = sel.Item(i).Path
                        if os.path.basename(item_path).lower() == name_lower:
                            if os.path.exists(item_path):
                                return os.path.abspath(item_path)
                except Exception:
                    pass

            # 其次匹配已打开的资源管理器目录下的同名文件/目录
            for w in windows:
                try:
                    doc = w.Document
                    folder_path = doc.Folder.Self.Path
                    candidate = os.path.join(folder_path, name)
                    if os.path.exists(candidate):
                        return os.path.abspath(candidate)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if com_initialized:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    # 2. 检查常见系统目录：桌面、下载、文档、公用桌面、程序当前工作目录
    candidates = []
    user_home = os.path.expanduser('~')
    candidates.append(os.path.join(user_home, 'Desktop'))
    candidates.append(os.path.join(user_home, 'Downloads'))
    candidates.append(os.path.join(user_home, 'Documents'))
    public_dir = os.environ.get('PUBLIC', 'C:\\Users\\Public')
    candidates.append(os.path.join(public_dir, 'Desktop'))
    candidates.append(os.getcwd())

    for base in candidates:
        if base:
            candidate = os.path.join(base, name)
            if os.path.exists(candidate):
                return os.path.abspath(candidate)

    return raw_input


class WebImageProcessorBridge:
    """仅暴露给 JavaScript 的窄接口，适配 pywebview 反射。"""

    def __init__(self, service):
        self.service = service

    def handle_dropped_path(self, path):
        resolved_path = _resolve_system_dropped_path(path)
        if not resolved_path or not os.path.exists(resolved_path):
            raw_display = str(path or '').strip()
            return {'ok': False, 'error': f'未能识别或访问拖拽的路径：{raw_display}'}

        resolved_path = os.path.abspath(resolved_path)
        if os.path.isdir(resolved_path):
            suggested_target = os.path.join(resolved_path, 'output')
            return {
                'ok': True,
                'type': 'dir',
                'path': resolved_path,
                'suggested_target_dir': suggested_target,
            }

        if os.path.isfile(resolved_path):
            ext = os.path.splitext(resolved_path)[1].lower()
            if ext == '.pdf':
                pdf_dir = os.path.dirname(resolved_path)
                pdf_name = os.path.splitext(os.path.basename(resolved_path))[0]
                return {
                    'ok': True,
                    'type': 'pdf',
                    'path': resolved_path,
                    'pdf_dir': pdf_dir,
                    'pdf_name': pdf_name,
                }
            elif ext in ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2'):
                parent_dir = os.path.dirname(resolved_path)
                suggested_target = os.path.join(parent_dir, 'output')
                return {
                    'ok': True,
                    'type': 'image',
                    'path': resolved_path,
                    'parent_dir': parent_dir,
                    'suggested_target_dir': suggested_target,
                }
            else:
                return {'ok': False, 'error': f'不支持的文件格式：{ext}（请拖入图片目录、PDF 文件或图片文件）'}

        return {'ok': False, 'error': '未知的路径类型。'}

    def choose_directory(self, initial_directory=''):
        if not self.service.window:
            return {'ok': False, 'error': '窗口尚未初始化。'}
        try:
            selected = self.service.window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=initial_directory or '',
                allow_multiple=False,
            )
            return {'ok': True, 'path': selected[0] if selected else ''}
        except Exception as e:
            return {'ok': False, 'error': f'无法打开目录选择器：{str(e)}'}

    def choose_pdf_file(self, initial_directory=''):
        if not self.service.window:
            return {'ok': False, 'error': '窗口尚未初始化。'}
        with self.service.state_lock:
            if self.service.is_processing:
                return {'ok': False, 'error': '当前已有任务正在运行。'}

        try:
            selected = self.service.window.create_file_dialog(
                webview.OPEN_DIALOG,
                directory=initial_directory or '',
                allow_multiple=False,
                file_types=('PDF 文件 (*.pdf)', '所有文件 (*.*)')
            )
        except Exception as e:
            return {'ok': False, 'error': f'无法打开文件选择器：{str(e)}'}

        if not selected or not selected[0]:
            return {'ok': True, 'cancelled': True}

        pdf_path = selected[0]
        if not os.path.isfile(pdf_path) or not pdf_path.lower().endswith('.pdf'):
            return {'ok': False, 'error': '所选文件不是有效的 PDF 文件。'}

        pdf_dir = os.path.dirname(pdf_path)
        pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
        extract_dir = os.path.join(pdf_dir, pdf_name)
        dir_exists_nonempty = bool(
            os.path.exists(extract_dir)
            and os.path.isdir(extract_dir)
            and os.listdir(extract_dir)
        )

        return {
            'ok': True,
            'pdf_path': pdf_path,
            'extract_dir': extract_dir,
            'dir_exists_nonempty': dir_exists_nonempty,
        }

    def start_pdf_extraction(self, pdf_path, extract_dir):
        return self.service.start_pdf_extraction(pdf_path, extract_dir)

    def choose_pdf_and_extract(self, initial_directory=''):
        res = self.choose_pdf_file(initial_directory)
        if not res.get('ok') or res.get('cancelled'):
            return res
        return self.service.start_pdf_extraction(res['pdf_path'], res['extract_dir'])

    def get_update_info(self):
        return self.service.get_update_info()

    def open_download_url(self, url):
        return self.service.open_download_url(url)

    def start_processing(self, raw_settings):
        return self.service.start_processing(raw_settings)

    def start_pdf_workflow(self, raw_settings):
        return self.service.start_pdf_workflow(raw_settings)

    def toggle_pause(self):
        return self.service.toggle_pause()

    def cancel_processing(self):
        return self.service.cancel_processing()

    def respond_pdf_prompt(self, generate_pdf):
        return self.service.respond_pdf_prompt(generate_pdf)

    def poll_events(self):
        return self.service.poll_events()

    def open_output_folder(self):
        return self.service.open_output_folder()

    def get_system_language(self):
        return {'ok': True, 'language': get_system_language()}

    def get_user_language(self):
        return self.service.get_user_language()

    def set_user_language(self, lang):
        return self.service.set_user_language(lang)


def _resource_path(*parts):
    root_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root_dir, *parts)


def _get_free_port():
    """向操作系统内核请求一个当前未被占用的随机空闲临时端口。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]
    except Exception:
        return None


def launch_web_ui():
    service = ImageProcessorService()
    bridge = WebImageProcessorBridge(service)
    dev_mode = '--dev' in sys.argv
    if dev_mode:
        target_url = 'http://localhost:5173/'
    else:
        target_url = _resource_path('webui', 'index.html')
    sys_lang = get_system_language()
    saved_lang = get_saved_language()
    current_lang = saved_lang if saved_lang else sys_lang
    service.current_language = current_lang

    app_title = APP_TITLES.get(current_lang, APP_TITLES['zh-CN'])
    quit_msg = QUIT_CONFIRMATIONS.get(current_lang, QUIT_CONFIRMATIONS['zh-CN'])

    window = webview.create_window(
        app_title,
        url=target_url,
        js_api=bridge,
        width=1040,
        height=760,
        min_size=(760, 540),
        resizable=True,
        confirm_close=True,
        background_color='#edf2f7',
        text_select=True,
    )
    service.bind_window(window)
    window.expose(
        bridge.handle_dropped_path,
        bridge.choose_directory,
        bridge.choose_pdf_file,
        bridge.start_pdf_extraction,
        bridge.choose_pdf_and_extract,
        bridge.get_update_info,
        bridge.open_download_url,
        bridge.start_processing,
        bridge.start_pdf_workflow,
        bridge.toggle_pause,
        bridge.cancel_processing,
        bridge.respond_pdf_prompt,
        bridge.poll_events,
        bridge.open_output_folder,
        bridge.get_system_language,
        bridge.get_user_language,
        bridge.set_user_language,
    )

    # 设置 Windows 原生窗口与任务栏图标
    if sys.platform == 'win32':
        try:
            import webview.platforms.winforms as winforms_platform
            from System.Drawing import Icon as NetIcon
            icon_file = _resource_path('hanji.ico')
            if os.path.exists(icon_file):
                orig_form_init = winforms_platform.BrowserView.BrowserForm.__init__
                def _patched_form_init(self, *args, **kwargs):
                    orig_form_init(self, *args, **kwargs)
                    try:
                        self.Icon = NetIcon(icon_file)
                    except Exception:
                        pass
                winforms_platform.BrowserView.BrowserForm.__init__ = _patched_form_init
        except Exception:
            pass

    # Win7 没有 WebView2，使用系统 IE11/MSHTML；新系统优先使用 WebView2。
    legacy_windows = sys.platform == 'win32' and sys.getwindowsversion().major <= 6
    free_port = _get_free_port()
    webview_data_dir = os.path.join(get_config_dir(), 'webview')
    try:
        os.makedirs(webview_data_dir, exist_ok=True)
    except Exception:
        pass
    webview.start(
        gui='mshtml' if legacy_windows else None,
        http_server=True,
        http_port=free_port,
        private_mode=False,
        storage_path=webview_data_dir,
        localization={
            'global.quitConfirmation': quit_msg,
        },
    )


class ImageProcessorApp:
    PDF_APPLICATION_NAME = "SHUGE.ORG"
    PDF_SPEC_VERSION = b"%PDF-1.5"

    def __init__(self, root):
        self.root = root
        self.root.title("智能图像预处理工具 v3.6")
        # 在较矮的屏幕上留出系统任务栏空间，其他内容通过滚动条访问。
        window_height = min(820, max(480, self.root.winfo_screenheight() - 100))
        self.root.geometry(f"700x{window_height}")
        self.root.resizable(False, False)

        # === 1. 全局字体放大设置 ===
        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(size=11)
        self.root.option_add("*Font", default_font)
        
        # 强制更新 ttk 控件样式
        style = ttk.Style()
        style.configure('.', font=('Microsoft YaHei', 11)) 
        style.configure('TLabelframe.Label', font=('Microsoft YaHei', 11, 'bold'))

        # === 变量定义 ===
        self.work_mode = tk.StringVar(value="dir")
        self.pdf_file_path = tk.StringVar()
        self.pdf_target_preview = tk.StringVar()
        self.source_dir = tk.StringVar()
        self.target_dir = tk.StringVar()
        self.max_threads = tk.IntVar(value=8)
        
        # 新增：包含子文件夹选项，默认为 False
        self.include_subfolders = tk.BooleanVar(value=False)
        
        self.enable_binarize = tk.BooleanVar(value=True) 
        self.bin_method = tk.StringVar(value="0") 
        self.threshold_val = tk.IntVar(value=50)  

        # 取消二值化时的文件大小优化选项
        self.size_opt_mode = tk.StringVar(value="original")
        self.custom_scale = tk.IntVar(value=80)
        self.custom_quality = tk.IntVar(value=80)
        self.non_bin_format = tk.StringVar(value="keep")
        
        self.enable_crop = tk.BooleanVar(value=True) 
        self.crop_percent = tk.IntVar(value=50)   
        self.crop_direction = tk.StringVar(value="R2L") 
        self.exclude_ratio = tk.DoubleVar(value=0.7) 
        self.enable_pdf = tk.BooleanVar(value=False)
        self.keep_images_after_pdf = tk.BooleanVar(value=False)
        self.pdf_no_convert = tk.BooleanVar(value=False)

        # --- 状态与线程控制 ---
        self.is_processing = False
        self.is_paused = tk.BooleanVar(value=False)
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self._service = ImageProcessorService()
        self._service.pause_event = self.pause_event
        self._service.cancel_event = self.cancel_event
        self._service.ui_events = self.ui_events
        self._service.output_write_lock = self.output_write_lock
        self.pause_event.set()
        self.ui_events = queue.Queue()
        self.active_target_dir = None
        # Pillow 的部分编码器在 Windows 上并不保证多线程同时写入时稳定；
        # 计算仍可并行，最终落盘则串行并使用临时文件，避免留下半个文件。
        self.output_write_lock = threading.Lock()

        self._build_ui()
        if sys.platform == 'win32':
            self.root.after(100, self._setup_drag_and_drop)

    def _build_ui(self):
        scroll_container = ttk.Frame(self.root)
        scroll_container.pack(fill=tk.BOTH, expand=True)

        self.content_canvas = tk.Canvas(scroll_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            scroll_container,
            orient=tk.VERTICAL,
            command=self.content_canvas.yview,
        )
        self.content_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.content_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        main_frame = ttk.Frame(self.content_canvas, padding="20")
        self.content_window = self.content_canvas.create_window(
            (0, 0),
            window=main_frame,
            anchor=tk.NW,
        )
        main_frame.columnconfigure(0, weight=1)
        main_frame.bind("<Configure>", self._update_scroll_region)
        self.content_canvas.bind("<Configure>", self._fit_content_width)
        self.root.bind_all("<MouseWheel>", self._scroll_with_mousewheel, add="+")

        # --- 模式选择区域 ---
        mode_frame = ttk.LabelFrame(main_frame, text="工作模式", padding="10")
        mode_frame.grid(row=0, column=0, sticky=tk.EW, pady=(0, 10))
        ttk.Radiobutton(
            mode_frame, text="从图片目录开始处理", variable=self.work_mode, value="dir", command=self._on_mode_changed
        ).pack(side=tk.LEFT, padx=15)
        ttk.Radiobutton(
            mode_frame, text="从PDF文件开始处理 (仅支持图片类型的PDF文件)", variable=self.work_mode, value="pdf", command=self._on_mode_changed
        ).pack(side=tk.LEFT, padx=15)

        # --- 1. 输入区域容器 ---
        self.input_card_frame = ttk.Frame(main_frame)
        self.input_card_frame.grid(row=1, column=0, sticky=tk.EW, pady=(0, 10))

        # 1.1 目录选择区域
        self.dir_frame = ttk.Frame(self.input_card_frame)
        ttk.Label(self.dir_frame, text="输入目录:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self.dir_frame, textvariable=self.source_dir, width=44).grid(row=0, column=1, pady=5, padx=8)
        ttk.Button(self.dir_frame, text="浏览...", command=self.select_source_dir, width=7).grid(row=0, column=2, pady=5, sticky=tk.W)

        ttk.Label(self.dir_frame, text="输出目录:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self.dir_frame, textvariable=self.target_dir, width=44).grid(row=1, column=1, pady=5, padx=8)
        ttk.Button(self.dir_frame, text="浏览...", command=lambda: self.select_dir(self.target_dir), width=7).grid(row=1, column=2, pady=5, sticky=tk.W)

        ttk.Checkbutton(self.dir_frame, text="处理子文件夹内的文件 (自动排除输出目录)", variable=self.include_subfolders).grid(row=2, column=1, sticky=tk.W, pady=3, padx=5)
        ttk.Checkbutton(
            self.dir_frame,
            text="合成PDF后保留处理后的图片 (默认不选，转换为PDF后自动清理图片)",
            variable=self.keep_images_after_pdf,
        ).grid(row=3, column=1, sticky=tk.W, pady=3, padx=5)

        # 1.2 PDF 选择区域
        self.pdf_frame = ttk.Frame(self.input_card_frame)
        ttk.Label(self.pdf_frame, text="PDF 文件:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self.pdf_frame, textvariable=self.pdf_file_path, width=44).grid(row=0, column=1, pady=5, padx=8)
        ttk.Button(self.pdf_frame, text="浏览...", command=self.select_pdf_file_for_mode, width=7).grid(row=0, column=2, pady=5, sticky=tk.W)

        ttk.Label(self.pdf_frame, text="生成目录:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self.pdf_frame, textvariable=self.pdf_target_preview, width=44, state="readonly").grid(row=1, column=1, pady=5, padx=8)
        ttk.Label(self.pdf_frame, text="(同名+任务后缀)").grid(row=1, column=2, sticky=tk.W, pady=5)

        ttk.Checkbutton(
            self.pdf_frame,
            text="不转换为PDF (仅保留处理后的图片文件)",
            variable=self.pdf_no_convert,
            command=self._update_pdf_hint,
        ).grid(row=2, column=1, sticky=tk.W, pady=3, padx=5)

        self.pdf_hint_label = ttk.Label(
            self.pdf_frame,
            text="提示: 提取原图并完成处理后，将自动合并为新 PDF 并删除临时分页图片",
            font=('Microsoft YaHei', 9),
            foreground="#0284c7"
        )
        self.pdf_hint_label.grid(row=3, column=1, columnspan=2, sticky=tk.W, pady=3)

        ttk.Label(
            self.pdf_frame,
            text="注：仅支持图片类型的 PDF 文件处理（扫描件、古籍、插画等图片打包生成的 PDF）",
            font=('Microsoft YaHei', 8),
            foreground="#d97706"
        ).grid(row=4, column=1, columnspan=2, sticky=tk.W, pady=(1, 3))

        self.dir_frame.pack(fill=tk.X)

        # --- 2. 二值化参数区域 ---
        bin_frame = ttk.LabelFrame(main_frame, text="色彩处理", padding="15")
        bin_frame.grid(row=2, column=0, sticky=tk.EW, pady=8)

        cb_bin = ttk.Checkbutton(bin_frame, text="转化为黑白二值图 (1位 TIFF Group 4, 强化文字压缩体积)", variable=self.enable_binarize, command=self.toggle_bin_options)
        cb_bin.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        self.bin_options_frame = ttk.Frame(bin_frame)
        self.bin_options_frame.grid(row=1, column=0, columnspan=2, sticky=tk.W)

        ttk.Label(self.bin_options_frame, text="二值化方式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        radio_frame = ttk.Frame(self.bin_options_frame)
        radio_frame.grid(row=0, column=1, sticky=tk.W, pady=5)
        
        self.rb_otsu = ttk.Radiobutton(radio_frame, text="OTSU (自适应)", variable=self.bin_method, value="0", command=self.toggle_threshold)
        self.rb_otsu.pack(side=tk.LEFT, padx=10)
        
        self.rb_custom = ttk.Radiobutton(radio_frame, text="自定义阈值(%)", variable=self.bin_method, value="1", command=self.toggle_threshold)
        self.rb_custom.pack(side=tk.LEFT, padx=10)
        
        self.thresh_entry = ttk.Entry(radio_frame, textvariable=self.threshold_val, width=6)
        self.thresh_entry.pack(side=tk.LEFT, padx=5)

        # 新增：取消二值化时的文件大小优化选择
        self.non_bin_options_frame = ttk.Frame(bin_frame)
        self.non_bin_options_frame.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(10, 0))

        ttk.Label(self.non_bin_options_frame, text="文件大小优化:").grid(row=0, column=0, sticky=tk.W, pady=5)
        non_bin_radio_frame = ttk.Frame(self.non_bin_options_frame)
        non_bin_radio_frame.grid(row=0, column=1, sticky=tk.W, pady=5)

        self.rb_size_original = ttk.Radiobutton(
            non_bin_radio_frame, text="原大图片（无优化）",
            variable=self.size_opt_mode, value="original", command=self.toggle_size_opt_options
        )
        self.rb_size_original.pack(side=tk.LEFT, padx=6)

        self.rb_size_mobile = ttk.Radiobutton(
            non_bin_radio_frame, text="精简尺寸（适合手机）",
            variable=self.size_opt_mode, value="mobile", command=self.toggle_size_opt_options
        )
        self.rb_size_mobile.pack(side=tk.LEFT, padx=6)

        self.rb_size_custom = ttk.Radiobutton(
            non_bin_radio_frame, text="自定义参数",
            variable=self.size_opt_mode, value="custom", command=self.toggle_size_opt_options
        )
        self.rb_size_custom.pack(side=tk.LEFT, padx=6)

        # 自定义参数子项（尺寸比例下拉、质量输入框）
        self.custom_opt_subframe = ttk.Frame(self.non_bin_options_frame)
        self.custom_opt_subframe.grid(row=1, column=1, sticky=tk.W, pady=(4, 0))

        ttk.Label(self.custom_opt_subframe, text="图片尺寸:").pack(side=tk.LEFT, padx=(0, 4))
        self.scale_combo = ttk.Combobox(
            self.custom_opt_subframe,
            values=["80%", "60%", "50%", "40%", "30%", "20%"],
            state="readonly",
            width=7,
        )
        self.scale_combo.set("80%")
        self.scale_combo.pack(side=tk.LEFT, padx=(0, 12))
        self.scale_combo.bind("<<ComboboxSelected>>", self._on_scale_selected)

        ttk.Label(self.custom_opt_subframe, text="JPEG质量:").pack(side=tk.LEFT, padx=(0, 4))
        self.quality_entry = ttk.Entry(self.custom_opt_subframe, textvariable=self.custom_quality, width=5)
        self.quality_entry.pack(side=tk.LEFT)

        # --- 3. 裁切参数区域 ---
        crop_frame = ttk.LabelFrame(main_frame, text="分页处理", padding="15")
        crop_frame.grid(row=3, column=0, sticky=tk.EW, pady=8)

        cb_crop = ttk.Checkbutton(crop_frame, text="启用页面一分为二裁切", variable=self.enable_crop, command=self.toggle_crop_options)
        cb_crop.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        self.crop_options_frame = ttk.Frame(crop_frame)
        self.crop_options_frame.grid(row=1, column=0, columnspan=2, sticky=tk.W)

        ttk.Label(self.crop_options_frame, text="排除单页比例:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self.crop_options_frame, textvariable=self.exclude_ratio, width=8).grid(row=0, column=1, sticky=tk.W, pady=5, padx=10)
        ttk.Label(self.crop_options_frame, text="(宽/高 < 此值，强制跳过裁切，默认0.7)").grid(row=0, column=2, sticky=tk.W, pady=5)

        ttk.Label(self.crop_options_frame, text="分割比例(%):").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(self.crop_options_frame, textvariable=self.crop_percent, width=8).grid(row=1, column=1, sticky=tk.W, pady=5, padx=10)

        ttk.Label(self.crop_options_frame, text="阅读顺序:").grid(row=2, column=0, sticky=tk.W, pady=5)
        dir_radio_frame = ttk.Frame(self.crop_options_frame)
        dir_radio_frame.grid(row=2, column=1, columnspan=2, sticky=tk.W, pady=5)
        ttk.Radiobutton(dir_radio_frame, text="从右到左 (古籍常用, 右侧为_A)", variable=self.crop_direction, value="R2L").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(dir_radio_frame, text="从左到右 (现代书籍, 左侧为_A)", variable=self.crop_direction, value="L2R").pack(side=tk.LEFT, padx=5)

        # 动态线框视觉展示区 (排除单页比例与分割比例)
        self.crop_wireframe_canvas = tk.Canvas(
            self.crop_options_frame,
            width=580,
            height=120,
            bg="#f8fafc",
            highlightthickness=1,
            highlightbackground="#cbd5e1"
        )
        self.crop_wireframe_canvas.grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=(8, 4), padx=2)

        self.exclude_ratio.trace_add("write", self._draw_crop_wireframe)
        self.crop_percent.trace_add("write", self._draw_crop_wireframe)
        self.crop_direction.trace_add("write", self._draw_crop_wireframe)
        self._draw_crop_wireframe()

        # --- 4. 性能与控制区域 ---
        sys_frame = ttk.Frame(main_frame)
        sys_frame.grid(row=4, column=0, sticky=tk.EW, pady=20)
        
        ttk.Label(sys_frame, text="最大线程数:").pack(side=tk.LEFT)
        ttk.Entry(sys_frame, textvariable=self.max_threads, width=8).pack(side=tk.LEFT, padx=10)

        ttk.Checkbutton(
            sys_frame,
            text="合并输出为单个 PDF（文件名同输入文件夹）",
            variable=self.enable_pdf,
        ).pack(side=tk.LEFT, padx=10)

        # --- 5. 状态与进度 ---
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=5, column=0, sticky=tk.EW, pady=10)

        # 操作按钮独占一行，避免与性能选项争夺水平空间。
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=6, column=0, sticky=tk.EW, pady=(0, 8))

        self.start_btn = ttk.Button(control_frame, text="开始处理", command=self.start_processing)
        self.start_btn.pack(side=tk.RIGHT)

        self.pause_btn = ttk.Button(control_frame, text="暂停", command=self.toggle_pause, state=tk.DISABLED)
        self.pause_btn.pack(side=tk.RIGHT, padx=10)

        self.cancel_btn = ttk.Button(control_frame, text="取消任务", command=self.cancel_processing, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT, padx=5)

        # 完成消息可能包含很长的 PDF 路径；限制宽度并换行，避免撑宽整个窗口。
        self.status_label = ttk.Label(
            main_frame,
            text="准备就绪",
            wraplength=610,
            justify=tk.LEFT,
        )
        self.status_label.grid(row=7, column=0, sticky=tk.EW)
        
        footer_label = ttk.Label(
            main_frame,
            text="By weiceng © 漢籍合璧",
            font=('Microsoft YaHei', 9),
            foreground="#6b7280",
            anchor=tk.CENTER,
            justify=tk.CENTER,
        )
        footer_label.grid(row=8, column=0, sticky=tk.EW, pady=(12, 6))
        
        self.toggle_bin_options()
        self.toggle_crop_options()

    def _update_scroll_region(self, _event=None):
        self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))

    def _fit_content_width(self, event):
        self.content_canvas.itemconfigure(self.content_window, width=event.width)

    def _scroll_with_mousewheel(self, event):
        if event.delta:
            self.content_canvas.yview_scroll(int(-event.delta / 120), "units")

    # --- UI 交互逻辑 ---
    def select_source_dir(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.source_dir.set(folder_selected)
            self.target_dir.set(os.path.join(folder_selected, "output"))

    def open_pdf_file(self):
        if self.is_processing:
            messagebox.showwarning("任务运行中", "当前已有任务正在运行，请等待或取消后再打开 PDF。")
            return
        pdf_path = filedialog.askopenfilename(
            title="选择要提取图片的 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")]
        )
        if not pdf_path:
            return

        pdf_dir = os.path.dirname(pdf_path)
        pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
        extract_dir = os.path.join(pdf_dir, pdf_name)

        msg = f"已选择 PDF 文件：\n{pdf_path}\n\n是否提取分页图片到同名目录：\n{extract_dir}？"
        if os.path.exists(extract_dir) and os.path.isdir(extract_dir) and os.listdir(extract_dir):
            msg += "\n\n注意：目标图片文件夹已存在且非空，继续提取可能会覆盖同名文件！"

        if not messagebox.askyesno("提取确认", msg, icon='question'):
            return

        self.is_processing = True
        self.cancel_event.clear()
        self.pause_event.set()
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.status_label.config(text="正在读取并提取 PDF 原始分页图片...")

        def _do_extract():
            def _progress(cur, total, msg):
                self.ui_events.put(('progress', (cur / total) * 100, msg))

            count, err = extract_images_from_pdf(
                pdf_path, extract_dir, progress_callback=_progress, cancel_event=self.cancel_event
            )
            self.ui_events.put(('pdf_extracted', pdf_path, extract_dir, count, err))

        threading.Thread(target=_do_extract, daemon=True).start()
        self.root.after(50, self._poll_ui_events)

    def _update_pdf_target_preview(self, *args):
        path = self.pdf_file_path.get().strip()
        if not path:
            self.pdf_target_preview.set("")
            return
        pdf_dir = os.path.dirname(os.path.abspath(path))
        pdf_name = os.path.splitext(os.path.basename(path))[0]
        suffix = get_task_suffix(self.enable_crop.get(), self.enable_binarize.get(), size_opt_mode=self.size_opt_mode.get())
        if not suffix:
            if self.pdf_no_convert.get():
                self.pdf_target_preview.set(os.path.join(pdf_dir, pdf_name))
            else:
                self.pdf_target_preview.set("（请至少勾选一种任务：裁切、黑白或大小优化）")
        else:
            self.pdf_target_preview.set(os.path.join(pdf_dir, f"{pdf_name}{suffix}"))

    def _update_pdf_hint(self, *args):
        self._update_pdf_target_preview()
        if hasattr(self, 'pdf_hint_label') and self.pdf_hint_label:
            if self.pdf_no_convert.get():
                if not self.enable_crop.get() and not self.enable_binarize.get():
                    self.pdf_hint_label.config(
                        text="提示: 仅提取 PDF 原始图片至同名目录，不进行后续处理与合并",
                        foreground="#059669"
                    )
                else:
                    self.pdf_hint_label.config(
                        text="提示: 提取原图并完成处理后，不合并为 PDF，保留处理后的图片文件夹",
                        foreground="#d97706"
                    )
            else:
                self.pdf_hint_label.config(
                    text="提示: 提取原图并完成处理后，将自动合并为新 PDF 并删除临时分页图片",
                    foreground="#0284c7"
                )

    def _on_mode_changed(self):
        if self.work_mode.get() == "pdf":
            self.dir_frame.pack_forget()
            self.pdf_frame.pack(fill=tk.X)
            self._update_pdf_target_preview()
        else:
            self.pdf_frame.pack_forget()
            self.dir_frame.pack(fill=tk.X)

    def select_pdf_file_for_mode(self):
        pdf_path = filedialog.askopenfilename(
            title="选择要处理的 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")]
        )
        if pdf_path:
            self.pdf_file_path.set(pdf_path)
            self._update_pdf_target_preview()

    def select_dir(self, var):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            var.set(folder_selected)

    def _setup_drag_and_drop(self):
        """为 Tkinter 原生界面启用 Windows 资源管理器文件/目录拖拽接收。"""
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = self.root.winfo_id()
            hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2) or hwnd
            ctypes.windll.shell32.DragAcceptFiles(hwnd, True)

            GWL_WNDPROC = -4
            WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

            if ctypes.sizeof(ctypes.c_void_p) == 8:
                GetWindowLong = ctypes.windll.user32.GetWindowLongPtrW
                SetWindowLong = ctypes.windll.user32.SetWindowLongPtrW
            else:
                GetWindowLong = ctypes.windll.user32.GetWindowLongW
                SetWindowLong = ctypes.windll.user32.SetWindowLongW

            old_wndproc = GetWindowLong(hwnd, GWL_WNDPROC)

            def new_wndproc(h_wnd, msg, w_param, l_param):
                if msg == 0x0233:  # WM_DROPFILES
                    h_drop = w_param
                    count = ctypes.windll.shell32.DragQueryFileW(h_drop, 0xFFFFFFFF, None, 0)
                    paths = []
                    for i in range(count):
                        length = ctypes.windll.shell32.DragQueryFileW(h_drop, i, None, 0)
                        buf = ctypes.create_unicode_buffer(length + 1)
                        ctypes.windll.shell32.DragQueryFileW(h_drop, i, buf, length + 1)
                        paths.append(buf.value)
                    ctypes.windll.shell32.DragFinish(h_drop)
                    if paths:
                        self.root.after(0, self._on_native_drop, paths)
                    return 0
                return ctypes.windll.user32.CallWindowProcW(old_wndproc, h_wnd, msg, w_param, l_param)

            self._drop_wndproc = WNDPROC(new_wndproc)
            SetWindowLong(hwnd, GWL_WNDPROC, self._drop_wndproc)
        except Exception:
            pass

    def _on_native_drop(self, paths):
        if not paths:
            return
        path = paths[0].strip().strip('"').strip("'")
        if not os.path.exists(path):
            return
        path = os.path.abspath(path)
        if os.path.isdir(path):
            self.work_mode.set("dir")
            self._on_mode_changed()
            self.source_dir.set(path)
            self.target_dir.set(os.path.join(path, "output"))
            self.status_label.config(text=f"已通过拖拽载入输入图片目录: {os.path.basename(path)}")
        elif os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            if ext == '.pdf':
                self.work_mode.set("pdf")
                self._on_mode_changed()
                self.pdf_file_path.set(path)
                self._update_pdf_target_preview()
                self.status_label.config(text=f"已通过拖拽载入待处理 PDF 文件: {os.path.basename(path)}")
            elif ext in ('.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2'):
                parent_dir = os.path.dirname(path)
                self.work_mode.set("dir")
                self._on_mode_changed()
                self.source_dir.set(parent_dir)
                self.target_dir.set(os.path.join(parent_dir, "output"))
                self.status_label.config(text=f"已通过拖拽载入图片所在目录: {os.path.basename(parent_dir)}")

    def _on_scale_selected(self, event=None):
        val_str = self.scale_combo.get().replace('%', '').strip()
        try:
            self.custom_scale.set(int(val_str))
        except (ValueError, tk.TclError):
            self.custom_scale.set(80)

    def toggle_size_opt_options(self):
        if not self.enable_binarize.get() and self.size_opt_mode.get() == "custom":
            self.scale_combo.config(state="readonly")
            self.quality_entry.config(state=tk.NORMAL)
        else:
            self.scale_combo.config(state=tk.DISABLED)
            self.quality_entry.config(state=tk.DISABLED)
        if self.size_opt_mode.get() == "original":
            self.non_bin_format.set("keep")
        else:
            self.non_bin_format.set("jpg80")
        self._update_pdf_hint()

    def toggle_bin_options(self):
        state = tk.NORMAL if self.enable_binarize.get() else tk.DISABLED
        self.rb_otsu.config(state=state)
        self.rb_custom.config(state=state)
        if not self.enable_binarize.get():
            self.thresh_entry.config(state=tk.DISABLED)
        else:
            self.toggle_threshold()

        # 取消二值化时才可选择大小优化，二者状态互斥
        non_bin_state = tk.DISABLED if self.enable_binarize.get() else tk.NORMAL
        self.rb_size_original.config(state=non_bin_state)
        self.rb_size_mobile.config(state=non_bin_state)
        self.rb_size_custom.config(state=non_bin_state)
        self.toggle_size_opt_options()
        self._update_pdf_hint()

    def toggle_threshold(self):
        if self.enable_binarize.get() and self.bin_method.get() == "1":
            self.thresh_entry.config(state=tk.NORMAL)
        else:
            self.thresh_entry.config(state=tk.DISABLED)

    def toggle_crop_options(self):
        state = tk.NORMAL if self.enable_crop.get() else tk.DISABLED
        for child in self.crop_options_frame.winfo_children():
            if isinstance(child, (ttk.Frame, ttk.LabelFrame)):
                for subchild in child.winfo_children():
                    try:
                        subchild.config(state=state)
                    except Exception:
                        pass
            else:
                try:
                    child.config(state=state)
                except Exception:
                    pass
        self._draw_crop_wireframe()
        self._update_pdf_hint()

    def _draw_crop_wireframe(self, *args):
        if not hasattr(self, "crop_wireframe_canvas"):
            return
        canvas = self.crop_wireframe_canvas
        canvas.delete("all")

        is_enabled = self.enable_crop.get()

        try:
            ex_ratio = float(self.exclude_ratio.get())
        except (ValueError, tk.TclError):
            ex_ratio = 0.7

        try:
            split_pct = int(self.crop_percent.get())
        except (ValueError, tk.TclError):
            split_pct = 50
        split_pct = max(1, min(99, split_pct))

        direction = self.crop_direction.get()

        # 配色定义 (启用状态 vs 禁用置灰)
        bg_card = "#ffffff" if is_enabled else "#f1f5f9"
        text_primary = "#1e293b" if is_enabled else "#94a3b8"
        text_secondary = "#64748b" if is_enabled else "#94a3b8"
        border_box = "#94a3b8" if is_enabled else "#cbd5e1"
        page_a_fill = "#dbeafe" if is_enabled else "#f1f5f9"
        page_a_outline = "#3b82f6" if is_enabled else "#cbd5e1"
        page_b_fill = "#f8fafc" if is_enabled else "#f1f5f9"
        page_b_outline = "#94a3b8" if is_enabled else "#cbd5e1"
        cut_line_color = "#e11d48" if is_enabled else "#cbd5e1"

        # 1. 左侧：单页判定示意 (排除单页)
        canvas.create_text(115, 15, text=f"单页判定线框 (宽/高 < {ex_ratio:.2f})", font=("Microsoft YaHei", 9, "bold"), fill=text_primary)

        box_h = 56
        box_w = max(16, min(84, int(box_h * ex_ratio)))
        bx1 = 115 - box_w // 2
        bx2 = 115 + box_w // 2
        by1 = 28
        by2 = by1 + box_h

        canvas.create_rectangle(bx1, by1, bx2, by2, fill=bg_card, outline=border_box, width=1.5)
        canvas.create_text(115, by1 + box_h // 2, text="单页原图\n(跳过裁切)", font=("Microsoft YaHei", 8), fill=text_secondary, justify=tk.CENTER)
        canvas.create_text(115, 103, text=f"宽/高 < {ex_ratio:.2f} 视为单页不裁切", font=("Microsoft YaHei", 8), fill=text_secondary)

        # 中间分割线
        canvas.create_line(230, 10, 230, 110, fill="#e2e8f0", dash=(2, 2))

        # 2. 右侧：双页裁切示意 (分割比例 & 阅读顺序 & 中缝重叠)
        dir_text = "从右到左 (古籍)" if direction == "R2L" else "从左到右 (现代)"
        canvas.create_text(405, 15, text=f"双页裁切线框 ({dir_text} · 左右各宽 {split_pct}%)", font=("Microsoft YaHei", 9, "bold"), fill=text_primary)

        sx1 = 265
        sx2 = 545
        spread_w = sx2 - sx1  # 280
        sy1 = 28
        sy2 = sy1 + box_h     # 84

        overlap_fill = "#fef3c7" if is_enabled else "#f1f5f9"
        overlap_outline = "#f59e0b" if is_enabled else "#cbd5e1"
        gap_fill = "#f1f5f9" if is_enabled else "#f8fafc"
        gap_outline = "#cbd5e1" if is_enabled else "#e2e8f0"

        left_is_a = (direction == "L2R")
        left_short = "① _A" if left_is_a else "② _B"
        right_short = "② _B" if left_is_a else "① _A"

        color_a = page_a_outline if is_enabled else text_secondary
        color_b = text_secondary

        if split_pct >= 50:
            overlap_pct = 2 * split_pct - 100
            exclusive_pct = 100 - split_pct

            cut1_x = sx1 + int(spread_w * (100 - split_pct) / 100.0)
            cut2_x = sx1 + int(spread_w * split_pct / 100.0)

            # 左侧独占区 [sx1, cut1_x]
            canvas.create_rectangle(sx1, sy1, cut1_x, sy2, fill=page_a_fill if left_is_a else page_b_fill, outline=page_a_outline if left_is_a else page_b_outline, width=1.5)
            left_w = cut1_x - sx1
            if left_w >= 28:
                txt_left = f"{left_short}\n{split_pct}%" if left_w < 55 else f"{'① 第1页 (_A)' if left_is_a else '② 第2页 (_B)'}\n宽 {split_pct}%"
                canvas.create_text((sx1 + cut1_x) // 2, sy1 + box_h // 2, text=txt_left, font=("Microsoft YaHei", 8, "bold" if left_is_a else "normal"), fill=color_a if left_is_a else color_b, justify=tk.CENTER)

            # 中缝重叠区 [cut1_x, cut2_x] (两页均包含)
            if overlap_pct > 0:
                canvas.create_rectangle(cut1_x, sy1, cut2_x, sy2, fill=overlap_fill, outline=overlap_outline, width=1.5)
                overlap_w = cut2_x - cut1_x
                if overlap_w >= 36:
                    canvas.create_text((cut1_x + cut2_x) // 2, sy1 + box_h // 2, text=f"中缝重叠\n{overlap_pct}%", font=("Microsoft YaHei", 8, "bold"), fill="#b45309" if is_enabled else text_secondary, justify=tk.CENTER)
                else:
                    canvas.create_text((cut1_x + cut2_x) // 2, sy1 + box_h // 2, text=f"{overlap_pct}%", font=("Microsoft YaHei", 7, "bold"), fill="#b45309" if is_enabled else text_secondary)

            # 右侧独占区 [cut2_x, sx2]
            canvas.create_rectangle(cut2_x, sy1, sx2, sy2, fill=page_b_fill if left_is_a else page_a_fill, outline=page_b_outline if left_is_a else page_a_outline, width=1.5)
            right_w = sx2 - cut2_x
            if right_w >= 28:
                txt_right = f"{right_short}\n{split_pct}%" if right_w < 55 else f"{'② 第2页 (_B)' if left_is_a else '① 第1页 (_A)'}\n宽 {split_pct}%"
                canvas.create_text((cut2_x + sx2) // 2, sy1 + box_h // 2, text=txt_right, font=("Microsoft YaHei", 8, "bold" if not left_is_a else "normal"), fill=color_a if not left_is_a else color_b, justify=tk.CENTER)

            # 裁切虚线与剪刀标记
            if overlap_pct > 0:
                canvas.create_line(cut1_x, sy1 - 4, cut1_x, sy2 + 4, fill=cut_line_color, width=2, dash=(4, 3))
                canvas.create_text(cut1_x, sy1 - 5, text="✂", font=("Segoe UI Symbol", 9), fill=cut_line_color)
            canvas.create_line(cut2_x, sy1 - 4, cut2_x, sy2 + 4, fill=cut_line_color, width=2, dash=(4, 3))
            canvas.create_text(cut2_x, sy1 - 5, text="✂", font=("Segoe UI Symbol", 9), fill=cut_line_color)

            if is_enabled:
                if overlap_pct > 0:
                    caption_right = f"左右各裁切 {split_pct}%，中缝重叠 {overlap_pct}%（保证中缝内容可阅读）"
                else:
                    caption_right = "左右各裁切 50%，居中均分裁切无重叠"
            else:
                caption_right = "已禁用页面裁切"
        else:
            # split_pct < 50
            gap_pct = 100 - 2 * split_pct
            cut1_x = sx1 + int(spread_w * split_pct / 100.0)
            cut2_x = sx1 + int(spread_w * (100 - split_pct) / 100.0)

            # 左页 [sx1, cut1_x]
            canvas.create_rectangle(sx1, sy1, cut1_x, sy2, fill=page_a_fill if left_is_a else page_b_fill, outline=page_a_outline if left_is_a else page_b_outline, width=1.5)
            canvas.create_text((sx1 + cut1_x) // 2, sy1 + box_h // 2, text=f"{left_short}\n{split_pct}%", font=("Microsoft YaHei", 8), fill=color_a if left_is_a else color_b, justify=tk.CENTER)

            # 中间未裁区 [cut1_x, cut2_x]
            canvas.create_rectangle(cut1_x, sy1, cut2_x, sy2, fill=gap_fill, outline=gap_outline, width=1.5)
            canvas.create_text((cut1_x + cut2_x) // 2, sy1 + box_h // 2, text=f"未裁入\n{gap_pct}%", font=("Microsoft YaHei", 8), fill=text_secondary, justify=tk.CENTER)

            # 右页 [cut2_x, sx2]
            canvas.create_rectangle(cut2_x, sy1, sx2, sy2, fill=page_b_fill if left_is_a else page_a_fill, outline=page_b_outline if left_is_a else page_a_outline, width=1.5)
            canvas.create_text((cut2_x + sx2) // 2, sy1 + box_h // 2, text=f"{right_short}\n{split_pct}%", font=("Microsoft YaHei", 8), fill=color_a if not left_is_a else color_b, justify=tk.CENTER)

            canvas.create_line(cut1_x, sy1 - 4, cut1_x, sy2 + 4, fill=cut_line_color, width=2, dash=(4, 3))
            canvas.create_text(cut1_x, sy1 - 5, text="✂", font=("Segoe UI Symbol", 9), fill=cut_line_color)
            canvas.create_line(cut2_x, sy1 - 4, cut2_x, sy2 + 4, fill=cut_line_color, width=2, dash=(4, 3))
            canvas.create_text(cut2_x, sy1 - 5, text="✂", font=("Segoe UI Symbol", 9), fill=cut_line_color)

            caption_right = f"左右各裁切 {split_pct}%，中间未裁入 {gap_pct}%" if is_enabled else "已禁用页面裁切"

        canvas.create_text(405, 103, text=caption_right, font=("Microsoft YaHei", 8), fill=text_secondary)

    def toggle_pause(self):
        if not self.is_processing: return
        if self.is_paused.get():
            self.pause_event.set()
            self.is_paused.set(False)
            self.pause_btn.config(text="暂停")
            self.status_label.config(text="处理已恢复...")
        else:
            self.pause_event.clear()
            self.is_paused.set(True)
            self.pause_btn.config(text="恢复")
            self.status_label.config(text="处理已暂停... (等待当前活动线程完成)")

    def cancel_processing(self):
        if not self.is_processing: return
        
        tgt_dir = self.active_target_dir or self.target_dir.get()
        msg = f"确实要取消并中止任务吗？\n\n警告：取消后将彻底删除输出目录及其所有文件！\n目录：{tgt_dir}"
        
        if messagebox.askyesno("危险操作确认", msg, icon='warning'):
            self.cancel_event.set()
            self.pause_event.set()       
            
            self.cancel_btn.config(state=tk.DISABLED)
            self.pause_btn.config(state=tk.DISABLED)
            self.status_label.config(text="正在中止任务，稍后将清理输出目录...")

    # ================= 核心图像处理部分 =================

    def _is_same_or_parent(parent, child):
        """判断 parent 是否等于或包含 child，使用规范路径避免误删输入目录。"""
        try:
            parent = os.path.normcase(os.path.realpath(os.path.abspath(parent)))
            child = os.path.normcase(os.path.realpath(os.path.abspath(child)))
            return os.path.commonpath([parent, child]) == parent
        except ValueError:
            return False

    def _get_validated_settings(self):
        try:
            source_text = self.source_dir.get().strip()
            target_text = self.target_dir.get().strip()
            settings = {
                'work_mode': 'dir',
                'source_dir': os.path.abspath(source_text) if source_text else '',
                'target_dir': os.path.abspath(target_text) if target_text else '',
                'include_subfolders': self.include_subfolders.get(),
                'keep_images_after_pdf': self.keep_images_after_pdf.get(),
                'enable_binarize': self.enable_binarize.get(),
                'bin_method': self.bin_method.get(),
                'threshold_val': self.threshold_val.get(),
                'size_opt_mode': self.size_opt_mode.get(),
                'custom_scale': self.custom_scale.get(),
                'custom_quality': self.custom_quality.get(),
                'non_bin_format': 'keep' if self.size_opt_mode.get() == 'original' else 'jpg80',
                'enable_crop': self.enable_crop.get(),
                'crop_percent': self.crop_percent.get(),
                'crop_direction': self.crop_direction.get(),
                'exclude_ratio': self.exclude_ratio.get(),
                'max_threads': self.max_threads.get(),
                'enable_pdf': self.enable_pdf.get(),
            }
        except tk.TclError:
            messagebox.showwarning("参数无效", "请使用有效的数字填写线程数、阈值和裁切参数。")
            return None

        if not settings['source_dir'] or not os.path.isdir(settings['source_dir']):
            messagebox.showwarning("输入目录无效", "请选择一个存在的输入目录。")
            return None
        if not settings['target_dir']:
            settings['target_dir'] = os.path.join(settings['source_dir'], 'output')
            self.target_dir.set(settings['target_dir'])
        if self._is_same_or_parent(settings['target_dir'], settings['source_dir']):
            messagebox.showwarning("输出目录不安全", "输出目录不能等于输入目录，也不能是输入目录的上级目录。")
            return None
        if os.path.exists(settings['target_dir']) and not os.path.isdir(settings['target_dir']):
            messagebox.showwarning("输出目录无效", "输出路径已存在，但不是目录。")
            return None
        if os.path.islink(settings['target_dir']):
            messagebox.showwarning("输出目录不安全", "输出目录不能是链接或快捷目录。")
            return None
        if os.path.isdir(settings['target_dir']) and os.listdir(settings['target_dir']):
            messagebox.showwarning("输出目录非空", "为避免覆盖或取消时删除已有文件，请选择一个不存在或空的输出目录。")
            return None
        if not 1 <= settings['max_threads'] <= 64:
            messagebox.showwarning("线程数无效", "最大线程数必须在 1 到 64 之间。")
            return None
        if not settings['enable_binarize'] and not settings['enable_crop']:
            if settings['size_opt_mode'] == 'original' and not settings['enable_pdf']:
                messagebox.showwarning("操作无效", "请至少启用一种处理任务（色彩处理、分页裁切或文件大小优化），或勾选合并输出为 PDF！")
                return None
        if settings['size_opt_mode'] == 'custom':
            if not 1 <= settings['custom_scale'] <= 100:
                messagebox.showwarning("缩放比例无效", "自定义缩放比例必须在 1 到 100 之间。")
                return None
            if not 1 <= settings['custom_quality'] <= 100:
                messagebox.showwarning("质量参数无效", "自定义 JPEG 质量必须在 1 到 100 之间。")
                return None
        if settings['enable_binarize'] and not 0 <= settings['threshold_val'] <= 100:
            messagebox.showwarning("阈值无效", "自定义阈值必须在 0 到 100 之间。")
            return None
        if settings['enable_crop'] and not 1 <= settings['crop_percent'] <= 100:
            messagebox.showwarning("分割比例无效", "分割比例必须在 1 到 100 之间。")
            return None
        if settings['enable_crop'] and not 0 < settings['exclude_ratio']:
            messagebox.showwarning("单页比例无效", "排除单页比例必须大于 0。")
            return None
        return settings

    def start_processing(self):
        if self.is_processing: return
        if not self.enable_binarize.get() and not self.enable_crop.get():
            if self.work_mode.get() == "pdf":
                if not self.pdf_no_convert.get():
                    messagebox.showwarning("操作无效", "请至少勾选一种处理任务（裁切或黑白二值化）！")
                    return
            else:
                if self.non_bin_format.get() == 'keep' and not self.enable_pdf.get():
                    messagebox.showwarning("操作无效", "请至少启用一种处理任务（色彩处理或分页裁切），或选择转为 JPG，或勾选合并输出为 PDF！")
                    return

        if self.work_mode.get() == "pdf":
            pdf_path = self.pdf_file_path.get().strip()
            if not pdf_path or not os.path.isfile(pdf_path):
                messagebox.showwarning("PDF 文件无效", "请先选择一个有效的待处理 PDF 文件。")
                return

            pdf_dir = os.path.dirname(pdf_path)
            pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
            enable_crop = self.enable_crop.get()
            enable_binarize = self.enable_binarize.get()
            no_convert_pdf = self.pdf_no_convert.get()

            size_opt_mode = self.size_opt_mode.get()
            if not enable_crop and not enable_binarize and size_opt_mode == 'original':
                if not no_convert_pdf:
                    messagebox.showwarning("操作无效", "请至少勾选一种处理任务（裁切、黑白二值化或文件大小优化）！")
                    return
                task_dir = os.path.join(pdf_dir, pdf_name)
                final_pdf_path = ''
            else:
                suffix = get_task_suffix(enable_crop, enable_binarize, size_opt_mode=size_opt_mode)
                task_dir = os.path.join(pdf_dir, f"{pdf_name}{suffix}")
                final_pdf_path = os.path.abspath(os.path.join(task_dir, f"{pdf_name}{suffix}.pdf"))

            try:
                threads = self.max_threads.get()
            except tk.TclError:
                threads = 8
            if not 1 <= threads <= 64:
                messagebox.showwarning("线程数无效", "最大线程数必须在 1 到 64 之间。")
                return

            try:
                thresh = self.threshold_val.get()
            except tk.TclError:
                thresh = 50
            if enable_binarize and not 0 <= thresh <= 100:
                messagebox.showwarning("阈值无效", "自定义阈值必须在 0 到 100 之间。")
                return

            try:
                c_pct = self.crop_percent.get()
            except tk.TclError:
                c_pct = 50
            if enable_crop and not 1 <= c_pct <= 100:
                messagebox.showwarning("分割比例无效", "分割比例必须在 1 到 100 之间。")
                return

            try:
                ex_ratio = self.exclude_ratio.get()
            except tk.TclError:
                ex_ratio = 0.7
            if enable_crop and not 0 < ex_ratio:
                messagebox.showwarning("单页比例无效", "排除单页比例必须大于 0。")
                return

            settings = {
                'work_mode': 'pdf',
                'pdf_path': os.path.abspath(pdf_path),
                'source_dir': os.path.abspath(task_dir),
                'target_dir': os.path.abspath(os.path.join(task_dir, 'output')),
                'final_pdf_path': final_pdf_path,
                'clean_dir': os.path.abspath(task_dir),
                'include_subfolders': False,
                'no_convert_pdf': no_convert_pdf,
                'enable_binarize': enable_binarize,
                'bin_method': self.bin_method.get(),
                'threshold_val': thresh,
                'size_opt_mode': size_opt_mode,
                'custom_scale': self.custom_scale.get(),
                'custom_quality': self.custom_quality.get(),
                'non_bin_format': 'keep' if size_opt_mode == 'original' else 'jpg80',
                'enable_crop': enable_crop,
                'crop_percent': c_pct,
                'crop_direction': self.crop_direction.get(),
                'exclude_ratio': ex_ratio,
                'max_threads': threads,
                'enable_pdf': not no_convert_pdf,
            }

            if not enable_crop and not enable_binarize and no_convert_pdf:
                confirm_msg = (
                    f"将直接从 PDF 提取原始图片至同名目录（不进行裁切、色彩处理或转 PDF）：\n\n"
                    f"PDF 文件：{pdf_path}\n"
                    f"提取目录：{task_dir}\n\n"
                    f"是否确认开始？"
                )
            elif no_convert_pdf:
                confirm_msg = (
                    f"将对 PDF 依次执行：\n"
                    f"1. 从 PDF 提取原始分页图片\n"
                    f"2. 按勾选任务批量处理\n"
                    f"3. 保留处理后的图片文件夹（不生成 PDF）\n\n"
                    f"PDF 文件：{pdf_path}\n"
                    f"输出目录：{task_dir}\n\n"
                    f"是否确认开始？"
                )
            else:
                confirm_msg = (
                    f"将对 PDF 依次执行：\n"
                    f"1. 从 PDF 提取原始分页图片\n"
                    f"2. 按勾选任务批量处理\n"
                    f"3. 汇总生成新 PDF\n"
                    f"4. 自动清理临时分页图片，仅保留新 PDF\n\n"
                    f"PDF 文件：{pdf_path}\n"
                    f"生成目录：{task_dir}\n\n"
                    f"是否确认开始？"
                )
            if not messagebox.askyesno("开始 PDF 处理任务", confirm_msg, icon='question'):
                return

            self.is_processing = True
            self.active_target_dir = settings['clean_dir']
            self.is_paused.set(False)
            self.cancel_event.clear()
            self.pause_event.set()

            self.start_btn.config(state=tk.DISABLED)
            self.pause_btn.config(state=tk.NORMAL, text="暂停")
            self.cancel_btn.config(state=tk.NORMAL)

            self.progress_var.set(0)
            self.status_label.config(text="正在读取并提取 PDF 原始分页图片...")

            threading.Thread(target=self._run_pdf_pipeline_safely, args=(settings,), daemon=True).start()
            self.root.after(50, self._poll_ui_events)
            return

        settings = self._get_validated_settings()
        if settings is None:
            return

        self.is_processing = True
        self.active_target_dir = settings['target_dir']
        
        self.is_paused.set(False)
        self.cancel_event.clear()
        self.pause_event.set() 
        
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL, text="暂停")
        self.cancel_btn.config(state=tk.NORMAL) 
        
        self.progress_var.set(0)
        self.status_label.config(text="正在扫描文件...")

        # 后台线程只使用此处冻结的普通 Python 数据，不再读取 Tkinter 变量。
        threading.Thread(target=self._run_task_safely, args=(settings,), daemon=True).start()
        self.root.after(50, self._poll_ui_events)



    # 代理至 core / service
    def _run_task_safely(self, settings):
        try:
            self._service._run_task(settings)
        except Exception as e:
            self.ui_events.put(('finish', f"任务异常终止：{str(e)}"))

    def _run_pdf_pipeline_safely(self, settings):
        try:
            self._service._run_pdf_pipeline(settings)
        except Exception as e:
            lang = settings.get('lang') or get_system_language()
            self.ui_events.put(('finish', get_backend_text('err_pdf_abort', lang, error=str(e))))


    def _poll_ui_events(self):
        """仅由主线程更新界面，避免 Tkinter 跨线程访问。"""
        while True:
            try:
                event = self.ui_events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'progress':
                self._update_progress(event[1], event[2])
            elif event[0] == 'status':
                self.status_label.config(text=event[1])
            elif event[0] == 'ask_pdf':
                self._prompt_pdf_generation(event[1], event[2])
                return
            elif event[0] == 'pdf_extracted':
                self._handle_pdf_extracted(*event[1:])
                return
            else:
                self._finish_processing(event[1])
                return
        if self.is_processing:
            self.root.after(50, self._poll_ui_events)

    def _handle_pdf_extracted(self, pdf_path, extract_dir, count, err):
        self.is_processing = False
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED, text="暂停")
        self.cancel_btn.config(state=tk.DISABLED)

        if err:
            self.status_label.config(text=f"PDF 提取失败: {err}")
            messagebox.showerror("提取失败", f"未能成功提取 PDF 图片：\n{err}")
            return

        self.progress_var.set(100)
        self.status_label.config(text=f"PDF 图片提取完成，共提取 {count} 张图片。")
        self.source_dir.set(extract_dir)
        self.target_dir.set(os.path.join(extract_dir, "output"))

        ask_msg = (
            f"已成功从 PDF 提取 {count} 张图片到文件夹：\n{extract_dir}\n\n"
            "是否立即将该文件夹执行后续裁切、黑白二值化和 PDF 汇总的处理？"
        )
        if messagebox.askyesno("执行后续预处理", ask_msg, icon='question'):
            self.start_processing()

    def _update_progress(self, percent, msg):
        self.progress_var.set(percent)
        if not self.is_paused.get() and not self.cancel_event.is_set():
            self.status_label.config(text=msg)

    def _open_output_folder(self, output_dir):
        try:
            os.startfile(output_dir)
        except (AttributeError, OSError):
            # 仅在 Windows 上运行；若资源管理器暂时无法打开，不影响已完成的任务。
            pass

    def _finish_processing(self, msg):
        output_dir = self.active_target_dir
        should_open_output = (
            not self.cancel_event.is_set()
            and output_dir is not None
            and os.path.isdir(output_dir)
        )
        self.is_processing = False
        self.active_target_dir = None
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED, text="暂停")
        self.cancel_btn.config(state=tk.DISABLED)
        self.status_label.config(text=msg)
        messagebox.showinfo("任务结束", msg)
        if should_open_output:
            # 等完成提示关闭后再交给资源管理器，避免新窗口遮住提示框。
            self.root.after(1, self._open_output_folder, output_dir)





def launch_tkinter_ui():
    root = tk.Tk()
    saved_lang = get_saved_language()
    current_lang = saved_lang if saved_lang else get_system_language()
    app_title = APP_TITLES.get(current_lang, APP_TITLES['zh-CN'])
    root.title(app_title)
    icon_file = _resource_path('hanji.ico')
    if os.path.exists(icon_file):
        try:
            root.iconbitmap(icon_file)
        except Exception:
            pass
    app = ImageProcessorApp(root)
    root.mainloop()


def main():
    # 在 Windows 上设置明确的 AppUserModelID，确保任务栏正确显示程序独立图标
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('c2bw.imageprocessor.gui.v36')
        except Exception:
            pass

    if '--tk' in sys.argv or '--classic' in sys.argv:
        launch_tkinter_ui()
    else:
        try:
            launch_web_ui()
        except Exception as e:
            try:
                launch_tkinter_ui()
            except Exception:
                import traceback
                err_str = traceback.format_exc()
                try:
                    with open(os.path.join(tempfile.gettempdir(), "c2bw_startup_error.log"), "w", encoding="utf-8") as lf:
                        lf.write(err_str)
                except Exception:
                    pass
                try:
                    import tkinter.messagebox as mb
                    mb.showerror("启动异常", f"程序启动发生错误：\n{str(e)}\n\n详情已记录。")
                except Exception:
                    pass


if __name__ == '__main__':
    main()
