from __future__ import annotations

import threading
from collections.abc import Callable

from src.services.database import Database


PageProcessor = Callable[[dict, int], str]


class OcrJobManager:
    """Runs one prioritized, cancellable OCR queue per document in the background."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: dict[str, tuple[threading.Thread, threading.Event, str]] = {}

    def start(self, database: Database, user_id: int, job_id: str, processor: PageProcessor) -> None:
        job = database.get_ocr_job(user_id, job_id)
        cancellation = threading.Event()
        worker = threading.Thread(
            target=self._run,
            args=(database, user_id, job_id, cancellation, processor),
            name=f"ocr-job-{job_id}",
            daemon=True,
        )
        with self._lock:
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

    def _run(
        self,
        database: Database,
        user_id: int,
        job_id: str,
        cancellation: threading.Event,
        processor: PageProcessor,
    ) -> None:
        try:
            database.mark_ocr_job_running(job_id)
            job = database.get_ocr_job(user_id, job_id)
            document = database.get_document(user_id, str(job["session_id"]), str(job["document_id"]))

            while True:
                if cancellation.is_set() or database.is_ocr_job_cancel_requested(job_id):
                    database.finish_ocr_job(job_id, "cancelled")
                    return

                page = database.dequeue_next_ocr_job_page(job_id)
                if not page:
                    if database.finalize_ocr_job_if_idle(job_id):
                        return
                    continue
                page_number = int(page["page_number"])

                if not database.claim_ocr_page(document["id"], page_number, job_id):
                    # Another request owns this page. Reuse its result if it has
                    # already committed; otherwise report the collision without
                    # ever submitting the same page twice.
                    if database.get_page_markdown(document["id"], page_number) is not None:
                        database.mark_ocr_job_page(job_id, page_number, "completed")
                    else:
                        database.mark_ocr_job_page(
                            job_id,
                            page_number,
                            "failed",
                            "Page is already being processed",
                        )
                    continue

                try:
                    markdown = processor(document, page_number)
                    if cancellation.is_set() or database.is_ocr_job_cancel_requested(job_id):
                        database.mark_ocr_job_page(job_id, page_number, "cancelled")
                        database.finish_ocr_job(job_id, "cancelled")
                        return
                    database.save_page_markdown(document["id"], page_number, markdown)
                    database.mark_ocr_job_page(job_id, page_number, "completed")
                except Exception as exc:  # the remaining pages can still succeed
                    database.mark_ocr_job_page(job_id, page_number, "failed", str(exc)[:500])
                finally:
                    database.release_ocr_page(document["id"], page_number, job_id)

        except KeyError:
            # The document/job may have been deleted while an upstream request
            # was still returning. Its cascade cleanup is already complete.
            return
        except Exception as exc:
            try:
                database.finish_ocr_job(job_id, "failed", str(exc)[:500])
            except Exception:
                pass
        finally:
            with self._lock:
                self._workers.pop(job_id, None)
