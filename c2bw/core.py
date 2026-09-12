"""
c2bw.core - 纯算力核心模块
包含：PDF提取、图像裁切、OTSU自适应阈值计算、二值化(CCITT G4/JPEG/JP2)、PDF 1.5封装与生成
"""

import os
import sys
import io
import shutil
import re
import struct
import tempfile
import threading
import locale
import numpy as np
from PIL import Image, JpegImagePlugin, PdfImagePlugin, Jpeg2KImagePlugin
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, NameObject, StreamObject,
    BooleanObject, NumberObject, DictionaryObject, DecodedStreamObject
)
from pypdf.filters import decode_stream_data
import pypdf.filters

# 针对 pypdf 部分版本在遇到末尾缺少 '>' 的 ASCIIHexDecode 数据流时报错的兼容性补丁
_orig_asciihex_decode = pypdf.filters.ASCIIHexDecode.decode
def _safe_asciihex_decode(data, decode_parms=None):
    if isinstance(data, str):
        data = data.encode('ascii')
    stripped = data.rstrip()
    if not stripped.endswith(b'>'):
        data = stripped + b'>'
    return _orig_asciihex_decode(data, decode_parms)
pypdf.filters.ASCIIHexDecode.decode = staticmethod(_safe_asciihex_decode)

PDF_APPLICATION_NAME = "SHUGE.ORG"
PDF_SPEC_VERSION = b"%PDF-1.5"

def _resolve_pdf_xobject(page, img_id):
    """从页面对象中解析指定 ID 的 XObject 数据流对象。"""
    curr = page
    path = list(img_id) if isinstance(img_id, (list, tuple)) else [img_id]
    for part in path:
        if isinstance(part, str) and part.startswith('~') and part.endswith('~'):
            return None
        try:
            res = curr.get('/Resources')
            if hasattr(res, 'get_object'):
                res = res.get_object()
            if not res:
                return None
            xobjs = res.get('/XObject')
            if hasattr(xobjs, 'get_object'):
                xobjs = xobjs.get_object()
            if not xobjs or part not in xobjs:
                return None
            curr = xobjs[part]
            if hasattr(curr, 'get_object'):
                curr = curr.get_object()
        except Exception:
            return None
    return curr


def _extract_raw_image_from_xobj(xobj):
    """直接从 XObject 提取未重压缩的原始图片数据流，保证 100% 原始画质与参数不变。"""
    if not isinstance(xobj, StreamObject):
        return None, None

    filters = xobj.get('/Filter')
    if hasattr(filters, 'get_object'):
        filters = filters.get_object()
    if isinstance(filters, (list, tuple, ArrayObject)):
        filter_names = [f.get_object() if hasattr(f, 'get_object') else f for f in filters]
    elif filters:
        filter_names = [filters]
    else:
        filter_names = []

    try:
        data = decode_stream_data(xobj)
    except Exception:
        data = None

    if data:
        # 1. 检查已知图像格式文件头魔数
        if data.startswith(b'\xff\xd8'):
            return '.jpg', data
        if data.startswith(b'II*\x00') or data.startswith(b'MM\x00*'):
            return '.tif', data
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            return '.png', data
        if data.startswith(b'\x00\x00\x00\x0cjP  ') or data.startswith(b'\xffO\xffQ'):
            return '.jp2', data
        if data.startswith(b'BM'):
            return '.bmp', data

        # 2. 根据滤镜类型匹配对应扩展名（DCT 为原始 JPEG，JPX 为 JPEG2000，CCITT 为 TIFF）
        last_filter = filter_names[-1] if filter_names else None
        if last_filter in ('/DCTDecode', '/DCT'):
            return '.jpg', data
        if last_filter in ('/JPXDecode',):
            return '.jp2', data
        if last_filter in ('/CCITTFaxDecode',):
            return '.tif', data

    return None, None


def extract_images_from_pdf(pdf_path, extract_dir, progress_callback=None, cancel_event=None):
    """从图片打包型 PDF 中提取所有原始分页图片到指定目录（保持原图质量与参数，不作有损重压缩）。"""
    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        return 0, f"打开 PDF 失败: {str(e)}"

    total_pages = len(reader.pages)
    if total_pages == 0:
        return 0, "PDF 中没有有效页面。"

    os.makedirs(extract_dir, exist_ok=True)
    extracted_count = 0

    for p_idx, page in enumerate(reader.pages):
        if cancel_event and cancel_event.is_set():
            return extracted_count, "已取消提取。"
        page_num = p_idx + 1

        try:
            img_keys = list(page.images.keys())
        except Exception:
            img_keys = []

        # 兜底：若未获取到 keys，尝试直接从 Resources['/XObject'] 抓取
        if not img_keys:
            try:
                res = page.get('/Resources')
                if hasattr(res, 'get_object'):
                    res = res.get_object()
                if res and '/XObject' in res:
                    xobjs = res['/XObject']
                    if hasattr(xobjs, 'get_object'):
                        xobjs = xobjs.get_object()
                    if isinstance(xobjs, dict):
                        img_keys = [
                            k for k, v in xobjs.items()
                            if hasattr(v, 'get_object') and getattr(v.get_object(), 'get', lambda *_: None)('/Subtype') == '/Image'
                        ]
            except Exception:
                img_keys = []

        page_extracted = []
        for img_id in img_keys:
            if cancel_event and cancel_event.is_set():
                return extracted_count, "已取消提取。"

            ext = None
            data = None

            # 优先 1：直接从 XObject 解码原始数据流，避免二次重压缩带来的画质与参数损失
            xobj = _resolve_pdf_xobject(page, img_id)
            if xobj is not None:
                ext, data = _extract_raw_image_from_xobj(xobj)

            # 兜底 2：若非标准内嵌图像流（如 Flate 原始栅格位图或行内图片），回退至 pypdf 的无损转换
            if not data:
                try:
                    img_obj = page.images[img_id]
                    ext = os.path.splitext(img_obj.name)[1].lower()
                    data = img_obj.data
                except Exception:
                    data = None

            if not data:
                continue

            if not ext or ext not in ['.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2']:
                ext = '.jpg'
            if ext == '.jpeg':
                ext = '.jpg'
            elif ext == '.tiff':
                ext = '.tif'

            page_extracted.append((ext, data))

        total_in_page = len(page_extracted)
        for sub_idx, (ext, data) in enumerate(page_extracted):
            if total_in_page == 1:
                filename = f"page_{page_num:04d}{ext}"
            else:
                filename = f"page_{page_num:04d}_{sub_idx+1:02d}{ext}"
            save_path = os.path.join(extract_dir, filename)
            try:
                with open(save_path, 'wb') as f:
                    f.write(data)
                extracted_count += 1
            except Exception as write_err:
                return extracted_count, f"写入图片失败: {str(write_err)}"

        if progress_callback:
            progress_callback(page_num, total_pages, f"正在提取 PDF 原始图片：{page_num} / {total_pages} 页...")

    if extracted_count == 0:
        return 0, "该 PDF 中未检测到可提取的分页图片（可能为纯文本矢量排版或受保护）。"

    return extracted_count, ""


def get_task_suffix(enable_crop, enable_binarize, size_opt_mode='original'):
    """根据选择的处理任务生成对应的目录与文件后缀。"""
    parts = []
    if enable_crop:
        parts.append("已裁切")
    if enable_binarize:
        parts.append("黑白版")
    elif size_opt_mode == 'mobile':
        parts.append("手机优化版")
    elif size_opt_mode == 'custom':
        parts.append("已优化")
    if parts:
        return "_" + "_".join(parts)
    return ""


BACKEND_LOGS = {
    'zh-CN': {
        'status_scanning': '正在扫描文件...',
        'status_extracting_pdf': '正在读取并提取 PDF 原始分页图片...',
        'status_organizing': '正在整理处理后的图片...',
        'status_packing_new_pdf': '正在打包生成新 PDF...',
        'status_cleaning_temp': '正在清理临时分页图片...',
        'status_packing_pdf': '正在打包 PDF...',
        'status_cleaning_images': '正在清理处理后的图片...',
        'status_direct_pdf': '正在直接打包 PDF（保持原图品质）...',
        'status_direct_pdf_file': '正在直接打包 PDF: {name}...',
        'status_generating_summary_pdf': '正在生成汇总 PDF...',
        'err_pdf_abort': 'PDF 任务异常终止：{error}',
        'err_pdf_extract': 'PDF 提取失败：{error}',
        'err_no_images_in_pdf': '未从 PDF 中提取到可处理的图片文件！',
        'err_no_images_for_pdf': '未找到可合并为 PDF 的处理结果图片。',
        'err_pdf_gen_failed': '生成新 PDF 失败：{error}',
        'err_task_aborted': '任务异常终止：{error}',
        'err_create_output_dir': '创建输出目录失败：{error}',
        'err_no_eligible_images': '未找到符合条件的图片文件！',
    },
    'zh-TW': {
        'status_scanning': '正在掃描檔案...',
        'status_extracting_pdf': '正在讀取並提取 PDF 原始分頁圖片...',
        'status_organizing': '正在整理處理後的圖片...',
        'status_packing_new_pdf': '正在封裝生成新 PDF...',
        'status_cleaning_temp': '正在清理臨時分頁圖片...',
        'status_packing_pdf': '正在打包 PDF...',
        'status_cleaning_images': '正在清理處理後的圖片...',
        'status_direct_pdf': '正在直接打包 PDF（保持原圖品質）...',
        'status_direct_pdf_file': '正在直接打包 PDF: {name}...',
        'status_generating_summary_pdf': '正在生成匯總 PDF...',
        'err_pdf_abort': 'PDF 任務異常終止：{error}',
        'err_pdf_extract': 'PDF 提取失敗：{error}',
        'err_no_images_in_pdf': '未從 PDF 中提取到可處理的圖片檔案！',
        'err_no_images_for_pdf': '未找到可合併為 PDF 的處理結果圖片。',
        'err_pdf_gen_failed': '生成新 PDF 失敗：{error}',
        'err_task_aborted': '任務異常終止：{error}',
        'err_create_output_dir': '建立輸出目錄失敗：{error}',
        'err_no_eligible_images': '未找到符合條件的圖片檔案！',
    },
    'ja': {
        'status_scanning': 'ファイルをスキャン中...',
        'status_extracting_pdf': 'PDFから元画像を読み込み抽出中...',
        'status_organizing': '処理済み画像を整理中...',
        'status_packing_new_pdf': '新規PDFを生成中...',
        'status_cleaning_temp': '一時画像を消去中...',
        'status_packing_pdf': 'PDFを生成中...',
        'status_cleaning_images': '処理済み画像を消去中...',
        'status_direct_pdf': '元の品質を保持して直接PDFにパック中...',
        'status_direct_pdf_file': 'PDFを直接パック中: {name}...',
        'status_generating_summary_pdf': '統合PDFを生成中...',
        'err_pdf_abort': 'PDFタスクが異常終了しました: {error}',
        'err_pdf_extract': 'PDF画像抽出に失敗しました: {error}',
        'err_no_images_in_pdf': 'PDFから処理可能な画像が抽出されませんでした！',
        'err_no_images_for_pdf': 'PDF統合可能な処理結果画像が見つかりませんでした。',
        'err_pdf_gen_failed': '新規PDF生成に失敗しました: {error}',
        'err_task_aborted': 'タスクが異常終了しました: {error}',
        'err_create_output_dir': '出力フォルダの作成に失敗しました: {error}',
        'err_no_eligible_images': '条件に適合する画像ファイルが見つかりませんでした！',
    },
    'en': {
        'status_scanning': 'Scanning files...',
        'status_extracting_pdf': 'Reading and extracting images from PDF...',
        'status_organizing': 'Organizing processed images...',
        'status_packing_new_pdf': 'Generating new PDF...',
        'status_cleaning_temp': 'Cleaning temporary page images...',
        'status_packing_pdf': 'Packing PDF...',
        'status_cleaning_images': 'Cleaning processed images...',
        'status_direct_pdf': 'Directly packing PDF (original quality)...',
        'status_direct_pdf_file': 'Directly packing PDF: {name}...',
        'status_generating_summary_pdf': 'Generating summary PDF...',
        'err_pdf_abort': 'PDF task aborted abnormally: {error}',
        'err_pdf_extract': 'Failed to extract PDF: {error}',
        'err_no_images_in_pdf': 'No processable images extracted from PDF!',
        'err_no_images_for_pdf': 'No output images found to merge into PDF.',
        'err_pdf_gen_failed': 'Failed to generate new PDF: {error}',
        'err_task_aborted': 'Task aborted abnormally: {error}',
        'err_create_output_dir': 'Failed to create output directory: {error}',
        'err_no_eligible_images': 'No matching image files found!',
    }
}

REPORT_TEXTS = {
    'zh-CN': {
        'banner': "==================== 最终任务日志报告 ====================",
        'sec_settings': "【转换设定参数】",
        'mode_dir': "从图片目录开始处理",
        'mode_pdf': "从PDF文件开始处理",
        'work_mode': "- 工作模式: {mode}",
        'pdf_input_file': "- 输入文件: {path}",
        'pdf_output_dir': "- 任务输出目录: {dir}",
        'dir_input_dir': "- 输入图片目录: {dir}",
        'dir_output_dir': "- 输出目标目录: {dir}",
        'recursive_sub': "- 递归子目录: {val}",
        'yes': "是",
        'no': "否",
        'color_enabled': "- 色彩处理: 已启用 [{detail}]",
        'color_disabled': "- 色彩处理: 未启用 (输出格式: {detail})",
        'bin_otsu': "局部动态自适应二值化 (默认)",
        'fmt_keep': "保持原格式与品质",
        'fmt_original': "原大图片（无优化）",
        'fmt_mobile': "精简尺寸（宽度≤2160px，JPEG 渐进质量 75）",
        'fmt_custom': "自定义参数（缩放 {scale}%，JPEG 渐进质量 {quality}）",
        'fmt_jpg80': "转换为 JPG (质量 80)",

        'crop_enabled': "- 分页处理: 已启用 [排除单页比例: < {ratio:.2f}，分割比例: {crop_detail}，阅读顺序: {dir_desc}]",
        'crop_disabled': "- 分页处理: 未启用 (不裁切)",
        'crop_r2l': "从右到左 (古籍常用, 右侧为_A)",
        'crop_l2r': "从左到右 (现代书籍, 左侧为_A)",
        'crop_width': "左右各宽 {p}%",
        'crop_overlap': "，中缝重叠 {overlap}%",
        'pdf_mode_no_conv': "- PDF 输出: 不转换为 PDF (仅保留处理后的图片文件)",
        'pdf_mode_reconstruct': "- PDF 输出: 合并为新 PDF 并自动清理临时分页图片",
        'dir_mode_direct': "- PDF 输出: 直接打包为 PDF (保持原图格式与品质，无中间图片)",
        'dir_mode_merge': "- PDF 输出: 合并输出为单个 PDF ({detail})",
        'dir_mode_merge_keep': "保留处理后的图片",
        'dir_mode_merge_clean': "转换为PDF后自动清理图片",
        'dir_mode_none': "- PDF 输出: 未合并为 PDF (仅输出图片)",
        'max_threads': "- 最大线程数: {val}",
        'sec_stats': "【图片与分页统计】",
        'input_stats_pdf': "- 原始文件包含的图片/分页数量: {count} 张 (从 PDF 提取)",
        'input_stats_dir': "- 原始文件包含的图片/分页数量: {count} 张",
        'output_stats_crop': "- 转换后的图片总量: {total} 张 (其中排除单页数量: {excluded} 张，裁切双页数量: {cropped} 张 -> 分割生成 {generated} 张)",
        'output_stats_nocrop_direct': "- 打包图片总量: {total} 张 (未启用裁切，全为单页)",
        'output_stats_nocrop': "- 转换后的图片总量: {total} 张 (未启用裁切，全为单页)",
        'succeeded_tasks': "- 任务成功项数: {succeeded} / {total}",
        'collision_groups': "- 同名消歧重命名: 为 {count} 组同名不同格式文件自动附加来源扩展名",
        'failures_head': "- 失败或跳过 {count} 项：",
        'failures_more': "  * ... 另有 {count} 项未显示",
        'sec_output': "【输出成果】",
        'pdf_file_gen': "- 生成 PDF 文件: {path}",
        'pdf_files_gen': "- 已生成 {count} 个 PDF 文件",
        'pdf_gen_error': "- PDF 生成异常: {error}",
        'output_direct_pdf': "- 处理图片文件: 保持原图品质未做修改，直接打包为 PDF",
        'output_img_dir': "- 处理图片输出目录: {dir}",
        'output_img_cleaned': "- 处理图片文件: 已自动清理临时分页图片，仅保留生成的 PDF 文件",
        'report_file_saved': "- 任务日志报告已保存至: {path}",
        'footer': "=========================================================="
    },
    'zh-TW': {
        'banner': "==================== 最終任務日誌報告 ====================",
        'sec_settings': "【轉換設定參數】",
        'mode_dir': "從圖片目錄開始處理",
        'mode_pdf': "從PDF文件開始處理",
        'work_mode': "- 工作模式: {mode}",
        'pdf_input_file': "- 輸入檔案: {path}",
        'pdf_output_dir': "- 任務輸出目錄: {dir}",
        'dir_input_dir': "- 輸入圖片目錄: {dir}",
        'dir_output_dir': "- 輸出目標目錄: {dir}",
        'recursive_sub': "- 遞迴子目錄: {val}",
        'yes': "是",
        'no': "否",
        'color_enabled': "- 色彩處理: 已啟用 [{detail}]",
        'color_disabled': "- 色彩處理: 未啟用 (輸出格式: {detail})",
        'bin_otsu': "局部動態自適應二值化 (預設)",
        'bin_threshold': "全域固定閾值二值化 (閾值: {val})",
        'fmt_keep': "保持原格式與品質",
        'fmt_original': "原大圖片（無優化）",
        'fmt_mobile': "精簡尺寸（寬度≤2160px，JPEG 漸進品質 75）",
        'fmt_custom': "自定義參數（縮放 {scale}%，JPEG 漸進品質 {quality}）",
        'fmt_jpg80': "轉換為 JPG (品質 80)",

        'crop_enabled': "- 分頁處理: 已啟用 [排除單頁比例: < {ratio:.2f}，分割比例: {crop_detail}，閱讀順序: {dir_desc}]",
        'crop_disabled': "- 分頁處理: 未啟用 (不裁切)",
        'crop_r2l': "從右到左 (古籍常用, 右側為_A)",
        'crop_l2r': "從左到右 (現代書籍, 左側為_A)",
        'crop_width': "左右各寬 {p}%",
        'crop_overlap': "，中縫重疊 {overlap}%",
        'pdf_mode_no_conv': "- PDF 輸出: 不轉換為 PDF (僅保留處理後的圖片檔案)",
        'pdf_mode_reconstruct': "- PDF 輸出: 合併為新 PDF 並自動清理臨時分頁圖片",
        'dir_mode_direct': "- PDF 輸出: 直接打包為 PDF (保持原圖格式與品質，無中間圖片)",
        'dir_mode_merge': "- PDF 輸出: 合併輸出為單個 PDF ({detail})",
        'dir_mode_merge_keep': "保留處理後的圖片",
        'dir_mode_merge_clean': "轉換為PDF後自動清理圖片",
        'dir_mode_none': "- PDF 輸出: 未合併為 PDF (僅輸出圖片)",
        'max_threads': "- 最大線程數: {val}",
        'sec_stats': "【圖片與分頁統計】",
        'input_stats_pdf': "- 原始檔案包含的圖片/分頁數量: {count} 張 (從 PDF 提取)",
        'input_stats_dir': "- 原始檔案包含的圖片/分頁數量: {count} 張",
        'output_stats_crop': "- 轉換後的圖片總量: {total} 張 (其中排除單頁數量: {excluded} 張，裁切雙頁數量: {cropped} 張 -> 分割生成 {generated} 張)",
        'output_stats_nocrop_direct': "- 打包圖片總量: {total} 張 (未啟用裁切，全為單頁)",
        'output_stats_nocrop': "- 轉換後的圖片總量: {total} 張 (未啟用裁切，全為單頁)",
        'succeeded_tasks': "- 任務成功項數: {succeeded} / {total}",
        'collision_groups': "- 同名消歧重命名: 為 {count} 組同名不同格式檔案自動附加來源副檔名",
        'failures_head': "- 失敗或略過 {count} 項：",
        'failures_more': "  * ... 另有 {count} 項未顯示",
        'sec_output': "【輸出成果】",
        'pdf_file_gen': "- 生成 PDF 檔案: {path}",
        'pdf_files_gen': "- 已生成 {count} 個 PDF 檔案",
        'pdf_gen_error': "- PDF 生成異常: {error}",
        'output_direct_pdf': "- 處理圖片檔案: 保持原圖品質未做修改，直接打包為 PDF",
        'output_img_dir': "- 處理圖片輸出目錄: {dir}",
        'output_img_cleaned': "- 處理圖片檔案: 已自動清理臨時分頁圖片，僅保留生成的 PDF 檔案",
        'report_file_saved': "- 任務日誌報告已儲存至: {path}",
        'footer': "=========================================================="
    },
    'ja': {
        'banner': "==================== 最終タスクログ報告 ====================",
        'sec_settings': "【変換設定パラメータ】",
        'mode_dir': "画像フォルダから処理",
        'mode_pdf': "PDFファイルから処理",
        'work_mode': "- 動作モード: {mode}",
        'pdf_input_file': "- 入力ファイル: {path}",
        'pdf_output_dir': "- タスク出力フォルダ: {dir}",
        'dir_input_dir': "- 入力画像フォルダ: {dir}",
        'dir_output_dir': "- 出力先フォルダ: {dir}",
        'recursive_sub': "- サブフォルダ再帰: {val}",
        'yes': "有効",
        'no': "無効",
        'color_enabled': "- カラー処理: 有効 [{detail}]",
        'color_disabled': "- カラー処理: 無効 (出力形式: {detail})",
        'bin_otsu': "大津の2値化 (デフォルト)",
        'bin_threshold': "固定閾値2値化 (閾値: {val})",
        'fmt_keep': "元の形式と品質を維持",
        'fmt_original': "原寸大（最適化なし）",
        'fmt_mobile': "縮小サイズ（幅≤2160px、プログレッシブ JPEG 品質 75）",
        'fmt_custom': "カスタム設定（縮小率 {scale}%、プログレッシブ JPEG 品質 {quality}）",
        'fmt_jpg80': "JPGに変換 (品質 80)",

        'crop_enabled': "- ページ分割処理: 有効 [単一ページ除外比率: < {ratio:.2f}，分割比率: {crop_detail}，読書順序: {dir_desc}]",
        'crop_disabled': "- ページ分割処理: 無効 (裁断なし)",
        'crop_r2l': "右から左へ (和綴じ/縦書き, 右側が_A)",
        'crop_l2r': "左から右へ (洋書/横書き, 左側が_A)",
        'crop_width': "左右各幅 {p}%",
        'crop_overlap': "，ノド重複 {overlap}%",
        'pdf_mode_no_conv': "- PDF 出力: PDFに変換しない (処理済み画像のみ保持)",
        'pdf_mode_reconstruct': "- PDF 出力: 新規PDFへ統合し一時画像を自動消去",
        'dir_mode_direct': "- PDF 出力: 直接PDFにパック (元の形式・品質を維持、中間画像なし)",
        'dir_mode_merge': "- PDF 出力: 単一PDFへ統合出力 ({detail})",
        'dir_mode_merge_keep': "処理済み画像を保持",
        'dir_mode_merge_clean': "PDF生成後に画像を自動消去",
        'dir_mode_none': "- PDF 出力: PDFへ統合しない (画像のみ出力)",
        'max_threads': "- 最大スレッド数: {val}",
        'sec_stats': "【画像およびページ統計】",
        'input_stats_pdf': "- 元ファイルに含まれる画像/ページ数: {count} 枚 (PDFから抽出)",
        'input_stats_dir': "- 元ファイルに含まれる画像/ページ数: {count} 枚",
        'output_stats_crop': "- 処理後の画像総数: {total} 枚 (除外された単一ページ: {excluded} 枚，裁断された見開き: {cropped} 枚 -> 分割生成 {generated} 枚)",
        'output_stats_nocrop_direct': "- パック画像総数: {total} 枚 (裁断無効、すべて単一ページ)",
        'output_stats_nocrop': "- 処理後の画像総数: {total} 枚 (裁断無効、すべて単一ページ)",
        'succeeded_tasks': "- 成功タスク数: {succeeded} / {total}",
        'collision_groups': "- 同名ファイル名衝突解消: {count} 組の同名・異形式ファイルに元の拡張子を付与",
        'failures_head': "- 失敗またはスキップ {count} 件：",
        'failures_more': "  * ... 他 {count} 件は非表示",
        'sec_output': "【出力成果物】",
        'pdf_file_gen': "- 生成されたPDFファイル: {path}",
        'pdf_files_gen': "- {count} 個のPDFファイルを生成しました",
        'pdf_gen_error': "- PDF 生成異常: {error}",
        'output_direct_pdf': "- 処理画像ファイル: 元の品質を保持して変更なし、直接PDFにパック",
        'output_img_dir': "- 処理済み画像出力フォルダ: {dir}",
        'output_img_cleaned': "- 処理画像ファイル: 一時画像は自動消去され、生成されたPDFファイルのみ保持",
        'report_file_saved': "- タスクログ報告を保存しました: {path}",
        'footer': "=========================================================="
    },
    'en': {
        'banner': "==================== Final Task Log Report ====================",
        'sec_settings': "[Conversion Settings]",
        'mode_dir': "Process from Image Folder",
        'mode_pdf': "Process from PDF File",
        'work_mode': "- Work Mode: {mode}",
        'pdf_input_file': "- Input File: {path}",
        'pdf_output_dir': "- Task Output Dir: {dir}",
        'dir_input_dir': "- Input Image Dir: {dir}",
        'dir_output_dir': "- Target Output Dir: {dir}",
        'recursive_sub': "- Recursive Subfolders: {val}",
        'yes': "Yes",
        'no': "No",
        'color_enabled': "- Color Processing: Enabled [{detail}]",
        'color_disabled': "- Color Processing: Disabled (Output Format: {detail})",
        'bin_otsu': "OTSU Adaptive (Default)",
        'bin_threshold': "Fixed Threshold (Threshold: {val})",
        'fmt_keep': "Keep Original Format & Quality",
        'fmt_original': "Original Size (No Optimization)",
        'fmt_mobile': "Compact Size (Width ≤ 2160px, Progressive JPEG Q75)",
        'fmt_custom': "Custom Parameters (Scale {scale}%, Progressive JPEG Q{quality})",
        'fmt_jpg80': "Convert to JPG (Quality 80)",

        'crop_enabled': "- Page Split/Crop: Enabled [Single-page ratio: < {ratio:.2f}, Split ratio: {crop_detail}, Reading order: {dir_desc}]",
        'crop_disabled': "- Page Split/Crop: Disabled (No crop)",
        'crop_r2l': "Right-to-Left (Ancient books, Right side is _A)",
        'crop_l2r': "Left-to-Right (Modern books, Left side is _A)",
        'crop_width': "Each side {p}% width",
        'crop_overlap': ", Gutter overlap {overlap}%",
        'pdf_mode_no_conv': "- PDF Output: Do not convert to PDF (Keep processed images only)",
        'pdf_mode_reconstruct': "- PDF Output: Merge into new PDF and clean temporary images",
        'dir_mode_direct': "- PDF Output: Directly pack into PDF (Keep original quality, no intermediate images)",
        'dir_mode_merge': "- PDF Output: Merge into a single PDF ({detail})",
        'dir_mode_merge_keep': "Keep processed images",
        'dir_mode_merge_clean': "Clean images after PDF creation",
        'dir_mode_none': "- PDF Output: Not merged into PDF (Images only)",
        'max_threads': "- Max Threads: {val}",
        'sec_stats': "[Image & Page Statistics]",
        'input_stats_pdf': "- Total input images/pages: {count} (Extracted from PDF)",
        'input_stats_dir': "- Total input images/pages: {count}",
        'output_stats_crop': "- Total output images: {total} (Excluded single pages: {excluded}, Cropped spreads: {cropped} -> Generated {generated} pages)",
        'output_stats_nocrop_direct': "- Total packed images: {total} (Crop disabled, all single pages)",
        'output_stats_nocrop': "- Total output images: {total} (Crop disabled, all single pages)",
        'succeeded_tasks': "- Succeeded items: {succeeded} / {total}",
        'collision_groups': "- Disambiguation renaming: Appended source extensions for {count} groups of same-name files",
        'failures_head': "- Failed or skipped {count} items:",
        'failures_more': "  * ... {count} more items not shown",
        'sec_output': "[Output Deliverables]",
        'pdf_file_gen': "- Generated PDF file: {path}",
        'pdf_files_gen': "- Generated {count} PDF files",
        'pdf_gen_error': "- PDF generation error: {error}",
        'output_direct_pdf': "- Processed images: Preserved original quality without modification, directly packed into PDF",
        'output_img_dir': "- Processed image output directory: {dir}",
        'output_img_cleaned': "- Processed images: Temporary images cleaned up, only generated PDF is retained",
        'report_file_saved': "- Task log report saved to: {path}",
        'footer': "=========================================================="
    }
}


def get_backend_text(key, lang='zh-CN', **kwargs):
    """获取后端日志与报告的多语言文本。"""
    lang = lang if lang in BACKEND_LOGS else 'zh-CN'
    tmpl = BACKEND_LOGS.get(lang, {}).get(key)
    if tmpl is None:
        tmpl = BACKEND_LOGS.get('zh-CN', {}).get(key, key)
    if kwargs:
        try:
            return tmpl.format(**kwargs)
        except Exception:
            return tmpl
    return tmpl



def get_system_language():
    """检测当前操作系统界面语言，返回 'zh-CN', 'zh-TW', 'ja', 或 'en'。若未匹配则默认 'zh-CN'。"""
    try:
        if sys.platform == 'win32':
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = lang_id & 0x3ff
            sub = (lang_id >> 10) & 0x3f
            if primary == 0x04:  # Chinese
                if sub in (0x02, 0x03, 0x04):  # zh-TW, zh-HK, zh-MO
                    return 'zh-TW'
                return 'zh-CN'
            elif primary == 0x11:  # Japanese
                return 'ja'
            elif primary == 0x09:  # English
                return 'en'
    except Exception:
        pass
    try:
        loc = locale.getdefaultlocale()[0]
        if loc:
            loc = loc.lower().replace('_', '-')
            if any(k in loc for k in ('zh-tw', 'zh-hk', 'zh-mo', 'zh-hant', 'hant')):
                return 'zh-TW'
            elif 'zh' in loc:
                return 'zh-CN'
            elif 'ja' in loc:
                return 'ja'
            elif 'en' in loc:
                return 'en'
    except Exception:
        pass
    return 'zh-CN'





def calculate_otsu_threshold(img_array):
    """自适应 OTSU 阈值计算。"""
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


def parse_jp2_dpi(filepath):
    """尝试从 JP2 文件的 res 盒子（resc 捕获分辨率或 resd 显示分辨率）中提取真实 DPI。"""
    try:
        with open(filepath, 'rb') as f:
            data = f.read(131072)
            idx = data.find(b'res ')
            if idx != -1:
                sub = data[idx:]
                for tag in (b'resc', b'resd'):
                    c_idx = sub.find(tag)
                    if c_idx != -1:
                        box_data = sub[c_idx + 4:c_idx + 14]
                        if len(box_data) >= 10:
                            vr_n, vr_d, hr_n, hr_d, vr_e, hr_e = struct.unpack('>HHHHbb', box_data)
                            if vr_d != 0 and hr_d != 0:
                                v_res = (vr_n / vr_d) * (10 ** vr_e)
                                h_res = (hr_n / hr_d) * (10 ** hr_e)
                                dpi_y = v_res * 0.0254
                                dpi_x = h_res * 0.0254
                                if dpi_x > 10.0 and dpi_y > 10.0:
                                    return (round(dpi_x, 2), round(dpi_y, 2))
    except Exception:
        pass
    return None


def get_normalized_dpi(pil_img, default_res=300.0):
    """
    获取图片的有效 DPI。
    若源图无 DPI 属性、为 1（无单位缺省值）或 <= 10.0，统一自动规范化为 default_res (300.0, 300.0)。
    """
    dpi_val = pil_img.info.get('dpi') if hasattr(pil_img, 'info') else None
    if not dpi_val:
        return (default_res, default_res)
    if isinstance(dpi_val, (tuple, list)):
        try:
            dx = float(dpi_val[0]) if dpi_val[0] else default_res
            dy = float(dpi_val[1]) if len(dpi_val) > 1 and dpi_val[1] else dx
            if dx <= 10.0 or dy <= 10.0:
                return (default_res, default_res)
            return (round(dx, 2), round(dy, 2))
        except (ValueError, TypeError):
            return (default_res, default_res)
    try:
        num = float(dpi_val)
        return (round(num, 2), round(num, 2)) if num > 10.0 else (default_res, default_res)
    except (ValueError, TypeError):
        return (default_res, default_res)


def get_jpeg_save_options(source_img):
    """尽量保留源 JPEG 的量化表和色度抽样，避免裁切后默认变成质量 95。"""
    quantization = getattr(source_img, 'quantization', None)
    if not quantization:
        return {}

    options = {'qtables': quantization}
    subsampling = JpegImagePlugin.get_sampling(source_img)
    if subsampling != -1:
        options['subsampling'] = subsampling
    options['dpi'] = get_normalized_dpi(source_img, default_res=300.0)
    if 'icc_profile' in source_img.info:
        options['icc_profile'] = source_img.info['icc_profile']
    return options


def save_image_atomically(pil_img, output_path, image_format, write_lock=None, **save_options):
    """写入同目录临时文件，成功后再替换，避免异常时出现残缺图片。"""
    temporary_path = f"{output_path}.{threading.get_ident()}.part"
    def _do_save():
        try:
            pil_img.save(temporary_path, format=image_format, **save_options)
            os.replace(temporary_path, output_path)
        finally:
            if os.path.exists(temporary_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass

    if write_lock:
        with write_lock:
            _do_save()
    else:
        _do_save()


def copy_file_atomically(source_path, output_path, write_lock=None):
    """复制不需重新编码的原图，同样不暴露未完成的目标文件。"""
    temporary_path = f"{output_path}.{threading.get_ident()}.part"
    def _do_copy():
        try:
            shutil.copy2(source_path, temporary_path)
            os.replace(temporary_path, output_path)
        finally:
            if os.path.exists(temporary_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass

    if write_lock:
        with write_lock:
            _do_copy()
    else:
        _do_copy()


def save_image(pil_img, out_path_base, original_ext, settings, jpeg_save_options=None, write_lock=None):
    """保存一张处理结果，并返回最终输出路径。"""
    norm_dpi = get_normalized_dpi(pil_img, default_res=300.0)

    if settings.get('enable_binarize'):
        gray_img = None
        final_img = None
        try:
            gray_img = pil_img.convert('L')
            img_array = np.array(gray_img)

            if str(settings.get('bin_method', '0')) == "0":
                t_val = calculate_otsu_threshold(img_array)
            else:
                t_val = int((settings.get('threshold_val', 50) / 100.0) * 255)

            binary_array = (img_array > t_val).astype(np.uint8) * 255
            final_img = Image.fromarray(binary_array).convert('1')
            output_path = f"{out_path_base}.tif"
            save_kwargs = {'compression': 'group4', 'dpi': norm_dpi}

            save_image_atomically(
                final_img,
                output_path,
                'TIFF',
                write_lock=write_lock,
                **save_kwargs,
            )
            return output_path
        finally:
            if final_img is not None:
                final_img.close()
            if gray_img is not None:
                gray_img.close()

    size_opt_mode = settings.get('size_opt_mode') or settings.get('non_bin_format', 'original')
    if size_opt_mode in ('mobile', 'custom', 'jpg80'):
        # 转换为 JPEG 渐进式格式
        if size_opt_mode == 'mobile':
            quality = 75
            progressive = True
        elif size_opt_mode == 'custom':
            quality = int(settings.get('custom_quality', 80))
            progressive = True
        else:  # jpg80
            quality = 80
            progressive = False

        if pil_img.mode in ('RGBA', 'LA'):
            bg = Image.new('RGB', pil_img.size, (255, 255, 255))
            bg.paste(pil_img, mask=pil_img.split()[-1])
            rgb_img = bg
        elif pil_img.mode != 'RGB':
            rgb_img = pil_img.convert('RGB')
        else:
            rgb_img = pil_img

        try:
            output_path = f"{out_path_base}.jpg"
            save_image_atomically(
                rgb_img,
                output_path,
                'JPEG',
                write_lock=write_lock,
                quality=quality,
                progressive=progressive,
                optimize=True,
                dpi=norm_dpi,
            )
            return output_path
        finally:
            if rgb_img is not pil_img:
                rgb_img.close()


    # 保持原始图片格式。
    output_path = f"{out_path_base}{original_ext}"
    format_by_extension = {
        '.jpg': 'JPEG', '.jpeg': 'JPEG',
        '.png': 'PNG', '.tif': 'TIFF', '.tiff': 'TIFF',
        '.bmp': 'BMP', '.jp2': 'JPEG2000',
    }
    if original_ext in ('.jpg', '.jpeg'):
        options = dict(jpeg_save_options or {})
        options['dpi'] = norm_dpi
        try:
            save_image_atomically(pil_img, output_path, 'JPEG', write_lock=write_lock, **options)
        except OSError:
            converted = pil_img.convert('RGB')
            try:
                save_image_atomically(converted, output_path, 'JPEG', write_lock=write_lock, **options)
            finally:
                converted.close()
    else:
        image_format = format_by_extension.get(original_ext, 'PNG')
        save_opts = {}
        if image_format in ('PNG', 'TIFF', 'BMP'):
            save_opts['dpi'] = norm_dpi
        try:
            save_image_atomically(pil_img, output_path, image_format, write_lock=write_lock, **save_opts)
        except OSError:
            converted = pil_img.convert('RGB')
            try:
                save_image_atomically(converted, output_path, image_format, write_lock=write_lock, **save_opts)
            finally:
                converted.close()
    return output_path


def remove_outputs(output_paths):
    """某一原图的双页写入失败时，回滚已经写好的另一页。"""
    for output_path in output_paths:
        try:
            if os.path.isfile(output_path):
                os.remove(output_path)
        except OSError:
            pass


def make_result(ok, message, error=None, is_single=False, is_excluded_single=False, output_count=0):
    return {
        'ok': ok,
        'message': message,
        'error': error,
        'is_single': is_single,
        'is_excluded_single': is_excluded_single,
        'output_count': output_count,
    }


def process_single_image(src_path, rel_path, filename, output_stem, settings,
                         pause_event=None, cancel_event=None, write_lock=None):
    """处理单张图片（裁切、二值化、格式转换）。"""
    if pause_event is not None:
        pause_event.wait()
    
    if cancel_event is not None and cancel_event.is_set():
        return make_result(False, "中止", "任务已取消", is_single=False, is_excluded_single=False, output_count=0)

    output_paths = []
    img = None
    try:
        img = Image.open(src_path)
        original_ext = os.path.splitext(filename)[1].lower()
        if original_ext in ('.jp2', '.j2k', '.jpc', '.jpf', '.jpx', '.j2c') and 'dpi' not in img.info:
            jp2_dpi = parse_jp2_dpi(src_path)
            if jp2_dpi:
                img.info['dpi'] = jp2_dpi

        # 统一规范化所有格式图片的 DPI（缺失或 <= 10 时缺省自动规范化为 300 DPI）
        norm_dpi = get_normalized_dpi(img, default_res=300.0)
        img.info['dpi'] = norm_dpi
        w, h = img.size 
        
        if h <= 0:
            return make_result(False, f"跳过: 图片高度为0 {filename}", "图片高度为 0", is_single=False, is_excluded_single=False, output_count=0)
            
        aspect_ratio = w / h
        if original_ext not in ['.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2']:
            return make_result(False, f"跳过: 非支持的扩展名 {filename}", "不支持的扩展名", is_single=False, is_excluded_single=False, output_count=0)

        out_dir = os.path.join(settings['target_dir'], os.path.dirname(rel_path))
        os.makedirs(out_dir, exist_ok=True)
        base_name = output_stem
        jpeg_save_options = get_jpeg_save_options(img)

        # 检查是否需在裁切前进行文件大小优化与尺寸重采样
        size_opt_mode = settings.get('size_opt_mode') or settings.get('non_bin_format', 'original')
        if not settings.get('enable_binarize'):
            resample_filter = getattr(Image, 'Resampling', Image).LANCZOS
            if size_opt_mode == 'mobile':
                if w > 2160:
                    img.load()
                    target_w = 2160
                    target_h = max(1, int(round(h * 2160.0 / w)))
                    resized = img.resize((target_w, target_h), resample_filter)
                    resized.info['dpi'] = norm_dpi
                    img.close()
                    img = resized
                    w, h = img.size
                    aspect_ratio = w / h
            elif size_opt_mode == 'custom':
                custom_scale = int(settings.get('custom_scale', 80))
                scale = custom_scale / 100.0
                if scale != 1.0:
                    img.load()
                    target_w = max(1, int(round(w * scale)))
                    target_h = max(1, int(round(h * scale)))
                    resized = img.resize((target_w, target_h), resample_filter)
                    resized.info['dpi'] = norm_dpi
                    img.close()
                    img = resized
                    w, h = img.size
                    aspect_ratio = w / h

        is_excluded = settings.get('enable_crop') and (aspect_ratio < settings.get('exclude_ratio', 0.7))
        if not settings.get('enable_crop') or aspect_ratio < settings.get('exclude_ratio', 0.7):
            # 未二值化、保持原大且未实际裁切时，直接复制源文件以完整保留 JPEG 品质与元数据。
            if not settings.get('enable_binarize') and size_opt_mode in ('original', 'keep'):
                output_path = os.path.join(out_dir, f"{base_name}{original_ext}")
                copy_file_atomically(src_path, output_path, write_lock=write_lock)
                output_paths.append(output_path)
            else:
                # 先完全解码，在任何输出写入前暴露损坏图片等读取错误。
                img.load()
                path_base = os.path.join(out_dir, base_name)
                output_paths.append(save_image(
                    img, path_base, original_ext, settings, jpeg_save_options, write_lock=write_lock
                ))
            tag = "排除单页" if is_excluded else "单页"
            return make_result(
                True,
                f"处理完成 ({tag}): {filename}",
                is_single=True,
                is_excluded_single=is_excluded,
                output_count=len(output_paths),
            )


        # 一次只保留一个裁切页，降低大图在多线程下的峰值内存。
        img.load()
        crop_ratio = settings.get('crop_percent', 50) / 100.0
        split_width = int(w * crop_ratio)

        if settings.get('crop_direction', 'R2L') == "R2L":
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
            cropped_img.info['dpi'] = norm_dpi
            try:
                output_paths.append(save_image(
                    cropped_img,
                    os.path.join(out_dir, crop_base_name),
                    original_ext,
                    settings,
                    jpeg_save_options,
                    write_lock=write_lock,
                ))
            finally:
                cropped_img.close()
        return make_result(
            True,
            f"处理完成 (裁切双页): {filename}",
            is_single=False,
            is_excluded_single=False,
            output_count=len(output_paths),
        )

    except Exception as e:
        remove_outputs(output_paths)
        return make_result(False, f"错误 {filename}: {str(e)}", str(e), is_single=False, is_excluded_single=False, output_count=0)
    finally:
        if img is not None:
            img.close()


def is_same_or_parent(parent, child):
    """判断 parent 是否等于或包含 child，使用规范路径避免误删输入目录。"""
    try:
        parent = os.path.normcase(os.path.realpath(os.path.abspath(parent)))
        child = os.path.normcase(os.path.realpath(os.path.abspath(child)))
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        return False


def natural_sort_key(path):
    """按文件的相对路径自然排序，使 page_2 位于 page_10 之前。"""
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r'(\d+)', path)
    ]


def assign_output_stems(tasks):
    """为会归并到同一输出名的源文件分配稳定、无冲突的文件名前缀。"""
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
            key=lambda task: natural_sort_key(task[1]),
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


def completion_text(summary, pdf_count=None, pdf_error=None, keep_images=False, pdf_path=None, lang=None):
    """生成格式化的任务完成/报告文本。"""
    settings = summary.get('settings') or {}
    effective_lang = lang or settings.get('lang') or summary.get('lang') or get_system_language()
    if effective_lang not in ('zh-CN', 'zh-TW', 'ja', 'en'):
        effective_lang = 'zh-CN'

    is_pdf_mode = (settings.get('work_mode') == 'pdf')
    t = REPORT_TEXTS[effective_lang]
    lines = [t['banner'], "", t['sec_settings']]
    mode_desc = t['mode_pdf'] if is_pdf_mode else t['mode_dir']
    lines.append(t['work_mode'].format(mode=mode_desc))

    if is_pdf_mode:
        lines.append(t['pdf_input_file'].format(path=settings.get('pdf_path', '')))
        lines.append(t['pdf_output_dir'].format(dir=settings.get('clean_dir', settings.get('target_dir', ''))))
    else:
        lines.append(t['dir_input_dir'].format(dir=settings.get('source_dir', '')))
        lines.append(t['dir_output_dir'].format(dir=settings.get('target_dir', '')))
        lines.append(t['recursive_sub'].format(val=t['yes'] if settings.get('include_subfolders') else t['no']))

    # 色彩处理参数
    if settings.get('enable_binarize'):
        m = t['bin_otsu'] if str(settings.get('bin_method')) == "0" else t['bin_threshold'].format(val=settings.get('threshold_val', 50))
        lines.append(t['color_enabled'].format(detail=m))
    else:
        size_opt_mode = settings.get('size_opt_mode') or settings.get('non_bin_format', 'original')
        if size_opt_mode in ('original', 'keep'):
            fmt = t.get('fmt_original', t.get('fmt_keep', '原大图片（无优化）'))
        elif size_opt_mode == 'mobile':
            fmt = t.get('fmt_mobile', '精简尺寸（适合手机）')
        elif size_opt_mode == 'custom':
            fmt = t.get('fmt_custom', '自定义参数（缩放 {scale}%，JPEG 质量 {quality}）').format(
                scale=settings.get('custom_scale', 80),
                quality=settings.get('custom_quality', 80),
            )
        else:
            fmt = t.get('fmt_original', t.get('fmt_keep', '原大图片（无优化）'))
        lines.append(t['color_disabled'].format(detail=fmt))


    # 分页裁切参数
    if settings.get('enable_crop'):
        d = t['crop_r2l'] if settings.get('crop_direction') == 'R2L' else t['crop_l2r']
        p = settings.get('crop_percent', 50)
        overlap = max(0, 2 * p - 100)
        crop_det = t['crop_width'].format(p=p)
        if overlap > 0:
            crop_det += t['crop_overlap'].format(overlap=overlap)
        lines.append(t['crop_enabled'].format(ratio=settings.get('exclude_ratio', 0.7), crop_detail=crop_det, dir_desc=d))
    else:
        lines.append(t['crop_disabled'])

    # PDF 输出参数
    if is_pdf_mode:
        if settings.get('no_convert_pdf'):
            lines.append(t['pdf_mode_no_conv'])
        else:
            lines.append(t['pdf_mode_reconstruct'])
    else:
        if summary.get('direct_pdf'):
            lines.append(t['dir_mode_direct'])
        elif pdf_count is not None or settings.get('enable_pdf'):
            m_det = t['dir_mode_merge_keep'] if settings.get('keep_images_after_pdf', keep_images) else t['dir_mode_merge_clean']
            lines.append(t['dir_mode_merge'].format(detail=m_det))
        else:
            lines.append(t['dir_mode_none'])

    lines.append(t['max_threads'].format(val=settings.get('max_threads', 4)))
    lines.append("")

    # 数量统计
    lines.append(t['sec_stats'])
    total_input = summary.get('total_input', summary.get('total', 0))
    if is_pdf_mode:
        lines.append(t['input_stats_pdf'].format(count=total_input))
    else:
        lines.append(t['input_stats_dir'].format(count=total_input))

    total_output = summary.get('total_output_images', 0)
    excluded_single = summary.get('excluded_single_count', 0)
    cropped_double = summary.get('cropped_double_count', 0)

    if settings.get('enable_crop'):
        lines.append(t['output_stats_crop'].format(total=total_output, excluded=excluded_single, cropped=cropped_double, generated=cropped_double * 2))
    else:
        if summary.get('direct_pdf'):
            lines.append(t['output_stats_nocrop_direct'].format(total=total_output))
        else:
            lines.append(t['output_stats_nocrop'].format(total=total_output))

    succeeded = summary.get('succeeded', 0)
    total_tasks = summary.get('total', total_input)
    lines.append(t['succeeded_tasks'].format(succeeded=succeeded, total=total_tasks))

    if summary.get('collision_groups'):
        lines.append(t['collision_groups'].format(count=summary['collision_groups']))
    if summary.get('errors'):
        lines.append(t['failures_head'].format(count=len(summary['errors'])))
        for item in summary['errors'][:5]:
            lines.append(f"  * {item}")
        if len(summary['errors']) > 5:
            lines.append(t['failures_more'].format(count=len(summary['errors']) - 5))
    lines.append("")

    # 输出成果
    lines.append(t['sec_output'])
    if pdf_path:
        lines.append(t['pdf_file_gen'].format(path=pdf_path))
    elif pdf_count is not None:
        lines.append(t['pdf_files_gen'].format(count=pdf_count))
    if pdf_error:
        lines.append(t['pdf_gen_error'].format(error=pdf_error))

    if summary.get('direct_pdf'):
        lines.append(t['output_direct_pdf'])
    elif summary.get('images_kept', True) and (not is_pdf_mode or settings.get('no_convert_pdf') or settings.get('keep_images_after_pdf', keep_images)):
        target_out = (settings.get('clean_dir') or settings.get('source_dir', '')) if is_pdf_mode else (settings.get('target_dir', ''))
        lines.append(t['output_img_dir'].format(dir=target_out))
    else:
        lines.append(t['output_img_cleaned'])

    if summary.get('report_file'):
        lines.append(t['report_file_saved'].format(path=summary['report_file']))

    lines.append(t['footer'])
    return '\n'.join(lines)


def build_task_report(summary, target_dir, **kwargs):
    """生成任务报告文本并在目标目录输出 task_report.txt 文件。"""
    report_file = os.path.join(target_dir, "task_report.txt") if (target_dir and os.path.isdir(target_dir)) else None
    summary['report_file'] = report_file
    report_text = completion_text(summary, **kwargs)
    if report_file:
        try:
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(report_text)
        except Exception:
            pass
    return report_text


def add_image_page_to_pdf_writer(writer, image_path, default_res=300.0):
    """将一张图片以最优且标准合规的流格式加入 PDF（1 位二值图使用 CCITT Group 4，彩色图使用 DCT/JPEG）。"""
    with Image.open(image_path) as im:
        w, h = im.size
        ext = os.path.splitext(image_path)[1].lower()
        if ext in ('.jp2', '.j2k', '.jpc', '.jpf', '.jpx', '.j2c') and 'dpi' not in im.info:
            jp2_dpi = parse_jp2_dpi(image_path)
            if jp2_dpi:
                im.info['dpi'] = jp2_dpi

        dpi = get_normalized_dpi(im, default_res=default_res)
        dpi_x = float(dpi[0])
        dpi_y = float(dpi[1])

        width_pt = w * 72.0 / dpi_x
        height_pt = h * 72.0 / dpi_y

        # 严格确保单页尺寸在 PDF 规范与 Adobe Acrobat 允许范围 [3, 14400] 磅内（最大 200 英寸，防止页面超出范围报错）
        if width_pt > 14400.0 or height_pt > 14400.0:
            scale = max(width_pt / 14400.0, height_pt / 14400.0)
            width_pt = max(3.0, width_pt / scale)
            height_pt = max(3.0, height_pt / scale)
        elif width_pt < 3.0 or height_pt < 3.0:
            scale = max(3.0 / width_pt, 3.0 / height_pt)
            width_pt = min(14400.0, width_pt * scale)
            height_pt = min(14400.0, height_pt * scale)

        is_bilevel = (im.mode == '1') or (im.format == 'TIFF' and im.tag_v2.get(259) == 4)
        if is_bilevel:
            # 1 位黑白二值图：必须严格使用 1 位 TIFF Group 4 (Filter /CCITTFaxDecode) 封装
            if im.mode != '1':
                im = im.convert('1')
            is_single_strip_g4 = False
            if im.format == 'TIFF' and im.tag_v2.get(259) == 4:
                offsets = im.tag_v2.get(273)
                counts = im.tag_v2.get(279)
                if offsets is not None and not isinstance(offsets, (list, tuple)):
                    offsets = [offsets]
                if counts is not None and not isinstance(counts, (list, tuple)):
                    counts = [counts]
                if offsets and counts and len(offsets) == 1:
                    is_single_strip_g4 = True
                    photometric = im.tag_v2.get(262, 1)
                    with open(image_path, 'rb') as f:
                        f.seek(int(offsets[0]))
                        ccitt_data = f.read(int(counts[0]))

            if not is_single_strip_g4:
                bio = io.BytesIO()
                im.save(bio, format='TIFF', compression='group4')
                bio.seek(0)
                with Image.open(bio) as t:
                    offsets = t.tag_v2[273]
                    counts = t.tag_v2[279]
                    photometric = t.tag_v2.get(262, 1)
                    off = offsets[0] if isinstance(offsets, (list, tuple)) else offsets
                    cnt = counts[0] if isinstance(counts, (list, tuple)) else counts
                    bio.seek(int(off))
                    ccitt_data = bio.read(int(cnt))

            img_obj = DecodedStreamObject()
            img_obj.set_data(ccitt_data)
            img_obj.update({
                NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Image'),
                NameObject('/Width'): NumberObject(w),
                NameObject('/Height'): NumberObject(h),
                NameObject('/ColorSpace'): NameObject('/DeviceGray'),
                NameObject('/BitsPerComponent'): NumberObject(1),
                NameObject('/Filter'): NameObject('/CCITTFaxDecode'),
                NameObject('/DecodeParms'): DictionaryObject({
                    NameObject('/K'): NumberObject(-1),
                    NameObject('/Columns'): NumberObject(w),
                    NameObject('/Rows'): NumberObject(h),
                    NameObject('/BlackIs1'): BooleanObject(True if photometric != 0 else False),
                }),
            })
        elif ext in ('.jp2', '.j2k', '.jpc', '.jpf', '.jpx', '.j2c'):
            # JPEG 2000 原格式流：直接保留源数据，以 /JPXDecode 滤镜封装，100% 保持原始格式与品质
            with open(image_path, 'rb') as f:
                jp2_data = f.read()

            img_obj = DecodedStreamObject()
            img_obj.set_data(jp2_data)
            img_dict = {
                NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Image'),
                NameObject('/Width'): NumberObject(w),
                NameObject('/Height'): NumberObject(h),
                NameObject('/Filter'): NameObject('/JPXDecode'),
            }
            if im.mode == 'L':
                img_dict[NameObject('/ColorSpace')] = NameObject('/DeviceGray')
                img_dict[NameObject('/BitsPerComponent')] = NumberObject(8)
            elif im.mode in ('RGB', 'RGBA'):
                img_dict[NameObject('/ColorSpace')] = NameObject('/DeviceRGB')
                img_dict[NameObject('/BitsPerComponent')] = NumberObject(8)
            img_obj.update(img_dict)
        else:
            # 彩色或灰度图：使用 /DCTDecode (JPEG) 编码
            if ext in ('.jpg', '.jpeg') and im.mode in ('RGB', 'L'):
                with open(image_path, 'rb') as f:
                    jpg_data = f.read()
                cs = '/DeviceRGB' if im.mode == 'RGB' else '/DeviceGray'
            else:
                bio = io.BytesIO()
                conv = im.convert('RGB') if im.mode not in ('RGB', 'L') else im
                cs = '/DeviceRGB' if conv.mode == 'RGB' else '/DeviceGray'
                conv.save(bio, format='JPEG', quality=85)
                jpg_data = bio.getvalue()

            img_obj = DecodedStreamObject()
            img_obj.set_data(jpg_data)
            img_obj.update({
                NameObject('/Type'): NameObject('/XObject'),
                NameObject('/Subtype'): NameObject('/Image'),
                NameObject('/Width'): NumberObject(w),
                NameObject('/Height'): NumberObject(h),
                NameObject('/ColorSpace'): NameObject(cs),
                NameObject('/BitsPerComponent'): NumberObject(8),
                NameObject('/Filter'): NameObject('/DCTDecode'),
            })

        content_str = f'q {width_pt:.4f} 0 0 {height_pt:.4f} 0 0 cm /Im0 Do Q'
        content_obj = DecodedStreamObject()
        content_obj.set_data(content_str.encode('ascii'))

        content_ref = writer._add_object(content_obj)
        img_ref = writer._add_object(img_obj)

        page = writer.add_blank_page(width=width_pt, height=height_pt)
        page[NameObject('/Contents')] = content_ref
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/XObject'): DictionaryObject({
                NameObject('/Im0'): img_ref
            })
        })


def build_single_pdf(image_paths, pdf_path, settings=None, progress_state=None, cancel_event=None, progress_callback=None):
    """将同一个输出目录内的图片写入一个 PDF（1 位二值图严格使用 CCITT Group 4 封装）。"""
    temporary_path = f"{pdf_path}.tmp"
    try:
        writer = PdfWriter()
        writer.pdf_header = PDF_SPEC_VERSION
        writer.add_metadata({
            '/Creator': PDF_APPLICATION_NAME,
            '/Producer': PDF_APPLICATION_NAME,
        })

        total = max(1, progress_state[1]) if progress_state is not None else len(image_paths)
        for image_path in image_paths:
            if cancel_event is not None and cancel_event.is_set():
                return False, "PDF 生成已取消。"
            add_image_page_to_pdf_writer(writer, image_path, default_res=300.0)
            if progress_state is not None:
                progress_state[0] += 1
                if progress_callback:
                    progress_callback(
                        progress_state[0] * 100.0 / total,
                        f'正在打包 PDF：{progress_state[0]} / {total}',
                    )

        if not writer.pages:
            return False, "没有可写入 PDF 的有效页面。"

        first_ref = writer.pages[0].indirect_reference
        if first_ref is not None:
            writer._root_object[NameObject('/OpenAction')] = ArrayObject([
                first_ref,
                NameObject('/Fit'),
            ])
            writer._root_object[NameObject('/PageLayout')] = NameObject('/SinglePage')

        with open(temporary_path, 'wb') as output_file:
            writer.write(output_file)

        os.replace(temporary_path, pdf_path)
        return True, pdf_path
    except Exception as e:
        if os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except OSError:
                pass
        return False, str(e)


def set_pdf_open_to_fit_page(pdf_path):
    """写入 PDF 初始视图：打开时将第一页完整适配到阅读器窗口。"""
    temporary_path = f"{pdf_path}.tmp"
    try:
        with open(pdf_path, 'rb') as source_file:
            reader = PdfReader(source_file)
            if not reader.pages:
                raise ValueError("PDF 中没有可设置的页面。")

            writer = PdfWriter()
            writer.clone_document_from_reader(reader)
            writer.pdf_header = PDF_SPEC_VERSION
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


def clean_cancelled_output(target_dir, lang='zh-CN'):
    lang = lang if lang in ('zh-CN', 'zh-TW', 'ja', 'en') else 'zh-CN'
    T = {
        'zh-CN': ("任务已取消，输出目录已被成功删除。\n({target_dir})", "任务已取消，但删除目录失败，请手动清理。\n原因: {error}"),
        'zh-TW': ("任務已取消，輸出目錄已被成功刪除。\n({target_dir})", "任務已取消，但刪除目錄失敗，請手動清理。\n原因: {error}"),
        'ja': ("タスクがキャンセルされ、出力フォルダが正常に削除されました。\n({target_dir})", "タスクがキャンセルされましたが、フォルダの削除に失敗しました。手動で削除してください。\n原因: {error}"),
        'en': ("Task cancelled. Output directory was successfully removed。\n({target_dir})", "Task cancelled, but failed to delete directory. Please clean it manually.\nReason: {error}"),
    }
    try:
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        return T[lang][0].format(target_dir=target_dir)
    except Exception as e:
        return T[lang][1].format(error=str(e))


# 兼容性别名映射
_calculate_otsu_threshold = calculate_otsu_threshold
_parse_jp2_dpi = parse_jp2_dpi
_get_normalized_dpi = get_normalized_dpi
_get_jpeg_save_options = get_jpeg_save_options
_save_image_atomically = save_image_atomically
_copy_file_atomically = copy_file_atomically
_remove_outputs = remove_outputs
_result = make_result
_is_same_or_parent = is_same_or_parent
_natural_sort_key = natural_sort_key
_assign_output_stems = assign_output_stems
_completion_text = completion_text
_build_and_save_task_report = build_task_report
_add_image_page_to_pdf_writer = add_image_page_to_pdf_writer
_build_single_pdf = build_single_pdf
_set_pdf_open_to_fit_page = set_pdf_open_to_fit_page
_clean_cancelled_output = clean_cancelled_output

__all__ = [
    'PDF_APPLICATION_NAME',
    'PDF_SPEC_VERSION',
    'extract_images_from_pdf',
    'get_task_suffix',
    'BACKEND_LOGS',
    'REPORT_TEXTS',
    'get_backend_text',
    'get_system_language',
    'calculate_otsu_threshold',
    'parse_jp2_dpi',
    'get_normalized_dpi',
    'get_jpeg_save_options',
    'save_image_atomically',
    'copy_file_atomically',
    'save_image',
    'remove_outputs',
    'make_result',
    'process_single_image',
    'is_same_or_parent',
    'natural_sort_key',
    'assign_output_stems',
    'completion_text',
    'build_task_report',
    'add_image_page_to_pdf_writer',
    'build_single_pdf',
    'set_pdf_open_to_fit_page',
    'clean_cancelled_output',
]
