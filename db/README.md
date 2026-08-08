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
│       ├── document_assets
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
- Extracted document objects are stored as BLOBs in `document_assets`. Each row
  belongs to a session, document, and page; Markdown uses portable
  `assets/{filename}` references instead of embedded data URLs.
- `document_pages.source_markdown` preserves the OCR response while `markdown`
  holds the editable version with short asset references.
- Batch progress is stored in `ocr_jobs` and `ocr_job_pages`. Incomplete jobs are
  marked `interrupted` when Flask restarts because an upstream HTTP call cannot
  survive the Python process.
- Every job page has a numeric priority. The worker atomically dequeues by
  `priority DESC, page_number ASC`, allowing a manually requested current page to
  jump ahead of the remaining automatic queue without duplicating work.
- `ocr_page_claims` has a `(document_id, page_number)` primary key. Manual and
  batch OCR must acquire it atomically before calling Mistral, preventing duplicate
  inference for the same page.
- Deleting a session cascades to its documents and page results.
- Deleting a document cascades to its Markdown, extracted assets, jobs, job
  pages, and page claims.

See [`schema.sql`](schema.sql) for the executable schema.
