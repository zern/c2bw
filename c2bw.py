import os
import sys
import multiprocessing

multiprocessing.freeze_support()

import io
import shutil
import tempfile
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
import socket
import numpy as np
from PIL import Image, JpegImagePlugin, PdfImagePlugin, Jpeg2KImagePlugin  # 显式导入以确保打包程序包含 PDF/JPEG2000 编码器。
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, NameObject, StreamObject,
    BooleanObject, NumberObject, DictionaryObject, DecodedStreamObject
)
from pypdf.filters import decode_stream_data
import pypdf.filters
import webview

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


def get_task_suffix(enable_crop, enable_binarize):
    """根据选择的处理任务生成对应的目录与文件后缀。"""
    if enable_crop and enable_binarize:
        return "_已裁切_黑白版"
    elif enable_crop:
        return "_已裁切"
    elif enable_binarize:
        return "_黑白版"
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
        'bin_threshold': "全局固定阈值二值化 (阈值: {val})",
        'fmt_keep': "保持原格式与品质",
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


class ImageProcessorApp:
    PDF_APPLICATION_NAME = "SHUGE.ORG"
    PDF_SPEC_VERSION = b"%PDF-1.5"

    def __init__(self, root):
        self.root = root
        self.root.title("智能图像预处理工具 v3.5")
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

        # 新增：取消二值化后的输出格式选项 ("keep"=保持原格式, "jpg80"=转换为JPG质量80)
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
        suffix = get_task_suffix(self.enable_crop.get(), self.enable_binarize.get())
        if not suffix:
            if self.pdf_no_convert.get():
                self.pdf_target_preview.set(os.path.join(pdf_dir, pdf_name))
            else:
                self.pdf_target_preview.set("（请至少勾选一种任务：裁切或黑白二值化）")
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
    def _parse_jp2_dpi(filepath):
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
                                import struct
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

    @staticmethod
    def _get_normalized_dpi(pil_img, default_res=300.0):
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

    @classmethod
    def _get_jpeg_save_options(cls, source_img):
        """尽量保留源 JPEG 的量化表和色度抽样，避免裁切后默认变成质量 95。"""
        quantization = getattr(source_img, 'quantization', None)
        if not quantization:
            return {}

        options = {'qtables': quantization}
        subsampling = JpegImagePlugin.get_sampling(source_img)
        if subsampling != -1:
            options['subsampling'] = subsampling
        options['dpi'] = cls._get_normalized_dpi(source_img, default_res=300.0)
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
        norm_dpi = self._get_normalized_dpi(pil_img, default_res=300.0)

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
                save_kwargs = {'compression': 'group4', 'dpi': norm_dpi}

                self._save_image_atomically(
                    final_img,
                    output_path,
                    'TIFF',
                    **save_kwargs,
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
                self._save_image_atomically(rgb_img, output_path, 'JPEG', quality=80, dpi=norm_dpi)
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
            options = dict(jpeg_save_options or {})
            options['dpi'] = norm_dpi
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
            save_opts = {}
            if image_format in ('PNG', 'TIFF', 'BMP'):
                save_opts['dpi'] = norm_dpi
            try:
                self._save_image_atomically(pil_img, output_path, image_format, **save_opts)
            except OSError:
                converted = pil_img.convert('RGB')
                try:
                    self._save_image_atomically(converted, output_path, image_format, **save_opts)
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
    def _result(ok, message, error=None, is_single=False, is_excluded_single=False, output_count=0):
        return {
            'ok': ok,
            'message': message,
            'error': error,
            'is_single': is_single,
            'is_excluded_single': is_excluded_single,
            'output_count': output_count,
        }

    def process_single_image(self, src_path, rel_path, filename, output_stem, settings):
        self.pause_event.wait()
        
        if self.cancel_event.is_set():
            return self._result(False, "中止", "任务已取消", is_single=False, is_excluded_single=False, output_count=0)

        output_paths = []
        img = None
        try:
            img = Image.open(src_path)
            original_ext = os.path.splitext(filename)[1].lower()
            if original_ext in ('.jp2', '.j2k', '.jpc', '.jpf', '.jpx', '.j2c') and 'dpi' not in img.info:
                jp2_dpi = self._parse_jp2_dpi(src_path)
                if jp2_dpi:
                    img.info['dpi'] = jp2_dpi

            # 统一规范化所有格式图片的 DPI（缺失或 <= 10 时缺省自动规范化为 300 DPI）
            norm_dpi = self._get_normalized_dpi(img, default_res=300.0)
            img.info['dpi'] = norm_dpi
            w, h = img.size 
            
            if h <= 0:
                return self._result(False, f"跳过: 图片高度为0 {filename}", "图片高度为 0", is_single=False, is_excluded_single=False, output_count=0)
                
            aspect_ratio = w / h
            if original_ext not in ['.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.jp2']:
                return self._result(False, f"跳过: 非支持的扩展名 {filename}", "不支持的扩展名", is_single=False, is_excluded_single=False, output_count=0)

            out_dir = os.path.join(settings['target_dir'], os.path.dirname(rel_path))
            os.makedirs(out_dir, exist_ok=True)
            base_name = output_stem
            jpeg_save_options = self._get_jpeg_save_options(img)

            is_excluded = settings['enable_crop'] and (aspect_ratio < settings['exclude_ratio'])
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
                tag = "排除单页" if is_excluded else "单页"
                return self._result(
                    True,
                    f"处理完成 ({tag}): {filename}",
                    is_single=True,
                    is_excluded_single=is_excluded,
                    output_count=len(output_paths),
                )

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
                cropped_img.info['dpi'] = norm_dpi
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
            return self._result(
                True,
                f"处理完成 (裁切双页): {filename}",
                is_single=False,
                is_excluded_single=False,
                output_count=len(output_paths),
            )

        except Exception as e:
            self._remove_outputs(output_paths)
            return self._result(False, f"错误 {filename}: {str(e)}", str(e), is_single=False, is_excluded_single=False, output_count=0)
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
                'work_mode': 'dir',
                'source_dir': os.path.abspath(source_text) if source_text else '',
                'target_dir': os.path.abspath(target_text) if target_text else '',
                'include_subfolders': self.include_subfolders.get(),
                'keep_images_after_pdf': self.keep_images_after_pdf.get(),
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
        if not settings['enable_binarize'] and not settings['enable_crop']:
            if settings['non_bin_format'] == 'keep' and not settings['enable_pdf']:
                messagebox.showwarning("操作无效", "请至少启用一种处理任务（色彩处理或分页裁切），或选择转为 JPG，或勾选合并输出为 PDF！")
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

            if not enable_crop and not enable_binarize:
                if not no_convert_pdf:
                    messagebox.showwarning("操作无效", "请至少勾选一种处理任务（裁切或黑白二值化）！")
                    return
                task_dir = os.path.join(pdf_dir, pdf_name)
                final_pdf_path = ''
            else:
                suffix = get_task_suffix(enable_crop, enable_binarize)
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
                'non_bin_format': self.non_bin_format.get(),
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

    def _run_pdf_pipeline_safely(self, settings):
        """运行 PDF 模式流水线：提取 -> 预处理 -> 打包 PDF -> 清理分页图片。"""
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

        # 阶段 1：提取原图
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

        # 如果勾选不转换为 PDF，且未选择色彩处理和处理分页：直接提取完成即可
        if not settings.get('enable_crop', False) and not settings.get('enable_binarize', False) and (settings.get('no_convert_pdf', False) or not settings.get('enable_pdf', True)):
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

        # 阶段 2：预处理扫描与执行
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
                    result = self._result(False, f"错误 {task[2]}: {str(e)}", str(e), is_single=False, is_excluded_single=False, output_count=0)
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

        # 检查是否不转换为 PDF
        if settings.get('no_convert_pdf', False) or not settings.get('enable_pdf', True):
            self.ui_events.put(('status', get_backend_text('status_organizing', lang)))
            try:
                # 1. 清理 raw_dir 中的提取原图（保留 out_dir）
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

                # 2. 将 out_dir 内的处理结果移动至 raw_dir
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

        # 阶段 3：打包生成新 PDF
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

        # 阶段 4：自动清理临时分页图片，仅保留生成的 PDF 文件
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

    def _build_and_save_task_report(self, summary, target_dir, **kwargs):
        """生成任务报告文本并在目标目录输出 task_report.txt 文件。"""
        report_file = os.path.join(target_dir, "task_report.txt") if (target_dir and os.path.isdir(target_dir)) else None
        summary['report_file'] = report_file
        report_text = self._completion_text(summary, **kwargs)
        if report_file:
            try:
                with open(report_file, "w", encoding="utf-8") as f:
                    f.write(report_text)
            except Exception:
                pass
        return report_text

    @staticmethod
    def _completion_text(summary, pdf_count=None, pdf_error=None, keep_images=False, pdf_path=None):
        settings = summary.get('settings') or {}
        lang = settings.get('lang') or summary.get('lang') or get_system_language()
        if lang not in ('zh-CN', 'zh-TW', 'ja', 'en'):
            lang = 'zh-CN'

        is_pdf_mode = (settings.get('work_mode') == 'pdf')
        t = REPORT_TEXTS[lang]
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
            fmt = t['fmt_keep'] if settings.get('non_bin_format') == 'keep' else t['fmt_jpg80']
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

    @classmethod
    def _add_image_page_to_pdf_writer(cls, writer, image_path, default_res=300.0):
        """将一张图片以最优且标准合规的流格式加入 PDF（1 位二值图使用 CCITT Group 4，彩色图使用 DCT/JPEG）。"""
        with Image.open(image_path) as im:
            w, h = im.size
            ext = os.path.splitext(image_path)[1].lower()
            if ext in ('.jp2', '.j2k', '.jpc', '.jpf', '.jpx', '.j2c') and 'dpi' not in im.info:
                jp2_dpi = cls._parse_jp2_dpi(image_path)
                if jp2_dpi:
                    im.info['dpi'] = jp2_dpi

            dpi = cls._get_normalized_dpi(im, default_res=default_res)
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

    def _build_single_pdf(self, image_paths, pdf_path, settings, progress_state=None):
        """将同一个输出目录内的图片写入一个 PDF（1 位二值图严格使用 CCITT Group 4 封装）。"""
        temporary_path = f"{pdf_path}.tmp"
        try:
            writer = PdfWriter()
            writer.pdf_header = getattr(self, 'PDF_SPEC_VERSION', b'%PDF-1.5')
            writer.add_metadata({
                '/Creator': self.PDF_APPLICATION_NAME,
                '/Producer': self.PDF_APPLICATION_NAME,
            })

            total = max(1, progress_state[1]) if progress_state is not None else len(image_paths)
            for image_path in image_paths:
                if self.cancel_event.is_set():
                    return False, "PDF 生成已取消。"
                self._add_image_page_to_pdf_writer(writer, image_path, default_res=300.0)
                if progress_state is not None:
                    progress_state[0] += 1
                    self.ui_events.put((
                        'progress',
                        progress_state[0] * 100.0 / total,
                        f'正在打包 PDF：{progress_state[0]} / {total}',
                    ))

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
                writer.pdf_header = getattr(ImageProcessorApp, 'PDF_SPEC_VERSION', b'%PDF-1.5')
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
    def _clean_cancelled_output(target_dir, lang='zh-CN'):
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

    def _generate_pdf_and_finish(self, settings, summary):
        """在后台生成 PDF，并将结果交给主线程显示。"""
        success, result = self._build_pdf(settings)
        if self.cancel_event.is_set():
            lang = settings.get('lang') or get_system_language()
            self.ui_events.put(('finish', self._clean_cancelled_output(settings['target_dir'], lang=lang)))
        elif success:
            summary['images_kept'] = settings.get('keep_images_after_pdf', False)
            summary_msg = self._build_and_save_task_report(
                summary,
                settings['target_dir'],
                pdf_count=len(result),
                pdf_path=result[0] if len(result) == 1 else None,
                keep_images=settings.get('keep_images_after_pdf', False),
            )
            self.ui_events.put(('finish', summary_msg))
        else:
            summary_msg = self._build_and_save_task_report(
                summary,
                settings['target_dir'],
                pdf_error=result,
            )
            self.ui_events.put(('finish', summary_msg))

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
            summary['images_kept'] = True
            summary_msg = self._build_and_save_task_report(summary, settings['target_dir'])
            self._finish_processing(summary_msg)

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

    def _run_task(self, settings):
        if (
            not settings.get('enable_binarize')
            and not settings.get('enable_crop')
            and settings.get('non_bin_format') == 'keep'
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
                    result = self._result(False, f"错误 {task[2]}: {str(e)}", str(e), is_single=False, is_excluded_single=False, output_count=0)
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

        if self.cancel_event.is_set():
            self.ui_events.put(('finish', self._clean_cancelled_output(tgt_dir, lang=lang)))
        else:
            if settings['enable_pdf']:
                lang = settings.get('lang') or get_system_language()
                self.ui_events.put(('status', get_backend_text('status_generating_summary_pdf', lang)))
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
        self.current_language = None

    def bind_window(self, window):
        self.window = window

    UPDATE_INFO_URL = 'https://tools.hanjihebi.com/aisoft/c2bw_update.json'

    def get_update_info(self):
        """从服务器读取更新描述；网络不可用时静默跳过。"""
        try:
            request = urllib.request.Request(
                self.UPDATE_INFO_URL,
                headers={'User-Agent': 'SHUGE-C2BW/3.5'},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8-sig'))
            if not isinstance(data, dict) or not data.get('version'):
                raise ValueError('服务器返回的更新信息格式无效。')
            return {'ok': True, 'current_version': '3.5.0.0', 'update': data, 'source': 'server'}
        except Exception:
            return {'ok': False, 'current_version': '3.5.0.0'}

    def open_download_url(self, url):
        try:
            value = str(url or '').strip()
            if not value.lower().startswith(('http://', 'https://')):
                return {'ok': False, 'error': '下载地址无效。'}
            webbrowser.open(value)
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def _resolve_system_dropped_path(self, raw_input):
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

    def handle_dropped_path(self, path):
        """解析拖拽到窗口的文件或目录路径，识别类型并返回建议的表单设定。"""
        resolved_path = self._resolve_system_dropped_path(path)
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

    def choose_pdf_file(self, initial_directory=''):
        """弹出文件选择器选择 PDF 文件，返回其路径和建议提取目录。"""
        if self.window is None:
            return {'ok': False, 'error': '窗口尚未初始化。'}
        with self.state_lock:
            if self.is_processing:
                return {'ok': False, 'error': '当前已有任务正在运行。'}

        try:
            selected = self.window.create_file_dialog(
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

    def choose_pdf_and_extract(self, initial_directory=''):
        """兼容旧接口：选择 PDF 并直接开始提取。"""
        res = self.choose_pdf_file(initial_directory)
        if not res.get('ok') or res.get('cancelled'):
            return res
        return self.start_pdf_extraction(res['pdf_path'], res['extract_dir'])

    def _validated_web_settings(self, raw_settings):
        try:
            raw_settings = raw_settings or {}
            source_text = str(raw_settings.get('source_dir', '')).strip()
            target_text = str(raw_settings.get('target_dir', '')).strip()
            settings = {
                'work_mode': 'dir',
                'source_dir': os.path.abspath(source_text) if source_text else '',
                'target_dir': os.path.abspath(target_text) if target_text else '',
                'include_subfolders': bool(raw_settings.get('include_subfolders', False)),
                'keep_images_after_pdf': bool(raw_settings.get('keep_images_after_pdf', False)),
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
                'lang': str(raw_settings.get('lang', '')),
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
            if settings['non_bin_format'] == 'keep' and not settings['enable_pdf']:
                return None, '请至少启用一种处理任务（色彩处理或分页裁切），或选择转为 JPG，或勾选合并输出为 PDF。'
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

    def _validated_pdf_settings(self, raw_settings):
        try:
            raw_settings = raw_settings or {}
            pdf_path = str(raw_settings.get('pdf_path', '')).strip()
            if not pdf_path or not os.path.isfile(pdf_path) or not pdf_path.lower().endswith('.pdf'):
                return None, '请先选择有效的待处理 PDF 文件。'

            enable_crop = bool(raw_settings.get('enable_crop', True))
            enable_binarize = bool(raw_settings.get('enable_binarize', True))
            no_convert_pdf = bool(raw_settings.get('no_convert_pdf', False))

            if not enable_crop and not enable_binarize:
                if not no_convert_pdf:
                    return None, '请至少启用一种处理任务（裁切或黑白二值化）。'

            pdf_dir = os.path.dirname(pdf_path)
            pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
            if not enable_crop and not enable_binarize and no_convert_pdf:
                task_dir = os.path.join(pdf_dir, pdf_name)
                final_pdf_path = ''
            else:
                suffix = get_task_suffix(enable_crop, enable_binarize)
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
                'non_bin_format': str(raw_settings.get('non_bin_format', 'keep')),
                'enable_crop': enable_crop,
                'crop_percent': int(raw_settings.get('crop_percent', 50)),
                'crop_direction': str(raw_settings.get('crop_direction', 'R2L')),
                'exclude_ratio': float(raw_settings.get('exclude_ratio', 0.7)),
                'max_threads': int(raw_settings.get('max_threads', 8)),
                'enable_pdf': not no_convert_pdf,
                'lang': str(raw_settings.get('lang', '')),
            }
        except (TypeError, ValueError, OverflowError):
            return None, '请使用有效的数字填写线程数、阈值和裁切参数。'

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

    def start_pdf_workflow(self, raw_settings):
        with self.state_lock:
            if self.is_processing:
                return {'ok': False, 'error': '当前已有任务正在运行。'}

            settings, error = self._validated_pdf_settings(raw_settings)
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

        summary['images_kept'] = True
        summary_msg = self._build_and_save_task_report(summary, settings['target_dir'])
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
            os.startfile(output_dir)
            return {'ok': True}
        except OSError as e:
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
            'saved_language': saved,
            'system_language': sys_lang,
            'effective_language': effective,
        }

    def set_user_language(self, lang):
        if lang in ('zh-CN', 'zh-TW', 'ja', 'en'):
            self.current_language = lang
            save_user_language(lang)
            self._sync_window_localization(lang)
            return {'ok': True, 'language': lang}
        return {'ok': False, 'error': f'Invalid language: {lang}'}


class WebImageProcessorBridge:
    """仅暴露给 JavaScript 的窄接口，避免 Tk 继承成员影响 pywebview 反射。"""

    def __init__(self, service):
        self.service = service

    def handle_dropped_path(self, path):
        return self.service.handle_dropped_path(path)

    def choose_directory(self, initial_directory=''):
        return self.service.choose_directory(initial_directory)

    def choose_pdf_file(self, initial_directory=''):
        return self.service.choose_pdf_file(initial_directory)

    def start_pdf_extraction(self, pdf_path, extract_dir):
        return self.service.start_pdf_extraction(pdf_path, extract_dir)

    def choose_pdf_and_extract(self, initial_directory=''):
        return self.service.choose_pdf_and_extract(initial_directory)

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
    root_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root_dir, *parts)


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


APP_TITLES = {
    'zh-CN': '智能图像预处理工具 v3.5',
    'zh-TW': '智能圖像預處理工具 v3.5',
    'ja': 'スマート画像前処理ツール v3.5',
    'en': 'Smart Image Preprocessor v3.5',
}

QUIT_CONFIRMATIONS = {
    'zh-CN': '任务可能仍在运行，确定要退出吗？',
    'zh-TW': '任務可能仍在運行，確定要退出嗎？',
    'ja': 'タスクが実行中の可能性があります。終了しますか？',
    'en': 'A task may still be running. Are you sure you want to quit?',
}


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
            base_dir = os.path.dirname(os.path.abspath(__file__))
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


def _get_free_port():
    """向操作系统内核请求一个当前未被占用的随机空闲临时端口。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]
    except Exception:
        return None


def launch_web_ui():
    service = WebImageProcessorService()
    bridge = WebImageProcessorBridge(service)
    index_path = _resource_path('webui', 'index.html')
    sys_lang = get_system_language()
    saved_lang = get_saved_language()
    current_lang = saved_lang if saved_lang else sys_lang
    service.current_language = current_lang

    app_title = APP_TITLES.get(current_lang, APP_TITLES['zh-CN'])
    quit_msg = QUIT_CONFIRMATIONS.get(current_lang, QUIT_CONFIRMATIONS['zh-CN'])

    window = webview.create_window(
        app_title,
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
    webview.start(
        gui='mshtml' if legacy_windows else None,
        http_server=True,
        http_port=free_port,
        private_mode=True,
        localization={
            'global.quitConfirmation': quit_msg,
        },
    )


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


if __name__ == "__main__":
    # 在 Windows 上设置明确的 AppUserModelID，确保任务栏正确显示程序独立图标而非默认宿主图标
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('c2bw.imageprocessor.gui.v35')
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
