CREATE TABLE IF NOT EXISTS ocr_scheduler_metrics (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pages_per_minute_limit INTEGER NOT NULL,
    target_utilization REAL NOT NULL,
    current_concurrency INTEGER NOT NULL,
    realtime_latency_ewma REAL,
    realtime_throughput_pps REAL,
    realtime_samples INTEGER NOT NULL DEFAULT 0,
    batch_seconds_per_page REAL,
    batch_samples INTEGER NOT NULL DEFAULT 0,
    rate_limit_events INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
