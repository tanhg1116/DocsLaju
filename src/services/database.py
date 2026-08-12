from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ADMIN_USER_ID = 1
EMBEDDED_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\((data:(image/[a-zA-Z0-9.+-]+);base64,([a-zA-Z0-9+/=\r\n]+))\)"
)


class Database:
    """SQLite repository for the local single-admin draft."""

    def __init__(self, path: str | Path, schema_path: str | Path) -> None:
        self.path = Path(path)
        self.schema_path = Path(schema_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        schema = self.schema_path.read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)
            document_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "checksum" not in document_columns:
                connection.execute("ALTER TABLE documents ADD COLUMN checksum TEXT")
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(ocr_job_pages)").fetchall()
            }
            if "priority" not in columns:
                connection.execute(
                    "ALTER TABLE ocr_job_pages ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"
                )
            page_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(document_pages)").fetchall()
            }
            if "source_markdown" not in page_columns:
                connection.execute("ALTER TABLE document_pages ADD COLUMN source_markdown TEXT")
            if "confidence_score" not in page_columns:
                connection.execute("ALTER TABLE document_pages ADD COLUMN confidence_score REAL")
            if "review_status" not in page_columns:
                connection.execute(
                    "ALTER TABLE document_pages ADD COLUMN review_status TEXT NOT NULL DEFAULT 'unreviewed'"
                )
            if "reviewed_at" not in page_columns:
                connection.execute("ALTER TABLE document_pages ADD COLUMN reviewed_at TEXT")
            extraction_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(document_extractions)").fetchall()
            }
            if "schema_version" not in extraction_columns:
                connection.execute(
                    "ALTER TABLE document_extractions ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1"
                )
            for row in connection.execute(
                "SELECT id, content FROM documents WHERE checksum IS NULL"
            ).fetchall():
                connection.execute(
                    "UPDATE documents SET checksum = ? WHERE id = ?",
                    (hashlib.sha256(bytes(row["content"])).hexdigest(), row["id"]),
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_documents_session_checksum
                ON documents (session_id, checksum) WHERE checksum IS NOT NULL
                """
            )
            connection.execute("DELETE FROM document_pages_fts")
            connection.execute(
                """
                INSERT INTO document_pages_fts (document_id, page_number, markdown)
                SELECT document_id, page_number, markdown FROM document_pages
                """
            )
            connection.execute("DELETE FROM document_extraction_claims")
            self._migrate_embedded_assets(connection)
        self.ensure_active_session(ADMIN_USER_ID)

    @staticmethod
    def _id() -> str:
        return secrets.token_hex(6)

    @staticmethod
    def _asset_stem(document_name: str) -> str:
        stem = Path(document_name).stem
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
        return cleaned or "document"

    @staticmethod
    def _asset_extension(mime_type: str) -> str:
        subtype = mime_type.partition("/")[2].lower()
        return {
            "jpeg": "jpg",
            "svg+xml": "svg",
            "x-icon": "ico",
        }.get(subtype, re.sub(r"[^a-z0-9]+", "", subtype) or "bin")

    def _migrate_embedded_assets(self, connection: sqlite3.Connection) -> None:
        """Move legacy Markdown data URLs into document-owned asset rows."""
        rows = connection.execute(
            """
            SELECT dp.document_id, dp.page_number, dp.markdown, dp.source_markdown,
                   d.name AS document_name, d.session_id
            FROM document_pages dp
            JOIN documents d ON d.id = dp.document_id
            WHERE dp.markdown LIKE '%data:image/%'
            """
        ).fetchall()
        for row in rows:
            asset_index = 0

            def replace(match: re.Match[str]) -> str:
                nonlocal asset_index
                try:
                    content = base64.b64decode(
                        re.sub(r"\s+", "", match.group(4)),
                        validate=True,
                    )
                except (ValueError, base64.binascii.Error):
                    return match.group(0)
                asset_index += 1
                mime_type = match.group(3).lower()
                extension = self._asset_extension(mime_type)
                filename = (
                    f"{self._asset_stem(row['document_name'])}-{row['page_number']}"
                    f"-image-{asset_index}.{extension}"
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO document_assets
                        (id, session_id, document_id, page_number, source_ref,
                         object_type, filename, mime_type, content, checksum)
                    VALUES (?, ?, ?, ?, ?, 'image', ?, ?, ?, ?)
                    """,
                    (
                        self._id(),
                        row["session_id"],
                        row["document_id"],
                        row["page_number"],
                        match.group(1),
                        filename,
                        mime_type,
                        content,
                        hashlib.sha256(content).hexdigest(),
                    ),
                )
                return f"![{match.group(1)}](assets/{filename})"

            migrated = EMBEDDED_IMAGE_RE.sub(replace, row["markdown"])
            if migrated != row["markdown"]:
                connection.execute(
                    """
                    UPDATE document_pages
                    SET source_markdown = COALESCE(source_markdown, markdown),
                        markdown = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE document_id = ? AND page_number = ?
                    """,
                    (migrated, row["document_id"], row["page_number"]),
                )

    def ensure_active_session(self, user_id: int) -> str:
        with self.transaction() as connection:
            preference = connection.execute(
                "SELECT active_session_id FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            active_id = preference["active_session_id"] if preference else None
            if active_id:
                active = connection.execute(
                    "SELECT id FROM sessions WHERE id = ? AND user_id = ? AND is_archived = 0",
                    (active_id, user_id),
                ).fetchone()
                if active:
                    return str(active["id"])

            next_session = connection.execute(
                """
                SELECT id FROM sessions
                WHERE user_id = ? AND is_archived = 0
                ORDER BY is_pinned DESC, last_opened_at DESC, created_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if next_session:
                active_id = str(next_session["id"])
            else:
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM sessions WHERE user_id = ?",
                    (user_id,),
                ).fetchone()["count"]
                active_id = self._id()
                connection.execute(
                    "INSERT INTO sessions (id, user_id, title) VALUES (?, ?, ?)",
                    (active_id, user_id, f"Session {count + 1}"),
                )
            connection.execute(
                """
                INSERT INTO user_preferences (user_id, active_session_id) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET active_session_id = excluded.active_session_id
                """,
                (user_id, active_id),
            )
            return active_id

    def get_user(self, user_id: int) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise KeyError("User not found")
        return dict(row)

    def get_project(self, user_id: int, project_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
        if not row:
            raise KeyError("Project not found")
        return dict(row)

    def create_project(self, user_id: int, name: str) -> str:
        project_id = self._id()
        with self.transaction() as connection:
            next_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM projects WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO projects (id, user_id, name, sort_order) VALUES (?, ?, ?, ?)",
                (project_id, user_id, name, next_order),
            )
        return project_id

    def rename_project(self, user_id: int, project_id: str, name: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE projects SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                (name, project_id, user_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("Project not found")

    def delete_project(self, user_id: int, project_id: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("Project not found")

    def create_session(self, user_id: int, project_id: str | None = None) -> str:
        session_id = self._id()
        with self.transaction() as connection:
            if project_id:
                project = connection.execute(
                    "SELECT id FROM projects WHERE id = ? AND user_id = ?",
                    (project_id, user_id),
                ).fetchone()
                if not project:
                    raise KeyError("Project not found")
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()["count"]
            connection.execute(
                "INSERT INTO sessions (id, user_id, project_id, title) VALUES (?, ?, ?, ?)",
                (session_id, user_id, project_id, f"Session {count + 1}"),
            )
            connection.execute(
                "UPDATE user_preferences SET active_session_id = ? WHERE user_id = ?",
                (session_id, user_id),
            )
        return session_id

    def get_session(self, user_id: int, session_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
        if not row:
            raise KeyError("OCR session not found")
        return dict(row)

    def activate_session(self, user_id: int, session_id: str) -> None:
        session = self.get_session(user_id, session_id)
        if session["is_archived"]:
            raise ValueError("Restore this session before opening it")
        with self.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET last_opened_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            connection.execute(
                "UPDATE user_preferences SET active_session_id = ? WHERE user_id = ?",
                (session_id, user_id),
            )

    def update_session(
        self,
        user_id: int,
        session_id: str,
        *,
        title: str | None = None,
        project_id: str | None | object = ...,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
    ) -> None:
        self.get_session(user_id, session_id)
        assignments: list[str] = []
        values: list[object] = []
        if title is not None:
            assignments.append("title = ?")
            values.append(title)
        if project_id is not ...:
            if project_id is not None:
                self.get_project(user_id, str(project_id))
            assignments.append("project_id = ?")
            values.append(project_id)
        if is_pinned is not None:
            assignments.append("is_pinned = ?")
            values.append(int(is_pinned))
        if is_archived is not None:
            assignments.append("is_archived = ?")
            values.append(int(is_archived))
            if is_archived:
                assignments.append("is_pinned = 0")
        if not assignments:
            return
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        values.extend((session_id, user_id))
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE sessions SET {', '.join(assignments)} WHERE id = ? AND user_id = ?",
                values,
            )
        if is_archived:
            self.ensure_active_session(user_id)

    def delete_session(self, user_id: int, session_id: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("OCR session not found")
        self.ensure_active_session(user_id)

    def delete_document(self, user_id: int, session_id: str, document_id: str) -> None:
        self.get_document(user_id, session_id, document_id)
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM documents WHERE id = ? AND session_id = ?",
                (document_id, session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("Document not found")
            replacement = connection.execute(
                "SELECT id FROM documents WHERE session_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            connection.execute(
                "UPDATE sessions SET active_document_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (replacement["id"] if replacement else None, session_id),
            )

    def add_document(
        self,
        user_id: int,
        session_id: str,
        *,
        name: str,
        content: bytes,
        mime_type: str,
        is_pdf: bool,
        num_pages: int,
    ) -> str:
        self.get_session(user_id, session_id)
        document_id = self._id()
        checksum = hashlib.sha256(content).hexdigest()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO documents
                    (id, session_id, name, content, mime_type, is_pdf, num_pages, checksum)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (document_id, session_id, name, content, mime_type, int(is_pdf), num_pages, checksum),
            )
            connection.execute(
                """
                UPDATE sessions
                SET active_document_id = ?, updated_at = CURRENT_TIMESTAMP, last_opened_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (document_id, session_id),
            )
            connection.execute(
                "UPDATE user_preferences SET active_session_id = ? WHERE user_id = ?",
                (session_id, user_id),
            )
        return document_id

    def find_document_by_checksum(
        self,
        user_id: int,
        session_id: str,
        content: bytes,
    ) -> dict | None:
        checksum = hashlib.sha256(content).hexdigest()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT d.* FROM documents d
                JOIN sessions s ON s.id = d.session_id
                WHERE d.session_id = ? AND d.checksum = ? AND s.user_id = ?
                LIMIT 1
                """,
                (session_id, checksum, user_id),
            ).fetchone()
        return dict(row) if row else None

    def get_document(self, user_id: int, session_id: str, document_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT d.* FROM documents d
                JOIN sessions s ON s.id = d.session_id
                WHERE d.id = ? AND d.session_id = ? AND s.user_id = ?
                """,
                (document_id, session_id, user_id),
            ).fetchone()
        if not row:
            raise KeyError("Document not found")
        document = dict(row)
        document["is_pdf"] = bool(document["is_pdf"])
        return document

    def activate_document(self, user_id: int, session_id: str, document_id: str) -> None:
        self.get_document(user_id, session_id, document_id)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET active_document_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (document_id, session_id),
            )
            connection.execute(
                "UPDATE user_preferences SET active_session_id = ? WHERE user_id = ?",
                (session_id, user_id),
            )

    def set_document_page(self, user_id: int, session_id: str, document_id: str, page_number: int) -> None:
        document = self.get_document(user_id, session_id, document_id)
        if page_number < 1 or page_number > document["num_pages"]:
            raise ValueError("Page is outside this document")
        with self.transaction() as connection:
            connection.execute(
                "UPDATE documents SET current_page = ? WHERE id = ?",
                (page_number, document_id),
            )

    def get_page_markdown(self, document_id: str, page_number: int) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT markdown FROM document_pages WHERE document_id = ? AND page_number = ?",
                (document_id, page_number),
            ).fetchone()
        return str(row["markdown"]) if row else None

    def get_page(self, document_id: str, page_number: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_pages WHERE document_id = ? AND page_number = ?",
                (document_id, page_number),
            ).fetchone()
        return dict(row) if row else None

    def count_document_ocr_pages(self, document_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM document_pages WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        return int(row["count"])

    def save_page_markdown(self, document_id: str, page_number: int, markdown: str) -> None:
        editable_markdown = str(markdown)
        source_markdown = getattr(markdown, "source_markdown", None)
        assets = getattr(markdown, "assets", None)
        confidence_score = getattr(markdown, "confidence_score", None)
        is_ocr_result = source_markdown is not None
        needs_review = not editable_markdown.strip() or (
            confidence_score is not None and float(confidence_score) < 0.80
        )
        review_status = "needs_review" if is_ocr_result and needs_review else "unreviewed"
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO document_pages
                    (document_id, page_number, source_markdown, markdown,
                     confidence_score, review_status, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(document_id, page_number) DO UPDATE SET
                    source_markdown = COALESCE(excluded.source_markdown, document_pages.source_markdown),
                    markdown = excluded.markdown,
                    confidence_score = CASE
                        WHEN excluded.source_markdown IS NOT NULL THEN excluded.confidence_score
                        ELSE document_pages.confidence_score
                    END,
                    review_status = excluded.review_status,
                    reviewed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document_id,
                    page_number,
                    source_markdown,
                    editable_markdown,
                    confidence_score,
                    review_status,
                ),
            )
            if assets is not None:
                document = connection.execute(
                    "SELECT session_id FROM documents WHERE id = ?",
                    (document_id,),
                ).fetchone()
                if not document:
                    raise KeyError("Document not found")
                connection.execute(
                    "DELETE FROM document_assets WHERE document_id = ? AND page_number = ?",
                    (document_id, page_number),
                )
                for asset in assets:
                    content = bytes(asset["content"])
                    connection.execute(
                        """
                        INSERT INTO document_assets
                            (id, session_id, document_id, page_number, source_ref,
                             object_type, filename, mime_type, content, checksum)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self._id(),
                            document["session_id"],
                            document_id,
                            page_number,
                            asset.get("source_ref"),
                            asset.get("object_type", "image"),
                            asset["filename"],
                            asset["mime_type"],
                            content,
                            hashlib.sha256(content).hexdigest(),
                        ),
                    )
            connection.execute(
                """
                UPDATE sessions SET updated_at = CURRENT_TIMESTAMP
                WHERE id = (SELECT session_id FROM documents WHERE id = ?)
                """,
                (document_id,),
            )

    def set_page_review_status(
        self,
        user_id: int,
        session_id: str,
        document_id: str,
        page_number: int,
        status: str,
    ) -> dict:
        if status not in {"unreviewed", "needs_review", "approved"}:
            raise ValueError("Unknown review status")
        document = self.get_document(user_id, session_id, document_id)
        if page_number < 1 or page_number > int(document["num_pages"]):
            raise ValueError("Page is outside this document")
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE document_pages
                SET review_status = ?,
                    reviewed_at = CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP ELSE NULL END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE document_id = ? AND page_number = ?
                """,
                (status, status, document_id, page_number),
            )
            if cursor.rowcount != 1:
                raise ValueError("Run OCR on this page before reviewing it")
        return self.get_page(document_id, page_number) or {}

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
        return " AND ".join(f'"{token}"*' for token in tokens[:12])

    def search_pages(self, user_id: int, query: str, limit: int = 30) -> list[dict]:
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.id AS session_id, s.title AS session_title,
                       p.name AS project_name, d.id AS document_id,
                       d.name AS document_name, CAST(f.page_number AS INTEGER) AS page_number,
                       snippet(document_pages_fts, 2, '', '', ' … ', 24) AS excerpt,
                       dp.review_status, dp.confidence_score
                FROM document_pages_fts f
                JOIN documents d ON d.id = f.document_id
                JOIN sessions s ON s.id = d.session_id
                LEFT JOIN projects p ON p.id = s.project_id
                JOIN document_pages dp
                  ON dp.document_id = f.document_id
                 AND dp.page_number = CAST(f.page_number AS INTEGER)
                WHERE document_pages_fts MATCH ? AND s.user_id = ?
                ORDER BY bm25(document_pages_fts), s.updated_at DESC
                LIMIT ?
                """,
                (fts_query, user_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def open_search_result(
        self,
        user_id: int,
        session_id: str,
        document_id: str,
        page_number: int,
    ) -> None:
        document = self.get_document(user_id, session_id, document_id)
        if page_number < 1 or page_number > int(document["num_pages"]):
            raise ValueError("Page is outside this document")
        with self.transaction() as connection:
            connection.execute(
                "UPDATE documents SET current_page = ? WHERE id = ?",
                (page_number, document_id),
            )
            connection.execute(
                """
                UPDATE sessions
                SET active_document_id = ?, updated_at = CURRENT_TIMESTAMP,
                    last_opened_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (document_id, session_id, user_id),
            )
            connection.execute(
                "UPDATE user_preferences SET active_session_id = ? WHERE user_id = ?",
                (session_id, user_id),
            )

    def get_document_asset(
        self,
        user_id: int,
        session_id: str,
        document_id: str,
        filename: str,
    ) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT a.* FROM document_assets a
                JOIN sessions s ON s.id = a.session_id
                WHERE a.session_id = ? AND a.document_id = ? AND a.filename = ?
                  AND s.user_id = ?
                """,
                (session_id, document_id, filename, user_id),
            ).fetchone()
        if not row:
            raise KeyError("Document asset not found")
        return dict(row)

    def list_document_assets(
        self,
        user_id: int,
        session_id: str,
        document_id: str,
    ) -> list[dict]:
        self.get_document(user_id, session_id, document_id)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM document_assets
                WHERE session_id = ? AND document_id = ?
                ORDER BY page_number, filename
                """,
                (session_id, document_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_document_extraction(
        self,
        user_id: int,
        session_id: str,
        document_id: str,
        profile: str,
    ) -> dict | None:
        self.get_document(user_id, session_id, document_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM document_extractions
                WHERE document_id = ? AND profile = ?
                """,
                (document_id, profile),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["data"] = json.loads(result.pop("data_json"))
        return result

    def save_document_extraction(
        self,
        user_id: int,
        session_id: str,
        document_id: str,
        profile: str,
        data: dict,
        model: str,
        *,
        schema_version: int = 1,
        status: str = "needs_review",
    ) -> dict:
        self.get_document(user_id, session_id, document_id)
        if status not in {"needs_review", "approved"}:
            raise ValueError("Unknown extraction review status")
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO document_extractions
                    (document_id, profile, data_json, schema_version, status, model, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP ELSE NULL END)
                ON CONFLICT(document_id, profile) DO UPDATE SET
                    data_json = excluded.data_json,
                    schema_version = excluded.schema_version,
                    status = excluded.status,
                    model = excluded.model,
                    reviewed_at = CASE
                        WHEN excluded.status = 'approved' THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (document_id, profile, payload, schema_version, status, model, status),
            )
        return self.get_document_extraction(
            user_id, session_id, document_id, profile
        ) or {}

    def claim_document_extraction(self, document_id: str, profile: str) -> bool:
        try:
            with self.transaction() as connection:
                connection.execute(
                    "INSERT INTO document_extraction_claims (document_id, profile) VALUES (?, ?)",
                    (document_id, profile),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def release_document_extraction(self, document_id: str, profile: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM document_extraction_claims WHERE document_id = ? AND profile = ?",
                (document_id, profile),
            )

    def recover_interrupted_ocr_jobs(self) -> list[dict]:
        """Reset unfinished work to a resumable queue after a process restart."""
        with self.transaction() as connection:
            connection.execute("DELETE FROM ocr_page_claims")
            connection.execute(
                """
                UPDATE ocr_job_pages
                SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP,
                    error = COALESCE(error, 'Cancelled before application restart')
                WHERE status IN ('queued', 'running')
                  AND job_id IN (
                      SELECT id FROM ocr_jobs
                      WHERE status = 'cancelling' OR cancel_requested = 1
                  )
                """
            )
            connection.execute(
                """
                UPDATE ocr_jobs
                SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP
                WHERE status = 'cancelling' OR
                      (status IN ('queued', 'running') AND cancel_requested = 1)
                """
            )
            resumable = connection.execute(
                """
                SELECT id, user_id FROM ocr_jobs
                WHERE status IN ('queued', 'running') AND cancel_requested = 0
                ORDER BY created_at
                """
            ).fetchall()
            connection.execute(
                """
                UPDATE ocr_job_pages
                SET status = 'queued', started_at = NULL, finished_at = NULL,
                    error = CASE WHEN status = 'running' THEN 'Resumed after application restart' ELSE error END
                WHERE status = 'running'
                  AND job_id IN (
                      SELECT id FROM ocr_jobs
                      WHERE status IN ('queued', 'running') AND cancel_requested = 0
                  )
                """
            )
            connection.execute(
                """
                UPDATE ocr_jobs
                SET status = 'queued', started_at = NULL, finished_at = NULL, error = NULL
                WHERE status IN ('queued', 'running') AND cancel_requested = 0
                """
            )
        return [dict(row) for row in resumable]

    def claim_ocr_page(self, document_id: str, page_number: int, owner_token: str) -> bool:
        try:
            with self.transaction() as connection:
                connection.execute(
                    "INSERT INTO ocr_page_claims (document_id, page_number, owner_token) VALUES (?, ?, ?)",
                    (document_id, page_number, owner_token),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def release_ocr_page(self, document_id: str, page_number: int, owner_token: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM ocr_page_claims WHERE document_id = ? AND page_number = ? AND owner_token = ?",
                (document_id, page_number, owner_token),
            )

    def create_ocr_job(
        self,
        user_id: int,
        session_id: str,
        document_id: str,
        *,
        force: bool = False,
        page_numbers: list[int] | None = None,
    ) -> str:
        document = self.get_document(user_id, session_id, document_id)
        requested_pages = (
            page_numbers
            if page_numbers is not None
            else range(1, int(document["num_pages"]) + 1)
        )
        selected_pages = sorted(set(requested_pages))
        if not selected_pages or selected_pages[0] < 1 or selected_pages[-1] > int(document["num_pages"]):
            raise ValueError("OCR job pages are outside this document")
        job_id = self._id()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO ocr_jobs (id, user_id, session_id, document_id, force_reprocess)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, user_id, session_id, document_id, int(force)),
            )
            completed = set()
            if not force:
                completed = {
                    int(row["page_number"])
                    for row in connection.execute(
                        "SELECT page_number FROM document_pages WHERE document_id = ?",
                        (document_id,),
                    ).fetchall()
                }
            connection.executemany(
                "INSERT INTO ocr_job_pages (job_id, page_number, status, finished_at) VALUES (?, ?, ?, ?)",
                [
                    (
                        job_id,
                        page_number,
                        "completed" if page_number in completed else "queued",
                        None,
                    )
                    for page_number in selected_pages
                ],
            )
            if completed:
                connection.execute(
                    "UPDATE ocr_job_pages SET finished_at = CURRENT_TIMESTAMP WHERE job_id = ? AND status = 'completed'",
                    (job_id,),
                )
        return job_id

    def get_ocr_job(self, user_id: int, job_id: str) -> dict:
        with self.connect() as connection:
            job = connection.execute(
                "SELECT * FROM ocr_jobs WHERE id = ? AND user_id = ?",
                (job_id, user_id),
            ).fetchone()
            if not job:
                raise KeyError("OCR job not found")
            pages = connection.execute(
                "SELECT page_number, priority, status, error FROM ocr_job_pages WHERE job_id = ? ORDER BY page_number",
                (job_id,),
            ).fetchall()
        payload = dict(job)
        payload["force_reprocess"] = bool(payload["force_reprocess"])
        payload["cancel_requested"] = bool(payload["cancel_requested"])
        payload["pages"] = [dict(row) for row in pages]
        payload["total_pages"] = len(pages)
        payload["completed_pages"] = sum(row["status"] == "completed" for row in pages)
        payload["failed_pages"] = sum(row["status"] == "failed" for row in pages)
        payload["cancelled_pages"] = sum(row["status"] == "cancelled" for row in pages)
        running = next((row["page_number"] for row in pages if row["status"] == "running"), None)
        payload["current_page"] = running
        return payload

    def get_active_ocr_job(self, user_id: int, document_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM ocr_jobs
                WHERE user_id = ? AND document_id = ? AND status IN ('queued', 'running', 'cancelling')
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, document_id),
            ).fetchone()
        return self.get_ocr_job(user_id, str(row["id"])) if row else None

    def get_latest_ocr_job(self, user_id: int, document_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM ocr_jobs
                WHERE user_id = ? AND document_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, document_id),
            ).fetchone()
        return self.get_ocr_job(user_id, str(row["id"])) if row else None

    def retry_failed_ocr_job(self, user_id: int, job_id: str) -> str:
        job = self.get_ocr_job(user_id, job_id)
        if job["status"] in {"queued", "running", "cancelling"}:
            return job_id
        failed_pages = [
            int(page["page_number"])
            for page in job["pages"]
            if page["status"] in {"failed", "cancelled"}
        ]
        if not failed_pages:
            raise ValueError("This OCR job has no failed pages to retry")
        return self.create_ocr_job(
            user_id,
            str(job["session_id"]),
            str(job["document_id"]),
            force=True,
            page_numbers=failed_pages,
        )

    def mark_ocr_job_running(self, job_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE ocr_jobs SET status = 'running', started_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'queued'",
                (job_id,),
            )

    def request_ocr_job_cancel(self, user_id: int, job_id: str) -> dict:
        self.get_ocr_job(user_id, job_id)
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE ocr_jobs SET cancel_requested = 1,
                    status = CASE WHEN status IN ('queued', 'running') THEN 'cancelling' ELSE status END
                WHERE id = ? AND user_id = ?
                """,
                (job_id, user_id),
            )
        return self.get_ocr_job(user_id, job_id)

    def is_ocr_job_cancel_requested(self, job_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM ocr_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return not row or bool(row["cancel_requested"])

    def dequeue_next_ocr_job_page(self, job_id: str) -> dict | None:
        """Atomically promote the highest-priority queued page to running."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT page_number, priority FROM ocr_job_pages
                WHERE job_id = ? AND status = 'queued'
                ORDER BY priority DESC, page_number ASC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if not row:
                connection.rollback()
                return None
            connection.execute(
                """
                UPDATE ocr_job_pages SET status = 'running', priority = 0,
                    error = NULL, started_at = CURRENT_TIMESTAMP, finished_at = NULL
                WHERE job_id = ? AND page_number = ? AND status = 'queued'
                """,
                (job_id, row["page_number"]),
            )
            connection.commit()
            return dict(row)
        finally:
            connection.close()

    def prioritize_ocr_job_page(self, user_id: int, job_id: str, page_number: int) -> dict:
        with self.transaction() as connection:
            job = connection.execute(
                """
                SELECT j.id, d.num_pages FROM ocr_jobs j
                JOIN documents d ON d.id = j.document_id
                WHERE j.id = ? AND j.user_id = ? AND j.status IN ('queued', 'running')
                """,
                (job_id, user_id),
            ).fetchone()
            if not job:
                raise ValueError("Automatic OCR is no longer running")
            if page_number < 1 or page_number > int(job["num_pages"]):
                raise ValueError("Page is outside this document")
            page = connection.execute(
                "SELECT status FROM ocr_job_pages WHERE job_id = ? AND page_number = ?",
                (job_id, page_number),
            ).fetchone()
            if not page:
                connection.execute(
                    """
                    INSERT INTO ocr_job_pages (job_id, page_number, priority, status)
                    VALUES (?, ?, 1000, 'queued')
                    """,
                    (job_id, page_number),
                )
            elif page["status"] != "running":
                connection.execute(
                    """
                    UPDATE ocr_job_pages
                    SET status = 'queued', priority = 1000, error = NULL,
                        started_at = NULL, finished_at = NULL
                    WHERE job_id = ? AND page_number = ?
                    """,
                    (job_id, page_number),
                )
        return self.get_ocr_job(user_id, job_id)

    def mark_ocr_job_page(self, job_id: str, page_number: int, status: str, error: str | None = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE ocr_job_pages SET status = ?, error = ?,
                    priority = CASE WHEN ? = 'running' THEN 0 ELSE priority END,
                    started_at = CASE WHEN ? = 'running' THEN CURRENT_TIMESTAMP ELSE started_at END,
                    finished_at = CASE WHEN ? IN ('completed', 'failed', 'cancelled') THEN CURRENT_TIMESTAMP ELSE finished_at END
                WHERE job_id = ? AND page_number = ?
                """,
                (status, error, status, status, status, job_id, page_number),
            )

    def finalize_ocr_job_if_idle(self, job_id: str) -> bool:
        """Atomically finish a job only when no page can still be promoted or run."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            pending = connection.execute(
                "SELECT 1 FROM ocr_job_pages WHERE job_id = ? AND status IN ('queued', 'running') LIMIT 1",
                (job_id,),
            ).fetchone()
            if pending:
                connection.rollback()
                return False
            failed = connection.execute(
                "SELECT COUNT(*) AS count FROM ocr_job_pages WHERE job_id = ? AND status = 'failed'",
                (job_id,),
            ).fetchone()["count"]
            status = "failed" if failed else "completed"
            error = f"{failed} page(s) failed" if failed else None
            cursor = connection.execute(
                """
                UPDATE ocr_jobs SET status = ?, error = ?, finished_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('queued', 'running')
                """,
                (status, error, job_id),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def finish_ocr_job(self, job_id: str, status: str, error: str | None = None) -> None:
        with self.transaction() as connection:
            if status == "cancelled":
                connection.execute(
                    """
                    UPDATE ocr_job_pages SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP
                    WHERE job_id = ? AND status = 'queued'
                    """,
                    (job_id,),
                )
            connection.execute(
                "UPDATE ocr_jobs SET status = ?, error = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, error, job_id),
            )

    def state(self, user_id: int, model: str) -> dict:
        active_session_id = self.ensure_active_session(user_id)
        with self.connect() as connection:
            user = dict(connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
            project_rows = connection.execute(
                """
                SELECT p.*, COUNT(s.id) AS session_count
                FROM projects p
                LEFT JOIN sessions s ON s.project_id = p.id AND s.is_archived = 0
                WHERE p.user_id = ?
                GROUP BY p.id
                ORDER BY p.sort_order, p.name
                """,
                (user_id,),
            ).fetchall()
            session_rows = connection.execute(
                """
                SELECT * FROM sessions
                WHERE user_id = ?
                ORDER BY is_archived, is_pinned DESC, sort_order, updated_at DESC
                """,
                (user_id,),
            ).fetchall()

            sessions = []
            active_document = None
            for session_row in session_rows:
                session = dict(session_row)
                document_rows = connection.execute(
                    "SELECT * FROM documents WHERE session_id = ? ORDER BY created_at",
                    (session["id"],),
                ).fetchall()
                files = []
                for document_row in document_rows:
                    completed = connection.execute(
                        """
                        SELECT page_number, review_status FROM document_pages
                        WHERE document_id = ? ORDER BY page_number
                        """,
                        (document_row["id"],),
                    ).fetchall()
                    files.append({
                        "id": document_row["id"],
                        "name": document_row["name"],
                        "is_pdf": bool(document_row["is_pdf"]),
                        "num_pages": document_row["num_pages"],
                        "current_page": document_row["current_page"],
                        "completed_pages": [row["page_number"] for row in completed],
                        "approved_pages": [
                            row["page_number"] for row in completed
                            if row["review_status"] == "approved"
                        ],
                        "needs_review_pages": [
                            row["page_number"] for row in completed
                            if row["review_status"] == "needs_review"
                        ],
                    })
                sessions.append({
                    "id": session["id"],
                    "title": session["title"],
                    "project_id": session["project_id"],
                    "is_pinned": bool(session["is_pinned"]),
                    "is_archived": bool(session["is_archived"]),
                    "active_file_id": session["active_document_id"],
                    "updated_at": session["updated_at"],
                    "files": files,
                })

                if session["id"] == active_session_id and session["active_document_id"]:
                    document = connection.execute(
                        "SELECT * FROM documents WHERE id = ? AND session_id = ?",
                        (session["active_document_id"], session["id"]),
                    ).fetchone()
                    if document:
                        page = document["current_page"]
                        page_row = connection.execute(
                            """
                            SELECT markdown, confidence_score, review_status, reviewed_at
                            FROM document_pages
                            WHERE document_id = ? AND page_number = ?
                            """,
                            (document["id"], page),
                        ).fetchone()
                        active_document = {
                            "id": document["id"],
                            "name": document["name"],
                            "is_pdf": bool(document["is_pdf"]),
                            "num_pages": document["num_pages"],
                            "current_page": page,
                            "markdown": page_row["markdown"] if page_row else "",
                            "has_ocr": page_row is not None,
                            "confidence_score": page_row["confidence_score"] if page_row else None,
                            "review_status": page_row["review_status"] if page_row else None,
                            "reviewed_at": page_row["reviewed_at"] if page_row else None,
                        }

        payload = {
            "user": {
                "id": user["id"],
                "username": user["username"],
                "display_name": user["display_name"],
                "role": user["role"],
            },
            "active_session_id": active_session_id,
            "projects": [dict(row) for row in project_rows],
            "sessions": sessions,
            "active_document": active_document,
            "model": model,
        }
        payload["active_ocr_job"] = (
            self.get_active_ocr_job(user_id, active_document["id"])
            if active_document
            else None
        )
        payload["recent_ocr_job"] = (
            self.get_latest_ocr_job(user_id, active_document["id"])
            if active_document
            else None
        )
        return payload
