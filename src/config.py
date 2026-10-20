"""Validated operational settings for DocsLaju.

Defaults live here; deployment-specific values and secrets stay in environment
variables or the project-local ``.env`` file.  Program logic such as schemas,
regexes, and accounting categories deliberately does not belong in settings.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AppSettings(BaseSettings):
    """Runtime configuration validated whenever a service reads its settings."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    port: int = Field(default=5000, ge=1, le=65535)
    flask_secret_key: str | None = None
    docslaju_db_path: Path = PROJECT_ROOT / "instance" / "docslaju.sqlite3"
    docslaju_internal_base_url: str | None = None
    docslaju_validate_uploads: bool = True
    docslaju_browser_path: Path | None = None

    max_upload_bytes: int = Field(default=30 * 1024 * 1024, ge=1024)
    max_bank_csv_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    max_bank_rows: int = Field(default=10_000, ge=1)
    pdf_render_timeout_seconds: float = Field(default=45.0, ge=5.0, le=300.0)
    max_rendered_pdf_bytes: int = Field(default=30 * 1024 * 1024, ge=1024)

    mistral_api_key: str | None = None
    mistral_ocr_model: str = "mistral-ocr-latest"
    document_annotation_page_limit: int = Field(default=8, ge=1, le=100)
    mistral_ocr_pages_per_minute: int = Field(default=1250, ge=1)
    ocr_target_utilization: float = Field(default=0.88, ge=0.1, le=0.98)
    ocr_initial_concurrency: int = Field(default=4, ge=1, le=256)
    ocr_max_concurrency: int = Field(default=24, ge=1, le=256)
    ocr_max_attempts: int = Field(default=3, ge=1, le=10)
    ocr_batch_enabled: bool = True
    ocr_batch_force: bool = True
    pdf_ocr_dpi: int = Field(default=120, ge=72, le=600)
    ocr_batch_min_pages: int = Field(default=500, ge=1)
    ocr_batch_size: int = Field(default=32, ge=1, le=1000)

    structured_extraction_provider: Literal["auto", "deepseek", "mistral"] = "auto"
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_max_markdown_chars: int = Field(default=500_000, ge=10_000)
    deepseek_max_output_tokens: int = Field(default=8192, ge=512)
    deepseek_timeout_seconds: float = Field(default=90.0, ge=10.0, le=600.0)
    deepseek_max_retries: int = Field(default=1, ge=0, le=5)
    deepseek_empty_response_retries: int = Field(default=1, ge=0, le=2)
    max_structured_review_notes: int = Field(default=20, ge=1, le=100)

    image_analysis_max_side: int = Field(default=420, ge=128, le=2048)
    image_ocr_max_side: int = Field(default=3600, ge=512, le=8192)
    image_ocr_min_short_side: int = Field(default=1200, ge=256, le=4096)

    accounting_money_tolerance: Decimal = Field(default=Decimal("0.02"), ge=0)
    accounting_match_threshold: Decimal = Field(default=Decimal("0.60"), ge=0, le=1)
    bank_match_threshold: Decimal = Field(default=Decimal("0.65"), ge=0, le=1)

    openai_api_key: str | None = None
    legacy_export_ocr_delay_seconds: float = Field(default=1.0, ge=0.0, le=60.0)

    @model_validator(mode="after")
    def validate_related_limits(self) -> "AppSettings":
        if self.ocr_initial_concurrency > self.ocr_max_concurrency:
            raise ValueError("OCR_INITIAL_CONCURRENCY cannot exceed OCR_MAX_CONCURRENCY")
        if self.image_ocr_min_short_side > self.image_ocr_max_side:
            raise ValueError("IMAGE_OCR_MIN_SHORT_SIDE cannot exceed IMAGE_OCR_MAX_SIDE")
        return self

    @property
    def internal_base_url(self) -> str:
        value = self.docslaju_internal_base_url or f"http://127.0.0.1:{self.port}"
        return value.rstrip("/")


def load_settings() -> AppSettings:
    """Load current values, allowing tests to safely override the environment."""

    return AppSettings()
