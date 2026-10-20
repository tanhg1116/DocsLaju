"""Cost-first auto-OCR routing and configurable PDF raster resolution."""
import io

import pypdf
from PIL import Image

from src.config import AppSettings
from src.services.document_ocr import pdf_page_image_for_ocr
from src.services.image_preprocessor import PreprocessedImage
from src.services.ocr_jobs import OcrJobManager


def test_auto_ocr_defaults_to_batch_even_for_one_page(monkeypatch):
    monkeypatch.delenv("OCR_BATCH_FORCE", raising=False)
    manager = OcrJobManager(batch_enabled=True)
    assert manager._choose_mode(1, lambda *_args: None) == "batch"
    assert manager._choose_mode(320, lambda *_args: None) == "batch"
    assert manager._choose_mode(320, None) == "realtime"


def test_batch_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv("OCR_BATCH_FORCE", "false")
    manager = OcrJobManager(batch_enabled=True)
    assert manager._choose_mode(1, lambda *_args: None) == "realtime"


def test_default_resolution_is_120_dpi(monkeypatch):
    monkeypatch.delenv("PDF_OCR_DPI", raising=False)
    assert AppSettings(_env_file=None).pdf_ocr_dpi == 120


def test_pdf_rasterizer_honors_configured_resolution(monkeypatch):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    source = io.BytesIO()
    writer.write(source)

    # Inspect the raster before enhancement, which can independently resize it.
    def passthrough(content, mime):
        return PreprocessedImage(content, mime, {})

    monkeypatch.setattr("src.services.document_ocr.preprocess_image", passthrough)
    for dpi, expected_size in ((120, (1020, 1320)), (240, (2040, 2640))):
        monkeypatch.setenv("PDF_OCR_DPI", str(dpi))
        result = pdf_page_image_for_ocr(source.getvalue(), 1)
        with Image.open(io.BytesIO(result.content)) as image:
            assert image.size == expected_size
        assert result.report["render_dpi"] == dpi
