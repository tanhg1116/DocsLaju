CREATE TABLE IF NOT EXISTS document_extraction_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    data_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('needs_review', 'approved')),
    origin TEXT NOT NULL CHECK (origin IN ('model', 'user', 'legacy')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE (document_id, profile, revision)
);

CREATE INDEX IF NOT EXISTS idx_extraction_revisions_document
    ON document_extraction_revisions (document_id, profile, revision DESC);

INSERT INTO document_extraction_revisions
    (document_id, profile, revision, data_json, status, origin, created_at)
SELECT document_id, profile, 1, data_json, status, 'legacy', created_at
FROM document_extractions
WHERE NOT EXISTS (
    SELECT 1 FROM document_extraction_revisions revisions
    WHERE revisions.document_id = document_extractions.document_id
      AND revisions.profile = document_extractions.profile
);
