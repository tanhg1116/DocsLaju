"""Flask composition root and HTTP API for the local DocsLaju workspace.

Routes validate user input and coordinate services. Durable data rules belong in
Database, queue/rate rules in OcrJobManager, and provider translation in
src.mistral_client. Start a review with docs/REVIEW_GUIDE.md rather than reading
this file linearly.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import sqlite3
import zipfile
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file
from werkzeug.exceptions import BadRequest, Conflict, NotFound, RequestEntityTooLarge
from werkzeug.utils import secure_filename

from src.mistral_client import (
    MODEL_DEFAULT,
    extract_document_annotation,
    ocr_document_with_annotation,
    ocr_images_batch,
    ocr_image_with_assets,
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
from src.services.document_ocr import (
    page_count as _page_count,
    pdf_page_image_for_ocr as _pdf_page_image_for_ocr,
    prepare_ocr_markdown as _prepare_ocr_markdown,
    rasterized_pdf_for_annotation as _rasterized_pdf_for_annotation,
    validated_upload_type as _validated_upload_type,
)
from src.services.extraction_normalizer import (
    normalize_template_data as _normalize_template_data,
)
from src.services.markdown_renderer import compile_markdown as _compile_markdown


BASE_DIR = Path(__file__).resolve().parent
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
APP_PORT = int(os.getenv("PORT", "5000"))
load_dotenv(override=False)
app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32),
    DATABASE=os.getenv("DOCSLAJU_DB_PATH") or str(BASE_DIR / "instance" / "docslaju.sqlite3"),
    INTERNAL_BASE_URL=(
        os.getenv("DOCSLAJU_INTERNAL_BASE_URL") or f"http://127.0.0.1:{APP_PORT}"
    ).rstrip("/"),
    VALIDATE_UPLOAD_CONTENT=os.getenv("DOCSLAJU_VALIDATE_UPLOADS", "true").lower()
    not in {"0", "false", "no", "off"},
)
app.extensions["database"] = Database(app.config["DATABASE"], BASE_DIR / "db" / "schema.sql")
app.extensions["ocr_jobs"] = OcrJobManager(database=app.extensions["database"])


def _db() -> Database:
    return app.extensions["database"]


def _job_manager() -> OcrJobManager:
    return app.extensions["ocr_jobs"]


def _state() -> dict:
    payload = _db().state(ADMIN_USER_ID, MODEL_DEFAULT)
    payload["ocr_scheduler"] = _job_manager().scheduler_status()
    return payload


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
            include_content=False,
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


def _annotation_document(document: dict) -> tuple[bytes, bool, str]:
    """Return a provider-safe annotation input without changing stored content.

    Invariant: structured extraction is downstream of page OCR for PDFs. This
    clean raster-only copy may fail independently without invalidating Markdown.
    """
    if document["is_pdf"]:
        return _rasterized_pdf_for_annotation(document["content"]), True, "application/pdf"
    optimized = _preprocessed_document_image(document)
    return optimized.content, False, optimized.mime_type


def _preprocessed_document_image(document: dict) -> PreprocessedImage:
    return preprocess_image(document["content"], str(document["mime_type"]))


def _infer_document_page(document: dict, page_number: int) -> str:
    if document["is_pdf"]:
        optimized = _pdf_page_image_for_ocr(document["content"], page_number)
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


def _infer_document_pages_batch(
    document: dict,
    page_numbers: list[int],
    cancellation,
) -> tuple[dict[int, str | Exception], str | None, float]:
    prepared_inputs: dict[int, PreprocessedImage] = {}
    for page_number in page_numbers:
        if cancellation.is_set():
            raise RuntimeError("OCR batch cancelled")
        prepared_inputs[page_number] = (
            _pdf_page_image_for_ocr(document["content"], page_number)
            if document["is_pdf"]
            else _preprocessed_document_image(document)
        )
    batch = ocr_images_batch(
        {
            str(page_number): (optimized.content, optimized.mime_type)
            for page_number, optimized in prepared_inputs.items()
        },
        model=MODEL_DEFAULT,
        is_cancelled=cancellation.is_set,
    )
    results: dict[int, str | Exception] = {}
    for page_number, optimized in prepared_inputs.items():
        custom_id = str(page_number)
        if custom_id in batch.errors:
            results[page_number] = RuntimeError(batch.errors[custom_id])
            continue
        page = batch.pages.get(custom_id)
        if page is None:
            results[page_number] = RuntimeError("Mistral returned no result for this page")
            continue
        results[page_number] = _prepare_ocr_markdown(
            document,
            page_number,
            page,
            preprocessing_report=optimized.report,
        )
    return results, batch.remote_job_id, batch.duration_seconds


def _resume_pending_ocr_jobs() -> None:
    for job in _db().recover_interrupted_ocr_jobs():
        _job_manager().start(
            _db(),
            int(job["user_id"]),
            str(job["id"]),
            _infer_document_page,
            _infer_document_pages_batch,
        )


_resume_pending_ocr_jobs()


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
    if app.config.get("VALIDATE_UPLOAD_CONTENT", True):
        is_pdf, mime_type = _validated_upload_type(content, extension)
    else:
        is_pdf = extension == ".pdf" or upload.mimetype == "application/pdf"
        mime_type = upload.mimetype or ("application/pdf" if is_pdf else "image/jpeg")
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
        mime_type=mime_type,
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
        import pymupdf

        with pymupdf.open(stream=document["content"], filetype="pdf") as pdf:
            page = pdf[page_number - 1]
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.7, 1.7), alpha=False)
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
    page_owner_token = f"structured-{secrets.token_hex(8)}"
    ocr_included = False
    try:
        if _db().get_active_ocr_job(ADMIN_USER_ID, file_id):
            raise Conflict("Let the active OCR job finish before starting structured extraction")

        # A single uploaded image is already one independent page. Preserve
        # the efficient one-call OCR + annotation path for common receipt and
        # invoice photos; multi-page PDFs use the resilient page-first flow.
        if not document["is_pdf"] and _db().count_document_ocr_pages(file_id) == 0:
            optimized = _preprocessed_document_image(document)
            try:
                combined = ocr_document_with_annotation(
                    optimized.content,
                    is_pdf=False,
                    mime_type=optimized.mime_type,
                    schema_name=f"{template['id']}_v{template['schema_version']}",
                    schema=template["schema"],
                    prompt=template["prompt"],
                    model=MODEL_DEFAULT,
                )
            except Exception as annotation_error:
                # If schema generation fails, make one plain OCR fallback so
                # the useful Markdown is not lost with the structured stage.
                prepared = _infer_document_page(document, 1)
                _db().save_page_markdown(
                    file_id,
                    1,
                    prepared,
                    expected_checksum=document.get("checksum"),
                )
                raise RuntimeError(
                    "Page OCR was saved, but structured extraction failed"
                ) from annotation_error
            if len(combined.pages) != 1:
                raise RuntimeError("Mistral returned no OCR page for this image")
            prepared = _prepare_ocr_markdown(
                document,
                1,
                combined.pages[0],
                preprocessing_report=optimized.report,
            )
            _db().save_page_markdown(
                file_id,
                1,
                prepared,
                expected_checksum=document.get("checksum"),
            )
            ocr_included = True
            normalized = _normalize_template_data(combined.document_annotation, template)
            extraction = _db().save_document_extraction(
                ADMIN_USER_ID,
                session_id,
                file_id,
                template["id"],
                normalized,
                MODEL_DEFAULT,
                schema_version=template["schema_version"],
            )
            payload = _extraction_payload(extraction, template)
            payload["ocr_included"] = True
            return jsonify(payload)

        failed_pages: list[tuple[int, str]] = []
        for page_number in range(1, int(document["num_pages"]) + 1):
            if _db().get_page_markdown(file_id, page_number) is not None:
                continue
            if not _db().claim_ocr_page(file_id, page_number, page_owner_token):
                failed_pages.append((page_number, "already processing"))
                continue
            try:
                prepared = _infer_document_page(document, page_number)
                _db().save_page_markdown(
                    file_id,
                    page_number,
                    prepared,
                    expected_checksum=document.get("checksum"),
                )
                ocr_included = True
            except Exception as exc:
                failed_pages.append((page_number, str(exc)[:160]))
            finally:
                _db().release_ocr_page(file_id, page_number, page_owner_token)

        if failed_pages:
            page_list = ", ".join(str(page) for page, _ in failed_pages)
            raise RuntimeError(
                f"OCR was saved for successful pages, but page(s) {page_list} failed. "
                "Retry those pages before structured extraction."
            )

        # Annotation is a separate stage. It operates on a clean raster-only
        # PDF for PDF documents and never replaces stored/user-edited Markdown.
        annotation_content, annotation_is_pdf, annotation_mime = _annotation_document(document)
        result = extract_document_annotation(
            annotation_content,
            is_pdf=annotation_is_pdf,
            mime_type=annotation_mime,
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
    if (
        result is None
        and not force
        and not document["is_pdf"]
        and template is not None
        and extraction is None
    ):
        owner_token = f"typed-image-{secrets.token_hex(8)}"
        if not _db().claim_ocr_page(file_id, page_number, owner_token):
            raise Conflict("This page is already being processed")
        if not _db().claim_document_extraction(file_id, template["id"]):
            _db().release_ocr_page(file_id, page_number, owner_token)
            raise Conflict("Structured extraction is already running for this document")
        try:
            optimized = _preprocessed_document_image(document)
            try:
                combined = ocr_document_with_annotation(
                    optimized.content,
                    is_pdf=False,
                    mime_type=optimized.mime_type,
                    schema_name=f"{template['id']}_v{template['schema_version']}",
                    schema=template["schema"],
                    prompt=template["prompt"],
                    model=MODEL_DEFAULT,
                )
            except Exception:
                result = _infer_document_page(document, page_number)
                _db().save_page_markdown(
                    file_id,
                    page_number,
                    result,
                    expected_checksum=document.get("checksum"),
                )
                combined = None
            if combined is None:
                extraction = None
            else:
                if len(combined.pages) != 1:
                    raise RuntimeError("Mistral returned no OCR page for this image")
                result = _prepare_ocr_markdown(
                    document,
                    page_number,
                    combined.pages[0],
                    preprocessing_report=optimized.report,
                )
                _db().save_page_markdown(
                    file_id,
                    page_number,
                    result,
                    expected_checksum=document.get("checksum"),
                )
                normalized = _normalize_template_data(combined.document_annotation, template)
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
            _db().release_document_extraction(file_id, template["id"])
            _db().release_ocr_page(file_id, page_number, owner_token)
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
            _db().save_page_markdown(
                file_id,
                page_number,
                result,
                expected_checksum=document.get("checksum"),
            )
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
            processing_mode="adaptive",
            rate_limit_ppm=_job_manager().rate_controller.pages_per_minute,
        )
    except sqlite3.IntegrityError as exc:
        raise Conflict("An OCR-all-pages job is already active for this document") from exc
    _job_manager().start(
        _db(), ADMIN_USER_ID, job_id, _infer_document_page, _infer_document_pages_batch
    )
    return jsonify(_db().get_ocr_job(ADMIN_USER_ID, job_id)), 202


@app.get("/api/ocr-jobs/<job_id>")
def get_ocr_job(job_id: str) -> Response:
    try:
        payload = _db().get_ocr_job(ADMIN_USER_ID, job_id)
        payload["scheduler"] = _job_manager().scheduler_status()
        return jsonify(payload)
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
    _job_manager().start(
        _db(), ADMIN_USER_ID, retry_id, _infer_document_page, _infer_document_pages_batch
    )
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
    return jsonify({"error": "Unexpected server error. Check logs/api.log for details."}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=APP_PORT, debug=True)
