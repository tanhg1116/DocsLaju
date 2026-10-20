# Cost-first OCR defaults

Auto-OCR submits locally rasterized page images through Mistral Batch by default.
PDF files remain local for this path. `OCR_BATCH_FORCE=true` bypasses the adaptive
latency comparison even for short page ranges. `OCR_BATCH_SIZE=32` controls the
number of page-image requests per remote job; jobs remain sequential in the
application scheduler. The parallel benchmark does not enable parallel jobs in
the application.

Manual current-page OCR continues to call the real-time OCR endpoint. Structured
extraction has its own provider cost. Batch pricing is a provider discount, not
a guarantee encoded by the application: the October 20, 2026 dashboard benchmark
observed USD 0.64 for 320 screenshots for both one large Batch job and ten
simultaneous jobs (USD 2 per 1,000 pages).

`PDF_OCR_DPI=120` controls PDF page raster resolution. The existing 3,600-pixel
long-side cap and conditional image enhancement still apply. Increase DPI for
documents that need more detail. The local 320-page comparison measured 124.330
seconds at 120 DPI versus 240.997 seconds at 240 DPI; OCR accuracy at the lower
resolution has not yet been benchmarked.

Set `OCR_BATCH_FORCE=false` to restore the previous latency-based adaptive
selection. Set `OCR_BATCH_ENABLED=false` to disable Batch completely. Explicit
environment or `.env` values override code defaults. Restart Flask after changing
these settings.

The historical speed-first policy in `OCR_BENCHMARK.md` describes the earlier
scheduler default and is superseded by this cost-first default.
