from pathlib import Path

import pytest

from app import app
from src.services.database import Database
from src.services.ocr_jobs import OcrJobManager


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path):
    """Give every test a clean SQLite database without touching local app data."""
    database_path = tmp_path / "docslaju-test.sqlite3"
    app.config.update(TESTING=True, DATABASE=str(database_path))
    app.extensions["database"] = Database(database_path, Path(app.root_path) / "db" / "schema.sql")
    app.extensions["ocr_jobs"] = OcrJobManager()
    yield database_path
