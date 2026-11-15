# -*- coding: utf-8 -*-
"""PDF 中 JPEG 2000（/JPXDecode）分页按原始 JP2 格式提取的回归测试。"""
import io
import os
import tempfile
import unittest

from PIL import Image, ImageChops

from c2bw.core import (
    _add_resolution_to_jp2,
    _jpx_stream_as_jp2_file,
    _wrap_j2k_codestream_as_jp2,
    build_single_pdf,
    extract_images_from_pdf,
    fitz,
    parse_jp2_dpi,
)


def _encode(img, no_jp2=False):
    # 旧版 Pillow（7.2）的 JPEG2000 编码器只能写真实文件；扩展名 .j2k 输出裸码流
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.j2k" if no_jp2 else "x.jp2")
        img.save(path, format="JPEG2000")
        with open(path, "rb") as f:
            return f.read()


def _dpi_of_bytes(data):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.jp2")
        with open(path, "wb") as f:
            f.write(data)
        return parse_jp2_dpi(path)


def _same_pixels(data, reference):
    with Image.open(io.BytesIO(data)) as im:
        return ImageChops.difference(im.convert(reference.mode), reference).getbbox() is None


class TestJp2Containers(unittest.TestCase):
    def setUp(self):
        self.rgb = Image.effect_noise((128, 96), 40).convert("RGB")
        self.gray = self.rgb.convert("L")

    def test_codestream_wrapped_losslessly_with_resolution(self):
        for img in (self.rgb, self.gray):
            codestream = _encode(img, no_jp2=True)
            self.assertTrue(codestream.startswith(b"\xff\x4f\xff\x51"))
            jp2 = _wrap_j2k_codestream_as_jp2(codestream, dpi=(400, 400))
            self.assertIsNotNone(jp2)
            self.assertTrue(jp2.startswith(b"\x00\x00\x00\x0cjP  \r\n\x87\n"))
            self.assertTrue(jp2.endswith(codestream))  # 码流字节原样保留
            with Image.open(io.BytesIO(jp2)) as im:
                self.assertEqual(im.format, "JPEG2000")
                self.assertEqual(im.size, img.size)
            self.assertTrue(_same_pixels(jp2, img))
            dpi = _dpi_of_bytes(jp2)
            self.assertAlmostEqual(dpi[0], 400, delta=0.1)
            self.assertAlmostEqual(dpi[1], 400, delta=0.1)

    def test_high_dpi_encoding_fits_box(self):
        jp2 = _wrap_j2k_codestream_as_jp2(_encode(self.gray, no_jp2=True), dpi=(2400, 1200))
        dpi = _dpi_of_bytes(jp2)
        self.assertAlmostEqual(dpi[0], 2400, delta=1)
        self.assertAlmostEqual(dpi[1], 1200, delta=1)

    def test_existing_jp2_keeps_codestream_and_gains_resolution(self):
        original = _encode(self.rgb)
        self.assertIsNone(_dpi_of_bytes(original))
        patched = _add_resolution_to_jp2(original, (350, 350))
        codestream = original[original.find(b"jp2c") + 4:]
        self.assertTrue(patched.endswith(codestream))
        self.assertTrue(_same_pixels(patched, self.rgb))
        self.assertAlmostEqual(_dpi_of_bytes(patched)[0], 350, delta=0.1)
        # 已有解析度时不再改动
        self.assertEqual(_add_resolution_to_jp2(patched, (600, 600)), patched)

    def test_non_jpeg2000_data_is_rejected(self):
        self.assertIsNone(_wrap_j2k_codestream_as_jp2(b"\xff\xd8\xff\xe0garbage"))
        self.assertIsNone(_jpx_stream_as_jp2_file(b""))


@unittest.skipIf(fitz is None, "PyMuPDF 不可用")
class TestJp2PdfExtraction(unittest.TestCase):
    def test_jpx_pages_extracted_as_original_jp2(self):
        rgb = Image.effect_noise((600, 800), 40).convert("RGB")
        gray = rgb.convert("L")
        streams = [(rgb, _encode(rgb)), (gray, _encode(gray, no_jp2=True))]
        with tempfile.TemporaryDirectory() as d:
            sources = []
            for i, (_img, data) in enumerate(streams):
                path = os.path.join(d, f"src{i}.jp2")
                with open(path, "wb") as f:
                    f.write(data)
                sources.append(path)
            # 400 DPI 的页面：600×800 像素 → 108×144 pt
            pdf_path = os.path.join(d, "jp2.pdf")
            doc = fitz.open()
            for path in sources:
                page = doc.new_page(width=108, height=144)
                with open(path, "rb") as f:
                    page.insert_image(page.rect, stream=f.read())
            doc.save(pdf_path)
            doc.close()

            with fitz.open(pdf_path) as check:
                for page in check:
                    xref = page.get_images()[0][0]
                    self.assertEqual(check.extract_image(xref)["ext"], "jpx")

            out_dir = os.path.join(d, "out")
            count, err = extract_images_from_pdf(pdf_path, out_dir)
            self.assertEqual((count, err), (2, ""))
            outputs = sorted(os.listdir(out_dir))
            self.assertEqual(outputs, ["page_0001.jp2", "page_0002.jp2"])

            for name, (img, data) in zip(outputs, streams):
                path = os.path.join(out_dir, name)
                with open(path, "rb") as f:
                    extracted = f.read()
                codestream = data[data.find(b"jp2c") + 4:] if data.startswith(b"\x00\x00\x00\x0cjP") else data
                self.assertTrue(extracted.endswith(codestream), name)
                self.assertTrue(_same_pixels(extracted, img), name)
                dpi = parse_jp2_dpi(path)
                self.assertAlmostEqual(dpi[0], 400, delta=0.5)
                self.assertAlmostEqual(dpi[1], 400, delta=0.5)

            # 重新打包后页面尺寸与原 PDF 一致
            rebuilt = os.path.join(d, "rebuilt.pdf")
            ok, _ = build_single_pdf([os.path.join(out_dir, n) for n in outputs], rebuilt)
            self.assertTrue(ok)
            with fitz.open(rebuilt) as again:
                for page in again:
                    self.assertAlmostEqual(page.rect.width, 108, delta=0.5)
                    self.assertAlmostEqual(page.rect.height, 144, delta=0.5)


@unittest.skipIf(fitz is None, "PyMuPDF 不可用")
class TestJp2RotatedPage(unittest.TestCase):
    def test_rotated_jpx_page_is_rotated_and_keeps_resolution(self):
        gray = Image.effect_noise((600, 800), 40).convert("L")
        data = _encode(gray)
        with tempfile.TemporaryDirectory() as d:
            pdf_path = os.path.join(d, "rot.pdf")
            doc = fitz.open()
            page = doc.new_page(width=108, height=144)
            page.insert_image(page.rect, stream=data)
            page.set_rotation(90)
            doc.save(pdf_path)
            doc.close()

            out_dir = os.path.join(d, "out")
            self.assertEqual(extract_images_from_pdf(pdf_path, out_dir), (1, ""))
            path = os.path.join(out_dir, "page_0001.jp2")
            with open(path, "rb") as f:
                extracted = f.read()
            expected = gray.transpose(Image.ROTATE_270)  # 顺时针 90°
            with Image.open(io.BytesIO(extracted)) as im:
                self.assertEqual(im.format, "JPEG2000")
                self.assertEqual(im.size, (800, 600))
            self.assertTrue(_same_pixels(extracted, expected))
            dpi = parse_jp2_dpi(path)
            self.assertAlmostEqual(dpi[0], 400, delta=0.5)
            self.assertAlmostEqual(dpi[1], 400, delta=0.5)


if __name__ == "__main__":
    unittest.main()
