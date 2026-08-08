import io
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import PyPDF2

from app import _prepare_ocr_markdown, app
from src.mistral_client import MODEL_DEFAULT, OcrImageResult, OcrMarkdown, OcrPageResult
from src.services.database import ADMIN_USER_ID, Database


def test_schema_contains_the_persistent_repository(isolated_database: Path):
    expected = {
        "users", "projects", "sessions", "documents", "document_pages", "user_preferences",
        "document_assets", "ocr_jobs", "ocr_job_pages", "ocr_page_claims",
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
        page_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_pages)")
        }
        assert "source_markdown" in page_columns


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
            b'id="dropOverlay"',
        ):
            assert marker in page.data
        assert b'id="uploadButtonTop"' not in page.data


def test_ui_uses_svg_icons_and_icon_only_middle_toolbar():
    with app.test_client() as client:
        page = client.get("/")
        script = client.get("/static/app.js")

        for icon_id in (
            b'id="icon-panel-left-close"',
            b'id="icon-chevron-right"',
            b'id="icon-more-horizontal"',
            b'id="icon-scan-text"',
            b'id="icon-printer"',
        ):
            assert icon_id in page.data
        assert b'data-tooltip="Run OCR on this page"' in page.data
        assert b'data-tooltip="Export document as PDF"' in page.data
        assert b'class="auto-ocr-track"' in page.data
        assert b'class="middle-icon-actions"' not in page.data
        assert b"function iconMarkup" in script.data
        assert b"function setIconButton" in script.data

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
        assert left < current_page_ocr < automatic_toggle < middle


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
        )
        app.extensions["database"].save_page_markdown(document_id, 1, markdown)

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
            assert bundle.read("report.md").decode() == str(markdown)
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
    writer = PyPDF2.PdfWriter()
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
    writer = PyPDF2.PdfWriter()
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
    writer = PyPDF2.PdfWriter()
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
        queued = client.post(f"/api/sessions/{session_id}/files/{document_id}/ocr-all", json={})
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

        script = client.get("/static/app.js")
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
    writer = PyPDF2.PdfWriter()
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
