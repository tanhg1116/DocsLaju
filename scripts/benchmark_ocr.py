"""Run a bounded, paid OCR throughput comparison against the real API.

The benchmark uses DocsLaju's actual Flask upload route, persistent queue,
PDF rasterizer, Mistral client, and scheduler. It never records OCR text in the
JSON report, but its isolated SQLite database contains the normal page results.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", type=Path, default=Path("tests/long_doc.pdf"))
    parser.add_argument(
        "--pages",
        type=int,
        default=64,
        help="Equivalent leading pages tested in each mode (default: 64).",
    )
    parser.add_argument(
        "--mode",
        choices=("realtime", "batch", "both"),
        default="both",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--realtime-concurrency",
        type=int,
        help="Benchmark-only fixed real-time concurrency (for example, 24).",
    )
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--timeout-minutes", type=float, default=30.0)
    parser.add_argument(
        "--database",
        type=Path,
        help="Isolated SQLite path; defaults to tmp/ocr-benchmark-<timestamp>.sqlite3.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON report path; defaults to output/benchmarks/<timestamp>.json.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required acknowledgement that this sends paid requests to Mistral.",
    )
    return parser.parse_args()


def _wait_for_job(database, job_id: str, poll_seconds: float, timeout_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_progress: tuple[int, int, int] | None = None
    while time.monotonic() < deadline:
        job = database.get_ocr_job(1, job_id)
        progress = (
            int(job["completed_pages"]),
            int(job["failed_pages"]),
            int(job["cancelled_pages"]),
        )
        if progress != last_progress:
            print(
                f"  {job['status']}: {progress[0]} completed, "
                f"{progress[1]} failed, {progress[2]} cancelled",
                flush=True,
            )
            last_progress = progress
        if job["status"] in TERMINAL_STATUSES:
            return job
        time.sleep(max(0.1, poll_seconds))
    raise TimeoutError(f"OCR job {job_id} exceeded the benchmark timeout")


def main() -> int:
    args = _arguments()
    if not args.confirm_live:
        raise SystemExit("Refusing paid API calls without --confirm-live")
    document_path = args.document.resolve()
    if not document_path.is_file():
        raise SystemExit(f"Document not found: {document_path}")
    if args.pages < 1 or args.batch_size < 1 or (
        args.realtime_concurrency is not None and args.realtime_concurrency < 1
    ):
        raise SystemExit("Page, batch, and concurrency values must be positive")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    database_path = (args.database or Path("tmp") / f"ocr-benchmark-{stamp}.sqlite3").resolve()
    output_path = (args.output or Path("output") / "benchmarks" / f"{stamp}.json").resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # These must be set before importing app.py because it constructs its
    # Database and OcrJobManager at module import time.
    os.environ["DOCSLAJU_DB_PATH"] = str(database_path)
    os.environ["DOCSLAJU_VALIDATE_UPLOADS"] = "true"

    from app import app  # pylint: disable=import-outside-toplevel
    from src.services.ocr_jobs import OcrJobManager  # pylint: disable=import-outside-toplevel

    database = app.extensions["database"]
    with app.test_client() as client:
        state = client.get("/api/state").get_json()
        session_id = state["active_session_id"]
        with document_path.open("rb") as source:
            response = client.post(
                f"/api/sessions/{session_id}/files",
                data={"file": (source, document_path.name)},
                content_type="multipart/form-data",
            )
        if response.status_code != 201:
            raise RuntimeError(f"Upload failed ({response.status_code}): {response.get_data(as_text=True)}")
        uploaded = response.get_json()["active_document"]
        total_pages = int(uploaded["num_pages"])
        page_count = min(args.pages, total_pages)
        page_range = f"1-{page_count}"
        document_id = str(uploaded["id"])

        modes = ["realtime", "batch"] if args.mode == "both" else [args.mode]
        results: list[dict] = []
        for mode in modes:
            print(f"Starting {mode} benchmark for pages {page_range}", flush=True)
            manager = OcrJobManager(
                database=database,
                initial_concurrency=(
                    args.realtime_concurrency if mode == "realtime" else None
                ),
                max_concurrency=(
                    args.realtime_concurrency if mode == "realtime" else None
                ),
                batch_enabled=(mode == "batch"),
                batch_min_pages=1,
                batch_size=args.batch_size,
            )
            app.extensions["ocr_jobs"] = manager
            previous_force = os.environ.get("OCR_BATCH_FORCE")
            if mode == "batch":
                os.environ["OCR_BATCH_FORCE"] = "true"
            else:
                os.environ.pop("OCR_BATCH_FORCE", None)
            try:
                started = time.monotonic()
                response = client.post(
                    f"/api/sessions/{session_id}/files/{document_id}/ocr-all",
                    json={"force": True, "page_range": page_range},
                )
                if response.status_code != 202:
                    raise RuntimeError(
                        f"Could not start {mode} job ({response.status_code}): "
                        f"{response.get_data(as_text=True)}"
                    )
                job_id = str(response.get_json()["id"])
                job = _wait_for_job(
                    database,
                    job_id,
                    args.poll_seconds,
                    args.timeout_minutes * 60.0,
                )
                elapsed = time.monotonic() - started
            finally:
                if previous_force is None:
                    os.environ.pop("OCR_BATCH_FORCE", None)
                else:
                    os.environ["OCR_BATCH_FORCE"] = previous_force

            completed = int(job["completed_pages"])
            achieved_ppm = completed * 60.0 / max(0.001, elapsed)
            result = {
                "mode": mode,
                "job_id": job_id,
                "status": job["status"],
                "requested_pages": page_count,
                "completed_pages": completed,
                "failed_pages": int(job["failed_pages"]),
                "duration_seconds": round(elapsed, 3),
                "achieved_pages_per_minute": round(achieved_ppm, 2),
                "target_utilization_percent": round(
                    achieved_ppm / max(1, manager.scheduler_status()["target_pages_per_minute"]) * 100,
                    2,
                ),
                "scheduler": manager.scheduler_status(),
            }
            results.append(result)
            print(json.dumps(result, indent=2), flush=True)

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "document": document_path.name,
        "document_bytes": document_path.stat().st_size,
        "document_pages": total_pages,
        "tested_pages_per_mode": page_count,
        "batch_size": args.batch_size,
        "realtime_concurrency": args.realtime_concurrency,
        "database": str(database_path),
        "results": results,
    }
    successful = [item for item in results if item["status"] == "completed"]
    if len(successful) == 2:
        fastest = min(successful, key=lambda item: item["duration_seconds"])
        report["comparison"] = {
            "faster_mode": fastest["mode"],
            "realtime_to_batch_speed_ratio": round(
                next(item["duration_seconds"] for item in successful if item["mode"] == "realtime")
                / max(0.001, next(item["duration_seconds"] for item in successful if item["mode"] == "batch")),
                3,
            ),
        }
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report written to {output_path}", flush=True)
    return 0 if len(successful) == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
