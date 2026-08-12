# DocsLaju database

The Flask draft uses one local SQLite database. It seeds a single `admin` user so
the app can persist user-owned sessions before authentication is introduced.

## Repository tree

```text
db/
├── README.md                  Database layout and ownership notes
└── schema.sql                 Tables, constraints, indexes, and admin seed
src/services/
└── database.py               SQLite repository used by Flask routes
instance/
└── docslaju.sqlite3           Runtime data (created automatically; Git-ignored)
```

## Data relationships

```text
users
├── projects
├── sessions
│   └── documents
│       ├── document_pages
│       │   └── document_pages_fts (search index)
│       ├── document_assets
│       ├── document_extractions
│       ├── document_extraction_claims
│       ├── ocr_jobs
│       │   └── ocr_job_pages
│       └── ocr_page_claims
└── user_preferences ── active session
```

- A project is owned by one user. Deleting a project keeps its sessions and moves
  them to **Unfiled** with `ON DELETE SET NULL`.
- A session is owned by one user and may belong to one project. Pin and archive
  state are stored on the session.
- Uploaded file bytes are stored in `documents` for this local testing phase.
- OCR Markdown is stored per document page, so edits survive a Flask restart.
- Page rows also store OCR confidence and a review state: `unreviewed`,
  `needs_review`, or `approved`. Editing an approved page returns it to review.
- `document_pages_fts` is an SQLite FTS5 index synchronized by triggers. Search
  results retain the owning session, document, and page for direct navigation.
- Extracted document objects are stored as BLOBs in `document_assets`. Each row
  belongs to a session, document, and page; Markdown uses portable
  `assets/{filename}` references instead of embedded data URLs.
- Template-driven structured results are stored as JSON in
  `document_extractions`, independently from raw OCR Markdown. The template id,
  schema version, review status, and model are retained with each result.
- `document_extraction_claims` prevents duplicate simultaneous structured API
  requests for the same document and profile.
- `document_pages.source_markdown` preserves the OCR response while `markdown`
  holds the editable version with short asset references.
- Batch progress is stored in `ocr_jobs` and `ocr_job_pages`. A page whose HTTP
  request was interrupted is returned to the queue when Flask restarts; completed
  pages remain completed, and cancelled jobs remain cancelled.
- Every job page has a numeric priority. The worker atomically dequeues by
  `priority DESC, page_number ASC`, allowing a manually requested current page to
  jump ahead of the remaining automatic queue without duplicating work.
- `ocr_page_claims` has a `(document_id, page_number)` primary key. Manual and
  batch OCR must acquire it atomically before calling Mistral, preventing duplicate
  inference for the same page.
- `documents.checksum` stores a SHA-256 digest used to avoid duplicate uploads
  within one session.
- Deleting a session cascades to its documents and page results.
- Deleting a document cascades to its Markdown, extracted assets, jobs, job
  pages, and page claims.

See [`schema.sql`](schema.sql) for the executable schema.
