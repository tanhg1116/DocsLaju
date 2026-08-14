"""Document validation, rasterization, and OCR-result asset preparation."""

from __future__ import annotations

import base64
import binascii
import io
import mimetypes
import re
from dataclasses import replace
from pathlib import Path

from werkzeug.exceptions import BadRequest
from werkzeug.utils import secure_filename

from src.mistral_client import OcrMarkdown, OcrPageResult, _fix_markdown_line_breaks
from src.services.image_preprocessor import PreprocessedImage, preprocess_image


def page_count(content: bytes, is_pdf: bool) -> int:
    if not is_pdf:
        return 1
    try:
        import pymupdf

        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            return max(1, len(pdf))
    except Exception:
        try:
            from pypdf import PdfReader

            return max(1, len(PdfReader(io.BytesIO(content)).pages))
        except Exception as exc:
            raise BadRequest(f"Could not read this PDF: {exc}") from exc


def validated_upload_type(content: bytes, extension: str) -> tuple[bool, str]:
    """Verify actual content rather than trusting a filename or browser MIME."""
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise BadRequest("This file has a PDF name but is not a valid PDF")
        page_count(content, True)
        return True, "application/pdf"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            image.verify()
            image_format = str(image.format or "").upper()
    except Exception as exc:
        raise BadRequest("This file is not a readable image") from exc
    mime_type = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "WEBP": "image/webp",
    }.get(image_format)
    if not mime_type:
        raise BadRequest("Supported images: PNG, JPG, JPEG, and WEBP")
    return False, mime_type


def pdf_page_image_for_ocr(content: bytes, page_number: int) -> PreprocessedImage:
    """Render one PDF page into a clean image so upstream parsing cannot fail."""
    try:
        import pymupdf

        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if page_number < 1 or page_number > len(pdf):
                raise ValueError("Page is outside this document")
            page = pdf[page_number - 1]
            longest_points = max(float(page.rect.width), float(page.rect.height), 1.0)
            scale = min(240.0 / 72.0, 3600.0 / longest_points)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            rendered = pixmap.tobytes("png")
    except Exception as exc:
        raise RuntimeError(f"Could not rasterize PDF page {page_number}: {exc}") from exc

    optimized = preprocess_image(rendered, "image/png")
    report = dict(optimized.report)
    report.update({
        "source": "rasterized_pdf_page",
        "page_number": page_number,
        "render_dpi": round(scale * 72),
    })
    return replace(optimized, report=report)


def rasterized_pdf_for_annotation(content: bytes) -> bytes:
    """Build an image-only PDF that avoids malformed source PDF objects."""
    try:
        import pymupdf

        source = pymupdf.open(stream=content, filetype="pdf")
        output = pymupdf.open()
        try:
            for source_page in source:
                target = output.new_page(
                    width=float(source_page.rect.width),
                    height=float(source_page.rect.height),
                )
                scale = min(
                    180.0 / 72.0,
                    3000.0 / max(float(source_page.rect.width), float(source_page.rect.height), 1.0),
                )
                pixmap = source_page.get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale), alpha=False
                )
                target.insert_image(target.rect, stream=pixmap.tobytes("png"))
            return output.tobytes(garbage=4, deflate=True)
        finally:
            output.close()
            source.close()
    except Exception as exc:
        raise RuntimeError(f"Could not prepare this PDF for structured extraction: {exc}") from exc


def decode_ocr_image(data_url: str, source_ref: str) -> tuple[str, bytes, str]:
    mime_type = mimetypes.guess_type(source_ref)[0] or "image/jpeg"
    encoded = data_url
    if data_url.startswith("data:"):
        header, separator, encoded = data_url.partition(",")
        if not separator or ";base64" not in header:
            raise ValueError("Unsupported OCR image encoding")
        mime_type = header[5:].split(";", 1)[0].lower()
    if not mime_type.startswith("image/"):
        raise ValueError("OCR object is not an image")
    try:
        content = base64.b64decode(re.sub(r"\s+", "", encoded), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("OCR returned invalid image data") from exc
    extension = {
        "image/jpeg": "jpg",
        "image/svg+xml": "svg",
        "image/x-icon": "ico",
    }.get(mime_type, mimetypes.guess_extension(mime_type, strict=False) or ".bin").lstrip(".")
    return mime_type, content, extension


def prepare_ocr_markdown(
    document: dict,
    page_number: int,
    page: OcrPageResult,
    preprocessing_report: dict | None = None,
) -> OcrMarkdown:
    """Preserve provider Markdown while replacing embedded assets with short paths."""
    source_markdown = page.markdown
    editable_markdown = source_markdown
    document_stem = secure_filename(Path(document["name"]).stem) or "document"
    assets: list[dict] = []
    for index, image in enumerate(page.images, start=1):
        mime_type, content, extension = decode_ocr_image(image.data_url, image.source_ref)
        filename = f"{document_stem}-{page_number}-image-{index}.{extension}"
        editable_markdown = editable_markdown.replace(
            f"]({image.source_ref})",
            f"](assets/{filename})",
        )
        assets.append({
            "source_ref": image.source_ref,
            "object_type": "image",
            "filename": filename,
            "mime_type": mime_type,
            "content": content,
        })
    editable_markdown = _fix_markdown_line_breaks(
        editable_markdown,
        parse_structured_md=True,
    )
    return OcrMarkdown(
        editable_markdown,
        source_markdown=source_markdown,
        assets=assets,
        confidence_score=page.confidence_score,
        preprocessing_report=preprocessing_report,
    )
