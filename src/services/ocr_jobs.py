"""Persistent OCR orchestration, adaptive throttling, retries, and cancellation.

The queue is durable in SQLite; threads are only executors. Review the lifecycle
and invariants in docs/REVIEW_GUIDE.md before changing dispatch behavior.
"""

from __future__ import annotations

import os
import random
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from collections.abc import Callable

from src.services.database import Database


PageProcessor = Callable[[dict, int], str]
BatchPageProcessor = Callable[
    [dict, list[int], threading.Event],
    tuple[dict[int, str | Exception], str | None, float],
]


@dataclass(frozen=True)
class OcrAttemptOutcome:
    success: bool
    duration_seconds: float
    rate_limited: bool = False


def _integer_setting(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _float_setting(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        return min(maximum, max(minimum, float(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _error_status(error: Exception) -> int | None:
    for candidate in (
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _retry_after(error: Exception) -> float | None:
    headers = getattr(getattr(error, "response", None), "headers", None)
    if not headers:
        return None
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _classify_error(error: Exception) -> tuple[str, bool, bool]:
    status = _error_status(error)
    message = str(error).lower()
    if status == 429 or "rate limit" in message or "too many requests" in message:
        return "rate_limited", True, True
    if status is not None and 500 <= status <= 599:
        return f"upstream_{status}", True, False
    if isinstance(error, (TimeoutError, ConnectionError)) or any(
        marker in message for marker in ("timed out", "timeout", "connection reset", "temporarily unavailable")
    ):
        return "transient_network", True, False
    if status is not None and 400 <= status <= 499:
        return f"request_{status}", False, False
    return "ocr_error", False, False


class AdaptiveRateController:
    """Shared page-rate and concurrency controller for all local OCR jobs.

    Rate invariant: all local jobs share this controller because Mistral applies
    the page quota at organization level, not independently per document.
    """

    def __init__(
        self,
        *,
        pages_per_minute: int | None = None,
        target_utilization: float | None = None,
        initial_concurrency: int | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self.pages_per_minute = pages_per_minute or _integer_setting(
            "MISTRAL_OCR_PAGES_PER_MINUTE", 1250
        )
        self.target_utilization = target_utilization or _float_setting(
            "OCR_TARGET_UTILIZATION", 0.88, 0.1, 0.98
        )
        configured_initial = initial_concurrency or _integer_setting(
            "OCR_INITIAL_CONCURRENCY", 4
        )
        configured_maximum = max_concurrency or _integer_setting(
            "OCR_MAX_CONCURRENCY", 24
        )
        self.initial_concurrency = min(configured_initial, configured_maximum)
        self.max_concurrency = max(self.initial_concurrency, configured_maximum)
        self.concurrency = self.initial_concurrency
        self._refill_per_second = self.pages_per_minute * self.target_utilization / 60.0
        self._token_capacity = float(max(2, self.max_concurrency))
        self._tokens = float(self.initial_concurrency)
        self._last_refill = time.monotonic()
        self._blocked_until = 0.0
        self._active = 0
        self._success_window = 0
        self._realtime_samples = 0
        self._rate_limit_events = 0
        self._latency_ewma: float | None = None
        self._throughput_window_started: float | None = None
        self._last_window_throughput: float | None = None
        self._lock = threading.Lock()

    @property
    def latency_ewma(self) -> float | None:
        with self._lock:
            return self._latency_ewma

    @property
    def throughput_pages_per_second(self) -> float | None:
        with self._lock:
            return self._last_window_throughput

    def snapshot(self) -> dict:
        """Return restart-safe learned values; token timing remains process-local."""
        with self._lock:
            return {
                "pages_per_minute_limit": self.pages_per_minute,
                "target_utilization": self.target_utilization,
                "current_concurrency": self.concurrency,
                "realtime_latency_ewma": self._latency_ewma,
                "realtime_throughput_pps": self._last_window_throughput,
                "realtime_samples": self._realtime_samples,
                "rate_limit_events": self._rate_limit_events,
            }

    def restore(self, metrics: dict) -> None:
        """Restore aggregates while respecting the current environment limits."""
        with self._lock:
            same_limit = int(metrics.get("pages_per_minute_limit") or 0) == self.pages_per_minute
            same_target = abs(
                float(metrics.get("target_utilization") or 0.0) - self.target_utilization
            ) < 0.0001
            if same_limit and same_target:
                self.concurrency = min(
                    self.max_concurrency,
                    max(1, int(metrics.get("current_concurrency") or self.initial_concurrency)),
                )
                throughput = metrics.get("realtime_throughput_pps")
                self._last_window_throughput = float(throughput) if throughput is not None else None
            latency = metrics.get("realtime_latency_ewma")
            self._latency_ewma = float(latency) if latency is not None else None
            self._realtime_samples = max(0, int(metrics.get("realtime_samples") or 0))
            self._rate_limit_events = max(0, int(metrics.get("rate_limit_events") or 0))

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(
            self._token_capacity,
            self._tokens + elapsed * self._refill_per_second,
        )
        self._last_refill = now

    def try_reserve(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            if now < self._blocked_until or self._active >= self.concurrency or self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            self._active += 1
            return True

    def wait_for_retry_token(self, cancellation: threading.Event) -> bool:
        while not cancellation.is_set():
            with self._lock:
                now = time.monotonic()
                self._refill(now)
                if now >= self._blocked_until and self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                wait_for = max(0.02, min(0.25, self._blocked_until - now))
            cancellation.wait(wait_for)
        return False

    def note_rate_limit(self, retry_after: float | None) -> None:
        with self._lock:
            self.concurrency = max(1, self.concurrency // 2)
            self._rate_limit_events += 1
            self._success_window = 0
            self._throughput_window_started = None
            delay = retry_after if retry_after is not None else 1.0
            self._blocked_until = max(self._blocked_until, time.monotonic() + max(0.25, delay))

    def finish(self, outcome: OcrAttemptOutcome) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
            if outcome.duration_seconds > 0:
                self._realtime_samples += 1
                self._latency_ewma = (
                    outcome.duration_seconds
                    if self._latency_ewma is None
                    else self._latency_ewma * 0.8 + outcome.duration_seconds * 0.2
                )
            if outcome.rate_limited:
                return
            if outcome.success:
                now = time.monotonic()
                if self._throughput_window_started is None:
                    self._throughput_window_started = now
                self._success_window += 1
                # Additive increase is deliberately slow. It only expands when
                # enough work has completed to demonstrate a stable window.
                if self._success_window >= max(8, self.concurrency * 2):
                    elapsed = max(0.001, now - self._throughput_window_started)
                    throughput = self._success_window / elapsed
                    improved = (
                        self._last_window_throughput is None
                        or throughput >= self._last_window_throughput * 1.05
                    )
                    self._last_window_throughput = throughput
                    if improved and self.concurrency < self.max_concurrency:
                        self.concurrency += 1
                    self._success_window = 0
                    self._throughput_window_started = None
            elif outcome.duration_seconds > 0:
                self._success_window = 0
                self._throughput_window_started = None


class OcrJobManager:
    """Runs prioritized, cancellable OCR queues with a shared adaptive limit."""

    def __init__(
        self,
        *,
        database: Database | None = None,
        initial_concurrency: int | None = None,
        max_concurrency: int | None = None,
        max_attempts: int | None = None,
        batch_enabled: bool | None = None,
        batch_min_pages: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._workers: dict[str, tuple[threading.Thread, threading.Event, str]] = {}
        self.rate_controller = AdaptiveRateController(
            initial_concurrency=initial_concurrency,
            max_concurrency=max_concurrency,
        )
        self.max_attempts = max_attempts or _integer_setting("OCR_MAX_ATTEMPTS", 3)
        self.batch_enabled = (
            batch_enabled
            if batch_enabled is not None
            else os.getenv("OCR_BATCH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        )
        self.batch_min_pages = batch_min_pages or _integer_setting(
            "OCR_BATCH_MIN_PAGES", 500
        )
        self.batch_size = batch_size or _integer_setting("OCR_BATCH_SIZE", 8)
        self._batch_seconds_per_page: float | None = None
        self._batch_samples = 0
        self._telemetry_database_key: str | None = None
        self._last_telemetry_flush = 0.0
        if database is not None:
            self.bind_database(database)

    def bind_database(self, database: Database) -> None:
        """Load persisted learning once for this database-backed account."""
        database_key = str(database.path.resolve())
        with self._lock:
            if self._telemetry_database_key == database_key:
                return
        metrics = database.get_scheduler_metrics()
        if metrics:
            self.rate_controller.restore(metrics)
            with self._lock:
                batch_value = metrics.get("batch_seconds_per_page")
                self._batch_seconds_per_page = (
                    float(batch_value) if batch_value is not None else None
                )
                self._batch_samples = max(0, int(metrics.get("batch_samples") or 0))
        with self._lock:
            self._telemetry_database_key = database_key

    def _persist_telemetry(self, database: Database, *, force: bool = False) -> None:
        """Checkpoint aggregates periodically and at every worker shutdown."""
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_telemetry_flush < 5.0:
                return
            batch_seconds = self._batch_seconds_per_page
            batch_samples = self._batch_samples
            self._last_telemetry_flush = now
        payload = self.rate_controller.snapshot()
        payload.update({
            "batch_seconds_per_page": batch_seconds,
            "batch_samples": batch_samples,
        })
        database.save_scheduler_metrics(payload)

    def scheduler_status(self) -> dict:
        controller = self.rate_controller.snapshot()
        with self._lock:
            batch_seconds = self._batch_seconds_per_page
            batch_samples = self._batch_samples
        return {
            "pages_per_minute_limit": self.rate_controller.pages_per_minute,
            "target_pages_per_minute": round(
                self.rate_controller.pages_per_minute
                * self.rate_controller.target_utilization
            ),
            "current_concurrency": self.rate_controller.concurrency,
            "max_concurrency": self.rate_controller.max_concurrency,
            "average_page_seconds": (
                round(self.rate_controller.latency_ewma, 3)
                if self.rate_controller.latency_ewma is not None
                else None
            ),
            "observed_pages_per_second": (
                round(self.rate_controller.throughput_pages_per_second, 3)
                if self.rate_controller.throughput_pages_per_second is not None
                else None
            ),
            "realtime_samples": controller["realtime_samples"],
            "rate_limit_events": controller["rate_limit_events"],
            "batch_enabled": self.batch_enabled,
            "batch_min_pages": self.batch_min_pages,
            "batch_size": self.batch_size,
            "batch_seconds_per_page": (
                round(batch_seconds, 3)
                if batch_seconds is not None
                else None
            ),
            "batch_samples": batch_samples,
        }

    def start(
        self,
        database: Database,
        user_id: int,
        job_id: str,
        processor: PageProcessor,
        batch_processor: BatchPageProcessor | None = None,
    ) -> None:
        self.bind_database(database)
        job = database.get_ocr_job(user_id, job_id)
        cancellation = threading.Event()
        worker = threading.Thread(
            target=self._run,
            args=(database, user_id, job_id, cancellation, processor, batch_processor),
            name=f"ocr-job-{job_id}",
            daemon=True,
        )
        with self._lock:
            existing = self._workers.get(job_id)
            if existing and existing[0].is_alive():
                return
            self._workers[job_id] = (worker, cancellation, str(job["document_id"]))
        worker.start()

    def cancel(self, job_id: str) -> None:
        with self._lock:
            worker = self._workers.get(job_id)
            if worker:
                worker[1].set()

    def cancel_document(self, document_id: str) -> None:
        with self._lock:
            for _, cancellation, worker_document_id in self._workers.values():
                if worker_document_id == document_id:
                    cancellation.set()

    def _choose_mode(self, pending_pages: int, batch_processor: BatchPageProcessor | None) -> str:
        """Choose the predicted faster provider path without delaying normal work.

        Latency: real-time remains the default until a genuinely large queue can
        bootstrap Batch telemetry. Later choices compare observed throughput.
        """
        if not self.batch_enabled or batch_processor is None:
            return "realtime"
        if os.getenv("OCR_BATCH_FORCE", "false").strip().lower() in {"1", "true", "yes", "on"}:
            return "batch"
        latency = self.rate_controller.latency_ewma or 5.0
        calculated_realtime_rate = min(
            self.rate_controller.pages_per_minute
            * self.rate_controller.target_utilization
            / 60.0,
            max(1, self.rate_controller.concurrency) / max(0.05, latency),
        )
        realtime_rate = min(
            calculated_realtime_rate,
            self.rate_controller.throughput_pages_per_second
            or calculated_realtime_rate,
        )
        realtime_eta = pending_pages / max(0.01, realtime_rate)
        with self._lock:
            batch_seconds_per_page = self._batch_seconds_per_page
        if batch_seconds_per_page is not None:
            if pending_pages < self.batch_min_pages:
                return "realtime"
            batch_eta = 5.0 + pending_pages * batch_seconds_per_page
            return "batch" if batch_eta < realtime_eta * 0.9 else "realtime"
        # Do not guess that a large queue makes Batch faster. The long_doc.pdf
        # calibration showed that provider queue and local preparation overhead
        # can make Batch slower even for dozens of pages. A forced benchmark can
        # establish durable Batch telemetry; only then may adaptive mode select it.
        return "realtime"

    def _run_batch(
        self,
        database: Database,
        user_id: int,
        job_id: str,
        cancellation: threading.Event,
        batch_processor: BatchPageProcessor,
    ) -> None:
        """Process small remote batches while retaining page-level durability.

        Concurrency: every page is claimed before submission and identified by
        page number. Response order is irrelevant. Unsubmitted pages remain in
        SQLite so a newly prioritized page can lead the next micro-batch.
        """
        database.mark_ocr_job_running(job_id)
        database.set_ocr_job_mode(job_id, "batch")
        job = database.get_ocr_job(user_id, job_id)
        document = database.get_document(user_id, str(job["session_id"]), str(job["document_id"]))
        if job.get("document_checksum") and document.get("checksum") != job["document_checksum"]:
            database.finish_ocr_job(job_id, "failed", "Document content changed before OCR started")
            return
        while True:
            if cancellation.is_set() or database.is_ocr_job_cancel_requested(job_id):
                database.finish_ocr_job(job_id, "cancelled")
                return

            selected: list[int] = []
            while len(selected) < self.batch_size:
                page = database.dequeue_next_ocr_job_page(job_id)
                if not page:
                    break
                page_number = int(page["page_number"])
                if not database.claim_ocr_page(document["id"], page_number, job_id):
                    if database.get_page_markdown(document["id"], page_number) is not None:
                        database.mark_ocr_job_page(job_id, page_number, "completed")
                    else:
                        database.mark_ocr_job_page(
                            job_id,
                            page_number,
                            "failed",
                            "Page is already being processed",
                            error_code="duplicate_claim",
                        )
                    continue
                database.record_ocr_job_page_attempt(job_id, page_number, "batch")
                selected.append(page_number)

            if not selected:
                if database.finalize_ocr_job_if_idle(job_id):
                    return
                cancellation.wait(0.1)
                continue

            started = time.monotonic()
            remote_batch_id: str | None = None
            try:
                results, remote_batch_id, _provider_duration = batch_processor(
                    document, selected, cancellation
                )
                # Compare like with like: real-time latency includes rasterizing
                # the source page, so Batch must include preparation, upload,
                # provider queueing, processing, and result download as well.
                elapsed = time.monotonic() - started
                per_page = elapsed / max(1, len(selected))
                with self._lock:
                    self._batch_seconds_per_page = (
                        per_page
                        if self._batch_seconds_per_page is None
                        else self._batch_seconds_per_page * 0.7 + per_page * 0.3
                    )
                    self._batch_samples += len(selected)
                for page_number in selected:
                    if cancellation.is_set() or database.is_ocr_job_cancel_requested(job_id):
                        database.mark_ocr_job_page(job_id, page_number, "cancelled")
                        continue
                    result = results.get(page_number)
                    if isinstance(result, Exception) or result is None:
                        error = result if isinstance(result, Exception) else RuntimeError("No batch result returned")
                        error_code, _, _ = _classify_error(error)
                        database.mark_ocr_job_page(
                            job_id,
                            page_number,
                            "failed",
                            str(error)[:500],
                            error_code=error_code,
                            duration_ms=round(elapsed * 1000),
                            processing_mode="batch",
                            remote_batch_id=remote_batch_id,
                        )
                    else:
                        database.save_page_markdown(
                            document["id"],
                            page_number,
                            result,
                            job_id=job_id,
                            expected_checksum=document.get("checksum"),
                            duration_ms=round(elapsed * 1000),
                            processing_mode="batch",
                            remote_batch_id=remote_batch_id,
                        )
            except Exception as exc:
                status = "cancelled" if cancellation.is_set() else "failed"
                error_code, _, _ = _classify_error(exc)
                for page_number in selected:
                    database.mark_ocr_job_page(
                        job_id,
                        page_number,
                        status,
                        None if status == "cancelled" else str(exc)[:500],
                        error_code=None if status == "cancelled" else error_code,
                        duration_ms=round((time.monotonic() - started) * 1000),
                        processing_mode="batch",
                        remote_batch_id=remote_batch_id,
                    )
            finally:
                for page_number in selected:
                    database.release_ocr_page(document["id"], page_number, job_id)
                self._persist_telemetry(database)

    def _process_page(
        self,
        database: Database,
        job_id: str,
        document: dict,
        page_number: int,
        cancellation: threading.Event,
        processor: PageProcessor,
    ) -> OcrAttemptOutcome:
        """Run one real-time page with bounded retries and atomic completion.

        Cancellation: an upstream response received after the kill switch is
        discarded. Retryable failures consume a fresh rate token; permanent
        failures affect only this page.
        """
        started = time.monotonic()
        rate_limited = False
        if not database.claim_ocr_page(document["id"], page_number, job_id):
            if database.get_page_markdown(document["id"], page_number) is not None:
                database.mark_ocr_job_page(job_id, page_number, "completed")
                return OcrAttemptOutcome(True, time.monotonic() - started)
            database.mark_ocr_job_page(
                job_id,
                page_number,
                "failed",
                "Page is already being processed",
                error_code="duplicate_claim",
            )
            return OcrAttemptOutcome(False, time.monotonic() - started)

        try:
            for attempt in range(1, self.max_attempts + 1):
                if cancellation.is_set() or database.is_ocr_job_cancel_requested(job_id):
                    database.mark_ocr_job_page(job_id, page_number, "cancelled")
                    return OcrAttemptOutcome(False, time.monotonic() - started, rate_limited)
                database.record_ocr_job_page_attempt(job_id, page_number, "realtime")
                try:
                    markdown = processor(document, page_number)
                    if cancellation.is_set() or database.is_ocr_job_cancel_requested(job_id):
                        database.mark_ocr_job_page(job_id, page_number, "cancelled")
                        return OcrAttemptOutcome(False, time.monotonic() - started, rate_limited)
                    duration_ms = round((time.monotonic() - started) * 1000)
                    database.save_page_markdown(
                        document["id"],
                        page_number,
                        markdown,
                        job_id=job_id,
                        expected_checksum=document.get("checksum"),
                        duration_ms=duration_ms,
                        processing_mode="realtime",
                    )
                    return OcrAttemptOutcome(True, time.monotonic() - started, rate_limited)
                except Exception as exc:
                    error_code, retryable, was_rate_limited = _classify_error(exc)
                    rate_limited = rate_limited or was_rate_limited
                    if was_rate_limited:
                        self.rate_controller.note_rate_limit(_retry_after(exc))
                    if not retryable or attempt >= self.max_attempts:
                        database.mark_ocr_job_page(
                            job_id,
                            page_number,
                            "failed",
                            str(exc)[:500],
                            error_code=error_code,
                            duration_ms=round((time.monotonic() - started) * 1000),
                            processing_mode="realtime",
                        )
                        return OcrAttemptOutcome(False, time.monotonic() - started, rate_limited)
                    delay = _retry_after(exc)
                    if delay is None:
                        delay = min(8.0, 0.5 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.25)
                    if cancellation.wait(delay) or not self.rate_controller.wait_for_retry_token(cancellation):
                        database.mark_ocr_job_page(job_id, page_number, "cancelled")
                        return OcrAttemptOutcome(False, time.monotonic() - started, rate_limited)
        finally:
            database.release_ocr_page(document["id"], page_number, job_id)

        return OcrAttemptOutcome(False, time.monotonic() - started, rate_limited)

    def _run(
        self,
        database: Database,
        user_id: int,
        job_id: str,
        cancellation: threading.Event,
        processor: PageProcessor,
        batch_processor: BatchPageProcessor | None,
    ) -> None:
        initial_job = database.get_ocr_job(user_id, job_id)
        pending_pages = sum(
            page["status"] == "queued" for page in initial_job.get("pages", [])
        )
        if self._choose_mode(pending_pages, batch_processor) == "batch" and batch_processor:
            try:
                self._run_batch(
                    database, user_id, job_id, cancellation, batch_processor
                )
            except KeyError:
                pass
            except Exception as exc:
                try:
                    database.finish_ocr_job(job_id, "failed", str(exc)[:500])
                except Exception:
                    pass
            finally:
                self._persist_telemetry(database, force=True)
                with self._lock:
                    self._workers.pop(job_id, None)
            return

        executor = ThreadPoolExecutor(
            max_workers=self.rate_controller.max_concurrency,
            thread_name_prefix=f"ocr-page-{job_id}",
        )
        futures: dict[Future[OcrAttemptOutcome], int] = {}
        try:
            database.mark_ocr_job_running(job_id)
            database.set_ocr_job_mode(job_id, "realtime")
            job = database.get_ocr_job(user_id, job_id)
            document = database.get_document(user_id, str(job["session_id"]), str(job["document_id"]))
            if job.get("document_checksum") and document.get("checksum") != job["document_checksum"]:
                database.finish_ocr_job(job_id, "failed", "Document content changed before OCR started")
                return

            while True:
                if cancellation.is_set() or database.is_ocr_job_cancel_requested(job_id):
                    database.finish_ocr_job(job_id, "cancelled")
                    return

                while self.rate_controller.try_reserve():
                    page = database.dequeue_next_ocr_job_page(job_id)
                    if not page:
                        self.rate_controller.finish(OcrAttemptOutcome(False, 0.0))
                        break
                    page_number = int(page["page_number"])
                    future = executor.submit(
                        self._process_page,
                        database,
                        job_id,
                        document,
                        page_number,
                        cancellation,
                        processor,
                    )
                    futures[future] = page_number

                if not futures:
                    if database.finalize_ocr_job_if_idle(job_id):
                        return
                    cancellation.wait(0.05)
                    continue

                done, _ = wait(tuple(futures), timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    futures.pop(future, None)
                    try:
                        outcome = future.result()
                    except Exception:
                        outcome = OcrAttemptOutcome(False, 0.0)
                    self.rate_controller.finish(outcome)
                    self._persist_telemetry(database)

        except KeyError:
            return
        except Exception as exc:
            try:
                database.finish_ocr_job(job_id, "failed", str(exc)[:500])
            except Exception:
                pass
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            self._persist_telemetry(database, force=True)
            with self._lock:
                self._workers.pop(job_id, None)
