import io
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import pypdf
from PIL import Image

from app import _infer_document_page, _normalize_template_data, _prepare_ocr_markdown, app
from src.extraction_templates import get_template
from src.mistral_client import (
    MODEL_DEFAULT,
    OcrDocumentResult,
    OcrImageResult,
    OcrMarkdown,
    OcrPageResult,
)
from src.services.database import ADMIN_USER_ID, Database
from src.services.ocr_jobs import AdaptiveRateController, OcrAttemptOutcome, OcrJobManager


def _test_png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), "white").save(output, format="PNG")
    return output.getvalue()


def test_schema_contains_the_persistent_repository(isolated_database: Path):
    expected = {
        "users", "projects", "sessions", "documents", "document_pages", "user_preferences",
        "document_assets", "ocr_jobs", "ocr_job_pages", "ocr_page_claims",
        "document_pages_fts",
        "document_extractions", "document_extraction_claims",
        "ocr_scheduler_metrics",
    }
    with sqlite3.connect(isolated_database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert expected <= tables
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        job_page_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(ocr_job_pages)")
        }
        assert "priority" in job_page_columns
        assert {
            "attempts", "processing_mode", "remote_batch_id", "error_code", "duration_ms"
        } <= job_page_columns
        job_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(ocr_jobs)")
        }
        assert {"processing_mode", "document_checksum", "rate_limit_ppm"} <= job_columns
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] >= 1
        page_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_pages)")
        }
        assert "source_markdown" in page_columns
        assert {"preprocessing_json", "confidence_score", "review_status", "reviewed_at"} <= page_columns
        document_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(documents)")
        }
        assert {"checksum", "document_type"} <= document_columns
        extraction_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_extractions)")
        }
        assert "schema_version" in extraction_columns


def test_scheduler_telemetry_survives_manager_restart(isolated_database: Path):
    database = app.extensions["database"]
    database.save_scheduler_metrics({
        "pages_per_minute_limit": 1250,
        "target_utilization": 0.88,
        "current_concurrency": 9,
        "realtime_latency_ewma": 1.25,
        "realtime_throughput_pps": 6.5,
        "realtime_samples": 120,
        "batch_seconds_per_page": 0.42,
        "batch_samples": 64,
        "rate_limit_events": 2,
    })

    restarted = OcrJobManager(database=database, max_concurrency=24)
    status = restarted.scheduler_status()

    assert status["current_concurrency"] == 9
    assert status["average_page_seconds"] == 1.25
    assert status["observed_pages_per_second"] == 6.5
    assert status["realtime_samples"] == 120
    assert status["batch_seconds_per_page"] == 0.42
    assert status["batch_samples"] == 64
    assert status["rate_limit_events"] == 2


def test_upload_persists_optional_document_type(isolated_database: Path):
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        response = client.post(
            f"/api/sessions/{session_id}/files",
            data={
                "file": (io.BytesIO(_test_png_bytes()), "quotation.png"),
                "document_type": "quotation",
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 201
        payload = response.get_json()
        assert payload["active_document"]["document_type"] == "quotation"
        assert payload["sessions"][0]["files"][0]["document_type"] == "quotation"


def test_invalid_upload_document_type_is_rejected(isolated_database: Path):
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        response = client.post(
            f"/api/sessions/{session_id}/files",
            data={
                "file": (io.BytesIO(_test_png_bytes()), "unknown.png"),
                "document_type": "not-a-template",
            },
            content_type="multipart/form-data",
        )

    assert response.status_code == 400


def test_extraction_template_catalog_is_versioned_and_layout_driven():
    with app.test_client() as client:
        response = client.get("/api/extraction-templates")

    assert response.status_code == 200
    templates = {item["id"]: item for item in response.get_json()["templates"]}
    assert set(templates) == {"invoice", "receipt", "quotation", "resume"}
    assert templates["invoice"]["schema_version"] == 1
    assert templates["receipt"]["schema_version"] == 1
    assert templates["quotation"]["schema_version"] == 1
    assert templates["resume"]["schema_version"] == 2
    assert templates["invoice"]["layout"]["tables"][0]["key"] == "line_items"
    assert templates["receipt"]["layout"]["tables"][0]["key"] == "purchased_items"
    assert templates["quotation"]["layout"]["tables"][0]["key"] == "quoted_items"
    assert {table["key"] for table in templates["resume"]["layout"]["tables"]} >= {
        "experience", "education", "projects", "certifications", "achievements"
    }


def test_resume_v2_has_contact_achievements_and_deduplicates_string_lists():
    template = get_template("resume")
    normalized = _normalize_template_data(
        {
            "document_type": "resume",
            "linkedin_url": "https://www.linkedin.com/in/example",
            "skills": ["Python", "python", "", "SQL"],
            "achievements": [{
                "title": "Competition winner",
                "date": "2026",
                "description": None,
            }],
            "review_notes": ["Unreadable date", "unreadable date"],
        },
        template,
    )

    assert normalized["linkedin_url"] == "https://www.linkedin.com/in/example"
    assert normalized["skills"] == ["Python", "SQL"]
    assert normalized["achievements"][0]["title"] == "Competition winner"
    assert normalized["review_notes"] == ["Unreadable date"]


def test_quotation_template_normalizes_commercial_terms_and_items():
    template = get_template("quotation")
    normalized = _normalize_template_data(
        {
            "document_type": "quotation",
            "quotation_number": "Q-1042",
            "valid_until": "2026-09-30",
            "payment_terms": "50% deposit",
            "quoted_items": [{
                "item_code": "SKU-1",
                "description": "Installation service",
                "quantity": 2,
                "unit": "job",
                "unit_price": 100,
                "discount_amount": None,
                "tax_amount": 16,
                "amount": 216,
            }],
            "review_notes": [],
        },
        template,
    )

    assert normalized["document_type"] == "quotation"
    assert normalized["quotation_number"] == "Q-1042"
    assert normalized["payment_terms"] == "50% deposit"
    assert normalized["quoted_items"][0]["item_code"] == "SKU-1"
    assert normalized["quoted_items"][0]["amount"] == 216.0


def test_search_opens_the_matching_page_and_review_status_resets_after_edit(isolated_database: Path):
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        uploaded = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"searchable-image"), "searchable.png")},
            content_type="multipart/form-data",
        ).get_json()
        document_id = uploaded["active_document"]["id"]
        client.patch(
            f"/api/sessions/{session_id}/files/{document_id}/markdown/1",
            json={"markdown": "Unique searchable sentinel 73921"},
        )

        search = client.get("/api/search?q=searchable%20sentinel")
        assert search.status_code == 200
        result = search.get_json()["results"][0]
        assert result["document_id"] == document_id
        assert result["page_number"] == 1

        opened = client.post("/api/search/open", json=result)
        assert opened.status_code == 200
        assert opened.get_json()["active_document"]["id"] == document_id

        approved = client.patch(
            f"/api/sessions/{session_id}/files/{document_id}/review/1",
            json={"status": "approved"},
        )
        assert approved.get_json()["review_status"] == "approved"
        assert client.get("/api/state").get_json()["active_document"]["review_status"] == "approved"

        client.patch(
            f"/api/sessions/{session_id}/files/{document_id}/markdown/1",
            json={"markdown": "Edited searchable sentinel"},
        )
        assert client.get("/api/state").get_json()["active_document"]["review_status"] == "unreviewed"


def test_duplicate_upload_is_reused_within_a_session(isolated_database: Path):
    content = b"same-document-content"
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        first = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(content), "first.png")},
            content_type="multipart/form-data",
        )
        second = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(content), "renamed-copy.png")},
            content_type="multipart/form-data",
        )
        assert first.status_code == 201
        assert second.status_code == 200
        payload = second.get_json()
        assert payload["upload"]["duplicate"] is True
        assert len(payload["sessions"][0]["files"]) == 1


def test_receipt_extraction_is_separate_reviewable_and_exportable(monkeypatch, isolated_database: Path):
    extracted = {
        "document_type": "receipt",
        "merchant_name": "Kedai Laju",
        "merchant_registration_number": None,
        "merchant_address": None,
        "receipt_number": "R-1042",
        "transaction_datetime": "2026-08-12",
        "currency": "MYR",
        "subtotal": 10.0,
        "discount_amount": 0,
        "tax_amount": 0.6,
        "total_amount": 10.6,
        "payment_method": "cash",
        "purchased_items": [{
            "description": "Kopi",
            "quantity": 2,
            "unit_price": 5,
            "amount": 10,
        }],
        "review_notes": [],
    }
    monkeypatch.setattr("app.extract_document_annotation", lambda *_args, **_kwargs: extracted)

    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(_test_png_bytes()), "receipt.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]
        client.patch(
            f"/api/sessions/{session_id}/files/{document_id}/markdown/1",
            json={"markdown": "# Original OCR remains unchanged"},
        )

        endpoint = (
            f"/api/sessions/{session_id}/files/{document_id}/"
            "extractions/receipt"
        )
        initial = client.get(endpoint)
        assert initial.status_code == 200
        assert initial.get_json()["extraction"] is None

        result = client.post(endpoint + "/run")
        assert result.status_code == 200
        extraction = result.get_json()["extraction"]
        assert extraction["status"] == "needs_review"
        assert extraction["data"]["merchant_name"] == "Kedai Laju"
        assert app.extensions["database"].get_page_markdown(document_id, 1) == "# Original OCR remains unchanged"

        edited = dict(extraction["data"])
        edited["total_amount"] = 11.2
        approved = client.patch(endpoint, json={"data": edited, "status": "approved"})
        assert approved.status_code == 200
        assert approved.get_json()["extraction"]["status"] == "approved"
        assert approved.get_json()["extraction"]["data"]["total_amount"] == 11.2

        exported = client.get(endpoint + ".csv")
        assert exported.status_code == 200
        assert exported.content_type.startswith("text/csv")
        assert b"Kedai Laju" in exported.data
        assert b"Kopi" in exported.data


def test_receipt_template_combines_initial_ocr_and_extraction_in_one_request(
    monkeypatch,
    isolated_database: Path,
):
    extracted = {
        "document_type": "receipt",
        "merchant_name": "Kedai Laju",
        "merchant_registration_number": None,
        "merchant_address": None,
        "receipt_number": "R-1042",
        "transaction_datetime": "2026-08-12",
        "currency": "MYR",
        "subtotal": 10.0,
        "discount_amount": 0,
        "tax_amount": 0.6,
        "total_amount": 10.6,
        "payment_method": "cash",
        "purchased_items": [],
        "review_notes": [],
    }
    combined_calls = []

    def combined(*_args, **_kwargs):
        combined_calls.append(True)
        return OcrDocumentResult(
            pages=(OcrPageResult("# OCR and extraction from one response"),),
            document_annotation=extracted,
        )

    monkeypatch.setattr("app.ocr_document_with_annotation", combined)
    monkeypatch.setattr(
        "app.extract_document_annotation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("annotation-only fallback must not run")
        ),
    )

    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(_test_png_bytes()), "receipt.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]
        endpoint = (
            f"/api/sessions/{session_id}/files/{document_id}/"
            "extractions/receipt/run"
        )

        response = client.post(endpoint)

        assert response.status_code == 200
        assert response.get_json()["ocr_included"] is True
        assert len(combined_calls) == 1
        assert app.extensions["database"].get_page_markdown(document_id, 1) == (
            "# OCR and extraction from one response"
        )


def test_typed_image_ocr_saves_markdown_and_structured_data_in_one_request(
    monkeypatch,
    isolated_database: Path,
):
    extracted = {
        "document_type": "quotation",
        "quotation_number": "Q-77",
        "supplier_name": "Example Supplier",
        "quoted_items": [],
        "review_notes": [],
    }
    calls = []

    def combined(*_args, **kwargs):
        calls.append(kwargs)
        return OcrDocumentResult(
            pages=(OcrPageResult("# Quotation Q-77"),),
            document_annotation=extracted,
        )

    monkeypatch.setattr("app.ocr_document_with_annotation", combined)
    monkeypatch.setattr(
        "app.ocr_image_with_assets",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("plain OCR must not run for a typed first OCR")
        ),
    )

    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        uploaded = client.post(
            f"/api/sessions/{session_id}/files",
            data={
                "file": (io.BytesIO(_test_png_bytes()), "quotation.png"),
                "document_type": "quotation",
            },
            content_type="multipart/form-data",
        ).get_json()
        document_id = uploaded["active_document"]["id"]

        response = client.post(
            f"/api/sessions/{session_id}/files/{document_id}/ocr/1",
            json={"force": False},
        )

        assert response.status_code == 200
        assert len(calls) == 1
        assert response.get_json()["markdown"] == "# Quotation Q-77"
        extraction = client.get(
            f"/api/sessions/{session_id}/files/{document_id}/extractions/quotation"
        ).get_json()["extraction"]
        assert extraction["data"]["quotation_number"] == "Q-77"
        assert client.get("/api/state").get_json()["active_document"]["has_ocr"] is True


def test_initial_state_has_one_session():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        response = client.get("/api/state")
        assert response.status_code == 200
        payload = response.get_json()
        assert len(payload["sessions"]) == 1
        assert payload["projects"] == []
        assert payload["user"]["username"] == "admin"
        assert payload["user"]["role"] == "admin"
        assert payload["active_document"] is None
        assert payload["model"] == "mistral-ocr-latest"
        assert payload["ocr_scheduler"]["pages_per_minute_limit"] == 1250
        assert payload["ocr_scheduler"]["target_pages_per_minute"] == 1100


def test_project_and_session_actions_persist_after_repository_reopens(isolated_database: Path):
    with app.test_client() as client:
        created_project = client.post("/api/projects", json={"name": "Invoices"})
        assert created_project.status_code == 201
        project_id = created_project.get_json()["projects"][0]["id"]

        created_session = client.post("/api/sessions", json={"project_id": project_id})
        assert created_session.status_code == 201
        session_id = created_session.get_json()["active_session_id"]

        renamed = client.patch(
            f"/api/sessions/{session_id}",
            json={"title": "August invoices", "is_pinned": True},
        )
        assert renamed.status_code == 200
        session = next(item for item in renamed.get_json()["sessions"] if item["id"] == session_id)
        assert session["title"] == "August invoices"
        assert session["project_id"] == project_id
        assert session["is_pinned"] is True

    reopened = Database(isolated_database, Path(app.root_path) / "db" / "schema.sql")
    persisted = reopened.state(ADMIN_USER_ID, MODEL_DEFAULT)
    session = next(item for item in persisted["sessions"] if item["id"] == session_id)
    assert persisted["projects"][0]["name"] == "Invoices"
    assert session["title"] == "August invoices"
    assert session["project_id"] == project_id
    assert session["is_pinned"] is True


def test_archive_restore_and_delete_project_keep_session_data():
    with app.test_client() as client:
        project_state = client.post("/api/projects", json={"name": "Receipts"}).get_json()
        project_id = project_state["projects"][0]["id"]
        session_state = client.post("/api/sessions", json={"project_id": project_id}).get_json()
        session_id = session_state["active_session_id"]

        archived = client.patch(f"/api/sessions/{session_id}", json={"is_archived": True})
        session = next(item for item in archived.get_json()["sessions"] if item["id"] == session_id)
        assert session["is_archived"] is True
        assert session["is_pinned"] is False
        assert archived.get_json()["active_session_id"] != session_id

        restored = client.patch(f"/api/sessions/{session_id}", json={"is_archived": False})
        session = next(item for item in restored.get_json()["sessions"] if item["id"] == session_id)
        assert session["is_archived"] is False

        deleted_project = client.delete(f"/api/projects/{project_id}")
        session = next(item for item in deleted_project.get_json()["sessions"] if item["id"] == session_id)
        assert deleted_project.get_json()["projects"] == []
        assert session["project_id"] is None


def test_sidebar_exposes_projects_and_complete_session_menu():
    with app.test_client() as client:
        page = client.get("/")
        assert page.status_code == 200
        for marker in (
            b'id="projectList"',
            b'id="newProjectButton"',
            b'data-session-action="rename"',
            b'data-session-action="delete"',
            b'data-session-action="pin"',
            b'data-session-action="move"',
            b'data-session-action="archive"',
            b'id="toggleSidebar"',
            b'id="documentPickerButton"',
            b'id="autoOcrToggle"',
            b'id="autoOcrControl"',
            b'id="autoOcrDialog"',
            b'id="autoOcrRange"',
            b'id="dropOverlay"',
            b'id="importDialog"',
            b'id="importClipboardButton"',
            b'id="exportDialog"',
            b'id="workspaceSearchInput"',
            b'id="needsReviewButton"',
            b'id="approvePageButton"',
            b'id="retryBatchButton"',
            b'id="extractButton"',
            b'id="markdownViewButton"',
            b'id="themeToggle"',
            b'id="workflowStatus"',
            b'id="previewPaneResizer"',
            b'id="rawPaneResizer"',
            b'id="minimizeDocumentPane"',
            b'id="minimizeRenderedPane"',
            b'id="minimizeRawPane"',
            b'id="structuredPane"',
            b'id="importDocumentType"',
            b'id="extractionProfile" type="search" role="combobox"',
            b'id="extractionProfileMenu" role="listbox"',
            b'id="extractionDynamicFields"',
            b'id="editExtractionButton"',
            b'value="markdown"',
            b'value="pdf"',
            b'value="bundle"',
        ):
            assert marker in page.data
        assert b'id="uploadButtonTop"' not in page.data
        assert b'id="webpageUrl"' not in page.data
        assert b'id="icon-clipboard"' in page.data


def test_ui_uses_svg_icons_and_icon_only_middle_toolbar():
    with app.test_client() as client:
        page = client.get("/")
        script = client.get("/static/app.js")
        icon_script = client.get("/static/modules/ui-icons.js")
        extraction_script = client.get("/static/modules/extraction-utils.js")
        markdown_script = client.get("/static/modules/markdown-layout.js")
        css = client.get("/static/app.css")

        for icon_id in (
            b'id="icon-panel-left-close"',
            b'id="icon-chevron-right"',
            b'id="icon-more-horizontal"',
            b'id="icon-scan-text"',
            b'id="icon-printer"',
            b'id="icon-user-round"',
        ):
            assert icon_id in page.data
        assert b'data-tooltip="Run OCR on this page"' in page.data
        assert b'data-tooltip="Export document"' in page.data
        assert b'class="auto-ocr-track"' in page.data
        assert b'id="adminAvatar" aria-hidden="true"' in page.data
        assert b'class="rendered-title-actions"' in page.data
        assert b"function iconMarkup" in icon_script.data
        assert b"function setIconButton" in icon_script.data
        assert b"navigator.clipboard.read" in script.data
        assert b'ui.importDialog.addEventListener("paste"' in script.data
        assert b'document.addEventListener("paste"' in script.data
        assert b"function pastedImageFiles" in script.data
        assert b"function setExtractionEditMode" in script.data
        assert b'function setWorkspaceView' in script.data
        assert b"function makePaneResizer" in script.data
        assert b"docslaju-pane-ratios" in script.data
        assert b"docslaju-minimized-panes" in script.data
        assert b"docslaju-workspace-view" in script.data
        assert b"function setTheme" in script.data
        assert b"docslaju-theme" in script.data
        assert b".pane-resizer" in css.data
        assert b".pane.is-minimized { min-width: 64px; }" in css.data
        assert b".workspace-grid > .pane-editor { grid-column: 5; }" in css.data
        assert b".workflow-status" in css.data
        assert b'html[data-theme="dark"]' in css.data
        assert b'id="icon-moon"' in page.data
        assert b'id="icon-sun"' in page.data
        assert b"function renderExtractionTemplateOptions" in script.data
        assert b"function normalizedTemplateSearch" in extraction_script.data
        assert b'ui.extractionProfile.addEventListener("input"' in script.data
        assert b"control.readOnly = !extractionEditMode" in script.data
        assert b'editExtractionButton.addEventListener("click"' in script.data
        assert b".extraction-edit-only" in css.data
        assert b".extraction-template-menu" in css.data
        assert b"Ctrl+V" in page.data
        assert b"function formatNumberedEquations" in markdown_script.data
        assert b"formatNumberedEquations(ui.renderedContent)" in script.data
        assert b"function normalizeRenderedLists" in markdown_script.data
        assert b"normalizeRenderedLists(ui.renderedContent)" in script.data
        assert b"function formatLetteredSubparts" in markdown_script.data
        assert b".prose .numbered-equation" in css.data
        assert b".prose .exercise-subparts" in css.data

        for old_glyph in ("‹", "×", "☰", "▤", "◇", "⌁", "✎", "⌖", "▰", "▣", "⌫", "⋯", "◆"):
            assert old_glyph.encode() not in page.data
            assert old_glyph.encode() not in script.data


def test_page_navigation_is_in_the_rendered_markdown_pane():
    with app.test_client() as client:
        markup = client.get("/").data.decode()
        middle_start = markup.index('class="pane pane-rendered"')
        right_start = markup.index('class="pane pane-editor"')
        page_controls = markup.index('id="pageControls"')
        assert middle_start < page_controls < right_start


def test_ocr_and_export_controls_are_in_the_requested_headers():
    with app.test_client() as client:
        markup = client.get("/").data.decode()
        topbar = markup.index('class="topbar"')
        workspace = markup.index('id="workspaceGrid"')
        upload = markup.index('id="uploadButton"')
        export = markup.index('id="exportButton"')
        left = markup.index('class="pane pane-document"')
        middle = markup.index('class="pane pane-rendered"')
        current_page_ocr = markup.index('id="ocrButton"')
        automatic_toggle = markup.index('id="autoOcrToggle"')
        assert topbar < upload < export < workspace
        right = markup.index('class="pane pane-editor"')
        assert left < automatic_toggle < middle
        assert middle < current_page_ocr < right


def test_document_delete_removes_content_and_selects_a_replacement(isolated_database: Path):
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        first = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"first"), "first.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]
        second = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"second"), "second.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]
        client.patch(
            f"/api/sessions/{session_id}/files/{second['id']}/markdown/1",
            json={"markdown": "temporary OCR"},
        )

        response = client.delete(f"/api/sessions/{session_id}/files/{second['id']}")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["active_document"]["id"] == first["id"]
        assert [item["id"] for item in payload["sessions"][0]["files"]] == [first["id"]]

    with sqlite3.connect(isolated_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (second["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM document_pages WHERE document_id = ?", (second["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM document_assets WHERE document_id = ?", (second["id"],)).fetchone()[0] == 0


def test_pdf_and_combined_exports_return_rendered_pdf(monkeypatch):
    rendered_pdf = b"%PDF-1.4\nrendered-output"
    monkeypatch.setattr("app._render_document_pdf", lambda *_args: rendered_pdf)
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"source-image"), "report.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]
        app.extensions["database"].save_page_markdown(document_id, 1, "# Rendered report")

        pdf = client.get(f"/api/sessions/{session_id}/files/{document_id}/export.pdf")
        combined = client.get(
            f"/api/sessions/{session_id}/files/{document_id}/export-bundle.zip"
        )

    assert pdf.status_code == 200
    assert pdf.content_type == "application/pdf"
    assert pdf.data == rendered_pdf
    assert "report_markdown.pdf" in pdf.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(combined.data)) as bundle:
        assert bundle.read("report_markdown.md").decode() == "# Rendered report"
        assert bundle.read("report_markdown.pdf") == rendered_pdf


def test_ocr_assets_use_short_links_render_live_export_and_cascade(isolated_database: Path):
    image_bytes = b"extracted-image-bytes"
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"source-image"), "report.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

        markdown = OcrMarkdown(
            "# Report\n\n![crop](assets/report-1-image-1.png)",
            source_markdown="# Report\n\n![crop](crop.png)",
            assets=[{
                "source_ref": "crop.png",
                "object_type": "image",
                "filename": "report-1-image-1.png",
                "mime_type": "image/png",
                "content": image_bytes,
            }],
            preprocessing_report={
                "version": 1,
                "used_original": False,
                "actions": ["upscaled_2.0x"],
            },
        )
        app.extensions["database"].save_page_markdown(document_id, 1, markdown)
        state = client.get("/api/state").get_json()
        assert state["active_document"]["ocr_preprocessing"]["actions"] == ["upscaled_2.0x"]

        rendered = client.post(
            "/api/render",
            json={
                "markdown": str(markdown),
                "session_id": session_id,
                "document_id": document_id,
            },
        )
        assert rendered.status_code == 200
        asset_url = (
            f"/api/sessions/{session_id}/files/{document_id}/assets/"
            "report-1-image-1.png"
        )
        assert f'<img alt="crop" src="{asset_url}"' in rendered.get_json()["html"]

        asset = client.get(asset_url)
        assert asset.status_code == 200
        assert asset.content_type == "image/png"
        assert asset.data == image_bytes

        printable = client.get(
            f"/api/sessions/{session_id}/files/{document_id}/print?autoprint=0"
        )
        assert printable.status_code == 200
        assert b'id="printDocument"' in printable.data
        assert b"# Report" not in printable.data
        assert b"<h1>Report</h1>" in printable.data
        assert asset_url.encode() in printable.data
        assert b'vendor/katex/katex.min.css' in printable.data
        assert b'print_document.js' in printable.data
        assert b'data-auto-print="false"' in printable.data
        assert app.extensions["database"].get_page_markdown(document_id, 1) == str(markdown)

        exported = client.get(
            f"/api/sessions/{session_id}/files/{document_id}/export.zip"
        )
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.data)) as bundle:
            assert bundle.read("report_markdown.md").decode() == str(markdown)
            assert bundle.read("assets/report-1-image-1.png") == image_bytes

        deleted = client.delete(f"/api/sessions/{session_id}/files/{document_id}")
        assert deleted.status_code == 200

    with sqlite3.connect(isolated_database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM document_assets WHERE document_id = ?",
            (document_id,),
        ).fetchone()[0] == 0


def test_print_view_preserves_source_page_order_and_marks_missing_ocr_pages():
    source = io.BytesIO()
    writer = pypdf.PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=320, height=480)
    writer.write(source)
    source.seek(0)

    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (source, "ordered-pages.pdf")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]
        app.extensions["database"].save_page_markdown(document_id, 1, "# First page")
        app.extensions["database"].save_page_markdown(
            document_id,
            3,
            "# Third page\n\n" + r"$$x = \frac{a}{b}$$",
        )

        printable = client.get(
            f"/api/sessions/{session_id}/files/{document_id}/print?autoprint=0"
        )

    assert printable.status_code == 200
    markup = printable.data.decode()
    assert markup.count('data-source-page="') == 3
    assert markup.index('data-source-page="1"') < markup.index('data-source-page="2"')
    assert markup.index('data-source-page="2"') < markup.index('data-source-page="3"')
    assert "Page 2 has no OCR output" in markup
    assert "First page" in markup
    assert r"$$x = \frac{a}{b}$$" in markup


def test_ocr_asset_preparation_preserves_source_and_uses_requested_filename():
    page = OcrPageResult(
        "![figure](figure.jpeg)",
        (OcrImageResult("figure.jpeg", "data:image/jpeg;base64,aW1hZ2U="),),
    )

    prepared = _prepare_ocr_markdown(
        {"name": "My Report.pdf"},
        3,
        page,
    )

    assert str(prepared) == "![figure](assets/My_Report-3-image-1.jpg)"
    assert prepared.source_markdown == "![figure](figure.jpeg)"
    assert prepared.assets[0]["filename"] == "My_Report-3-image-1.jpg"
    assert prepared.assets[0]["content"] == b"image"


def test_database_migrates_legacy_embedded_image_urls(isolated_database: Path):
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"source"), "legacy.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

    legacy = "![legacy](data:image/png;base64,aW1hZ2U=)"
    app.extensions["database"].save_page_markdown(document_id, 1, legacy)
    reopened = Database(isolated_database, Path(app.root_path) / "db" / "schema.sql")

    assert reopened.get_page_markdown(document_id, 1) == (
        "![legacy](assets/legacy-1-image-1.png)"
    )
    assets = reopened.list_document_assets(ADMIN_USER_ID, session_id, document_id)
    assert len(assets) == 1
    assert assets[0]["content"] == b"image"
    with sqlite3.connect(isolated_database) as connection:
        source = connection.execute(
            "SELECT source_markdown FROM document_pages WHERE document_id = ? AND page_number = 1",
            (document_id,),
        ).fetchone()[0]
    assert source == legacy


def test_page_claim_is_atomic_and_prevents_duplicate_inference(isolated_database: Path):
    database = app.extensions["database"]
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"image"), "claim.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

    assert database.claim_ocr_page(document_id, 1, "worker-a") is True
    assert database.claim_ocr_page(document_id, 1, "worker-b") is False
    database.release_ocr_page(document_id, 1, "worker-a")
    assert database.claim_ocr_page(document_id, 1, "worker-b") is True


def test_unfinished_queue_is_resumable_and_failed_pages_can_be_retried(isolated_database: Path):
    database = app.extensions["database"]
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"queue-image"), "queue.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

    job_id = database.create_ocr_job(ADMIN_USER_ID, session_id, document_id)
    database.mark_ocr_job_running(job_id)
    assert database.dequeue_next_ocr_job_page(job_id)["page_number"] == 1

    resumable = database.recover_interrupted_ocr_jobs()
    assert [job["id"] for job in resumable] == [job_id]
    resumed = database.get_ocr_job(ADMIN_USER_ID, job_id)
    assert resumed["status"] == "queued"
    assert resumed["pages"][0]["status"] == "queued"

    database.mark_ocr_job_running(job_id)
    assert database.dequeue_next_ocr_job_page(job_id)["page_number"] == 1
    database.mark_ocr_job_page(job_id, 1, "failed", "temporary upstream error")
    assert database.finalize_ocr_job_if_idle(job_id) is True
    retry_id = database.retry_failed_ocr_job(ADMIN_USER_ID, job_id)
    retry = database.get_ocr_job(ADMIN_USER_ID, retry_id)
    assert retry["status"] == "queued"
    assert len(retry["pages"]) == 1
    assert retry["pages"][0] == {
        "page_number": 1,
        "priority": 0,
        "status": "queued",
        "attempts": 0,
        "processing_mode": None,
        "remote_batch_id": None,
        "error_code": None,
        "duration_ms": None,
        "error": None,
    }


def test_ocr_all_runs_in_background_and_kill_switch_discards_inflight_result(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_inference(_document, page_number):
        started.set()
        release.wait(timeout=3)
        return f"page {page_number} should be discarded"

    monkeypatch.setattr("app._infer_document_page", slow_inference)

    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"image"), "cancel.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

        queued = client.post(f"/api/sessions/{session_id}/files/{document_id}/ocr-all", json={})
        assert queued.status_code == 202
        job_id = queued.get_json()["id"]
        assert started.wait(timeout=1)

        duplicate = client.post(f"/api/sessions/{session_id}/files/{document_id}/ocr/1", json={"force": True})
        assert duplicate.status_code == 409

        cancelled = client.delete(f"/api/ocr-jobs/{job_id}")
        assert cancelled.status_code == 202
        assert cancelled.get_json()["cancel_requested"] is True
        release.set()

        deadline = time.monotonic() + 2
        job = None
        while time.monotonic() < deadline:
            job = client.get(f"/api/ocr-jobs/{job_id}").get_json()
            if job["status"] == "cancelled":
                break
            time.sleep(0.02)
        assert job["status"] == "cancelled"
        assert app.extensions["database"].get_page_markdown(document_id, 1) is None


def test_ocr_all_processes_every_pdf_page_without_blocking_request(monkeypatch):
    monkeypatch.setattr("app._infer_document_page", lambda _document, page: f"# Page {page}")
    writer = pypdf.PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=320, height=480)
    source = io.BytesIO()
    writer.write(source)

    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(source.getvalue()), "three-pages.pdf")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

        queued = client.post(f"/api/sessions/{session_id}/files/{document_id}/ocr-all", json={})
        assert queued.status_code == 202
        job_id = queued.get_json()["id"]

        deadline = time.monotonic() + 2
        job = None
        while time.monotonic() < deadline:
            job = client.get(f"/api/ocr-jobs/{job_id}").get_json()
            if job["status"] == "completed":
                break
            time.sleep(0.02)
        assert job["status"] == "completed"
        assert job["completed_pages"] == 3
        assert [
            app.extensions["database"].get_page_markdown(document_id, page)
            for page in range(1, 4)
        ] == ["# Page 1", "# Page 2", "# Page 3"]


def test_ocr_all_processes_only_the_requested_page_range(monkeypatch):
    processed_pages = []

    def record_inference(_document, page):
        processed_pages.append(page)
        return f"# Page {page}"

    monkeypatch.setattr("app._infer_document_page", record_inference)
    writer = pypdf.PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=320, height=480)
    source = io.BytesIO()
    writer.write(source)

    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(source.getvalue()), "selected-pages.pdf")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]
        queued = client.post(
            f"/api/sessions/{session_id}/files/{document_id}/ocr-all",
            json={"page_range": "2, 4-5"},
        )
        assert queued.status_code == 202
        job_id = queued.get_json()["id"]

        deadline = time.monotonic() + 2
        job = None
        while time.monotonic() < deadline:
            job = client.get(f"/api/ocr-jobs/{job_id}").get_json()
            if job["status"] == "completed":
                break
            time.sleep(0.02)

        assert job["status"] == "completed"
        assert job["total_pages"] == 3
        assert [page["page_number"] for page in job["pages"]] == [2, 4, 5]
        assert processed_pages == [2, 4, 5]
        assert app.extensions["database"].get_page_markdown(document_id, 1) is None
        assert app.extensions["database"].get_page_markdown(document_id, 3) is None

        invalid = client.post(
            f"/api/sessions/{session_id}/files/{document_id}/ocr-all",
            json={"page_range": "1, nope"},
        )
        assert invalid.status_code == 400


def test_current_page_promotion_jumps_ahead_of_the_automatic_queue(monkeypatch):
    first_page_started = threading.Event()
    release_first_page = threading.Event()
    processing_order = []

    def ordered_inference(_document, page_number):
        processing_order.append(page_number)
        if page_number == 1:
            first_page_started.set()
            release_first_page.wait(timeout=3)
        return f"# Page {page_number}"

    monkeypatch.setattr("app._infer_document_page", ordered_inference)
    writer = pypdf.PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=320, height=480)
    source = io.BytesIO()
    writer.write(source)

    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(source.getvalue()), "priority.pdf")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]
        queued = client.post(
            f"/api/sessions/{session_id}/files/{document_id}/ocr-all",
            json={"page_range": "1-2"},
        )
        job_id = queued.get_json()["id"]
        assert first_page_started.wait(timeout=1)

        promoted = client.post(f"/api/ocr-jobs/{job_id}/prioritize/3")
        assert promoted.status_code == 200
        page_three = next(page for page in promoted.get_json()["pages"] if page["page_number"] == 3)
        assert page_three["priority"] == 1000
        release_first_page.set()

        deadline = time.monotonic() + 2
        job = None
        while time.monotonic() < deadline:
            job = client.get(f"/api/ocr-jobs/{job_id}").get_json()
            if job["status"] == "completed":
                break
            time.sleep(0.02)
        assert job["status"] == "completed"
        assert processing_order == [1, 3, 2]


def test_pdf_page_ocr_is_submitted_as_a_raster_image(monkeypatch):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=320, height=480)
    source = io.BytesIO()
    writer.write(source)
    captured = {}

    def image_ocr(content, *, mime_type, model):
        captured.update(content=content, mime_type=mime_type, model=model)
        return OcrPageResult("# Raster page", confidence_score=0.92)

    monkeypatch.setattr("app.ocr_image_with_assets", image_ocr)
    result = _infer_document_page(
        {
            "name": "problematic.pdf",
            "content": source.getvalue(),
            "is_pdf": True,
            "mime_type": "application/pdf",
        },
        1,
    )

    assert str(result) == "# Raster page"
    assert captured["mime_type"].startswith("image/")
    assert captured["content"].startswith((b"\x89PNG", b"\xff\xd8"))
    assert result.preprocessing_report["source"] == "rasterized_pdf_page"


def test_adaptive_rate_controller_increases_slowly_and_halves_on_429():
    controller = AdaptiveRateController(
        pages_per_minute=1250,
        target_utilization=0.88,
        initial_concurrency=2,
        max_concurrency=6,
    )

    for _ in range(8):
        controller.finish(OcrAttemptOutcome(True, 0.25))
    assert controller.concurrency == 3

    controller.note_rate_limit(0)
    assert controller.concurrency == 1
    assert controller.pages_per_minute == 1250

    fixed = AdaptiveRateController(
        pages_per_minute=1250,
        target_utilization=0.88,
        initial_concurrency=2,
        max_concurrency=2,
    )
    for _ in range(8):
        fixed.finish(OcrAttemptOutcome(True, 0.25))
    assert fixed.throughput_pages_per_second is not None


def test_adaptive_mode_does_not_guess_that_batch_is_faster():
    manager = OcrJobManager(batch_enabled=True, batch_min_pages=1)

    assert manager._choose_mode(1000, lambda *_args: None) == "realtime"


def test_realtime_job_retries_transient_page_without_repeating_success(isolated_database: Path):
    database = app.extensions["database"]
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"retry-image"), "retry.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

    calls = []

    class TemporaryError(RuntimeError):
        status_code = 503

    def flaky(_document, page_number):
        calls.append(page_number)
        if len(calls) == 1:
            raise TemporaryError("temporary upstream failure")
        return "# Saved after retry"

    manager = OcrJobManager(
        initial_concurrency=1,
        max_concurrency=1,
        max_attempts=2,
        batch_enabled=False,
    )
    job_id = database.create_ocr_job(ADMIN_USER_ID, session_id, document_id)
    manager.start(database, ADMIN_USER_ID, job_id, flaky)

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = database.get_ocr_job(ADMIN_USER_ID, job_id)
        if job["status"] == "completed":
            break
        time.sleep(0.02)

    assert job["status"] == "completed"
    assert calls == [1, 1]
    assert job["pages"][0]["attempts"] == 2
    assert database.get_page_markdown(document_id, 1) == "# Saved after retry"


def test_remote_batch_results_remain_page_independent(monkeypatch, isolated_database: Path):
    monkeypatch.setenv("OCR_BATCH_FORCE", "true")
    database = app.extensions["database"]
    writer = pypdf.PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=320, height=480)
    source = io.BytesIO()
    writer.write(source)
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(source.getvalue()), "batch.pdf")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

    def batch_processor(_document, pages, _cancellation):
        return (
            {page: f"# Batch page {page}" for page in reversed(pages)},
            "remote-batch-1",
            0.5,
        )

    manager = OcrJobManager(
        initial_concurrency=1,
        max_concurrency=1,
        batch_enabled=True,
        batch_min_pages=1,
        batch_size=2,
    )
    job_id = database.create_ocr_job(ADMIN_USER_ID, session_id, document_id)
    manager.start(
        database,
        ADMIN_USER_ID,
        job_id,
        lambda *_args: (_ for _ in ()).throw(AssertionError("real-time path used")),
        batch_processor,
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = database.get_ocr_job(ADMIN_USER_ID, job_id)
        if job["status"] == "completed":
            break
        time.sleep(0.02)

    assert job["status"] == "completed"
    assert job["processing_mode"] == "batch"
    assert {page["remote_batch_id"] for page in job["pages"]} == {"remote-batch-1"}
    assert database.get_page_markdown(document_id, 1) == "# Batch page 1"
    assert database.get_page_markdown(document_id, 2) == "# Batch page 2"
    metrics = database.get_scheduler_metrics()
    assert metrics["batch_samples"] == 2
    assert metrics["batch_seconds_per_page"] is not None


def test_structured_extraction_failure_keeps_successful_pdf_pages(monkeypatch):
    writer = pypdf.PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=320, height=480)
    source = io.BytesIO()
    writer.write(source)

    def partially_failing(_document, page_number):
        if page_number == 2:
            raise RuntimeError("page two upstream error")
        return "# Page one is durable"

    monkeypatch.setattr("app._infer_document_page", partially_failing)
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(source.getvalue()), "partial.pdf")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

        response = client.post(
            f"/api/sessions/{session_id}/files/{document_id}/extractions/receipt/run"
        )

    assert response.status_code == 500
    assert app.extensions["database"].get_page_markdown(document_id, 1) == "# Page one is durable"
    assert app.extensions["database"].get_page_markdown(document_id, 2) is None


def test_upload_content_validation_rejects_renamed_non_image():
    app.config["VALIDATE_UPLOAD_CONTENT"] = True
    try:
        with app.test_client() as client:
            session_id = client.get("/api/state").get_json()["active_session_id"]
            response = client.post(
                f"/api/sessions/{session_id}/files",
                data={"file": (io.BytesIO(b"not really a PNG"), "pretend.png")},
                content_type="multipart/form-data",
            )
    finally:
        app.config["VALIDATE_UPLOAD_CONTENT"] = False

    assert response.status_code == 400
    assert "readable image" in response.get_json()["error"]


def test_image_upload_edit_render_and_export():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        initial = client.get("/api/state").get_json()
        session_id = initial["active_session_id"]

        upload = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"draft-image"), "receipt.png")},
            content_type="multipart/form-data",
        )
        assert upload.status_code == 201
        document = upload.get_json()["active_document"]

        original = client.get(f"/api/sessions/{session_id}/files/{document['id']}/content")
        assert original.status_code == 200
        assert original.data == b"draft-image"

        update = client.patch(
            f"/api/sessions/{session_id}/files/{document['id']}/markdown/1",
            json={"markdown": "# Receipt\n\n| Item | Price |\n|---|---:|\n| Tea | 4.50 |"},
        )
        assert update.status_code == 200

        rendered = client.post("/api/render", json={"markdown": "# Receipt\n\n**Total**"})
        assert rendered.status_code == 200
        assert "<h1>Receipt</h1>" in rendered.get_json()["html"]
        assert "<strong>Total</strong>" in rendered.get_json()["html"]

        exported = client.get(f"/api/sessions/{session_id}/files/{document['id']}/export.md")
        assert exported.status_code == 200
        assert b"# Receipt" in exported.data
        assert "receipt_markdown.md" in exported.headers["Content-Disposition"]


def test_markdown_renderer_escapes_raw_html():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        response = client.post("/api/render", json={"markdown": "<script>alert(1)</script>"})
        body = response.get_json()["html"]
        assert "<script>" not in body
        assert "&lt;script&gt;" in body


def test_markdown_preview_bundles_katex_and_preserves_table_rendering():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        page = client.get("/")
        assert b'vendor/katex/katex.min.css' in page.data
        assert b'vendor/katex/katex.min.js' in page.data
        assert b'vendor/katex/contrib/auto-render.min.js' in page.data

        script = client.get("/static/modules/markdown-layout.js")
        assert b"renderMathInElement" in script.data
        assert b'{ left: "$$", right: "$$", display: true }' in script.data

        rendered = client.post(
            "/api/render",
            json={"markdown": "| Symbol | Value |\n|---|---|\n| rho | $\\rho$ |"},
        )
        body = rendered.get_json()["html"]
        assert "<table>" in body
        assert r"\(\rho\)" in body


def test_markdown_compiler_preserves_latex_array_row_separators():
    source = (
        r"$$\begin{array}{l} "
        r"\rho = \frac {\lambda}{\mu} \\ "
        r"\pi_ {0} = 1 - \rho \\ "
        r"\pi_ {k} = \rho^ {k} (1 - \rho), \quad k \geq 1 "
        r"\end{array}$$"
    )

    with app.test_client() as client:
        response = client.post("/api/render", json={"markdown": source})
        body = response.get_json()["html"]

    assert source in body
    assert body.count(r"\\") == 2


def test_markdown_compiler_keeps_html_escaped_inside_math():
    with app.test_client() as client:
        response = client.post(
            "/api/render",
            json={"markdown": r"$$x < y</script><script>alert(1)</script>$$"},
        )
        body = response.get_json()["html"]

    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_markdown_compiler_does_not_treat_currency_as_inline_math():
    source = (
        "The outlet costs $12K (i.e. $12,000), with expected revenue "
        "$20K or $25K and probabilities 0.4 and 0.6."
    )

    with app.test_client() as client:
        response = client.post("/api/render", json={"markdown": source})
        body = response.get_json()["html"]

    assert source in body
    assert r"\(" not in body
    assert r"\)" not in body


def test_markdown_compiler_handles_currency_and_inline_math_together():
    source = r"The cost is $12K. Utilization is $\rho = \frac{\lambda}{\mu}$."

    with app.test_client() as client:
        response = client.post("/api/render", json={"markdown": source})
        body = response.get_json()["html"]

    assert "$12K" in body
    assert r"\(\rho = \frac{\lambda}{\mu}\)" in body


def test_pdf_preview_uses_the_browser_viewer_without_a_synthetic_text_layer():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        page = client.get("/")
        assert page.status_code == 200
        assert b'id="pdfViewer"' in page.data
        assert b"pdfTextLayer" not in page.data
        assert b"ocrTextLayer" not in page.data

        script = client.get("/static/app.js")
        assert script.status_code == 200
        assert b"pdfViewer.src" in script.data
        assert b"getTextContent" not in script.data
        assert b"pdfjs.TextLayer" not in script.data
        assert b"ocrTextLayer" not in script.data
        assert b"ocr_layout" not in script.data

        styles = client.get("/static/app.css")
        assert styles.status_code == 200
        assert b"#documentPreview[hidden]" in styles.data
        assert b".document-stage.pdf-mode" in styles.data


def test_pdf_content_route_serves_the_unmodified_original_file():
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=320, height=480)
    source = io.BytesIO()
    writer.write(source)
    original_pdf = source.getvalue()

    app.config.update(TESTING=True)
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        upload = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(original_pdf), "original.pdf")},
            content_type="multipart/form-data",
        )
        assert upload.status_code == 201
        document_id = upload.get_json()["active_document"]["id"]

        response = client.get(f"/api/sessions/{session_id}/files/{document_id}/content")
        assert response.status_code == 200
        assert response.content_type == "application/pdf"
        assert response.headers["Content-Disposition"].startswith("inline;")
        assert response.data == original_pdf


def test_rendered_pane_has_an_ocr_loading_indicator():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        page = client.get("/")
        assert b'id="renderedLoading"' in page.data
        assert b'id="renderedLoadingLabel"' in page.data
        assert b'class="loading-spinner"' in page.data

        script = client.get("/static/app.js")
        assert b"setOcrLoading(true, document.current_page)" in script.data
        assert b"setOcrLoading(false)" in script.data

        styles = client.get("/static/app.css")
        assert b"@keyframes loading-spin" in styles.data
        assert b".rendered-loading[hidden]" in styles.data
