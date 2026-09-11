"""
c2bw.android - 安卓端宿主模块
包含：Flask REST API 本地服务、Android SAF (Storage Access Framework) 适配层、URI ↔ 临时文件同步及 Android WebView 启动
"""

import os
import sys
import io
import shutil
import tempfile
import threading
import logging
from flask import Flask, request, jsonify, send_from_directory, abort

from c2bw.core import (
    PDF_APPLICATION_NAME,
    PDF_SPEC_VERSION,
    get_system_language,
    clean_cancelled_output,
)
from c2bw.service import (
    ImageProcessorService,
    get_saved_language,
    save_user_language,
)


IS_ANDROID = ('ANDROID_ARGUMENT' in os.environ) or ('PYTHON_SERVICE_ARGUMENT' in os.environ)


class AndroidSAFBridge:
    """
    Android 存储访问框架 (Storage Access Framework, SAF) 适配层。
    在 Android 10+ (Scoped Storage) 下，应用无法直接按传统 POSIX 文件路径访问外部存储；
    本类负责通过 Android ContentResolver 将用户通过 SAF 授权的 content:// URI
    流式传输到应用内部 Cache 目录供 c2bw.core 高速处理，并在完成后将结果输出写回 SAF 目标。
    在非 Android 环境中自动启用 Fallback 模式以便开发调试与测试。
    """

    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), 'c2bw_android_cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        self._j_context = None
        if IS_ANDROID:
            self._init_android_context()

    def _init_android_context(self):
        try:
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            self._j_context = PythonActivity.mActivity
        except Exception as e:
            logging.warning(f"无法初始化 Android 上下文: {e}")

    def get_cache_dir(self):
        return self.cache_dir

    def uri_to_cache_file(self, uri_str, subfolder='', filename=None):
        """
        将 content:// URI 的数据流式拷贝至内部缓存目录。
        返回本地 POSIX 绝对路径。
        """
        if not uri_str:
            return None

        # 若已是标准本地路径且存在，直接返回
        if os.path.exists(uri_str):
            return os.path.abspath(uri_str)

        target_dir = os.path.join(self.cache_dir, subfolder) if subfolder else self.cache_dir
        os.makedirs(target_dir, exist_ok=True)

        if not filename:
            filename = os.path.basename(uri_str.split('?')[0]) or 'imported_file'

        dest_path = os.path.join(target_dir, filename)

        if IS_ANDROID and self._j_context and uri_str.startswith('content://'):
            try:
                from jnius import autoclass
                Uri = autoclass('android.net.Uri')
                uri = Uri.parse(uri_str)
                cr = self._j_context.getContentResolver()
                in_stream = cr.openInputStream(uri)
                if in_stream is None:
                    raise IOError(f"无法打开 Android URI 输入流: {uri_str}")

                # 4KB 缓冲流式写入
                byte_array_class = autoclass('java.lang.reflect.Array')
                byte_class = autoclass('java.lang.Byte').TYPE
                buffer = byte_array_class.newInstance(byte_class, 4096)

                with open(dest_path, 'wb') as out_f:
                    while True:
                        read_bytes = in_stream.read(buffer)
                        if read_bytes == -1:
                            break
                        # 将 java byte array 转换为 python bytes
                        out_f.write(bytes(buffer[:read_bytes]))
                in_stream.close()
                return dest_path
            except Exception as e:
                logging.error(f"从 SAF URI 读取失败: {e}")
                raise IOError(f"读取 SAF URI 失败: {str(e)}")
        else:
            # 模拟/测试环境：若路径为有效文本，直接在缓存中建立模拟空文件或读取
            if not os.path.exists(dest_path):
                with open(dest_path, 'wb') as f:
                    f.write(b'')
            return dest_path

    def cache_file_to_uri(self, local_path, target_uri_str):
        """
        将缓存中生成好的文件写回目标 content:// URI。
        """
        if not os.path.isfile(local_path):
            raise IOError(f"本地文件不存在: {local_path}")

        if IS_ANDROID and self._j_context and target_uri_str.startswith('content://'):
            try:
                from jnius import autoclass
                Uri = autoclass('android.net.Uri')
                uri = Uri.parse(target_uri_str)
                cr = self._j_context.getContentResolver()
                out_stream = cr.openOutputStream(uri, 'w')
                if out_stream is None:
                    raise IOError(f"无法打开 Android URI 输出流: {target_uri_str}")

                with open(local_path, 'rb') as in_f:
                    while True:
                        chunk = in_f.read(4096)
                        if not chunk:
                            break
                        out_stream.write(chunk)
                out_stream.flush()
                out_stream.close()
                return True
            except Exception as e:
                logging.error(f"写回 SAF URI 失败: {e}")
                raise IOError(f"写入 SAF URI 失败: {str(e)}")
        else:
            # 模拟/测试环境
            if target_uri_str.startswith('content://'):
                return True
            shutil.copy2(local_path, target_uri_str)
            return True

    def clean_cache(self):
        """清理临时缓存目录。"""
        try:
            if os.path.exists(self.cache_dir):
                shutil.rmtree(self.cache_dir, ignore_errors=True)
                os.makedirs(self.cache_dir, exist_ok=True)
            return True
        except Exception:
            return False


def create_app(service=None, webui_dir=None):
    """
    创建并配置本地 Flask Web API 服务器。
    托管 Vue 前端静态资源，并向前端提供所有后端服务 RPC。
    """
    if service is None:
        service = ImageProcessorService()

    if webui_dir is None:
        # 定位项目中的 webui 目录
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        webui_dir = os.path.join(base_dir, 'webui')

    app = Flask(__name__, static_folder=None)
    saf_bridge = AndroidSAFBridge()

    # --- 静态资源托管 ---

    @app.route('/')
    def index():
        return send_from_directory(webui_dir, 'index.html')

    @app.route('/webui/<path:filename>')
    def serve_webui_subpath(filename):
        return send_from_directory(webui_dir, filename)

    @app.route('/<path:filename>')
    def serve_root_static(filename):
        if os.path.exists(os.path.join(webui_dir, filename)):
            return send_from_directory(webui_dir, filename)
        abort(404)

    # --- REST API 端点 ---

    @app.route('/api/poll_events', methods=['GET', 'POST'])
    def api_poll_events():
        return jsonify(service.poll_events())

    @app.route('/api/start_processing', methods=['POST'])
    def api_start_processing():
        data = request.get_json(silent=True) or {}
        args = data.get('args')
        settings = args[0] if (args and len(args) > 0) else data
        return jsonify(service.start_processing(settings))

    @app.route('/api/start_pdf_workflow', methods=['POST'])
    def api_start_pdf_workflow():
        data = request.get_json(silent=True) or {}
        args = data.get('args')
        settings = args[0] if (args and len(args) > 0) else data
        return jsonify(service.start_pdf_workflow(settings))

    @app.route('/api/start_pdf_extraction', methods=['POST'])
    def api_start_pdf_extraction():
        data = request.get_json(silent=True) or {}
        args = data.get('args') or []
        if len(args) >= 2:
            return jsonify(service.start_pdf_extraction(args[0], args[1]))
        return jsonify(service.start_pdf_extraction(
            data.get('pdf_path', ''),
            data.get('extract_dir', '')
        ))

    @app.route('/api/toggle_pause', methods=['POST'])
    def api_toggle_pause():
        return jsonify(service.toggle_pause())

    @app.route('/api/cancel_processing', methods=['POST'])
    def api_cancel_processing():
        return jsonify(service.cancel_processing())

    @app.route('/api/respond_pdf_prompt', methods=['POST'])
    def api_respond_pdf_prompt():
        data = request.get_json(silent=True) or {}
        args = data.get('args')
        generate_pdf = args[0] if (args and len(args) > 0) else data.get('generate_pdf', False)
        return jsonify(service.respond_pdf_prompt(generate_pdf))

    @app.route('/api/get_system_language', methods=['GET', 'POST'])
    def api_get_system_language():
        return jsonify({'ok': True, 'language': get_system_language()})

    @app.route('/api/get_user_language', methods=['GET', 'POST'])
    def api_get_user_language():
        return jsonify(service.get_user_language())

    @app.route('/api/set_user_language', methods=['POST'])
    def api_set_user_language():
        data = request.get_json(silent=True) or {}
        args = data.get('args')
        lang = args[0] if (args and len(args) > 0) else data.get('language', 'zh-CN')
        return jsonify(service.set_user_language(lang))

    @app.route('/api/get_update_info', methods=['GET', 'POST'])
    def api_get_update_info():
        return jsonify(service.get_update_info())

    @app.route('/api/open_download_url', methods=['POST'])
    def api_open_download_url():
        data = request.get_json(silent=True) or {}
        args = data.get('args')
        url = args[0] if (args and len(args) > 0) else data.get('url', '')
        return jsonify(service.open_download_url(url))

    @app.route('/api/open_output_folder', methods=['POST'])
    def api_open_output_folder():
        return jsonify(service.open_output_folder())

    # --- SAF 相关接口 ---

    @app.route('/api/saf/import', methods=['POST'])
    def api_saf_import():
        data = request.get_json(silent=True) or {}
        uri = data.get('uri')
        filename = data.get('filename')
        try:
            local_path = saf_bridge.uri_to_cache_file(uri, filename=filename)
            return jsonify({'ok': True, 'path': local_path})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)})

    @app.route('/api/saf/export', methods=['POST'])
    def api_saf_export():
        data = request.get_json(silent=True) or {}
        local_path = data.get('local_path')
        target_uri = data.get('target_uri')
        try:
            saf_bridge.cache_file_to_uri(local_path, target_uri)
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)})

    # --- 通用 RPC 映射，确保前端调用任何 service 方法均能自动适配 ---

    @app.route('/api/<method_name>', methods=['POST'])
    def api_generic_dispatch(method_name):
        if not hasattr(service, method_name):
            return jsonify({'ok': False, 'error': f'未知的 API 方法：{method_name}'}), 404

        method = getattr(service, method_name)
        data = request.get_json(silent=True) or {}
        args = data.get('args', [])
        if not isinstance(args, list):
            args = [args]

        try:
            result = method(*args)
            return jsonify(result if isinstance(result, dict) else {'ok': True, 'result': result})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    return app


def main(host='127.0.0.1', port=5000):
    """安卓端主启动入口。"""
    service = ImageProcessorService()
    app = create_app(service=service)

    if IS_ANDROID:
        # 在 Android 下，启动后台线程运行 Flask，随后通过 WebView 加载
        server_thread = threading.Thread(
            target=lambda: app.run(host=host, port=port, threaded=True, use_reloader=False),
            daemon=True,
        )
        server_thread.start()

        # 尝试初始化 Android WebView
        try:
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity

            WebView = autoclass('android.webkit.WebView')
            WebViewClient = autoclass('android.webkit.WebViewClient')
            WebSettings = autoclass('android.webkit.WebSettings')

            def setup_webview():
                webview = WebView(activity)
                settings = webview.getSettings()
                settings.setJavaScriptEnabled(True)
                settings.setDomStorageEnabled(True)
                settings.setAllowFileAccess(True)
                webview.setWebViewClient(WebViewClient())
                activity.setContentView(webview)
                webview.loadUrl(f"http://{host}:{port}/")

            activity.runOnUiThread(setup_webview)
        except Exception as e:
            print(f"Android WebView 初始化提示: {e}")
    else:
        print(f"c2bw Android 模拟模式已启动：http://{host}:{port}/")
        app.run(host=host, port=port, threaded=True)


if __name__ == '__main__':
    main()
