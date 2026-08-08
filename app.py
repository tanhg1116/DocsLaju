from __future__ import annotations

import base64
import binascii
import html
import io
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
    ocr_image_with_assets,
    ocr_pdf_pages_with_assets,
)
from src.services.database import ADMIN_USER_ID, Database
from src.services.ocr_jobs import OcrJobManager


BASE_DIR = Path(__file__).resolve().parent
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
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
)
app.extensions["database"] = Database(app.config["DATABASE"], BASE_DIR / "db" / "schema.sql")
app.extensions["database"].recover_interrupted_ocr_jobs()
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
    )


def _infer_document_page(document: dict, page_number: int) -> str:
    if document["is_pdf"]:
        pages = ocr_pdf_pages_with_assets(
            _single_pdf_page(document["content"], page_number),
            model=MODEL_DEFAULT,
        )
        page = pages[0] if pages else OcrPageResult("")
    else:
        page = ocr_image_with_assets(document["content"], model=MODEL_DEFAULT)
    return _prepare_ocr_markdown(document, page_number, page)


@app.get("/")
def index() -> str:
    _db().ensure_active_session(ADMIN_USER_ID)
    return render_template("index.html")


@app.get("/api/state")
def get_state() -> Response:
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
    _db().add_document(
        ADMIN_USER_ID,
        session_id,
        name=name,
        content=content,
        mime_type=upload.mimetype or ("application/pdf" if is_pdf else "image/jpeg"),
        is_pdf=is_pdf,
        num_pages=_page_count(content, is_pdf),
    )
    return jsonify(_state()), 201


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


@app.post("/api/sessions/<session_id>/files/<file_id>/ocr/<int:page_number>")
def run_ocr(session_id: str, file_id: str, page_number: int) -> Response:
    document = _document(session_id, file_id)
    if page_number < 1 or page_number > document["num_pages"]:
        raise BadRequest("Page is outside this document")

    force = bool((request.get_json(silent=True) or {}).get("force"))
    result = _db().get_page_markdown(file_id, page_number)
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
    return jsonify({"markdown": result, "model": MODEL_DEFAULT})


@app.post("/api/sessions/<session_id>/files/<file_id>/ocr-all")
def run_all_ocr(session_id: str, file_id: str) -> tuple[Response, int]:
    _document(session_id, file_id)
    existing = _db().get_active_ocr_job(ADMIN_USER_ID, file_id)
    if existing:
        return jsonify(existing), 202
    force = bool((request.get_json(silent=True) or {}).get("force"))
    try:
        job_id = _db().create_ocr_job(
            ADMIN_USER_ID,
            session_id,
            file_id,
            force=force,
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


@app.get("/api/sessions/<session_id>/files/<file_id>/export.md")
def export_markdown(session_id: str, file_id: str) -> Response:
    document = _document(session_id, file_id)
    payload = _export_document_markdown(document)
    stem = os.path.splitext(document["name"])[0]
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
        bundle.writestr(f"{stem}.md", markdown_payload.encode("utf-8"))
        for asset in assets:
            bundle.writestr(f"assets/{asset['filename']}", asset["content"])
    archive.seek(0)
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{stem}.zip",
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
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)
