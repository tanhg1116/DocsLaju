PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    project_id TEXT,
    title TEXT NOT NULL,
    active_document_id TEXT,
    is_pinned INTEGER NOT NULL DEFAULT 0 CHECK (is_pinned IN (0, 1)),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    content BLOB NOT NULL,
    mime_type TEXT NOT NULL,
    is_pdf INTEGER NOT NULL CHECK (is_pdf IN (0, 1)),
    num_pages INTEGER NOT NULL DEFAULT 1 CHECK (num_pages > 0),
    current_page INTEGER NOT NULL DEFAULT 1 CHECK (current_page > 0),
    checksum TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS document_pages (
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    source_markdown TEXT,
    markdown TEXT NOT NULL DEFAULT '',
    confidence_score REAL CHECK (confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)),
    review_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (review_status IN ('unreviewed', 'needs_review', 'approved')),
    reviewed_at TEXT,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (document_id, page_number),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- Search index for OCR text. Triggers keep it synchronized with page edits while
-- the source of truth remains document_pages.
CREATE VIRTUAL TABLE IF NOT EXISTS document_pages_fts USING fts5(
    document_id UNINDEXED,
    page_number UNINDEXED,
    markdown,
    tokenize = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS document_pages_fts_insert
AFTER INSERT ON document_pages BEGIN
    INSERT INTO document_pages_fts (document_id, page_number, markdown)
    VALUES (new.document_id, new.page_number, new.markdown);
END;

CREATE TRIGGER IF NOT EXISTS document_pages_fts_update
AFTER UPDATE OF markdown ON document_pages BEGIN
    DELETE FROM document_pages_fts
    WHERE document_id = old.document_id AND page_number = old.page_number;
    INSERT INTO document_pages_fts (document_id, page_number, markdown)
    VALUES (new.document_id, new.page_number, new.markdown);
END;

CREATE TRIGGER IF NOT EXISTS document_pages_fts_delete
AFTER DELETE ON document_pages BEGIN
    DELETE FROM document_pages_fts
    WHERE document_id = old.document_id AND page_number = old.page_number;
END;

CREATE TABLE IF NOT EXISTS document_assets (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    source_ref TEXT,
    object_type TEXT NOT NULL DEFAULT 'image',
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    content BLOB NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE (document_id, filename)
);

CREATE TABLE IF NOT EXISTS document_extractions (
    document_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    data_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'needs_review'
        CHECK (status IN ('needs_review', 'approved')),
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    PRIMARY KEY (document_id, profile),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS document_extraction_claims (
    document_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (document_id, profile),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ocr_jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'cancelling', 'cancelled', 'completed', 'failed', 'interrupted')),
    force_reprocess INTEGER NOT NULL DEFAULT 0 CHECK (force_reprocess IN (0, 1)),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ocr_job_pages (
    job_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (job_id, page_number),
    FOREIGN KEY (job_id) REFERENCES ocr_jobs(id) ON DELETE CASCADE
);

-- Atomic claim table shared by manual and batch OCR requests. Its primary key
-- prevents two workers from sending the same page to Mistral concurrently.
CREATE TABLE IF NOT EXISTS ocr_page_claims (
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    owner_token TEXT NOT NULL,
    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (document_id, page_number),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY,
    active_session_id TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (active_session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_user_sort
    ON projects (user_id, sort_order, name);

CREATE INDEX IF NOT EXISTS idx_sessions_user_state
    ON sessions (user_id, is_archived, is_pinned DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_project
    ON sessions (project_id, is_archived, is_pinned DESC, sort_order, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_documents_session
    ON documents (session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_document_assets_session
    ON document_assets (session_id, document_id, page_number);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ocr_jobs_one_active_document
    ON ocr_jobs (document_id)
    WHERE status IN ('queued', 'running', 'cancelling');

CREATE INDEX IF NOT EXISTS idx_ocr_jobs_user_status
    ON ocr_jobs (user_id, status, created_at DESC);

INSERT OR IGNORE INTO users (id, username, display_name, role)
VALUES (1, 'admin', 'Admin User', 'admin');

INSERT OR IGNORE INTO user_preferences (user_id, active_session_id)
VALUES (1, NULL);
