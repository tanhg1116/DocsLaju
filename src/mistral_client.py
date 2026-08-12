from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Any

from dotenv import load_dotenv
from src.services.apilog import log_api

# Mistral SDK
try:
    try:
        from mistralai import Mistral
    except ImportError:
        from mistralai.client import Mistral
except Exception as e:  # pragma: no cover - import-time errors surface clearly to user
    Mistral = None  # type: ignore
    _MISTRAL_IMPORT_ERROR: Exception | None = e
else:
    _MISTRAL_IMPORT_ERROR = None


MODEL_DEFAULT = "mistral-ocr-latest"


@dataclass(frozen=True)
class OcrImageResult:
    source_ref: str
    data_url: str


@dataclass(frozen=True)
class OcrPageResult:
    markdown: str
    images: tuple[OcrImageResult, ...] = ()
    confidence_score: float | None = None


class OcrMarkdown(str):
    """Editable OCR Markdown carrying its immutable source and extracted assets."""

    source_markdown: str
    assets: list[dict]
    confidence_score: float | None

    def __new__(
        cls,
        value: str,
        *,
        source_markdown: str,
        assets: list[dict],
        confidence_score: float | None = None,
    ) -> "OcrMarkdown":
        instance = super().__new__(cls, value)
        instance.source_markdown = source_markdown
        instance.assets = assets
        instance.confidence_score = confidence_score
        return instance


def _page_confidence(page: Any) -> float | None:
    scores = getattr(page, "confidence_scores", None)
    value = (
        scores.get("average_page_confidence_score")
        if isinstance(scores, dict)
        else getattr(scores, "average_page_confidence_score", None)
    )
    try:
        return max(0.0, min(1.0, float(value))) if value is not None else None
    except (TypeError, ValueError):
        return None


def _get_api_key() -> str:
    # Load environment variables from .env once
    if not os.environ.get("DOTENV_LOADED"):
        try:
            load_dotenv(override=False)
        finally:
            os.environ["DOTENV_LOADED"] = "1"
    key = os.getenv("MISTRAL_API_KEY")
    if not key:
        raise RuntimeError("Missing MISTRAL_API_KEY in environment (.env). Create a .env with MISTRAL_API_KEY=...")
    return key


@lru_cache(maxsize=1)
def get_client() -> Any:
    if Mistral is None:
        detail = f": {_MISTRAL_IMPORT_ERROR}" if _MISTRAL_IMPORT_ERROR else ""
        raise RuntimeError(f"mistralai could not be imported{detail}")
    return Mistral(api_key=_get_api_key())


def _replace_images_in_markdown(markdown_str: str, images_dict: dict) -> str:
    # Replace placeholders ![id](id) with base64 strings
    for img_name, base64_str in images_dict.items():
        markdown_str = markdown_str.replace(f"![{img_name}]({img_name})", f"![{img_name}]({base64_str})")
    return markdown_str


def _fix_markdown_line_breaks(markdown_str: str, parse_structured_md: bool = False) -> str:
    """Fix single newlines in plain text to render as line breaks in markdown.
    
    Args:
        markdown_str: The markdown string to process
        parse_structured_md: If True, uses regex to detect and preserve structured markdown.
                            If False, simply adds two spaces before all single newlines.
    
    When parse_structured_md=True, excludes structured markdown blocks:
    - Code blocks (fenced ``` or indented)
    - Tables (rows with |)
    - Math blocks ($$...$$)
    - Lists (-, *, numbers)
    - Block quotes (>)
    - Headings (#)
    - Horizontal rules (---, ***, ___)
    
    When parse_structured_md=False, adds two spaces before single newlines
    (but only before the first newline in consecutive sequences like \\n\\n\\n).
    """
    import re
    
    if parse_structured_md:
        # Part 1: Smart parsing with regex to detect structured markdown
        lines = markdown_str.split('\n')
        result = []
        
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Check if we're starting a fenced code block
            if re.match(r'^```', line):
                result.append(line)
                i += 1
                # Capture everything until closing ```
                while i < len(lines) and not re.match(r'^```', lines[i]):
                    result.append(lines[i])
                    i += 1
                if i < len(lines):
                    result.append(lines[i])  # closing ```
                i += 1
                continue
            
            # Check if we're in a math block
            if re.match(r'^\$\$', line):
                result.append(line)
                i += 1
                # Capture everything until closing $$
                while i < len(lines) and not re.match(r'^\$\$', lines[i]):
                    result.append(lines[i])
                    i += 1
                if i < len(lines):
                    result.append(lines[i])  # closing $$
                i += 1
                continue
            
            # Check if line is part of a structured element (no line break needed)
            is_structured = (
                line.strip() == '' or  # Empty line (paragraph separator)
                '|' in line or  # Table row
                re.match(r'^\s{4,}', line) or  # Indented code block
                re.match(r'^\s*#{1,6}\s', line) or  # Heading
                re.match(r'^\s*[-*_]{3,}\s*$', line) or  # Horizontal rule
                re.match(r'^\s*[-*+]\s', line) or  # Unordered list
                re.match(r'^\s*\d+\.\s', line) or  # Ordered list
                re.match(r'^\s*>', line) or  # Block quote
                line.endswith('  ') or  # Already has hard break
                line.strip().endswith('\\')  # Backslash line break
            )
            
            # Check if next line is structured (don't add break before structured content)
            next_is_structured = False
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                next_is_structured = (
                    next_line.strip() == '' or
                    '|' in next_line or
                    re.match(r'^\s{4,}', next_line) or
                    re.match(r'^\s*#{1,6}\s', next_line) or
                    re.match(r'^\s*[-*_]{3,}\s*$', next_line) or
                    re.match(r'^\s*[-*+]\s', next_line) or
                    re.match(r'^\s*\d+\.\s', next_line) or
                    re.match(r'^\s*>', next_line) or
                    re.match(r'^```', next_line) or
                    re.match(r'^\$\$', next_line)
                )
            
            # Add line with or without hard break
            if is_structured or next_is_structured or i == len(lines) - 1:
                result.append(line)
            else:
                # Plain text line that needs hard break
                result.append(line + '  ')
            
            i += 1
        
        return '\n'.join(result)
    
    else:
        # Part 2: Simple approach - add two spaces before single newlines
        # For consecutive newlines (\n\n\n...), only add spaces before the first one
        
        # Replace single \n (not followed by another \n) with two spaces + \n
        # This preserves paragraph breaks (\n\n) while adding hard breaks to single newlines
        result = re.sub(r'(?<!  )(?<!\n)\n(?!\n)', '  \n', markdown_str)
        
        return result


def ocr_pdf_pages_with_assets(
    pdf_bytes: bytes,
    *,
    include_images: bool = True,
    model: str | None = None,
) -> List[OcrPageResult]:
    """Process a PDF and return its exact page Markdown plus extracted images."""
    client = get_client()

    log_api("files.upload:start", extra={"purpose": "ocr", "bytes": len(pdf_bytes)})
    uploaded_file = client.files.upload(
        file={
            "file_name": "document.pdf",
            "content": pdf_bytes,
        },
        purpose="ocr",
    )
    log_api("files.upload:ok", extra={"file_id": getattr(uploaded_file, "id", None)})
    log_api("files.get_signed_url:start", extra={"file_id": getattr(uploaded_file, "id", None)})
    signed_url = client.files.get_signed_url(file_id=uploaded_file.id, expiry=1)
    log_api("files.get_signed_url:ok")

    log_api("ocr.process:start", extra={"model": model or MODEL_DEFAULT, "type": "pdf"})
    ocr_response = client.ocr.process(
        document={"type": "document_url", "document_url": signed_url.url},
        model=model or MODEL_DEFAULT,
        include_image_base64=include_images,
        confidence_scores_granularity="page",
    )
    log_api("ocr.process:ok", extra={"pages": len(getattr(ocr_response, "pages", []) or [])})

    pages: List[OcrPageResult] = []
    for page in ocr_response.pages:
        images = tuple(
            OcrImageResult(str(image.id), str(image.image_base64))
            for image in (getattr(page, "images", None) or [])
            if getattr(image, "id", None) and getattr(image, "image_base64", None)
        )
        pages.append(
            OcrPageResult(
                str(getattr(page, "markdown", "")),
                images,
                _page_confidence(page),
            )
        )
    return pages


def ocr_pdf_pages_markdown(pdf_bytes: bytes, *, include_images: bool = True, model: str | None = None) -> List[str]:
    """Compatibility wrapper returning one renderable Markdown string per page."""
    pages_md: List[str] = []
    for page in ocr_pdf_pages_with_assets(
        pdf_bytes,
        include_images=include_images,
        model=model,
    ):
        images = {image.source_ref: image.data_url for image in page.images}
        markdown_text = _replace_images_in_markdown(page.markdown, images)
        pages_md.append(_fix_markdown_line_breaks(markdown_text, parse_structured_md=True))
    return pages_md


def ocr_image_with_assets(image_bytes: bytes, *, model: str | None = None) -> OcrPageResult:
    """Process an image and return its exact Markdown plus extracted images."""
    client = get_client()
    encoded = base64.b64encode(image_bytes).decode()
    base64_data_url = f"data:image/jpeg;base64,{encoded}"
    log_api("ocr.process:start", extra={"model": model or MODEL_DEFAULT, "type": "image", "bytes": len(image_bytes)})
    ocr_response = client.ocr.process(
        document={"type": "image_url", "image_url": base64_data_url},
        model=model or MODEL_DEFAULT,
        include_image_base64=True,
        confidence_scores_granularity="page",
    )
    log_api("ocr.process:ok", extra={"pages": len(getattr(ocr_response, "pages", []) or [])})
    if not ocr_response.pages:
        return OcrPageResult("")
    page = ocr_response.pages[0]
    images = tuple(
        OcrImageResult(str(image.id), str(image.image_base64))
        for image in (getattr(page, "images", None) or [])
        if getattr(image, "id", None) and getattr(image, "image_base64", None)
    )
    return OcrPageResult(
        str(getattr(page, "markdown", "")),
        images,
        _page_confidence(page),
    )


def ocr_image_markdown(image_bytes: bytes, *, model: str | None = None) -> str:
    """Compatibility wrapper returning renderable Markdown text."""
    page = ocr_image_with_assets(image_bytes, model=model)
    return _fix_markdown_line_breaks(
        page.markdown,
        parse_structured_md=True,
    )
