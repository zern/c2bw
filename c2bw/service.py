"""
c2bw.service - 任务调度与状态机模块
包含：ImageProcessorService（start/pause/cancel/events/配置持久化）
"""

import os
import sys
import io
import shutil
import re
import json
import queue
import threading
import concurrent.futures
import urllib.request
import webbrowser

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


APP_TITLES = {
    'zh-CN': '智能图像预处理工具 v3.7',
    'zh-TW': '智能圖像預處理工具 v3.7',
    'ja': 'スマート画像前処理ツール v3.7',
    'en': 'Smart Image Preprocessor v3.7',
}

QUIT_CONFIRMATIONS = {
    'zh-CN': '任务可能仍在运行，确定要退出吗？',
    'zh-TW': '任務可能仍在運行，確定要退出嗎？',
    'ja': 'タスクが実行中の可能性があります。終了しますか？',
    'en': 'A task may still be running. Are you sure you want to quit?',
}

UPDATE_INFO_URL = 'https://tools.hanjihebi.com/aisoft/c2bw_update.json'


def get_config_dir():
    """获取用户配置存储目录，优先使用 %APPDATA%/c2bw 或 ~/.c2bw"""
    app_data = os.environ.get('APPDATA')

    if app_data:
        config_dir = os.path.join(app_data, 'c2bw')
    else:
        config_dir = os.path.join(os.path.expanduser('~'), '.c2bw')
    return config_dir


def get_config_path():
    """获取配置文件路径。若程序同目录下已有 c2bw_config.json 则优先使用（便携模式），否则保存在用户配置目录中。"""
    try:
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_config = os.path.join(base_dir, 'c2bw_config.json')
        if os.path.exists(local_config):
            return local_config
    except Exception:
        pass
    return os.path.join(get_config_dir(), 'config.json')


def load_user_config():
    """读取用户配置字典"""
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_user_config(cfg):
    """保存用户配置字典"""
    config_path = get_config_path()
    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def get_saved_language():
    """获取用户已保存的语言习惯；若无有效设置则返回空字符串"""
    cfg = load_user_config()
    lang = cfg.get('language')
    if lang in ('zh-CN', 'zh-TW', 'ja', 'en'):
        return lang
    return ''


def save_user_language(lang):
    """保存用户的语言习惯（保持关闭时的语言状态）"""
    if lang in ('zh-CN', 'zh-TW', 'ja', 'en'):
        cfg = load_user_config()
        cfg['language'] = lang
        return save_user_config(cfg)
    return False


class ImageProcessorService:
    """供 Web UI / Pywebview 及 RPC 调用的线程安全核心业务服务。"""

    UPDATE_INFO_URL = UPDATE_INFO_URL
    PDF_APPLICATION_NAME = PDF_APPLICATION_NAME
    PDF_SPEC_VERSION = PDF_SPEC_VERSION

    def __init__(self):
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
        self.current_language = None

    def bind_window(self, window):
        self.window = window

    def get_update_info(self):
        """从服务器读取更新描述；网络不可用时静默跳过。"""
        try:
            request = urllib.request.Request(
                self.UPDATE_INFO_URL,
                headers={'User-Agent': 'SHUGE-C2BW/3.7'},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8-sig'))
            if not isinstance(data, dict) or not data.get('version'):
                raise ValueError('服务器返回的更新信息格式无效。')
            return {'ok': True, 'current_version': '3.7.0.0', 'update': data, 'source': 'server'}
        except Exception:
            return {'ok': False, 'current_version': '3.7.0.0'}

    def open_download_url(self, url):
        try:
            value = str(url or '').strip()
            if not value.lower().startswith(('http://', 'https://')):
                return {'ok': False, 'error': '下载地址无效。'}
            webbrowser.open(value)
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def validate_dir_settings(self, raw_settings):
        """校验并规范化文件夹处理任务参数。"""
        try:
            raw_settings = raw_settings or {}
            source_text = str(raw_settings.get('source_dir', '')).strip()
            target_text = str(raw_settings.get('target_dir', '')).strip()

            size_opt_mode = str(raw_settings.get('size_opt_mode', '')).strip()
            if not size_opt_mode:
                non_bin_format = str(raw_settings.get('non_bin_format', 'keep')).strip()
                if non_bin_format == 'jpg80':
                    size_opt_mode = 'custom'
                else:
                    size_opt_mode = 'original'
            elif size_opt_mode == 'keep':
                size_opt_mode = 'original'
            elif size_opt_mode == 'jpg80':
                size_opt_mode = 'custom'

            custom_scale = int(raw_settings.get('custom_scale', 80))
            custom_quality = int(raw_settings.get('custom_quality', 80))

            settings = {
                'work_mode': 'dir',
                'source_dir': os.path.abspath(source_text) if source_text else '',
                'target_dir': os.path.abspath(target_text) if target_text else '',
                'include_subfolders': bool(raw_settings.get('include_subfolders', False)),
                'keep_images_after_pdf': bool(raw_settings.get('keep_images_after_pdf', False)),
                'enable_binarize': bool(raw_settings.get('enable_binarize', True)),
                'bin_method': str(raw_settings.get('bin_method', '0')),
                'threshold_val': int(raw_settings.get('threshold_val', 50)),
                'size_opt_mode': size_opt_mode,
                'custom_scale': custom_scale,
                'custom_quality': custom_quality,
                'non_bin_format': 'keep' if size_opt_mode == 'original' else 'jpg80',
                'enable_crop': bool(raw_settings.get('enable_crop', True)),
                'crop_percent': int(raw_settings.get('crop_percent', 50)),
                'crop_direction': str(raw_settings.get('crop_direction', 'R2L')),
                'exclude_ratio': float(raw_settings.get('exclude_ratio', 0.8)),
                'max_threads': int(raw_settings.get('max_threads', 8)),
                'enable_pdf': bool(raw_settings.get('enable_pdf', False)),
                'lang': str(raw_settings.get('lang', '')),
            }
        except (TypeError, ValueError, OverflowError):
            return None, '请使用有效的数字填写线程数、阈值和裁切参数。'

        if not settings['source_dir'] or not os.path.isdir(settings['source_dir']):
            return None, '请选择一个存在的输入目录。'
        if not settings['target_dir']:
            settings['target_dir'] = os.path.join(settings['source_dir'], 'output')
        if is_same_or_parent(settings['target_dir'], settings['source_dir']):
            return None, '输出目录不能等于输入目录，也不能是输入目录的上级目录。'
        if os.path.exists(settings['target_dir']) and not os.path.isdir(settings['target_dir']):
            return None, '输出路径已存在，但不是目录。'
        if os.path.islink(settings['target_dir']):
            return None, '输出目录不能是链接或快捷目录。'
        if os.path.isdir(settings['target_dir']) and os.listdir(settings['target_dir']):
            return None, '为避免覆盖已有文件，请选择一个不存在或空的输出目录。'
        if not settings['enable_binarize'] and not settings['enable_crop']:
            if settings['size_opt_mode'] == 'original' and not settings['enable_pdf']:
                return None, '请至少启用一种处理任务（色彩处理、分页裁切或文件大小优化），或勾选合并输出为 PDF。'
        if settings['bin_method'] not in ('0', '1'):
            return None, '二值化方式无效。'
        if settings['size_opt_mode'] not in ('original', 'mobile', 'custom'):
            return None, '文件大小优化选项无效。'
        if settings['size_opt_mode'] == 'custom':
            if not (1 <= settings['custom_scale'] <= 100):
                return None, '自定义缩放比例必须在 1 到 100 之间。'
            if not (1 <= settings['custom_quality'] <= 100):
                return None, '自定义 JPEG 质量必须在 1 到 100 之间。'
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

    _validated_web_settings = validate_dir_settings

    def validate_pdf_settings(self, raw_settings):
        """校验并规范化 PDF 处理流水线参数。"""
        try:
            raw_settings = raw_settings or {}
            pdf_path = str(raw_settings.get('pdf_path', '')).strip()
            if not pdf_path or not os.path.isfile(pdf_path) or not pdf_path.lower().endswith('.pdf'):
                return None, '请先选择有效的待处理 PDF 文件。'

            enable_crop = bool(raw_settings.get('enable_crop', True))
            enable_binarize = bool(raw_settings.get('enable_binarize', True))
            no_convert_pdf = bool(raw_settings.get('no_convert_pdf', False))

            size_opt_mode = str(raw_settings.get('size_opt_mode', '')).strip()
            if not size_opt_mode:
                non_bin_format = str(raw_settings.get('non_bin_format', 'keep')).strip()
                if non_bin_format == 'jpg80':
                    size_opt_mode = 'custom'
                else:
                    size_opt_mode = 'original'
            elif size_opt_mode == 'keep':
                size_opt_mode = 'original'
            elif size_opt_mode == 'jpg80':
                size_opt_mode = 'custom'

            custom_scale = int(raw_settings.get('custom_scale', 80))
            custom_quality = int(raw_settings.get('custom_quality', 80))

            if not enable_crop and not enable_binarize:
                if not no_convert_pdf and size_opt_mode == 'original':
                    return None, '请至少启用一种处理任务（裁切、黑白二值化或文件大小优化）。'

            pdf_dir = os.path.dirname(pdf_path)
            pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
            if not enable_crop and not enable_binarize and no_convert_pdf and size_opt_mode == 'original':
                task_dir = os.path.join(pdf_dir, pdf_name)
                final_pdf_path = ''
            else:
                suffix = get_task_suffix(enable_crop, enable_binarize, size_opt_mode=size_opt_mode)
                task_dir = os.path.join(pdf_dir, f"{pdf_name}{suffix}")
                final_pdf_path = os.path.abspath(os.path.join(task_dir, f"{pdf_name}{suffix}.pdf"))

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
                'bin_method': str(raw_settings.get('bin_method', '0')),
                'threshold_val': int(raw_settings.get('threshold_val', 50)),
                'size_opt_mode': size_opt_mode,
                'custom_scale': custom_scale,
                'custom_quality': custom_quality,
                'non_bin_format': 'keep' if size_opt_mode == 'original' else 'jpg80',
                'enable_crop': enable_crop,
                'crop_percent': int(raw_settings.get('crop_percent', 50)),
                'crop_direction': str(raw_settings.get('crop_direction', 'R2L')),
                'exclude_ratio': float(raw_settings.get('exclude_ratio', 0.8)),
                'max_threads': int(raw_settings.get('max_threads', 8)),
                'enable_pdf': not no_convert_pdf,
                'lang': str(raw_settings.get('lang', '')),
            }
        except (TypeError, ValueError, OverflowError):
            return None, '请使用有效的数字填写线程数、阈值和裁切参数。'

        if settings['bin_method'] not in ('0', '1'):
            return None, '二值化方式无效。'
        if settings['size_opt_mode'] not in ('original', 'mobile', 'custom'):
            return None, '文件大小优化选项无效。'
        if settings['size_opt_mode'] == 'custom':
            if not (1 <= settings['custom_scale'] <= 100):
                return None, '自定义缩放比例必须在 1 到 100 之间。'
            if not (1 <= settings['custom_quality'] <= 100):
                return None, '自定义 JPEG 质量必须在 1 到 100 之间。'
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

    _validated_pdf_settings = validate_pdf_settings

    def start_pdf_workflow(self, raw_settings):
        with self.state_lock:
            if self.is_processing:
                return {'ok': False, 'error': '当前已有任务正在运行。'}

            settings, error = self.validate_pdf_settings(raw_settings)
            if error:
                return {'ok': False, 'error': error}

            while True:
                try:
                    self.ui_events.get_nowait()
                except queue.Empty:
                    break

            self.is_processing = True
            self.is_paused = False
            self.phase = 'extracting_pdf'
            self.active_target_dir = settings['clean_dir']
            self.last_output_dir = settings['clean_dir']
            self.pending_pdf = None
            self.cancel_event.clear()
            self.pause_event.set()

        threading.Thread(
            target=self._run_pdf_pipeline_safely,
            args=(settings,),
            daemon=True,
        ).start()
        return {'ok': True, 'target_dir': settings['clean_dir']}

    def start_processing(self, raw_settings):
        with self.state_lock:
            if self.is_processing:
                return {'ok': False, 'error': '当前已有任务正在运行。'}

            settings, error = self.validate_dir_settings(raw_settings)
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

    def start_pdf_extraction(self, pdf_path, extract_dir):
        """确认后在后台启动 PDF 分页图片提取。"""
        with self.state_lock:
            if self.is_processing:
                return {'ok': False, 'error': '当前已有任务正在运行。'}
            if not pdf_path or not os.path.isfile(pdf_path):
                return {'ok': False, 'error': '无效的 PDF 文件路径。'}
            if not extract_dir:
                return {'ok': False, 'error': '无效的目标提取目录。'}

            while True:
                try:
                    self.ui_events.get_nowait()
                except queue.Empty:
                    break

            self.is_processing = True
            self.is_paused = False
            self.phase = 'extracting_pdf'
            self.active_target_dir = None
            self.last_output_dir = None
            self.pending_pdf = None
            self.cancel_event.clear()
            self.pause_event.set()

        def _do_extract():
            def _progress(cur, total, msg):
                self.ui_events.put(('progress', (cur / total) * 100, msg))

            lang = get_system_language()
            self.ui_events.put(('status', get_backend_text('status_extracting_pdf', lang)))
            count, err = extract_images_from_pdf(
                pdf_path, extract_dir, progress_callback=_progress, cancel_event=self.cancel_event
            )
            with self.state_lock:
                self.is_processing = False
                self.phase = 'idle'
            self.ui_events.put(('pdf_extracted', pdf_path, extract_dir, count, err))

        threading.Thread(target=_do_extract, daemon=True).start()
        return {'ok': True, 'pdf_path': pdf_path, 'extract_dir': extract_dir}

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
                    clean_cancelled_output(settings['target_dir']),
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

        summary['images_kept'] = True
        summary_msg = build_task_report(summary, settings['target_dir'])
        self.ui_events.put(('finish', summary_msg))
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
            elif event_type == 'pdf_extracted':
                frontend_events.append({
                    'type': 'pdf_extracted',
                    'pdf_path': event[1],
                    'extract_dir': event[2],
                    'count': event[3],
                    'error': event[4],
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
            if hasattr(os, 'startfile'):
                os.startfile(output_dir)
            elif sys.platform == 'darwin':
                import subprocess
                subprocess.Popen(['open', output_dir])
            else:
                import subprocess
                subprocess.Popen(['xdg-open', output_dir])
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': f'无法打开输出目录：{str(e)}'}

    def _sync_window_localization(self, lang):
        if not self.window:
            return
        title = APP_TITLES.get(lang, APP_TITLES['zh-CN'])
        quit_msg = QUIT_CONFIRMATIONS.get(lang, QUIT_CONFIRMATIONS['zh-CN'])
        try:
            self.window.set_title(title)
        except Exception:
            pass
        try:
            if hasattr(self.window, 'localization') and isinstance(self.window.localization, dict):
                self.window.localization['global.quitConfirmation'] = quit_msg
            if hasattr(self.window, 'localization_override') and isinstance(self.window.localization_override, dict):
                self.window.localization_override['global.quitConfirmation'] = quit_msg
        except Exception:
            pass
        try:
            import webview.platforms.winforms as winforms_platform
            browser_form = winforms_platform.BrowserView.instances.get(self.window.uid)
            if browser_form and hasattr(browser_form, 'localization') and isinstance(browser_form.localization, dict):
                browser_form.localization['global.quitConfirmation'] = quit_msg
        except Exception:
            pass
        try:
            import webview.localization
            webview.localization.original_localization['global.quitConfirmation'] = quit_msg
        except Exception:
            pass

    def get_user_language(self):
        saved = get_saved_language()
        sys_lang = get_system_language()
        effective = saved if saved else sys_lang
        self.current_language = effective
        self._sync_window_localization(effective)
        return {
            'ok': True,
            'language': effective,
            'saved_language': saved,
            'system_language': sys_lang,
            'effective_language': effective,
        }

    def set_user_language(self, lang):
        if isinstance(lang, (list, tuple)) and len(lang) > 0:
            lang = lang[0]
        if isinstance(lang, str):
            lang = lang.strip()
        if lang in ('zh-CN', 'zh-TW', 'ja', 'en'):
            self.current_language = lang
            save_user_language(lang)
            self._sync_window_localization(lang)
            return {'ok': True, 'language': lang}
        return {'ok': False, 'error': f'Invalid language: {lang}'}

    # --- 任务执行核心流程 ---

    def process_single_image(self, src_path, rel_path, filename, output_stem, settings):
        return process_single_image(
            src_path, rel_path, filename, output_stem, settings,
            pause_event=self.pause_event,
            cancel_event=self.cancel_event,
            write_lock=self.output_write_lock,
        )

    def _assign_output_stems(self, tasks):
        return assign_output_stems(tasks)

    def _natural_sort_key(self, path):
        return natural_sort_key(path)

    def _build_and_save_task_report(self, summary, target_dir, **kwargs):
        return build_task_report(summary, target_dir, **kwargs)

    def _clean_cancelled_output(self, target_dir, lang='zh-CN'):
        return clean_cancelled_output(target_dir, lang=lang)

    def _build_single_pdf(self, image_paths, pdf_path, settings, progress_state=None):
        def _cb(pct, msg):
            self.ui_events.put(('progress', pct, msg))
        return build_single_pdf(
            image_paths, pdf_path, settings=settings,
            progress_state=progress_state,
            cancel_event=self.cancel_event,
            progress_callback=_cb,
        )

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
        lang = settings.get('lang') or get_system_language()
        self.ui_events.put(('status', get_backend_text('status_packing_pdf', lang)))
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

        # 检查是否保留处理后的图片（从图片目录开始处理时，默认不保留）
        if not settings.get('keep_images_after_pdf', False):
            self.ui_events.put(('status', get_backend_text('status_cleaning_images', lang)))
            generated_pdf_set = {
                os.path.normcase(os.path.abspath(p)) for p in generated_pdfs
            }
            # 删除所有被合并到 PDF 中的图片文件
            for _, image_paths in groups:
                for img_path in image_paths:
                    abs_p = os.path.normcase(os.path.abspath(img_path))
                    if abs_p not in generated_pdf_set and os.path.isfile(img_path):
                        try:
                            os.remove(img_path)
                        except OSError:
                            pass
            # 自底向上清理 target_dir 内空出来的子文件夹
            for root, dirs, files in os.walk(target_dir, topdown=False):
                if os.path.normcase(os.path.abspath(root)) != os.path.normcase(os.path.abspath(target_dir)):
                    try:
                        if not os.listdir(root):
                            os.rmdir(root)
                    except OSError:
                        pass

        return True, generated_pdfs

    def _generate_pdf_and_finish(self, settings, summary):
        """在后台生成 PDF，并将结果交给主线程显示。"""
        success, result = self._build_pdf(settings)
        if self.cancel_event.is_set():
            lang = settings.get('lang') or get_system_language()
            self.ui_events.put(('finish', self._clean_cancelled_output(settings['target_dir'], lang=lang)))
            return

        pdf_count = len(result) if success else 0
        pdf_error = None if success else result
        pdf_path = result[0] if (success and len(result) == 1) else None
        summary['images_kept'] = settings.get('keep_images_after_pdf', False)
        summary_msg = self._build_and_save_task_report(
            summary,
            settings['target_dir'],
            pdf_count=pdf_count,
            pdf_error=pdf_error,
            keep_images=summary['images_kept'],
            pdf_path=pdf_path,
        )
        self.ui_events.put(('finish', summary_msg))

    def _run_direct_image_to_pdf_task(self, settings):
        """当不选择色彩处理和分页处理，保持原格式与品质且勾选合并为PDF时，直接将输入目录的图片打包为PDF。"""
        src_dir = settings['source_dir']
        tgt_dir = settings['target_dir']
        include_subfolders = settings.get('include_subfolders', False)
        valid_exts = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2'}

        try:
            os.makedirs(tgt_dir, exist_ok=True)
        except OSError as e:
            self.ui_events.put(('finish', f"创建输出目录失败：{str(e)}"))
            return

        images_by_dir = {}
        tgt_abs = os.path.normcase(os.path.realpath(tgt_dir))

        if include_subfolders:
            for root, dirs, files in os.walk(src_dir):
                dirs[:] = [
                    d for d in dirs
                    if os.path.normcase(os.path.realpath(os.path.join(root, d))) != tgt_abs
                ]
                for file in files:
                    if os.path.splitext(file)[1].lower() in valid_exts:
                        images_by_dir.setdefault(root, []).append(os.path.join(root, file))
        else:
            if os.path.exists(src_dir):
                for file in os.listdir(src_dir):
                    full_path = os.path.join(src_dir, file)
                    if os.path.isfile(full_path) and os.path.splitext(file)[1].lower() in valid_exts:
                        images_by_dir.setdefault(src_dir, []).append(full_path)

        total_files = sum(len(paths) for paths in images_by_dir.values())
        if total_files == 0:
            lang = settings.get('lang') or get_system_language()
            self.ui_events.put(('finish', get_backend_text('err_no_eligible_images', lang)))
            return

        group_targets = []
        if include_subfolders:
            sorted_dirs = sorted(
                images_by_dir.items(),
                key=lambda item: self._natural_sort_key(
                    os.path.relpath(item[0], src_dir)
                ),
            )
            for dir_path, image_paths in sorted_dirs:
                image_paths.sort(
                    key=lambda path: self._natural_sort_key(
                        os.path.relpath(path, dir_path)
                    )
                )
                rel_dir = os.path.relpath(dir_path, src_dir)
                if rel_dir == '.' or not rel_dir:
                    folder_name = os.path.basename(os.path.normpath(src_dir)) or 'output'
                    pdf_path = os.path.join(tgt_dir, f"{folder_name}.pdf")
                else:
                    folder_name = os.path.basename(os.path.normpath(dir_path))
                    rel_parent = os.path.dirname(rel_dir)
                    pdf_parent_dir = os.path.join(tgt_dir, rel_parent) if rel_parent else tgt_dir
                    os.makedirs(pdf_parent_dir, exist_ok=True)
                    pdf_path = os.path.join(pdf_parent_dir, f"{folder_name}.pdf")
                group_targets.append((dir_path, image_paths, pdf_path))
        else:
            all_images = [p for paths in images_by_dir.values() for p in paths]
            all_images.sort(key=lambda path: self._natural_sort_key(os.path.relpath(path, src_dir)))
            folder_name = os.path.basename(os.path.normpath(src_dir)) or 'output'
            pdf_path = os.path.join(tgt_dir, f"{folder_name}.pdf")
            group_targets.append((src_dir, all_images, pdf_path))

        pdf_total = sum(len(paths) for _, paths, _ in group_targets)
        pdf_done = 0
        generated_pdfs = []
        lang = settings.get('lang') or get_system_language()
        self.ui_events.put(('status', get_backend_text('status_direct_pdf', lang)))

        for dir_path, image_paths, pdf_path in group_targets:
            if self.cancel_event.is_set():
                self.ui_events.put(('finish', self._clean_cancelled_output(tgt_dir, lang=lang)))
                return

            pdf_name = os.path.basename(pdf_path)
            self.ui_events.put(('status', get_backend_text('status_direct_pdf_file', lang, name=pdf_name)))
            success, result = self._build_single_pdf(
                image_paths, pdf_path, settings,
                progress_state=[pdf_done, pdf_total],
            )
            pdf_done = min(pdf_total, pdf_done + len(image_paths))

            if self.cancel_event.is_set():
                self.ui_events.put(('finish', self._clean_cancelled_output(tgt_dir, lang=lang)))
                return

            if not success:
                for g_pdf in generated_pdfs:
                    try:
                        os.remove(g_pdf)
                    except OSError:
                        pass
                self.ui_events.put(('finish', f"生成 PDF 失败：{result}"))
                return
            generated_pdfs.append(result)

        summary = {
            'settings': settings,
            'total_input': total_files,
            'total': total_files,
            'succeeded': total_files,
            'total_output_images': total_files,
            'excluded_single_count': total_files,
            'cropped_double_count': 0,
            'errors': [],
            'collision_groups': 0,
            'images_kept': True,
            'direct_pdf': True,
        }
        summary_msg = self._build_and_save_task_report(
            summary,
            tgt_dir,
            pdf_count=len(generated_pdfs),
            pdf_path=generated_pdfs[0] if len(generated_pdfs) == 1 else None,
            keep_images=True,
        )
        self.ui_events.put(('finish', summary_msg))

    def _run_task_safely(self, settings):
        try:
            self._run_task(settings)
        except Exception as e:
            self.ui_events.put(('finish', f"任务异常终止：{str(e)}"))

    def _run_task(self, settings):
        size_opt_mode = settings.get('size_opt_mode') or settings.get('non_bin_format', 'original')
        if size_opt_mode == 'keep':
            size_opt_mode = 'original'
        if (
            not settings.get('enable_binarize')
            and not settings.get('enable_crop')
            and size_opt_mode == 'original'
            and settings.get('enable_pdf')
        ):
            self._run_direct_image_to_pdf_task(settings)
            return

        src_dir = settings['source_dir']
        tgt_dir = settings['target_dir']
        include_subfolders = settings['include_subfolders']
        valid_exts = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2'}
        tasks = []
        
        tgt_abs = os.path.normcase(os.path.realpath(tgt_dir))

        if include_subfolders:
            for root, dirs, files in os.walk(src_dir):
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
            if os.path.exists(src_dir):
                for file in os.listdir(src_dir):
                    full_path = os.path.join(src_dir, file)
                    if os.path.isfile(full_path) and os.path.splitext(file)[1].lower() in valid_exts:
                        tasks.append((full_path, file, file))

        total_files = len(tasks)
        if total_files == 0:
            lang = settings.get('lang') or get_system_language()
            self.ui_events.put(('finish', get_backend_text('err_no_eligible_images', lang)))
            return

        tasks, collision_groups = self._assign_output_stems(tasks)
        processed = 0
        succeeded = 0
        errors = []
        total_output_images = 0
        excluded_single_count = 0
        cropped_double_count = 0

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
                    result = make_result(False, f"错误 {task[2]}: {str(e)}", str(e), is_single=False, is_excluded_single=False, output_count=0)
                processed += 1
                if result['ok']:
                    succeeded += 1
                    total_output_images += result.get('output_count', 1)
                    if result.get('is_excluded_single'):
                        excluded_single_count += 1
                    elif not result.get('is_single'):
                        cropped_double_count += 1
                elif not self.cancel_event.is_set():
                    errors.append(f"{task[1]}：{result['error'] or result['message']}")
                self.ui_events.put((
                    'progress',
                    (processed / total_files) * 100,
                    result['message'],
                ))

        summary = {
            'settings': settings,
            'total_input': total_files,
            'total': total_files,
            'succeeded': succeeded,
            'total_output_images': total_output_images,
            'excluded_single_count': excluded_single_count,
            'cropped_double_count': cropped_double_count,
            'errors': errors,
            'collision_groups': collision_groups,
            'images_kept': True,
        }

        lang = settings.get('lang') or get_system_language()
        if self.cancel_event.is_set():
            self.ui_events.put(('finish', self._clean_cancelled_output(tgt_dir, lang=lang)))
        else:
            if settings['enable_pdf']:
                self.ui_events.put(('status', get_backend_text('status_generating_summary_pdf', lang)))
                self._generate_pdf_and_finish(settings, summary)
            else:
                self.ui_events.put(('ask_pdf', settings, summary))

    def _run_pdf_pipeline_safely(self, settings):
        try:
            self._run_pdf_pipeline(settings)
        except Exception as e:
            lang = settings.get('lang') or get_system_language()
            self.ui_events.put(('finish', get_backend_text('err_pdf_abort', lang, error=str(e))))

    def _run_pdf_pipeline(self, settings):
        pdf_path = settings['pdf_path']
        raw_dir = settings['source_dir']
        out_dir = settings['target_dir']
        final_pdf = settings['final_pdf_path']

        lang = settings.get('lang') or get_system_language()
        self.ui_events.put(('status', get_backend_text('status_extracting_pdf', lang)))
        def _extract_progress(cur, total, msg):
            self.ui_events.put(('progress', (cur / total) * 100, msg))

        extracted_count, err = extract_images_from_pdf(
            pdf_path, raw_dir, progress_callback=_extract_progress, cancel_event=self.cancel_event
        )
        if self.cancel_event.is_set():
            self.ui_events.put(('finish', self._clean_cancelled_output(settings['clean_dir'], lang=lang)))
            return
        if err:
            self.ui_events.put(('finish', get_backend_text('err_pdf_extract', lang, error=err)))
            return

        size_opt_mode = settings.get('size_opt_mode') or settings.get('non_bin_format', 'original')
        if size_opt_mode == 'keep':
            size_opt_mode = 'original'
        if (
            not settings.get('enable_crop', False)
            and not settings.get('enable_binarize', False)
            and size_opt_mode == 'original'
            and (settings.get('no_convert_pdf', False) or not settings.get('enable_pdf', True))
        ):
            summary = {
                'settings': settings,
                'total_input': extracted_count,
                'total': extracted_count,
                'succeeded': extracted_count,
                'total_output_images': extracted_count,
                'excluded_single_count': extracted_count,
                'cropped_double_count': 0,
                'errors': [],
                'collision_groups': 0,
                'images_kept': True,
            }
            summary_msg = self._build_and_save_task_report(summary, raw_dir)
            self.ui_events.put(('finish', summary_msg))
            return

        valid_exts = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2'}
        tasks = []
        if os.path.exists(raw_dir):
            for file in os.listdir(raw_dir):
                full_path = os.path.join(raw_dir, file)
                if os.path.isfile(full_path) and os.path.splitext(file)[1].lower() in valid_exts:
                    tasks.append((full_path, file, file))

        total_files = len(tasks)
        if total_files == 0:
            self.ui_events.put(('finish', get_backend_text('err_no_images_in_pdf', lang)))
            return

        tasks, collision_groups = self._assign_output_stems(tasks)
        processed = 0
        succeeded = 0
        errors = []
        total_output_images = 0
        excluded_single_count = 0
        cropped_double_count = 0

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
                    result = make_result(False, f"错误 {task[2]}: {str(e)}", str(e), is_single=False, is_excluded_single=False, output_count=0)
                processed += 1
                if result['ok']:
                    succeeded += 1
                    total_output_images += result.get('output_count', 1)
                    if result.get('is_excluded_single'):
                        excluded_single_count += 1
                    elif not result.get('is_single'):
                        cropped_double_count += 1
                elif not self.cancel_event.is_set():
                    errors.append(f"{task[1]}：{result['error'] or result['message']}")
                self.ui_events.put((
                    'progress',
                    (processed / total_files) * 100,
                    result['message'],
                ))

        if self.cancel_event.is_set():
            self.ui_events.put(('finish', self._clean_cancelled_output(settings['clean_dir'], lang=lang)))
            return

        if settings.get('no_convert_pdf', False) or not settings.get('enable_pdf', True):
            self.ui_events.put(('status', get_backend_text('status_organizing', lang)))
            try:
                for fname in os.listdir(raw_dir):
                    fpath = os.path.join(raw_dir, fname)
                    if os.path.normcase(os.path.abspath(fpath)) == os.path.normcase(os.path.abspath(out_dir)):
                        continue
                    try:
                        if os.path.isfile(fpath) or os.path.islink(fpath):
                            os.remove(fpath)
                        elif os.path.isdir(fpath):
                            shutil.rmtree(fpath, ignore_errors=True)
                    except OSError:
                        pass

                if os.path.isdir(out_dir):
                    for fname in os.listdir(out_dir):
                        src_item = os.path.join(out_dir, fname)
                        dst_item = os.path.join(raw_dir, fname)
                        if os.path.exists(dst_item):
                            if os.path.isdir(dst_item):
                                shutil.rmtree(dst_item, ignore_errors=True)
                            else:
                                os.remove(dst_item)
                        shutil.move(src_item, dst_item)
                    shutil.rmtree(out_dir, ignore_errors=True)
            except Exception:
                pass

            summary = {
                'settings': settings,
                'total_input': extracted_count,
                'total': total_files,
                'succeeded': succeeded,
                'total_output_images': total_output_images,
                'excluded_single_count': excluded_single_count,
                'cropped_double_count': cropped_double_count,
                'errors': errors,
                'collision_groups': collision_groups,
                'images_kept': True,
            }
            summary_msg = self._build_and_save_task_report(summary, raw_dir)
            self.ui_events.put(('finish', summary_msg))
            return

        self.ui_events.put(('status', get_backend_text('status_packing_new_pdf', lang)))
        processed_images = []
        for root, _, files in os.walk(out_dir):
            for filename in files:
                if os.path.splitext(filename)[1].lower() in valid_exts:
                    processed_images.append(os.path.join(root, filename))

        if not processed_images:
            self.ui_events.put(('finish', get_backend_text('err_no_images_for_pdf', lang)))
            return

        processed_images.sort(
            key=lambda path: self._natural_sort_key(os.path.relpath(path, out_dir))
        )

        pdf_success, pdf_res = self._build_single_pdf(
            processed_images, final_pdf, settings,
            progress_state=[0, len(processed_images)]
        )

        if self.cancel_event.is_set():
            self.ui_events.put(('finish', self._clean_cancelled_output(settings['clean_dir'], lang=lang)))
            return

        if not pdf_success:
            self.ui_events.put(('finish', get_backend_text('err_pdf_gen_failed', lang, error=pdf_res)))
            return

        self.ui_events.put(('status', get_backend_text('status_cleaning_temp', lang)))
        try:
            if os.path.isdir(out_dir):
                shutil.rmtree(out_dir, ignore_errors=True)
            for fname in os.listdir(raw_dir):
                fpath = os.path.join(raw_dir, fname)
                if os.path.normcase(os.path.abspath(fpath)) == os.path.normcase(os.path.abspath(final_pdf)):
                    continue
                if fname.lower() in ['task_report.txt', 'task_log.txt']:
                    continue
                try:
                    if os.path.isfile(fpath) or os.path.islink(fpath):
                        os.remove(fpath)
                    elif os.path.isdir(fpath):
                        shutil.rmtree(fpath, ignore_errors=True)
                except OSError:
                    pass
        except Exception:
            pass

        summary = {
            'settings': settings,
            'total_input': extracted_count,
            'total': total_files,
            'succeeded': succeeded,
            'total_output_images': total_output_images,
            'excluded_single_count': excluded_single_count,
            'cropped_double_count': cropped_double_count,
            'errors': errors,
            'collision_groups': collision_groups,
            'images_kept': False,
        }
        summary_msg = self._build_and_save_task_report(
            summary, raw_dir,
            pdf_path=final_pdf,
            pdf_count=1,
            keep_images=False,
        )
        self.ui_events.put(('finish', summary_msg))


# 兼容性别名
WebImageProcessorService = ImageProcessorService

__all__ = [
    'APP_TITLES',
    'QUIT_CONFIRMATIONS',
    'UPDATE_INFO_URL',
    'get_config_dir',
    'get_config_path',
    'load_user_config',
    'save_user_config',
    'get_saved_language',
    'save_user_language',
    'ImageProcessorService',
    'WebImageProcessorService',
]
