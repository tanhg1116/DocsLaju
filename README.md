# DocsLaju — Mistral OCR Workspace

A Flask-based document OCR workspace powered by Mistral. Upload PDFs or images, compare the original page with rendered Markdown, and edit the raw OCR result in a focused three-pane interface.

![DocsLaju OCR workspace showing a receipt with rendered and raw Markdown](docs/assets/receipt-demo.jpg)

## Flask draft

This branch transitions the UI from Streamlit to a custom Flask frontend while preserving the original workflow:

- **Left pane:** interactive PDF page or original image, file picker, and page navigation
- Native browser PDF viewer with the original document's text selection, zoom, and navigation
- **Middle pane:** safely rendered Markdown preview with tables and locally bundled KaTeX math
- **Right pane:** raw Markdown editor with autosave and clipboard copy
- Multiple independent OCR sessions
- Project folders with drag-and-drop organization
- Rename, pin, archive, move, and delete session actions
- Collapsible sidebar with the preference retained in the browser
- Header document picker with per-document deletion and an adjacent multi-file upload dialog
- Optional, reassuring document-type choice during upload; typed first-time OCR
  returns Markdown and structured fields together in one Mistral request
- Drag-and-drop PDF/image upload directly onto the preview pane
- Non-blocking automatic OCR toggle with a configurable page range, progress, and immediate queue cancellation
- Adaptive OCR scheduling for the 1,250 pages/minute account limit, with bounded concurrency, retry backoff, current-page priority, and optional Mistral micro-batches
- Workspace-wide OCR text search with direct page navigation and result highlighting
- Per-page review states for approval and follow-up, including OCR confidence indicators
- Restart-resilient OCR jobs, failed-page retry, and duplicate-upload detection
- Template-driven Invoice, Receipt, Quotation, and CV/Résumé extraction with editable fields, tables, approval, and CSV export
- Markdown/structured workspace switcher that keeps the original document preview visible
- SQLite persistence for sessions, uploads, and per-page OCR edits
- Per-page OCR and editing with `mistral-ocr-latest`
- Markdown ZIP, rendered PDF, and combined Markdown + PDF ZIP exports
- Responsive mobile/tablet layout

The local draft runs as a seeded admin user without a login screen. Its sessions,
projects, uploaded files, active selection, and page-level OCR results persist in
`instance/docslaju.sqlite3`, so restarting Flask does not clear the workspace.

## Reviewing the code

New contributors should begin with
[`docs/REVIEW_GUIDE.md`](docs/REVIEW_GUIDE.md). It provides the recommended
reading order, architecture and request flows, concurrency/cost invariants,
frontend and database navigation maps, and the review checklist for OCR changes.
The entry points now delegate pure document, Markdown, extraction, and browser
layout helpers to focused modules; the guide explains those boundaries.

Live scheduler measurements and their reproducible commands are recorded in
[`docs/OCR_BENCHMARK.md`](docs/OCR_BENCHMARK.md).

## Setup

Requires Python 3.10 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

If `MISTRAL_API_KEY` is already configured in your system environment, no `.env` file is required. Otherwise, add your key to `.env`:

```dotenv
MISTRAL_API_KEY="your-key-here"
FLASK_SECRET_KEY="a-long-random-development-secret"
# DOCSLAJU_DB_PATH="instance/docslaju.sqlite3"  # optional
```

Start the development server:

```powershell
python app.py
```

If Python is not installed system-wide but `uv` is available:

```powershell
uv run --python 3.12 --with-requirements requirements.txt python app.py
```

Then open <http://127.0.0.1:5000>.

## Test

```powershell
python -m pytest tests -q
```

The route tests do not call Mistral. Live OCR requires a valid `MISTRAL_API_KEY`.

## Project layout

```text
app.py                         Flask application and JSON API
db/
├── README.md                  Database structure and ownership notes
├── schema.sql                 SQLite tables, constraints, indexes, and seed
└── migrations/               Versioned forward database migrations
instance/
└── docslaju.sqlite3           Runtime database (automatic; Git-ignored)
src/
├── mistral_client.py          Mistral SDK integration
└── services/
    ├── browser_pdf.py         Installed-browser PDF rendering
    ├── database.py            SQLite repository and full-text search
    ├── document_ocr.py         PDF rasterization and OCR asset preparation
    ├── extraction_normalizer.py Structured result validation
    ├── markdown_renderer.py    Safe server-side Markdown compilation
    └── ocr_jobs.py            Persistent prioritized OCR worker
templates/index.html           Three-pane application shell
static/app.css                 Responsive visual design
static/app.js                  Sidebar, upload, OCR, editor, and export behavior
static/modules/                Pure browser helpers and Markdown layout rules
static/vendor/katex/           Local KaTeX renderer, fonts, and license
tests/                         Unit, persistence, and Flask route tests
docs/REVIEW_GUIDE.md           Guided architecture and code-review entry point
docs/OCR_BENCHMARK.md          Live scheduler measurements and decisions
```

The complete database repository tree and relationship diagram are in
[`db/README.md`](db/README.md). The executable schema is in
[`db/schema.sql`](db/schema.sql).

## Notes

- Supported inputs: PDF, PNG, JPG/JPEG, and WEBP (30 MB maximum in this draft).
- PDFs are loaded directly from Flask into the browser's built-in PDF viewer; the app does not rasterize PDF previews or synthesize a text layer.
- PDF OCR is deliberately different from previewing: each page is rasterized locally into a clean image before submission so malformed or unsupported PDF internals cannot break the Mistral request. The original PDF remains unchanged.
- Only text selectable in the original PDF is selectable in the preview. Scanned/image-only PDFs and uploaded images remain non-selectable even after OCR.
- HTML typed into the Markdown editor is escaped before rendering.
- KaTeX is bundled in `static/vendor/katex`, so math rendering does not require a CDN or npm at runtime.
- OCR-extracted images are stored as document-owned SQLite BLOB assets. Editable
  Markdown uses short paths such as `assets/report-2-image-1.jpg`; the live
  preview resolves them through an ownership-checked Flask endpoint.
- The header export dialog offers a Markdown-and-assets ZIP, a rendered A4 PDF,
  or one ZIP containing Markdown, assets, and the rendered PDF. PDF generation
  reuses Chrome or Edge already installed on the host; no additional browser
  dependency is downloaded.
- Deleting a project moves its sessions to **Unfiled**. Deleting a session also
  deletes its stored documents and OCR pages.
- Switching on automatic OCR opens a page-range dialog. The full document is
  selected by default, while individual pages and comma-separated ranges such as
  `1-3, 5, 8-10` are also supported. The worker processes the selected queue
  outside the Flask request, allowing page navigation and the rest of the UI to
  remain responsive. Switching the toggle off cancels queued pages immediately;
  if Mistral is already processing a page, its returned result is discarded.
- Triggering current-page OCR while automatic OCR is active promotes that page to
  the front of the persistent queue, adding it even when it was outside the chosen
  range. A page already sent to Mistral finishes first, then the promoted page runs
  next.
- SQLite page claims prevent manual and batch requests from processing the same
  document page concurrently.
- Active OCR queues resume after a Flask restart. Each page result and its job
  completion state are committed atomically. Failed or cancelled pages can be
  retried without reprocessing pages that already completed. Transient server
  and network failures receive bounded exponential-backoff retries.
- The scheduler starts conservatively, increases concurrency only after stable
  success, and halves it after a `429`. Normal documents use the lower-latency
  real-time endpoint. Mistral Batch is selected only after a forced calibration
  has saved end-to-end measurements proving it faster for the local workload.
- Uploads are hashed with SHA-256; selecting the same file twice in one session
  reopens the stored document instead of duplicating it or paying for OCR again.
- Image uploads are inspected locally before OCR. Orientation correction,
  confident page/browser-border cropping, deskewing, low-resolution upscaling,
  denoising, contrast adjustment, and light sharpening run only when their
  measured thresholds are met. The original upload is preserved, and the UI
  reports every action applied to the temporary OCR input.
- Deleting a document cascades to all of its extracted asset rows.
- Structured extraction is opened from the green button in the document header.
  Choose the versioned Invoice, Receipt, Quotation, or CV/Résumé template. A
  single uploaded image keeps the efficient one-call OCR-and-annotation path.
  Multi-page PDFs first preserve page OCR independently, then annotate a clean
  raster-only PDF. Annotation failure never removes successful Markdown. The
  reusable UI renders fields and tables from the trusted template layout;
  missing scalar values stay `null` in SQLite and appear as `–`.
- The existing OpenAI dependency is retained for future restoration of AI-generated filenames, but it is not used by this Flask draft.

## License

MIT License
