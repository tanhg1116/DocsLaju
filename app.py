from __future__ import annotations

import base64
import binascii
import csv
import html
import io
import json
import math
import mimetypes
import os
import re
import secrets
import sqlite3
import zipfile
from pathlib import Path
from urllib.parse import quote

import markdown
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file
from werkzeug.exceptions import BadRequest, Conflict, NotFound, RequestEntityTooLarge
from werkzeug.utils import secure_filename

from src.mistral_client import (
    MODEL_DEFAULT,
    OcrMarkdown,
    OcrPageResult,
    _fix_markdown_line_breaks,
    extract_document_annotation,
    ocr_document_with_annotation,
    ocr_image_with_assets,
    ocr_pdf_pages_with_assets,
)
from src.extraction_templates import (
    DOCUMENT_ANNOTATION_PAGE_LIMIT,
    get_template,
    public_templates,
)
from src.services.database import ADMIN_USER_ID, Database
from src.services.browser_pdf import BrowserPdfError, render_url_to_pdf
from src.services.ocr_jobs import OcrJobManager
from src.services.image_preprocessor import PreprocessedImage, preprocess_image


BASE_DIR = Path(__file__).resolve().parent
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
APP_PORT = int(os.getenv("PORT", "5000"))
MATH_EXPRESSION_RE = re.compile(
    r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|"
    r"(?<!\$)\$(?![\s$])[^\n$]*?(?<!\s)\$(?![\d$])",
    re.DOTALL,
)
ASSET_REFERENCE_RE = re.compile(r"(!\[[^\]]*\]\()assets/([^\s)]+)(\))")
load_dotenv(override=False)
app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32),
    DATABASE=os.getenv("DOCSLAJU_DB_PATH") or str(BASE_DIR / "instance" / "docslaju.sqlite3"),
    INTERNAL_BASE_URL=(
        os.getenv("DOCSLAJU_INTERNAL_BASE_URL") or f"http://127.0.0.1:{APP_PORT}"
    ).rstrip("/"),
)
app.extensions["database"] = Database(app.config["DATABASE"], BASE_DIR / "db" / "schema.sql")
app.extensions["ocr_jobs"] = OcrJobManager()


def _db() -> Database:
    return app.extensions["database"]


def _job_manager() -> OcrJobManager:
    return app.extensions["ocr_jobs"]


def _compile_markdown(source: str, asset_urls: dict[str, str] | None = None) -> str:
    """Compile Markdown without allowing it to consume LaTeX control characters."""
    math_expressions: list[tuple[str, str]] = []
    placeholder_prefix = f"DOCSLAJUMATH{secrets.token_hex(12).upper()}"

    def stash_math(match: re.Match[str]) -> str:
        placeholder = f"{placeholder_prefix}{len(math_expressions)}END"
        expression = match.group(0)
        # Single-dollar math is ambiguous with currency. Only expressions that
        # passed the boundary rules above reach here, and the conversion exists
        # solely in rendered HTML; the stored OCR Markdown remains untouched.
        if expression.startswith("$") and not expression.startswith("$$"):
            expression = rf"\({expression[1:-1]}\)"
        math_expressions.append((placeholder, expression))
        return placeholder

    if asset_urls:
        source = ASSET_REFERENCE_RE.sub(
            lambda match: (
                f"{match.group(1)}{asset_urls.get(match.group(2), match.group(0))}{match.group(3)}"
                if match.group(2) in asset_urls
                else match.group(0)
            ),
            source,
        )
    protected_source = MATH_EXPRESSION_RE.sub(stash_math, source)
    safe_source = html.escape(protected_source, quote=False)
    rendered = markdown.markdown(
        safe_source,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    for placeholder, expression in math_expressions:
        rendered = rendered.replace(placeholder, html.escape(expression, quote=False))
    return rendered


def _state() -> dict:
    return _db().state(ADMIN_USER_ID, MODEL_DEFAULT)


def _session(session_id: str) -> dict:
    try:
        return _db().get_session(ADMIN_USER_ID, session_id)
    except KeyError as exc:
        raise NotFound("OCR session not found") from exc


def _document(session_id: str, file_id: str) -> dict:
    try:
        return _db().get_document(ADMIN_USER_ID, session_id, file_id)
    except KeyError as exc:
        raise NotFound("Document not found") from exc


def _document_asset_urls(session_id: str, document_id: str) -> dict[str, str]:
    try:
        assets = _db().list_document_assets(
            ADMIN_USER_ID,
            session_id,
            document_id,
        )
    except KeyError as exc:
        raise NotFound("Document not found") from exc
    return {
        asset["filename"]: (
            f"/api/sessions/{quote(session_id, safe='')}/files/"
            f"{quote(document_id, safe='')}/assets/{quote(asset['filename'], safe='')}"
        )
        for asset in assets
    }


def _page_count(content: bytes, is_pdf: bool) -> int:
    if not is_pdf:
        return 1
    try:
        import fitz

        with fitz.open(stream=content, filetype="pdf") as pdf:
            return max(1, len(pdf))
    except Exception:
        try:
            import PyPDF2

            return max(1, len(PyPDF2.PdfReader(io.BytesIO(content)).pages))
        except Exception as exc:
            raise BadRequest(f"Could not read this PDF: {exc}") from exc


def _single_pdf_page(content: bytes, page_number: int) -> bytes:
    import PyPDF2

    reader = PyPDF2.PdfReader(io.BytesIO(content))
    writer = PyPDF2.PdfWriter()
    writer.add_page(reader.pages[page_number - 1])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _decode_ocr_image(data_url: str, source_ref: str) -> tuple[str, bytes, str]:
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


def _prepare_ocr_markdown(
    document: dict,
    page_number: int,
    page: OcrPageResult,
    preprocessing_report: dict | None = None,
) -> OcrMarkdown:
    source_markdown = page.markdown
    editable_markdown = source_markdown
    document_stem = secure_filename(Path(document["name"]).stem) or "document"
    assets: list[dict] = []
    for index, image in enumerate(page.images, start=1):
        mime_type, content, extension = _decode_ocr_image(image.data_url, image.source_ref)
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


def _preprocessed_document_image(document: dict) -> PreprocessedImage:
    return preprocess_image(document["content"], str(document["mime_type"]))


def _ocr_document_with_template(document: dict, template: dict) -> tuple[list[OcrMarkdown], dict]:
    content = document["content"]
    mime_type = str(document["mime_type"])
    preprocessing_report = None
    if not document["is_pdf"]:
        optimized = _preprocessed_document_image(document)
        content = optimized.content
        mime_type = optimized.mime_type
        preprocessing_report = optimized.report
    combined = ocr_document_with_annotation(
        content,
        is_pdf=bool(document["is_pdf"]),
        mime_type=mime_type,
        schema_name=f"{template['id']}_v{template['schema_version']}",
        schema=template["schema"],
        prompt=template["prompt"],
        model=MODEL_DEFAULT,
    )
    if len(combined.pages) != int(document["num_pages"]):
        raise RuntimeError("Mistral returned an unexpected number of OCR pages; nothing was saved")
    pages = [
        _prepare_ocr_markdown(
            document,
            page_number,
            page,
            preprocessing_report=preprocessing_report,
        )
        for page_number, page in enumerate(combined.pages, start=1)
    ]
    return pages, _normalize_template_data(combined.document_annotation, template)


def _infer_document_page(document: dict, page_number: int) -> str:
    if document["is_pdf"]:
        pages = ocr_pdf_pages_with_assets(
            _single_pdf_page(document["content"], page_number),
            model=MODEL_DEFAULT,
        )
        page = pages[0] if pages else OcrPageResult("")
    else:
        optimized = _preprocessed_document_image(document)
        page = ocr_image_with_assets(
            optimized.content,
            mime_type=optimized.mime_type,
            model=MODEL_DEFAULT,
        )
        return _prepare_ocr_markdown(
            document,
            page_number,
            page,
            preprocessing_report=optimized.report,
        )
    return _prepare_ocr_markdown(document, page_number, page)


def _resume_pending_ocr_jobs() -> None:
    for job in _db().recover_interrupted_ocr_jobs():
        _job_manager().start(
            _db(),
            int(job["user_id"]),
            str(job["id"]),
            _infer_document_page,
        )


_resume_pending_ocr_jobs()


def _nullable_text(value: object, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _nullable_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Amounts must be numbers")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Amounts must be numbers") from exc
    if not math.isfinite(number) or not (-1_000_000_000_000 < number < 1_000_000_000_000):
        raise ValueError("Amount is outside the supported range")
    return round(number, 6)


def _normalize_template_data(data: object, template: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Structured extraction must be an object")
    schema = template["schema"]

    def normalize(value: object, field_schema: dict, depth: int = 0) -> object:
        if depth > 4:
            raise ValueError("Structured extraction is nested too deeply")
        raw_type = field_schema.get("type")
        allowed_types = raw_type if isinstance(raw_type, list) else [raw_type]
        non_null_type = next((item for item in allowed_types if item != "null"), None)
        if value is None:
            return [] if non_null_type == "array" else None
        if non_null_type == "string":
            return _nullable_text(value, 4000)
        if non_null_type == "number":
            return _nullable_number(value)
        if non_null_type == "boolean":
            if isinstance(value, bool):
                return value
            raise ValueError("Boolean fields must be true or false")
        if non_null_type == "array":
            if not isinstance(value, list):
                raise ValueError("Repeated fields must be lists")
            item_schema = field_schema.get("items", {})
            normalized_items = [
                normalize(item, item_schema, depth + 1) for item in value[:500]
            ]
            item_type = item_schema.get("type")
            if item_type == "string" or (
                isinstance(item_type, list) and "string" in item_type
            ):
                unique_items: list[str] = []
                seen: set[str] = set()
                for item in normalized_items:
                    if not isinstance(item, str) or not item:
                        continue
                    identity = item.casefold()
                    if identity in seen:
                        continue
                    seen.add(identity)
                    unique_items.append(item)
                return unique_items
            return normalized_items
        if non_null_type == "object":
            if not isinstance(value, dict):
                raise ValueError("Structured sections must be objects")
            properties = field_schema.get("properties", {})
            return {
                key: normalize(value.get(key), child_schema, depth + 1)
                for key, child_schema in properties.items()
            }
        return None

    normalized = {
        key: normalize(data.get(key), field_schema)
        for key, field_schema in schema["properties"].items()
    }
    normalized["document_type"] = template["id"]
    return normalized


def _template_from_request(template_id: str) -> dict:
    try:
        return get_template(template_id)
    except KeyError as exc:
        raise NotFound("Extraction template not found") from exc


def _optional_template(template_id: object) -> dict | None:
    value = str(template_id or "").strip()
    if not value:
        return None
    try:
        return get_template(value)
    except KeyError as exc:
        raise BadRequest("Unknown document type") from exc


def _extraction_payload(extraction: dict | None, template: dict) -> dict:
    return {
        "profile": template["id"],
        "profile_name": template["label"],
        "schema_version": template["schema_version"],
        "layout": template["layout"],
        "extraction": extraction,
    }


@app.get("/api/extraction-templates")
def extraction_templates() -> Response:
    return jsonify({"templates": public_templates()})


@app.get("/")
def index() -> str:
    _db().ensure_active_session(ADMIN_USER_ID)
    return render_template("index.html")


@app.get("/api/state")
def get_state() -> Response:
    return jsonify(_state())


@app.get("/api/search")
def search_workspace() -> Response:
    query = str(request.args.get("q", "")).strip()
    if len(query) > 200:
        raise BadRequest("Search is limited to 200 characters")
    try:
        results = _db().search_pages(ADMIN_USER_ID, query)
    except sqlite3.OperationalError as exc:
        raise BadRequest("Could not parse that search") from exc
    return jsonify({"query": query, "results": results})


@app.post("/api/search/open")
def open_search_result() -> Response:
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id", ""))
    document_id = str(payload.get("document_id", ""))
    try:
        page_number = int(payload.get("page_number", 0))
    except (TypeError, ValueError) as exc:
        raise BadRequest("A valid page number is required") from exc
    try:
        _db().open_search_result(
            ADMIN_USER_ID,
            session_id,
            document_id,
            page_number,
        )
    except KeyError as exc:
        raise NotFound("Search result no longer exists") from exc
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return jsonify(_state())


@app.post("/api/projects")
def create_project() -> tuple[Response, int]:
    name = str((request.get_json(silent=True) or {}).get("name", "")).strip()
    if not name:
        raise BadRequest("A project name is required")
    try:
        _db().create_project(ADMIN_USER_ID, name[:80])
    except sqlite3.IntegrityError as exc:
        raise Conflict("A project with that name already exists") from exc
    return jsonify(_state()), 201


@app.patch("/api/projects/<project_id>")
def rename_project(project_id: str) -> Response:
    name = str((request.get_json(silent=True) or {}).get("name", "")).strip()
    if not name:
        raise BadRequest("A project name is required")
    try:
        _db().rename_project(ADMIN_USER_ID, project_id, name[:80])
    except KeyError as exc:
        raise NotFound("Project not found") from exc
    except sqlite3.IntegrityError as exc:
        raise Conflict("A project with that name already exists") from exc
    return jsonify(_state())


@app.delete("/api/projects/<project_id>")
def delete_project(project_id: str) -> Response:
    try:
        _db().delete_project(ADMIN_USER_ID, project_id)
    except KeyError as exc:
        raise NotFound("Project not found") from exc
    return jsonify(_state())


@app.post("/api/sessions")
def create_session() -> tuple[Response, int]:
    project_id = (request.get_json(silent=True) or {}).get("project_id")
    try:
        _db().create_session(ADMIN_USER_ID, str(project_id) if project_id else None)
    except KeyError as exc:
        raise NotFound("Project not found") from exc
    return jsonify(_state()), 201


@app.post("/api/sessions/<session_id>/activate")
def activate_session(session_id: str) -> Response:
    try:
        _db().activate_session(ADMIN_USER_ID, session_id)
    except KeyError as exc:
        raise NotFound("OCR session not found") from exc
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return jsonify(_state())


@app.patch("/api/sessions/<session_id>")
def update_session(session_id: str) -> Response:
    _session(session_id)
    payload = request.get_json(silent=True) or {}
    changes: dict[str, object] = {}

    if "title" in payload:
        title = str(payload.get("title", "")).strip()
        if not title:
            raise BadRequest("A session title is required")
        changes["title"] = title[:80]
    if "project_id" in payload:
        changes["project_id"] = str(payload["project_id"]) if payload["project_id"] else None
    if "is_pinned" in payload:
        changes["is_pinned"] = bool(payload["is_pinned"])
    if "is_archived" in payload:
        changes["is_archived"] = bool(payload["is_archived"])

    try:
        _db().update_session(ADMIN_USER_ID, session_id, **changes)
    except KeyError as exc:
        raise NotFound(str(exc)) from exc
    return jsonify(_state())


@app.delete("/api/sessions/<session_id>")
def delete_session(session_id: str) -> Response:
    try:
        _db().delete_session(ADMIN_USER_ID, session_id)
    except KeyError as exc:
        raise NotFound("OCR session not found") from exc
    return jsonify(_state())


@app.post("/api/sessions/<session_id>/files")
def upload_file(session_id: str) -> tuple[Response, int]:
    _session(session_id)
    upload = request.files.get("file")
    if not upload or not upload.filename:
        raise BadRequest("Choose a PDF or image to upload")

    name = secure_filename(upload.filename) or "document"
    extension = os.path.splitext(name)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise BadRequest("Supported files: PDF, PNG, JPG, JPEG, and WEBP")

    content = upload.read()
    if not content:
        raise BadRequest("The uploaded file is empty")
    is_pdf = extension == ".pdf" or upload.mimetype == "application/pdf"
    template = _optional_template(request.form.get("document_type"))
    document_type = template["id"] if template else None
    duplicate = _db().find_document_by_checksum(ADMIN_USER_ID, session_id, content)
    if duplicate:
        if document_type is not None:
            _db().set_document_type(ADMIN_USER_ID, session_id, str(duplicate["id"]), document_type)
        _db().activate_document(ADMIN_USER_ID, session_id, str(duplicate["id"]))
        payload = _state()
        payload["upload"] = {
            "duplicate": True,
            "document_id": duplicate["id"],
            "message": f"{duplicate['name']} is already in this session",
        }
        return jsonify(payload), 200
    _db().add_document(
        ADMIN_USER_ID,
        session_id,
        name=name,
        content=content,
        mime_type=upload.mimetype or ("application/pdf" if is_pdf else "image/jpeg"),
        is_pdf=is_pdf,
        num_pages=_page_count(content, is_pdf),
        document_type=document_type,
    )
    payload = _state()
    payload["upload"] = {"duplicate": False, "message": f"{name} uploaded"}
    return jsonify(payload), 201


@app.patch("/api/sessions/<session_id>/files/<file_id>/document-type")
def update_document_type(session_id: str, file_id: str) -> Response:
    _document(session_id, file_id)
    template = _optional_template((request.get_json(silent=True) or {}).get("document_type"))
    _db().set_document_type(
        ADMIN_USER_ID,
        session_id,
        file_id,
        template["id"] if template else None,
    )
    return jsonify(_state())


@app.post("/api/sessions/<session_id>/files/<file_id>/activate")
def activate_file(session_id: str, file_id: str) -> Response:
    try:
        _db().activate_document(ADMIN_USER_ID, session_id, file_id)
    except KeyError as exc:
        raise NotFound("Document not found") from exc
    return jsonify(_state())


@app.delete("/api/sessions/<session_id>/files/<file_id>")
def delete_file(session_id: str, file_id: str) -> Response:
    _document(session_id, file_id)
    active_job = _db().get_active_ocr_job(ADMIN_USER_ID, file_id)
    if active_job:
        _db().request_ocr_job_cancel(ADMIN_USER_ID, active_job["id"])
        _job_manager().cancel(active_job["id"])
    _job_manager().cancel_document(file_id)
    try:
        _db().delete_document(ADMIN_USER_ID, session_id, file_id)
    except KeyError as exc:
        raise NotFound("Document not found") from exc
    return jsonify(_state())


@app.post("/api/sessions/<session_id>/files/<file_id>/page/<int:page_number>")
def change_page(session_id: str, file_id: str, page_number: int) -> Response:
    try:
        _db().set_document_page(ADMIN_USER_ID, session_id, file_id, page_number)
    except KeyError as exc:
        raise NotFound("Document not found") from exc
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return jsonify(_state())


@app.get("/api/sessions/<session_id>/files/<file_id>/preview")
def preview_file(session_id: str, file_id: str) -> Response:
    document = _document(session_id, file_id)
    page_number = request.args.get("page", document["current_page"], type=int)
    if page_number < 1 or page_number > document["num_pages"]:
        raise BadRequest("Page is outside this document")

    if not document["is_pdf"]:
        return send_file(
            io.BytesIO(document["content"]),
            mimetype=document["mime_type"],
            download_name=document["name"],
        )

    try:
        import fitz

        with fitz.open(stream=document["content"], filetype="pdf") as pdf:
            page = pdf[page_number - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
            png = pixmap.tobytes("png")
        return send_file(io.BytesIO(png), mimetype="image/png", download_name=f"page-{page_number}.png")
    except Exception as exc:
        raise BadRequest(f"Could not render this PDF page: {exc}") from exc


@app.get("/api/sessions/<session_id>/files/<file_id>/content")
def original_file(session_id: str, file_id: str) -> Response:
    """Serve the unmodified upload to the browser's native PDF viewer."""
    document = _document(session_id, file_id)
    response = send_file(
        io.BytesIO(document["content"]),
        mimetype=document["mime_type"],
        download_name=document["name"],
        conditional=True,
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.get("/api/sessions/<session_id>/files/<file_id>/assets/<path:filename>")
def document_asset(session_id: str, file_id: str, filename: str) -> Response:
    try:
        asset = _db().get_document_asset(ADMIN_USER_ID, session_id, file_id, filename)
    except KeyError as exc:
        raise NotFound("Document asset not found") from exc
    response = send_file(
        io.BytesIO(asset["content"]),
        mimetype=asset["mime_type"],
        download_name=asset["filename"],
        conditional=True,
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.get("/api/sessions/<session_id>/files/<file_id>/extractions/<template_id>")
def get_document_extraction(session_id: str, file_id: str, template_id: str) -> Response:
    template = _template_from_request(template_id)
    try:
        extraction = _db().get_document_extraction(
            ADMIN_USER_ID, session_id, file_id, template["id"]
        )
    except KeyError as exc:
        raise NotFound("Document not found") from exc
    return jsonify(_extraction_payload(extraction, template))


@app.post("/api/sessions/<session_id>/files/<file_id>/extractions/<template_id>/run")
def run_document_extraction(session_id: str, file_id: str, template_id: str) -> Response:
    template = _template_from_request(template_id)
    document = _document(session_id, file_id)
    if int(document["num_pages"]) > DOCUMENT_ANNOTATION_PAGE_LIMIT:
        raise BadRequest(
            "Structured extraction currently supports documents up to 8 pages. "
            "Split this document before using the structured template."
        )
    if not _db().claim_document_extraction(file_id, template["id"]):
        raise Conflict("Structured extraction is already running for this document")
    claimed_pages: list[int] = []
    page_owner_token = f"structured-{secrets.token_hex(8)}"
    ocr_included = False
    try:
        ocr_content = document["content"]
        ocr_mime_type = str(document["mime_type"])
        preprocessing_report = None
        if not document["is_pdf"]:
            optimized = _preprocessed_document_image(document)
            ocr_content = optimized.content
            ocr_mime_type = optimized.mime_type
            preprocessing_report = optimized.report
        has_existing_ocr = _db().count_document_ocr_pages(file_id) > 0
        if not has_existing_ocr:
            if _db().get_active_ocr_job(ADMIN_USER_ID, file_id):
                raise Conflict("Stop the active OCR job before starting structured extraction")
            for page_number in range(1, int(document["num_pages"]) + 1):
                if not _db().claim_ocr_page(file_id, page_number, page_owner_token):
                    raise Conflict(f"Page {page_number} is already being processed")
                claimed_pages.append(page_number)

            combined = ocr_document_with_annotation(
                ocr_content,
                is_pdf=bool(document["is_pdf"]),
                mime_type=ocr_mime_type,
                schema_name=f"{template['id']}_v{template['schema_version']}",
                schema=template["schema"],
                prompt=template["prompt"],
                model=MODEL_DEFAULT,
            )
            if len(combined.pages) != int(document["num_pages"]):
                raise RuntimeError(
                    "Mistral returned an unexpected number of OCR pages; nothing was saved"
                )
            normalized = _normalize_template_data(combined.document_annotation, template)
            try:
                _db().get_document(ADMIN_USER_ID, session_id, file_id)
            except KeyError as exc:
                raise NotFound("Document was deleted while OCR was running") from exc
            for page_number, page in enumerate(combined.pages, start=1):
                prepared = _prepare_ocr_markdown(
                    document,
                    page_number,
                    page,
                    preprocessing_report=preprocessing_report,
                )
                _db().save_page_markdown(file_id, page_number, prepared)
            ocr_included = True
        else:
            # Existing Markdown may contain user edits. Preserve it exactly and
            # request only the annotation instead of replacing page content.
            result = extract_document_annotation(
                ocr_content,
                is_pdf=bool(document["is_pdf"]),
                mime_type=ocr_mime_type,
                schema_name=f"{template['id']}_v{template['schema_version']}",
                schema=template["schema"],
                prompt=template["prompt"],
                model=MODEL_DEFAULT,
            )
            normalized = _normalize_template_data(result, template)
        extraction = _db().save_document_extraction(
            ADMIN_USER_ID,
            session_id,
            file_id,
            template["id"],
            normalized,
            MODEL_DEFAULT,
            schema_version=template["schema_version"],
        )
    finally:
        for page_number in claimed_pages:
            _db().release_ocr_page(file_id, page_number, page_owner_token)
        _db().release_document_extraction(file_id, template["id"])
    payload = _extraction_payload(extraction, template)
    payload["ocr_included"] = ocr_included
    return jsonify(payload)


@app.patch("/api/sessions/<session_id>/files/<file_id>/extractions/<template_id>")
def update_document_extraction(session_id: str, file_id: str, template_id: str) -> Response:
    template = _template_from_request(template_id)
    _document(session_id, file_id)
    existing = _db().get_document_extraction(
        ADMIN_USER_ID, session_id, file_id, template["id"]
    )
    if not existing:
        raise NotFound("Run structured extraction before editing it")
    payload = request.get_json(silent=True) or {}
    try:
        normalized = _normalize_template_data(payload.get("data"), template)
        status = str(payload.get("status", "needs_review"))
        extraction = _db().save_document_extraction(
            ADMIN_USER_ID,
            session_id,
            file_id,
            template["id"],
            normalized,
            str(existing["model"]),
            schema_version=template["schema_version"],
            status=status,
        )
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return jsonify(_extraction_payload(extraction, template))


@app.get("/api/sessions/<session_id>/files/<file_id>/extractions/<template_id>.csv")
def export_document_extraction_csv(session_id: str, file_id: str, template_id: str) -> Response:
    template = _template_from_request(template_id)
    document = _document(session_id, file_id)
    extraction = _db().get_document_extraction(
        ADMIN_USER_ID, session_id, file_id, template["id"]
    )
    if not extraction:
        raise NotFound("Run structured extraction before exporting it")
    data = extraction["data"]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["field", "value"])
    for section in template["layout"].get("sections", []):
        for field in section.get("fields", []):
            writer.writerow([field["label"], data.get(field["key"])])
    for list_definition in template["layout"].get("lists", []):
        values = data.get(list_definition["key"]) or []
        writer.writerow([list_definition["label"], " | ".join(str(value) for value in values)])
    for table in template["layout"].get("tables", []):
        writer.writerow([])
        writer.writerow([table["title"]])
        writer.writerow([column["label"] for column in table["columns"]])
        for item in data.get(table["key"], []):
            writer.writerow([item.get(column["key"]) for column in table["columns"]])
    stem = secure_filename(Path(document["name"]).stem) or "document"
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}-{template_id}-data.csv"'
        },
    )


@app.post("/api/sessions/<session_id>/files/<file_id>/ocr/<int:page_number>")
def run_ocr(session_id: str, file_id: str, page_number: int) -> Response:
    document = _document(session_id, file_id)
    if page_number < 1 or page_number > document["num_pages"]:
        raise BadRequest("Page is outside this document")

    force = bool((request.get_json(silent=True) or {}).get("force"))
    result = _db().get_page_markdown(file_id, page_number)
    template = _optional_template(document.get("document_type"))
    extraction = (
        _db().get_document_extraction(ADMIN_USER_ID, session_id, file_id, template["id"])
        if template else None
    )
    can_combine = (
        result is None
        and not force
        and template is not None
        and extraction is None
        and _db().count_document_ocr_pages(file_id) == 0
        and int(document["num_pages"]) <= DOCUMENT_ANNOTATION_PAGE_LIMIT
    )
    if can_combine:
        owner_token = f"typed-{secrets.token_hex(8)}"
        claimed_pages: list[int] = []
        extraction_claimed = False
        try:
            if not _db().claim_document_extraction(file_id, template["id"]):
                raise Conflict("Structured extraction is already running for this document")
            extraction_claimed = True
            for claimed_page in range(1, int(document["num_pages"]) + 1):
                if not _db().claim_ocr_page(file_id, claimed_page, owner_token):
                    raise Conflict(f"Page {claimed_page} is already being processed")
                claimed_pages.append(claimed_page)
            prepared_pages, normalized = _ocr_document_with_template(document, template)
            _db().get_document(ADMIN_USER_ID, session_id, file_id)
            for claimed_page, prepared in enumerate(prepared_pages, start=1):
                _db().save_page_markdown(file_id, claimed_page, prepared)
            extraction = _db().save_document_extraction(
                ADMIN_USER_ID,
                session_id,
                file_id,
                template["id"],
                normalized,
                MODEL_DEFAULT,
                schema_version=template["schema_version"],
            )
            result = prepared_pages[page_number - 1]
        finally:
            for claimed_page in claimed_pages:
                _db().release_ocr_page(file_id, claimed_page, owner_token)
            if extraction_claimed:
                _db().release_document_extraction(file_id, template["id"])
    if result is None or force:
        owner_token = f"manual-{secrets.token_hex(8)}"
        if not _db().claim_ocr_page(file_id, page_number, owner_token):
            raise Conflict("This page is already being processed")
        try:
            result = _infer_document_page(document, page_number)
            try:
                _db().get_document(ADMIN_USER_ID, session_id, file_id)
            except KeyError as exc:
                raise NotFound("Document was deleted while OCR was running") from exc
            _db().save_page_markdown(file_id, page_number, result)
        finally:
            _db().release_ocr_page(file_id, page_number, owner_token)
    return jsonify({"markdown": result, "model": MODEL_DEFAULT, "extraction": extraction})


@app.post("/api/sessions/<session_id>/files/<file_id>/ocr-all")
def run_all_ocr(session_id: str, file_id: str) -> tuple[Response, int]:
    document = _document(session_id, file_id)
    existing = _db().get_active_ocr_job(ADMIN_USER_ID, file_id)
    if existing:
        return jsonify(existing), 202
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force"))
    page_range = str(payload.get("page_range", "all")).strip().lower()
    if not page_range or page_range in {"all", "*"}:
        page_numbers = list(range(1, int(document["num_pages"]) + 1))
    else:
        page_numbers_set: set[int] = set()
        for part in page_range.split(","):
            part = part.strip()
            match = re.fullmatch(r"(\d+)\s*(?:-\s*(\d+))?", part)
            if not match:
                raise BadRequest("Use page ranges such as 1-3, 5, 8-10")
            start = int(match.group(1))
            end = int(match.group(2) or start)
            if start > end:
                start, end = end, start
            if start < 1 or end > int(document["num_pages"]):
                raise BadRequest(f"Pages must be between 1 and {document['num_pages']}")
            page_numbers_set.update(range(start, end + 1))
        page_numbers = sorted(page_numbers_set)
        if not page_numbers:
            raise BadRequest("Select at least one page")
    try:
        job_id = _db().create_ocr_job(
            ADMIN_USER_ID,
            session_id,
            file_id,
            force=force,
            page_numbers=page_numbers,
        )
    except sqlite3.IntegrityError as exc:
        raise Conflict("An OCR-all-pages job is already active for this document") from exc
    _job_manager().start(_db(), ADMIN_USER_ID, job_id, _infer_document_page)
    return jsonify(_db().get_ocr_job(ADMIN_USER_ID, job_id)), 202


@app.get("/api/ocr-jobs/<job_id>")
def get_ocr_job(job_id: str) -> Response:
    try:
        return jsonify(_db().get_ocr_job(ADMIN_USER_ID, job_id))
    except KeyError as exc:
        raise NotFound("OCR job not found") from exc


@app.post("/api/ocr-jobs/<job_id>/prioritize/<int:page_number>")
def prioritize_ocr_job_page(job_id: str, page_number: int) -> Response:
    try:
        return jsonify(_db().prioritize_ocr_job_page(ADMIN_USER_ID, job_id, page_number))
    except KeyError as exc:
        raise NotFound("OCR job page not found") from exc
    except ValueError as exc:
        raise Conflict(str(exc)) from exc


@app.post("/api/ocr-jobs/<job_id>/retry")
def retry_ocr_job(job_id: str) -> tuple[Response, int]:
    try:
        current = _db().get_ocr_job(ADMIN_USER_ID, job_id)
        if current["status"] in {"queued", "running", "cancelling"}:
            return jsonify(current), 202
        retry_id = _db().retry_failed_ocr_job(ADMIN_USER_ID, job_id)
        retry_job = _db().get_ocr_job(ADMIN_USER_ID, retry_id)
    except KeyError as exc:
        raise NotFound("OCR job not found") from exc
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise Conflict(str(exc)) from exc
    _job_manager().start(_db(), ADMIN_USER_ID, retry_id, _infer_document_page)
    return jsonify(retry_job), 202


@app.delete("/api/ocr-jobs/<job_id>")
def cancel_ocr_job(job_id: str) -> tuple[Response, int]:
    try:
        job = _db().request_ocr_job_cancel(ADMIN_USER_ID, job_id)
    except KeyError as exc:
        raise NotFound("OCR job not found") from exc
    _job_manager().cancel(job_id)
    return jsonify(job), 202


@app.patch("/api/sessions/<session_id>/files/<file_id>/markdown/<int:page_number>")
def update_markdown(session_id: str, file_id: str, page_number: int) -> Response:
    document = _document(session_id, file_id)
    if page_number < 1 or page_number > document["num_pages"]:
        raise BadRequest("Page is outside this document")
    source = str((request.get_json(silent=True) or {}).get("markdown", ""))
    _db().save_page_markdown(file_id, page_number, source)
    return jsonify({"ok": True})


@app.patch("/api/sessions/<session_id>/files/<file_id>/review/<int:page_number>")
def update_page_review(session_id: str, file_id: str, page_number: int) -> Response:
    status = str((request.get_json(silent=True) or {}).get("status", "")).strip()
    try:
        page = _db().set_page_review_status(
            ADMIN_USER_ID,
            session_id,
            file_id,
            page_number,
            status,
        )
    except KeyError as exc:
        raise NotFound("Document not found") from exc
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return jsonify({
        "ok": True,
        "review_status": page.get("review_status"),
        "reviewed_at": page.get("reviewed_at"),
    })


@app.post("/api/render")
def render_markdown() -> Response:
    payload = request.get_json(silent=True) or {}
    source = str(payload.get("markdown", ""))
    session_id = str(payload.get("session_id", ""))
    document_id = str(payload.get("document_id", ""))
    asset_urls: dict[str, str] = {}
    if session_id or document_id:
        if not session_id or not document_id:
            raise BadRequest("Both session and document context are required")
        asset_urls = _document_asset_urls(session_id, document_id)
    return jsonify({"html": _compile_markdown(source, asset_urls)})


@app.get("/api/sessions/<session_id>/files/<file_id>/print")
def print_document(session_id: str, file_id: str) -> str:
    document = _document(session_id, file_id)
    asset_urls = _document_asset_urls(session_id, file_id)
    pages = []
    for page_number in range(1, document["num_pages"] + 1):
        source = _db().get_page_markdown(file_id, page_number)
        pages.append({
            "number": page_number,
            "has_ocr": source is not None,
            "html": _compile_markdown(source or "", asset_urls),
        })
    return render_template(
        "print_document.html",
        document=document,
        pages=pages,
        auto_print=request.args.get("autoprint", "1") != "0",
    )


def _export_document_markdown(document: dict) -> str:
    sections = []
    for page_number in range(1, document["num_pages"] + 1):
        page_markdown = _db().get_page_markdown(document["id"], page_number)
        if page_markdown is not None:
            if document["num_pages"] > 1:
                sections.append(f"<!-- Page {page_number} -->\n\n{page_markdown}")
            else:
                sections.append(page_markdown)
    return "\n\n---\n\n".join(sections)


def _markdown_export_stem(document: dict) -> str:
    stem = secure_filename(Path(document["name"]).stem) or "document"
    return f"{stem}_markdown"


def _render_document_pdf(session_id: str, file_id: str) -> bytes:
    print_url = (
        f"{app.config['INTERNAL_BASE_URL']}/api/sessions/"
        f"{quote(session_id, safe='')}/files/{quote(file_id, safe='')}/print?autoprint=0"
    )
    try:
        return render_url_to_pdf(print_url)
    except BrowserPdfError as exc:
        raise BadRequest(f"Could not create the rendered PDF: {exc}") from exc


def _write_markdown_package(
    bundle: zipfile.ZipFile,
    document: dict,
    markdown_payload: str,
    assets: list[dict],
) -> None:
    stem = _markdown_export_stem(document)
    bundle.writestr(f"{stem}.md", markdown_payload.encode("utf-8"))
    for asset in assets:
        bundle.writestr(f"assets/{asset['filename']}", asset["content"])


@app.get("/api/sessions/<session_id>/files/<file_id>/export.md")
def export_markdown(session_id: str, file_id: str) -> Response:
    document = _document(session_id, file_id)
    payload = _export_document_markdown(document)
    stem = _markdown_export_stem(document)
    return Response(
        payload,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{stem}.md"'},
    )


@app.get("/api/sessions/<session_id>/files/<file_id>/export.zip")
def export_document_package(session_id: str, file_id: str) -> Response:
    document = _document(session_id, file_id)
    markdown_payload = _export_document_markdown(document)
    assets = _db().list_document_assets(ADMIN_USER_ID, session_id, file_id)
    stem = secure_filename(Path(document["name"]).stem) or "document"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        _write_markdown_package(bundle, document, markdown_payload, assets)
    archive.seek(0)
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{stem}.zip",
    )


@app.get("/api/sessions/<session_id>/files/<file_id>/export.pdf")
def export_document_pdf(session_id: str, file_id: str) -> Response:
    document = _document(session_id, file_id)
    stem = _markdown_export_stem(document)
    payload = _render_document_pdf(session_id, file_id)
    return send_file(
        io.BytesIO(payload),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{stem}.pdf",
    )


@app.get("/api/sessions/<session_id>/files/<file_id>/export-bundle.zip")
def export_document_bundle(session_id: str, file_id: str) -> Response:
    document = _document(session_id, file_id)
    markdown_payload = _export_document_markdown(document)
    assets = _db().list_document_assets(ADMIN_USER_ID, session_id, file_id)
    pdf_payload = _render_document_pdf(session_id, file_id)
    stem = _markdown_export_stem(document)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        _write_markdown_package(bundle, document, markdown_payload, assets)
        bundle.writestr(f"{stem}.pdf", pdf_payload)
    archive.seek(0)
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{stem}-markdown-and-pdf.zip",
    )


@app.errorhandler(BadRequest)
@app.errorhandler(Conflict)
@app.errorhandler(NotFound)
@app.errorhandler(RequestEntityTooLarge)
def handle_http_error(error: Exception) -> tuple[Response, int]:
    code = getattr(error, "code", 400)
    description = getattr(error, "description", str(error))
    if isinstance(error, RequestEntityTooLarge):
        description = "Upload is too large. The draft limit is 30 MB."
    return jsonify({"error": description}), code


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception) -> tuple[Response, int]:
    app.logger.exception("Unhandled application error")
    return jsonify({"error": str(error) or "Unexpected server error"}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=APP_PORT, debug=True)
