from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


MAX_PDF_BYTES = 30 * 1024 * 1024
RENDER_TIMEOUT_SECONDS = 45


class BrowserPdfError(RuntimeError):
    """Raised when the installed browser cannot render a PDF."""


def _browser_candidates() -> list[Path]:
    configured = os.getenv("DOCSLAJU_BROWSER_PATH", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())

    for command in ("chrome", "google-chrome", "chromium", "msedge"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(Path(resolved))

    if os.name == "nt":
        roots = [
            os.getenv("PROGRAMFILES"),
            os.getenv("PROGRAMFILES(X86)"),
            os.getenv("LOCALAPPDATA"),
        ]
        for root in filter(None, roots):
            candidates.extend([
                Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ])

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve(strict=False)).lower()
        if key not in seen and candidate.is_file():
            seen.add(key)
            unique.append(candidate)
    return unique


def _render_with_browser(browser: Path, url: str, output: Path, profile: Path) -> None:
    arguments = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=8000",
        f"--user-data-dir={profile}",
        f"--print-to-pdf={output}",
        url,
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RENDER_TIMEOUT_SECONDS,
        creationflags=creationflags,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"browser exited with code {completed.returncode}"
        raise BrowserPdfError(message)
    if not output.is_file() or output.stat().st_size == 0:
        raise BrowserPdfError("browser finished without producing a PDF")


def render_url_to_pdf(url: str) -> bytes:
    """Render a trusted DocsLaju print URL using Chrome or Edge already installed."""
    browsers = _browser_candidates()
    if not browsers:
        raise BrowserPdfError("Google Chrome or Microsoft Edge is required for PDF export")

    temp_root = Path(tempfile.mkdtemp(prefix="docslaju-pdf-"))
    failures: list[str] = []
    try:
        for index, browser in enumerate(browsers):
            output = temp_root / f"render-{index}.pdf"
            profile = temp_root / f"profile-{index}"
            try:
                _render_with_browser(browser, url, output, profile)
                content = output.read_bytes()
                if len(content) > MAX_PDF_BYTES:
                    raise BrowserPdfError("The rendered PDF exceeds the 30 MB export limit")
                if not content.startswith(b"%PDF-"):
                    raise BrowserPdfError("The browser produced an invalid PDF")
                return content
            except (BrowserPdfError, OSError, subprocess.SubprocessError) as exc:
                failures.append(f"{browser.name}: {exc}")
        raise BrowserPdfError(
            "No installed browser could render the PDF. " + "; ".join(failures)
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
