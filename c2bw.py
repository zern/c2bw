import os
import sys
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont
import threading
import concurrent.futures
import queue
import re
import json
import webbrowser
import urllib.request
import numpy as np
from PIL import Image, JpegImagePlugin, PdfImagePlugin  # 显式导入以确保打包程序包含 PDF 编码器。
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject
import webview

class ImageProcessorApp:
    PDF_APPLICATION_NAME = "SHUGE.ORG"

    def __init__(self, root):
        self.root = root
        self.root.title("智能图像预处理工具 v3.0")
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
        self.source_dir = tk.StringVar()
        self.target_dir = tk.StringVar()
        self.max_threads = tk.IntVar(value=8)
        
        # 新增：包含子文件夹选项，默认为 False
        self.include_subfolders = tk.BooleanVar(value=False)
        
        self.enable_binarize = tk.BooleanVar(value=True) 
        self.bin_method = tk.StringVar(value="0") 
        self.threshold_val = tk.IntVar(value=50)  

        # 新增：取消二值化后的输出格式选项 ("keep"=保持原格式, "jpg80"=转换为JPG质量80)
        self.non_bin_format = tk.StringVar(value="keep")
        
        self.enable_crop = tk.BooleanVar(value=True) 
        self.crop_percent = tk.IntVar(value=50)   
        self.crop_direction = tk.StringVar(value="R2L") 
        self.exclude_ratio = tk.DoubleVar(value=0.7) 
        self.enable_pdf = tk.BooleanVar(value=False)

        # --- 状态与线程控制 ---
        self.is_processing = False
        self.is_paused = tk.BooleanVar(value=False)
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self.pause_event.set()
        self.ui_events = queue.Queue()
        self.active_target_dir = None
        # Pillow 的部分编码器在 Windows 上并不保证多线程同时写入时稳定；
        # 计算仍可并行，最终落盘则串行并使用临时文件，避免留下半个文件。
        self.output_write_lock = threading.Lock()

        self._build_ui()

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

        # --- 1. 目录选择区域 ---
        dir_frame = ttk.Frame(main_frame)
        dir_frame.grid(row=0, column=0, sticky=tk.EW, pady=(0, 10))
        ttk.Label(dir_frame, text="输入目录:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(dir_frame, textvariable=self.source_dir, width=47).grid(row=0, column=1, pady=5, padx=10)
        ttk.Button(dir_frame, text="浏览...", command=self.select_source_dir).grid(row=0, column=2, pady=5)

        ttk.Label(dir_frame, text="输出目录:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(dir_frame, textvariable=self.target_dir, width=47).grid(row=1, column=1, pady=5, padx=10)
        ttk.Button(dir_frame, text="浏览...", command=lambda: self.select_dir(self.target_dir)).grid(row=1, column=2, pady=5)

        # 新增：包含子文件夹的复选框
        ttk.Checkbutton(dir_frame, text="处理子文件夹内的文件 (自动排除输出目录)", variable=self.include_subfolders).grid(row=2, column=1, sticky=tk.W, pady=5, padx=5)

        # --- 2. 二值化参数区域 ---
        bin_frame = ttk.LabelFrame(main_frame, text="色彩处理", padding="15")
        bin_frame.grid(row=1, column=0, sticky=tk.EW, pady=8)

        cb_bin = ttk.Checkbutton(bin_frame, text="转化为黑白二值图 (压缩体积，强化文字)", variable=self.enable_binarize, command=self.toggle_bin_options)
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

        # 新增：取消二值化时可选的输出格式
        self.non_bin_options_frame = ttk.Frame(bin_frame)
        self.non_bin_options_frame.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(10, 0))

        ttk.Label(self.non_bin_options_frame, text="不二值化时的输出格式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        non_bin_radio_frame = ttk.Frame(self.non_bin_options_frame)
        non_bin_radio_frame.grid(row=0, column=1, sticky=tk.W, pady=5)

        self.rb_keep_format = ttk.Radiobutton(non_bin_radio_frame, text="保持原始图片格式", variable=self.non_bin_format, value="keep")
        self.rb_keep_format.pack(side=tk.LEFT, padx=10)

        self.rb_jpg80 = ttk.Radiobutton(non_bin_radio_frame, text="转换为JPG (质量80)", variable=self.non_bin_format, value="jpg80")
        self.rb_jpg80.pack(side=tk.LEFT, padx=10)

        # --- 3. 裁切参数区域 ---
        crop_frame = ttk.LabelFrame(main_frame, text="分页处理", padding="15")
        crop_frame.grid(row=2, column=0, sticky=tk.EW, pady=8)

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

        # --- 4. 性能与控制区域 ---
        sys_frame = ttk.Frame(main_frame)
        sys_frame.grid(row=3, column=0, sticky=tk.EW, pady=20)
        
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
        self.progress_bar.grid(row=4, column=0, sticky=tk.EW, pady=10)

        # 操作按钮独占一行，避免与性能选项争夺水平空间。
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=5, column=0, sticky=tk.EW, pady=(0, 8))

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
        self.status_label.grid(row=6, column=0, sticky=tk.EW)
        
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

    def select_dir(self, var):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            var.set(folder_selected)

    def toggle_bin_options(self):
        state = tk.NORMAL if self.enable_binarize.get() else tk.DISABLED
        self.rb_otsu.config(state=state)
        self.rb_custom.config(state=state)
        if not self.enable_binarize.get():
            self.thresh_entry.config(state=tk.DISABLED)
        else:
            self.toggle_threshold()

        # 取消二值化时才可选择输出格式，二者状态互斥
        non_bin_state = tk.DISABLED if self.enable_binarize.get() else tk.NORMAL
        self.rb_keep_format.config(state=non_bin_state)
        self.rb_jpg80.config(state=non_bin_state)

    def toggle_threshold(self):
        if self.enable_binarize.get() and self.bin_method.get() == "1":
            self.thresh_entry.config(state=tk.NORMAL)
        else:
            self.thresh_entry.config(state=tk.DISABLED)

    def toggle_crop_options(self):
        state = tk.NORMAL if self.enable_crop.get() else tk.DISABLED
        for child in self.crop_options_frame.winfo_children():
            if isinstance(child, ttk.Frame):
                for subchild in child.winfo_children():
                    subchild.config(state=state)
            else:
                child.config(state=state)

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
    def _calculate_otsu_threshold(self, img_array):
        counts, _ = np.histogram(img_array, bins=256, range=(0, 256))
        if counts.sum() == 0:
            return 127

        # OTSU 对单一灰度值的图片没有可用的类间方差；直接给出稳定阈值。
        nonzero_bins = np.flatnonzero(counts)
        if len(nonzero_bins) == 1:
            return max(0, int(nonzero_bins[0]) - 1)

        p = counts / counts.sum()
        omega = np.cumsum(p)
        mu = np.cumsum(p * np.arange(256))
        mu_t = mu[-1]
        
        with np.errstate(divide='ignore', invalid='ignore'):
            sigma_b_squared = (mu_t * omega - mu)**2 / (omega * (1 - omega))
            
        if np.all(np.isnan(sigma_b_squared)):
            return 127
        return int(np.nanargmax(sigma_b_squared))

    @staticmethod
    def _get_jpeg_save_options(source_img):
        """尽量保留源 JPEG 的量化表和色度抽样，避免裁切后默认变成质量 95。"""
        quantization = getattr(source_img, 'quantization', None)
        if not quantization:
            return {}

        options = {'qtables': quantization}
        subsampling = JpegImagePlugin.get_sampling(source_img)
        if subsampling != -1:
            options['subsampling'] = subsampling
        if 'dpi' in source_img.info:
            options['dpi'] = source_img.info['dpi']
        if 'icc_profile' in source_img.info:
            options['icc_profile'] = source_img.info['icc_profile']
        return options

    def _save_image_atomically(self, pil_img, output_path, image_format, **save_options):
        """写入同目录临时文件，成功后再替换，避免异常时出现残缺图片。"""
        temporary_path = f"{output_path}.{threading.get_ident()}.part"
        with self.output_write_lock:
            try:
                pil_img.save(temporary_path, format=image_format, **save_options)
                os.replace(temporary_path, output_path)
            finally:
                if os.path.exists(temporary_path):
                    try:
                        os.remove(temporary_path)
                    except OSError:
                        pass

    def _copy_file_atomically(self, source_path, output_path):
        """复制不需重新编码的原图，同样不暴露未完成的目标文件。"""
        temporary_path = f"{output_path}.{threading.get_ident()}.part"
        with self.output_write_lock:
            try:
                shutil.copy2(source_path, temporary_path)
                os.replace(temporary_path, output_path)
            finally:
                if os.path.exists(temporary_path):
                    try:
                        os.remove(temporary_path)
                    except OSError:
                        pass

    def save_image(self, pil_img, out_path_base, original_ext, settings, jpeg_save_options=None):
        """保存一张处理结果，并返回最终输出路径。"""
        if settings['enable_binarize']:
            gray_img = None
            final_img = None
            try:
                gray_img = pil_img.convert('L')
                img_array = np.array(gray_img)

                if settings['bin_method'] == "0":
                    t_val = self._calculate_otsu_threshold(img_array)
                else:
                    t_val = int((settings['threshold_val'] / 100.0) * 255)

                binary_array = (img_array > t_val).astype(np.uint8) * 255
                final_img = Image.fromarray(binary_array).convert('1')
                output_path = f"{out_path_base}.tif"
                self._save_image_atomically(
                    final_img,
                    output_path,
                    'TIFF',
                    compression='group4',
                )
                return output_path
            finally:
                if final_img is not None:
                    final_img.close()
                if gray_img is not None:
                    gray_img.close()

        if settings['non_bin_format'] == "jpg80":
            # 转换为JPG，质量80。JPEG不支持透明通道/调色板，需先转RGB。
            rgb_img = pil_img.convert('RGB') if pil_img.mode in ('RGBA', 'P', 'LA') else pil_img
            try:
                output_path = f"{out_path_base}.jpg"
                self._save_image_atomically(rgb_img, output_path, 'JPEG', quality=80)
                return output_path
            finally:
                if rgb_img is not pil_img:
                    rgb_img.close()

        # 保持原始图片格式。
        output_path = f"{out_path_base}{original_ext}"
        format_by_extension = {
            '.png': 'PNG', '.tif': 'TIFF', '.tiff': 'TIFF',
            '.bmp': 'BMP', '.jp2': 'JPEG2000',
        }
        if original_ext in ('.jpg', '.jpeg'):
            options = jpeg_save_options or {}
            try:
                self._save_image_atomically(pil_img, output_path, 'JPEG', **options)
            except OSError:
                converted = pil_img.convert('RGB')
                try:
                    self._save_image_atomically(converted, output_path, 'JPEG', **options)
                finally:
                    converted.close()
        else:
            image_format = format_by_extension[original_ext]
            try:
                self._save_image_atomically(pil_img, output_path, image_format)
            except OSError:
                converted = pil_img.convert('RGB')
                try:
                    self._save_image_atomically(converted, output_path, image_format)
                finally:
                    converted.close()
        return output_path

    @staticmethod
    def _remove_outputs(output_paths):
        """某一原图的双页写入失败时，回滚已经写好的另一页。"""
        for output_path in output_paths:
            try:
                if os.path.isfile(output_path):
                    os.remove(output_path)
            except OSError:
                pass

    @staticmethod
    def _result(ok, message, error=None):
        return {'ok': ok, 'message': message, 'error': error}

    def process_single_image(self, src_path, rel_path, filename, output_stem, settings):
        self.pause_event.wait()
        
        if self.cancel_event.is_set():
            return self._result(False, "中止", "任务已取消")

        output_paths = []
        img = None
        try:
            img = Image.open(src_path)
            w, h = img.size 
            
            if h <= 0:
                return self._result(False, f"跳过: 图片高度为0 {filename}", "图片高度为 0")
                
            aspect_ratio = w / h
            original_ext = os.path.splitext(filename)[1].lower()
            if original_ext not in ['.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2']:
                return self._result(False, f"跳过: 非支持的扩展名 {filename}", "不支持的扩展名")

            out_dir = os.path.join(settings['target_dir'], os.path.dirname(rel_path))
            os.makedirs(out_dir, exist_ok=True)
            base_name = output_stem
            jpeg_save_options = self._get_jpeg_save_options(img)

            if not settings['enable_crop'] or aspect_ratio < settings['exclude_ratio']:
                # 未二值化、保持格式且未实际裁切时，直接复制源文件以完整保留 JPEG 品质与元数据。
                if not settings['enable_binarize'] and settings['non_bin_format'] == 'keep':
                    output_path = os.path.join(out_dir, f"{base_name}{original_ext}")
                    self._copy_file_atomically(src_path, output_path)
                    output_paths.append(output_path)
                else:
                    # 先完全解码，在任何输出写入前暴露损坏图片等读取错误。
                    img.load()
                    path_base = os.path.join(out_dir, base_name)
                    output_paths.append(self.save_image(
                        img, path_base, original_ext, settings, jpeg_save_options,
                    ))
                return self._result(True, f"处理完成 (单页): {filename}")

            # 一次只保留一个裁切页，降低大图在多线程下的峰值内存。
            img.load()
            crop_ratio = settings['crop_percent'] / 100.0
            split_width = int(w * crop_ratio)

            if settings['crop_direction'] == "R2L":
                crop_jobs = [
                    ((w - split_width, 0, w, h), f"{base_name}_A"),
                    ((0, 0, split_width, h), f"{base_name}_B"),
                ]
            else:
                crop_jobs = [
                    ((0, 0, split_width, h), f"{base_name}_A"),
                    ((w - split_width, 0, w, h), f"{base_name}_B"),
                ]

            for crop_box, crop_base_name in crop_jobs:
                cropped_img = img.crop(crop_box)
                try:
                    output_paths.append(self.save_image(
                        cropped_img,
                        os.path.join(out_dir, crop_base_name),
                        original_ext,
                        settings,
                        jpeg_save_options,
                    ))
                finally:
                    cropped_img.close()
            return self._result(True, f"处理完成 (裁切): {filename}")

        except Exception as e:
            self._remove_outputs(output_paths)
            return self._result(False, f"错误 {filename}: {str(e)}", str(e))
        finally:
            if img is not None:
                img.close()

    # =========================================================

    @staticmethod
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
                'source_dir': os.path.abspath(source_text) if source_text else '',
                'target_dir': os.path.abspath(target_text) if target_text else '',
                'include_subfolders': self.include_subfolders.get(),
                'enable_binarize': self.enable_binarize.get(),
                'bin_method': self.bin_method.get(),
                'threshold_val': self.threshold_val.get(),
                'non_bin_format': self.non_bin_format.get(),
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
            messagebox.showwarning("操作无效", "请至少勾选一种处理任务！")
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

    def _run_task_safely(self, settings):
        """确保扫描或调度异常也能恢复界面状态。"""
        try:
            self._run_task(settings)
        except Exception as e:
            self.ui_events.put(('finish', f"任务异常终止：{str(e)}"))

    @staticmethod
    def _natural_sort_key(path):
        """按文件的相对路径自然排序，使 page_2 位于 page_10 之前。"""
        return [
            int(part) if part.isdigit() else part.casefold()
            for part in re.split(r'(\d+)', path)
        ]

    def _assign_output_stems(self, tasks):
        """为会归并到同一输出名的源文件分配稳定、无冲突的文件名前缀。

        例如 ``page.jpg`` 与 ``page.png`` 都二值化时，旧版本都会写成
        ``page_A.tif``、``page_B.tif``；多个线程会互相覆盖，表现为只得到
        半页或随机失败。第一个保留原名，后续文件附加来源扩展名。
        """
        groups = {}
        reserved_stems = {}
        for task in tasks:
            _, rel_path, filename = task
            relative_dir = os.path.normcase(os.path.dirname(rel_path))
            base_name = os.path.splitext(filename)[0]
            stem_key = os.path.normcase(base_name)
            groups.setdefault((relative_dir, stem_key), []).append(task)
            reserved_stems.setdefault(relative_dir, set()).add(stem_key)

        assigned = []
        used_stems = {}
        collision_groups = 0
        for group_key in sorted(groups):
            relative_dir, _ = group_key
            same_stem_tasks = sorted(
                groups[group_key],
                key=lambda task: self._natural_sort_key(task[1]),
            )
            if len(same_stem_tasks) > 1:
                collision_groups += 1

            used = used_stems.setdefault(relative_dir, set())
            for index, task in enumerate(same_stem_tasks):
                _, _, filename = task
                base_name = os.path.splitext(filename)[0]
                candidate = base_name
                if index:
                    extension_name = os.path.splitext(filename)[1].lstrip('.').casefold() or 'file'
                    candidate = f"{base_name}__{extension_name}"
                    suffix = 2
                    while (
                        os.path.normcase(candidate) in reserved_stems[relative_dir]
                        or os.path.normcase(candidate) in used
                    ):
                        candidate = f"{base_name}__{extension_name}_{suffix}"
                        suffix += 1
                used.add(os.path.normcase(candidate))
                assigned.append((*task, candidate))

        return assigned, collision_groups

    @staticmethod
    def _completion_text(summary, pdf_count=None, pdf_error=None):
        lines = [f"处理完成！成功处理 {summary['succeeded']} / {summary['total']} 个文件。"]
        if summary['collision_groups']:
            lines.append(
                f"已为 {summary['collision_groups']} 组同名不同格式文件自动附加来源扩展名，避免互相覆盖。"
            )
        if summary['errors']:
            lines.append(f"失败或跳过 {len(summary['errors'])} 个文件：")
            lines.extend(f"- {item}" for item in summary['errors'][:5])
            if len(summary['errors']) > 5:
                lines.append(f"- 另有 {len(summary['errors']) - 5} 个文件未完成。")
        if pdf_count is not None:
            lines.append(f"已生成 {pdf_count} 个 PDF。")
        if pdf_error:
            lines.append(f"PDF 生成失败：{pdf_error}")
        return '\n'.join(lines)

    def _build_pdf(self, settings):
        """按输出目录汇总图片；递归处理时，每个目录生成自己的 PDF。"""
        valid_exts = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2'}
        target_dir = settings['target_dir']
        images_by_dir = {}

        for root, _, files in os.walk(target_dir):
            for filename in files:
                if os.path.splitext(filename)[1].lower() in valid_exts:
                    images_by_dir.setdefault(root, []).append(os.path.join(root, filename))

        if not images_by_dir:
            return False, "未找到可合并为 PDF 的输出图片。"

        if settings['include_subfolders']:
            groups = sorted(
                images_by_dir.items(),
                key=lambda item: self._natural_sort_key(
                    os.path.relpath(item[0], target_dir)
                ),
            )
        else:
            # 非递归模式保留原行为：将所有结果合并为输入文件夹同名的 PDF。
            groups = [(target_dir, [
                path
                for paths in images_by_dir.values()
                for path in paths
            ])]

        generated_pdfs = []
        pdf_total = sum(len(paths) for _, paths in groups)
        pdf_done = 0
        self.ui_events.put(('status', '正在打包 PDF...'))
        for output_dir, image_paths in groups:
            image_paths.sort(
                key=lambda path: self._natural_sort_key(
                    os.path.relpath(path, output_dir)
                )
            )

            if os.path.normcase(os.path.abspath(output_dir)) == os.path.normcase(os.path.abspath(target_dir)):
                folder_name = os.path.basename(os.path.normpath(settings['source_dir'])) or 'output'
                pdf_parent_dir = target_dir
            else:
                folder_name = os.path.basename(os.path.normpath(output_dir))
                # 子目录 PDF 放到其父目录，与该子目录并列。
                pdf_parent_dir = os.path.dirname(output_dir)
            pdf_path = os.path.join(pdf_parent_dir, f"{folder_name}.pdf")

            success, result = self._build_single_pdf(
                image_paths, pdf_path, settings,
                progress_state=[pdf_done, pdf_total],
            )
            pdf_done = min(pdf_total, pdf_done + len(image_paths))
            if not success:
                for generated_pdf in generated_pdfs:
                    try:
                        os.remove(generated_pdf)
                    except OSError:
                        pass
                return False, result
            generated_pdfs.append(result)

        return True, generated_pdfs

    def _build_single_pdf(self, image_paths, pdf_path, settings, progress_state=None):
        """将同一个输出目录内的图片写入一个 PDF。"""
        pages = []
        try:
            for image_path in image_paths:
                if self.cancel_event.is_set():
                    return False, "PDF 生成已取消。"
                with Image.open(image_path) as image:
                    # 保留 1 位黑白页。Pillow 会将其编码为 CCITT Group 4，
                    # 避免原先转换 RGB 后使二值 TIFF 的体积急剧膨胀。
                    if image.mode in ('1', 'L', 'RGB', 'CMYK'):
                        pages.append(image.copy())
                    else:
                        # 带透明通道或调色板的图片仍转为 RGB，保证 PDF 兼容性。
                        pages.append(image.convert('RGB'))
                if progress_state is not None:
                    progress_state[0] += 1
                    total = max(1, progress_state[1])
                    self.ui_events.put((
                        'progress',
                        progress_state[0] * 100.0 / total,
                        f'正在打包 PDF：{progress_state[0]} / {total}',
                    ))

            first_page, *remaining_pages = pages
            first_page.save(
                pdf_path,
                format='PDF',
                save_all=True,
                append_images=remaining_pages,
                resolution=300.0,
                creator=self.PDF_APPLICATION_NAME,
                producer=self.PDF_APPLICATION_NAME,
            )
            self._set_pdf_open_to_fit_page(pdf_path)
            return True, pdf_path
        except Exception as e:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass
            return False, str(e)
        finally:
            for page in pages:
                page.close()

    @staticmethod
    def _set_pdf_open_to_fit_page(pdf_path):
        """写入 PDF 初始视图：打开时将第一页完整适配到阅读器窗口。"""
        temporary_path = f"{pdf_path}.tmp"
        try:
            with open(pdf_path, 'rb') as source_file:
                reader = PdfReader(source_file)
                if not reader.pages:
                    raise ValueError("PDF 中没有可设置的页面。")

                writer = PdfWriter()
                writer.clone_document_from_reader(reader)
                first_page = writer.pages[0].indirect_reference
                if first_page is None:
                    raise ValueError("PDF 首页引用无效。")

                writer._root_object[NameObject('/OpenAction')] = ArrayObject([
                    first_page,
                    NameObject('/Fit'),
                ])
                writer._root_object[NameObject('/PageLayout')] = NameObject('/SinglePage')
                with open(temporary_path, 'wb') as output_file:
                    writer.write(output_file)
            os.replace(temporary_path, pdf_path)
        except Exception:
            if os.path.exists(temporary_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass
            raise

    @staticmethod
    def _clean_cancelled_output(target_dir):
        try:
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            return f"任务已取消，输出目录已被成功删除。\n({target_dir})"
        except Exception as e:
            return f"任务已取消，但删除目录失败，请手动清理。\n原因: {str(e)}"

    def _generate_pdf_and_finish(self, settings, summary):
        """在后台生成 PDF，并将结果交给主线程显示。"""
        success, result = self._build_pdf(settings)
        if self.cancel_event.is_set():
            self.ui_events.put(('finish', self._clean_cancelled_output(settings['target_dir'])))
        elif success:
            self.ui_events.put((
                'finish',
                self._completion_text(summary, pdf_count=len(result)),
            ))
        else:
            self.ui_events.put((
                'finish',
                self._completion_text(summary, pdf_error=result),
            ))

    def _prompt_pdf_generation(self, settings, summary):
        """图片处理完成后，在主线程询问是否继续合并 PDF。"""
        message = "图片处理完成。是否合并输出为 PDF？"
        if settings['include_subfolders']:
            message += "\n\n已启用子文件夹处理，将按子文件夹分别生成 PDF。"

        if messagebox.askyesno("合并输出为 PDF", message, icon='question'):
            self.status_label.config(text="正在生成汇总 PDF...")
            self.pause_btn.config(state=tk.DISABLED)
            threading.Thread(
                target=self._generate_pdf_and_finish,
                args=(settings, summary),
                daemon=True,
            ).start()
            self.root.after(50, self._poll_ui_events)
        else:
            self._finish_processing(self._completion_text(summary))

    def _run_task(self, settings):
        src_dir = settings['source_dir']
        tgt_dir = settings['target_dir']
        include_subfolders = settings['include_subfolders']
        valid_exts = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2'}
        tasks = []
        
        tgt_abs = os.path.normcase(os.path.realpath(tgt_dir))

        if include_subfolders:
            # 勾选了包含子文件夹：使用 os.walk 并只过滤当前任务的输出目录。
            for root, dirs, files in os.walk(src_dir):
                # 动态修改 dirs 列表，避免递归处理本次任务刚生成的文件。
                dirs[:] = [
                    d for d in dirs
                    if os.path.normcase(os.path.realpath(os.path.join(root, d))) != tgt_abs
                ]
                
                for file in files:
                    if os.path.splitext(file)[1].lower() in valid_exts:
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, src_dir)
                        tasks.append((full_path, rel_path, file))
        else:
            # 未勾选：仅处理当前目录下的文件（原逻辑）
            if os.path.exists(src_dir):
                for file in os.listdir(src_dir):
                    full_path = os.path.join(src_dir, file)
                    if os.path.isfile(full_path) and os.path.splitext(file)[1].lower() in valid_exts:
                        tasks.append((full_path, file, file))

        total_files = len(tasks)
        if total_files == 0:
            self.ui_events.put(('finish', "未找到符合条件的图片文件！"))
            return

        tasks, collision_groups = self._assign_output_stems(tasks)
        processed = 0
        succeeded = 0
        errors = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=settings['max_threads']) as executor:
            future_to_file = {
                executor.submit(
                    self.process_single_image,
                    task[0], task[1], task[2], task[3], settings,
                ): task
                for task in tasks
            }
            
            for future in concurrent.futures.as_completed(future_to_file):
                task = future_to_file[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = self._result(False, f"错误 {task[2]}: {str(e)}", str(e))
                processed += 1
                if result['ok']:
                    succeeded += 1
                elif not self.cancel_event.is_set():
                    errors.append(f"{task[1]}：{result['error'] or result['message']}")
                self.ui_events.put((
                    'progress',
                    (processed / total_files) * 100,
                    result['message'],
                ))

        summary = {
            'total': total_files,
            'succeeded': succeeded,
            'errors': errors,
            'collision_groups': collision_groups,
        }

        if self.cancel_event.is_set():
            self.ui_events.put(('finish', self._clean_cancelled_output(tgt_dir)))
        else:
            if settings['enable_pdf']:
                self.ui_events.put(('status', '正在生成汇总 PDF...'))
                self._generate_pdf_and_finish(settings, summary)
            else:
                self.ui_events.put(('ask_pdf', settings, summary))

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
            else:
                self._finish_processing(event[1])
                return
        if self.is_processing:
            self.root.after(50, self._poll_ui_events)

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


class WebImageProcessorService(ImageProcessorApp):
    """供 Vue 界面调用的线程安全 Python 接口。"""

    def __init__(self):
        # 不初始化旧 Tk 界面，只复用经过验证的图像与 PDF 处理方法。
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self.pause_event.set()
        self.ui_events = queue.Queue()
        self.output_write_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.is_processing = False
        self.is_paused = False
        self.phase = 'idle'
        self.active_target_dir = None
        self.last_output_dir = None
        self.pending_pdf = None
        self.window = None

    def bind_window(self, window):
        self.window = window

    UPDATE_INFO_URL = 'https://tools.hanjihebi.com/aisoft/c2bw_update.json'

    def get_update_info(self):
        """从服务器读取更新描述；网络不可用时静默跳过。"""
        try:
            request = urllib.request.Request(
                self.UPDATE_INFO_URL,
                headers={'User-Agent': 'SHUGE-C2BW/3.0'},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8-sig'))
            if not isinstance(data, dict) or not data.get('version'):
                raise ValueError('服务器返回的更新信息格式无效。')
            return {'ok': True, 'current_version': '3.0.0.0', 'update': data, 'source': 'server'}
        except Exception:
            return {'ok': False, 'current_version': '3.0.0.0'}

    def open_download_url(self, url):
        try:
            value = str(url or '').strip()
            if not value.lower().startswith(('http://', 'https://')):
                return {'ok': False, 'error': '下载地址无效。'}
            webbrowser.open(value)
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def choose_directory(self, initial_directory=''):
        """显示系统目录选择器，返回所选路径。"""
        if self.window is None:
            return {'ok': False, 'error': '窗口尚未初始化。'}
        try:
            selected = self.window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=initial_directory or '',
                allow_multiple=False,
            )
            return {'ok': True, 'path': selected[0] if selected else ''}
        except Exception as e:
            return {'ok': False, 'error': f'无法打开目录选择器：{str(e)}'}

    def _validated_web_settings(self, raw_settings):
        try:
            raw_settings = raw_settings or {}
            source_text = str(raw_settings.get('source_dir', '')).strip()
            target_text = str(raw_settings.get('target_dir', '')).strip()
            settings = {
                'source_dir': os.path.abspath(source_text) if source_text else '',
                'target_dir': os.path.abspath(target_text) if target_text else '',
                'include_subfolders': bool(raw_settings.get('include_subfolders', False)),
                'enable_binarize': bool(raw_settings.get('enable_binarize', True)),
                'bin_method': str(raw_settings.get('bin_method', '0')),
                'threshold_val': int(raw_settings.get('threshold_val', 50)),
                'non_bin_format': str(raw_settings.get('non_bin_format', 'keep')),
                'enable_crop': bool(raw_settings.get('enable_crop', True)),
                'crop_percent': int(raw_settings.get('crop_percent', 50)),
                'crop_direction': str(raw_settings.get('crop_direction', 'R2L')),
                'exclude_ratio': float(raw_settings.get('exclude_ratio', 0.7)),
                'max_threads': int(raw_settings.get('max_threads', 8)),
                'enable_pdf': bool(raw_settings.get('enable_pdf', False)),
            }
        except (TypeError, ValueError, OverflowError):
            return None, '请使用有效的数字填写线程数、阈值和裁切参数。'

        if not settings['source_dir'] or not os.path.isdir(settings['source_dir']):
            return None, '请选择一个存在的输入目录。'
        if not settings['target_dir']:
            settings['target_dir'] = os.path.join(settings['source_dir'], 'output')
        if self._is_same_or_parent(settings['target_dir'], settings['source_dir']):
            return None, '输出目录不能等于输入目录，也不能是输入目录的上级目录。'
        if os.path.exists(settings['target_dir']) and not os.path.isdir(settings['target_dir']):
            return None, '输出路径已存在，但不是目录。'
        if os.path.islink(settings['target_dir']):
            return None, '输出目录不能是链接或快捷目录。'
        if os.path.isdir(settings['target_dir']) and os.listdir(settings['target_dir']):
            return None, '为避免覆盖已有文件，请选择一个不存在或空的输出目录。'
        if not settings['enable_binarize'] and not settings['enable_crop']:
            return None, '请至少启用一种处理任务。'
        if settings['bin_method'] not in ('0', '1'):
            return None, '二值化方式无效。'
        if settings['non_bin_format'] not in ('keep', 'jpg80'):
            return None, '非二值化输出格式无效。'
        if settings['crop_direction'] not in ('R2L', 'L2R'):
            return None, '阅读顺序无效。'
        if not 1 <= settings['max_threads'] <= 64:
            return None, '最大线程数必须在 1 到 64 之间。'
        if settings['enable_binarize'] and not 0 <= settings['threshold_val'] <= 100:
            return None, '自定义阈值必须在 0 到 100 之间。'
        if settings['enable_crop'] and not 1 <= settings['crop_percent'] <= 100:
            return None, '分割比例必须在 1 到 100 之间。'
        if settings['enable_crop'] and settings['exclude_ratio'] <= 0:
            return None, '排除单页比例必须大于 0。'
        return settings, None

    def start_processing(self, raw_settings):
        with self.state_lock:
            if self.is_processing:
                return {'ok': False, 'error': '当前已有任务正在运行。'}

            settings, error = self._validated_web_settings(raw_settings)
            if error:
                return {'ok': False, 'error': error}

            while True:
                try:
                    self.ui_events.get_nowait()
                except queue.Empty:
                    break

            self.is_processing = True
            self.is_paused = False
            self.phase = 'processing'
            self.active_target_dir = settings['target_dir']
            self.last_output_dir = settings['target_dir']
            self.pending_pdf = None
            self.cancel_event.clear()
            self.pause_event.set()

        threading.Thread(
            target=self._run_task_safely,
            args=(settings,),
            daemon=True,
        ).start()
        return {'ok': True, 'target_dir': settings['target_dir']}

    def toggle_pause(self):
        with self.state_lock:
            if not self.is_processing:
                return {'ok': False, 'error': '当前没有正在运行的任务。'}
            if self.phase != 'processing':
                return {'ok': False, 'error': '当前阶段不能暂停。'}
            if self.is_paused:
                self.pause_event.set()
                self.is_paused = False
                message = '处理已恢复...'
            else:
                self.pause_event.clear()
                self.is_paused = True
                message = '处理已暂停...（活动线程完成当前步骤后暂停）'
            return {'ok': True, 'paused': self.is_paused, 'message': message}

    def cancel_processing(self):
        with self.state_lock:
            if not self.is_processing:
                return {'ok': False, 'error': '当前没有正在运行的任务。'}
            self.cancel_event.set()
            self.pause_event.set()
            self.is_paused = False

            # 图片任务已完成、正在等待是否合并 PDF 时，没有后台线程负责清理。
            if self.pending_pdf is not None:
                settings, _ = self.pending_pdf
                self.pending_pdf = None
                self.phase = 'cancelling'
                self.ui_events.put((
                    'finish',
                    self._clean_cancelled_output(settings['target_dir']),
                ))
            else:
                self.phase = 'cancelling'
        return {'ok': True, 'message': '正在中止任务并清理输出目录...'}

    def respond_pdf_prompt(self, generate_pdf):
        with self.state_lock:
            if self.pending_pdf is None:
                return {'ok': False, 'error': '当前没有等待确认的 PDF 任务。'}
            settings, summary = self.pending_pdf
            self.pending_pdf = None
            if generate_pdf:
                self.phase = 'pdf'
            else:
                self.phase = 'finishing'

        if generate_pdf:
            threading.Thread(
                target=self._generate_pdf_and_finish,
                args=(settings, summary),
                daemon=True,
            ).start()
            return {'ok': True, 'message': '正在生成汇总 PDF...'}

        self.ui_events.put(('finish', self._completion_text(summary)))
        return {'ok': True, 'message': '处理完成。'}

    def poll_events(self):
        frontend_events = []
        for _ in range(100):
            try:
                event = self.ui_events.get_nowait()
            except queue.Empty:
                break

            event_type = event[0]
            if event_type == 'progress':
                frontend_events.append({
                    'type': 'progress',
                    'percent': event[1],
                    'message': event[2],
                })
            elif event_type == 'status':
                if 'PDF' in event[1]:
                    self.phase = 'pdf'
                frontend_events.append({'type': 'status', 'message': event[1]})
            elif event_type == 'ask_pdf':
                self.pending_pdf = (event[1], event[2])
                self.phase = 'awaiting_pdf'
                frontend_events.append({
                    'type': 'ask_pdf',
                    'include_subfolders': event[1]['include_subfolders'],
                })
            elif event_type == 'finish':
                with self.state_lock:
                    self.is_processing = False
                    self.is_paused = False
                    self.phase = 'idle'
                    self.active_target_dir = None
                    self.pending_pdf = None
                frontend_events.append({
                    'type': 'finish',
                    'message': event[1],
                    'can_open_output': bool(
                        self.last_output_dir
                        and os.path.isdir(self.last_output_dir)
                        and not self.cancel_event.is_set()
                    ),
                })

        return {
            'ok': True,
            'events': frontend_events,
            'processing': self.is_processing,
            'paused': self.is_paused,
            'phase': self.phase,
        }

    def open_output_folder(self):
        output_dir = self.last_output_dir
        if not output_dir or not os.path.isdir(output_dir):
            return {'ok': False, 'error': '输出目录不存在。'}
        try:
            os.startfile(output_dir)
            return {'ok': True}
        except OSError as e:
            return {'ok': False, 'error': f'无法打开输出目录：{str(e)}'}


class WebImageProcessorBridge:
    """仅暴露给 JavaScript 的窄接口，避免 Tk 继承成员影响 pywebview 反射。"""

    def __init__(self, service):
        self.service = service

    def choose_directory(self, initial_directory=''):
        return self.service.choose_directory(initial_directory)

    def get_update_info(self):
        return self.service.get_update_info()

    def open_download_url(self, url):
        return self.service.open_download_url(url)

    def start_processing(self, raw_settings):
        return self.service.start_processing(raw_settings)

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


def _resource_path(*parts):
    root_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root_dir, *parts)


def launch_web_ui():
    service = WebImageProcessorService()
    bridge = WebImageProcessorBridge(service)
    index_path = _resource_path('webui', 'index.html')
    window = webview.create_window(
        '智能图像预处理工具 v3.0',
        url=index_path,
        # 同时使用显式 expose，避免部分 Win7/MSHTML 环境在反射继承类时
        # 生成空的 API 列表。
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
        bridge.choose_directory,
        bridge.get_update_info,
        bridge.open_download_url,
        bridge.start_processing,
        bridge.toggle_pause,
        bridge.cancel_processing,
        bridge.respond_pdf_prompt,
        bridge.poll_events,
        bridge.open_output_folder,
    )

    # Win7 没有 WebView2，使用系统 IE11/MSHTML；新系统优先使用 WebView2。
    legacy_windows = sys.platform == 'win32' and sys.getwindowsversion().major <= 6
    webview.start(
        gui='mshtml' if legacy_windows else None,
        http_server=True,
        private_mode=True,
        localization={
            'global.quitConfirmation': '任务可能仍在运行，确定要退出吗？',
        },
    )


if __name__ == "__main__":
    launch_web_ui()
