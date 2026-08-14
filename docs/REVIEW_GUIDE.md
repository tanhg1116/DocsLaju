# DocsLaju review guide

This document is the entry point for someone reviewing DocsLaju for the first
time. It explains the intended boundaries and the order in which to read the
code. It does not replace the executable tests or the schema.

## Scope and non-goals

DocsLaju is currently a **local, single-admin Flask application**. It binds to
`127.0.0.1` and stores its working data in SQLite. Authentication, public
deployment, billing, multi-tenant isolation, and distributed workers are
deliberately deferred. Do not interpret those missing production features as
accidental omissions in the current local build.

The application has three important promises:

1. The left preview displays the original document. OCR never adds a synthetic
   selectable-text layer to it.
2. Raw OCR Markdown changes only through OCR replacement or an explicit user
   edit. Rendering fixes do not silently rewrite stored Markdown.
3. Each page is independently recoverable. One failed page must not discard or
   cause repeat billing for pages that already succeeded.

## Recommended reading order

Read the repository in this order rather than beginning at the top of the large
Flask or JavaScript entry points:

1. [`README.md`](../README.md) — product behavior, setup, and supported inputs.
2. [`db/schema.sql`](../db/schema.sql) — persisted entities and constraints.
3. [`db/README.md`](../db/README.md) — ownership and database relationships.
4. [`src/services/database.py`](../src/services/database.py) — transaction and
   persistence rules.
5. [`src/services/ocr_jobs.py`](../src/services/ocr_jobs.py) — queueing,
   priority, rate control, retries, Batch selection, and cancellation.
6. [`src/mistral_client.py`](../src/mistral_client.py) — the external Mistral
   API boundary.
7. [`src/services/document_ocr.py`](../src/services/document_ocr.py),
   [`markdown_renderer.py`](../src/services/markdown_renderer.py), and
   [`extraction_normalizer.py`](../src/services/extraction_normalizer.py) —
   pure document transformation and validation boundaries.
8. [`app.py`](../app.py) — HTTP routes and service composition.
9. [`static/modules/`](../static/modules/) — pure browser helpers and rendered
   Markdown layout rules.
10. [`static/app.js`](../static/app.js) — browser state, polling, and user
   interactions.
11. [`tests/`](../tests) — executable examples of expected behavior.

The entry points retain route and browser orchestration while reusable pure
logic lives in focused modules. Keep new transformation rules out of `app.py`
and avoid returning pure DOM-formatting utilities to `static/app.js`.

## Architecture at a glance

```text
Browser
  ├── original PDF/image preview
  ├── rendered Markdown / structured view
  └── raw Markdown editor
          │ JSON API + document content routes
          ▼
Flask routes (app.py)
  ├── Database repository ──────────────► SQLite
  ├── OCR job manager
  │     ├── adaptive real-time workers ─► Mistral OCR
  │     └── asynchronous micro-batches ─► Mistral Batch OCR
  ├── conditional image preprocessing
  └── rendered PDF export through installed Chrome/Edge
```

`app.py` composes the system. Business rules that require durable state belong
in `Database`; scheduling rules belong in `OcrJobManager`; Mistral request and
response translation belongs in `mistral_client.py`.

## Main workflow: upload and OCR

```text
POST uploaded file
  → validate actual PDF/image contents
  → calculate SHA-256 checksum
  → reuse an identical document in the session, or store a new BLOB
  → create an OCR job containing independent page rows
  → atomically dequeue the highest-priority page
  → acquire the document/page claim
  → render a PDF page to a clean image (images are conditionally enhanced)
  → choose real-time or Batch OCR
  → save Markdown, assets, confidence, and completed status atomically
  → release the claim
  → browser polling refreshes progress and the active page
```

The original upload remains stored unchanged. A PDF is rasterized only for its
temporary OCR input because some valid-looking source PDFs are rejected by the
upstream PDF processor.

### Page status and recovery

```text
queued → running → completed
                 ↘ failed
                 ↘ cancelled
```

- `ocr_page_claims` prevents manual and automatic OCR from submitting the same
  page simultaneously.
- `document_pages` is the source of truth for a completed OCR result.
- A job retry includes only failed or cancelled page numbers.
- On restart, interrupted `running` pages return to the queue. A page result and
  its job completion status are committed in the same SQLite transaction.
- A job stores the original document checksum; a stale result cannot be saved
  against changed document content.

## Scheduling and rate limits

The configured account ceiling is 1,250 OCR pages/minute. The scheduler targets
88% (1,100 pages/minute) to preserve headroom for current-page requests, timing
bursts, and other consumers of the same organization quota.

Real-time concurrency starts at four and is capped at 24. It increases only
after a stable measurement window improves completed-page throughput by at
least 5%. A `429` response halves concurrency, respects `Retry-After` when
available, and applies bounded retry backoff.

Measured behavior and the reproducible paid test commands are recorded in
[`OCR_BENCHMARK.md`](OCR_BENCHMARK.md). Review those results before changing
the real-time/Batch selection policy.

Manual current-page work is represented by a large queue priority. Work already
submitted remotely cannot be safely reprioritized without paying for a duplicate
request, so only unsent pages can jump ahead.

### Real-time versus Batch

- Ordinary and interactive documents use real-time OCR.
- Batch mode is considered only after a successful forced calibration has
  stored end-to-end Batch telemetry. Queue size alone is not evidence that the
  asynchronous provider path will be faster.
- After Batch telemetry exists, estimated real-time and Batch completion times
  determine the mode.
- A Batch job contains independent page requests identified by `custom_id`;
  response order is never trusted.
- Disabling Auto-OCR stops new submissions and requests remote cancellation.
  An already transmitted request may still finish upstream, but its result is
  discarded after local cancellation.

## Structured extraction

Structured extraction and Markdown have different recovery needs:

- A single receipt/invoice image can use one combined OCR-and-annotation call
  because it contains one independently recoverable page.
- A multi-page PDF completes missing page OCR first, saving each page
  independently. Annotation then uses a clean raster-only PDF.
- Annotation failure never deletes or rewrites saved Markdown.
- Existing user-edited Markdown is preserved when extraction is re-run.
- Template layouts are trusted application definitions; model output supplies
  only the data values.

## Important invariants to review

Search the code for comments beginning with `Invariant:`, `Concurrency:`,
`Cost:`, `Cancellation:`, `Compatibility:`, and `Security:`. These comments
identify decisions where an apparently simpler rewrite could introduce data
loss, duplicate API costs, or unsafe behavior.

Verify these invariants when changing OCR behavior:

- A successful page survives failures on other pages.
- Completed pages are skipped unless force-reprocessing is explicit.
- Markdown, extracted assets, and job completion commit together.
- Only one active owner exists for a document/page pair.
- A stale checksum cannot overwrite current document data.
- Cancellation prevents new work and discards in-flight results.
- Out-of-order Batch responses map to the correct pages.
- Structured extraction failure does not invalidate Markdown.
- The original document and the temporary optimized OCR input are distinct.
- Temporary Mistral file uploads are deleted in `finally` blocks.

## Frontend review map

Within `static/app.js`, review behavior by feature rather than reading straight
through the file:

| Feature | Search for |
| --- | --- |
| State loading and rendering | `loadState`, `renderState` |
| Auto-OCR status | `updateBatchControls`, `pollOcrJob` |
| Auto-OCR start/stop | `runAllOcr`, `cancelAllOcr` |
| Current-page priority | `runOcr` |
| PDF preview | `loadPdfPreview`, `renderPdfPage` |
| Markdown editing | `saveMarkdown`, `savePendingMarkdown` |
| Structured workspace | `renderStructuredWorkspace` |
| Upload and clipboard paste | `uploadFiles`, `readClipboardImage` |
| Export | `openExportDialog`, `runExport` |

The browser treats the Flask state response as the current durable state. It
may optimistically update controls, but persisted OCR/page state comes back from
the API during polling.

## Database review map

| Table | Responsibility |
| --- | --- |
| `documents` | Original upload, MIME, page count, checksum, selected type |
| `document_pages` | Source/editable Markdown, confidence, review state |
| `document_assets` | OCR-extracted images referenced by short Markdown paths |
| `document_extractions` | Versioned structured template results |
| `ocr_jobs` | Document-level automatic OCR lifecycle and scheduler metadata |
| `ocr_job_pages` | Page priority, attempts, mode, result/error timing |
| `ocr_page_claims` | Cross-request duplicate-inference lock |
| `schema_migrations` | Applied forward database migrations |

The `/api/state` path deliberately selects document metadata rather than BLOB
content. Document and asset bytes are fetched only from their dedicated routes.

## Tests and safe review commands

Run the suite without contacting Mistral:

```powershell
python -m pytest tests -q
```

If only `uv` is available:

```powershell
$env:UV_CACHE_DIR = (Resolve-Path '.uv-cache').Path
uv run --with-requirements requirements.txt python -m pytest tests -q
```

The normal tests mock the external client. A live OCR test sends document
content to Mistral and may incur cost; do not run one merely as part of routine
code review.

High-value tests are:

- page claim and duplicate prevention;
- restart recovery and failed-page retry;
- kill-switch handling of an in-flight result;
- current-page promotion;
- raster-image submission for PDF pages;
- adaptive concurrency response to `429`;
- out-of-order Batch result mapping;
- partial structured-extraction failure preserving successful pages.

## Deferred production work

Before any public deployment, separately review authentication, CSRF, security
headers, secret management, production WSGI serving, tenant isolation, storage
limits, and background workers that survive multiple application processes.
Those concerns are intentionally outside this local-only milestone.
