CREATE INDEX IF NOT EXISTS idx_ocr_job_pages_dispatch
    ON ocr_job_pages (job_id, status, priority DESC, page_number);

CREATE INDEX IF NOT EXISTS idx_ocr_job_pages_remote_batch
    ON ocr_job_pages (remote_batch_id)
    WHERE remote_batch_id IS NOT NULL;
