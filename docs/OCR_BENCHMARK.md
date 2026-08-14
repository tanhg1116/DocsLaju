# OCR scheduler benchmark

This benchmark validates DocsLaju's scheduler and provider paths rather than
Mistral's recognition quality. It used `tests/long_doc.pdf` (705 pages,
10,334,136 bytes) on 14 August 2026. Each comparison processed the same first
64 pages through the application's real upload, PDF rasterization, queue,
Mistral client, and persistence paths.

## Results

| Mode | Configuration | Completed | Failed | End-to-end time | Throughput |
| --- | --- | ---: | ---: | ---: | ---: |
| Real-time | Normal adaptive ramp, 4 to 7 workers | 64 | 0 | 81.766 s | 46.96 pages/min |
| Real-time | Fixed ceiling, 24 workers | 64 | 0 | 37.515 s | 102.36 pages/min |
| Batch | 8 pages per remote job | 64 | 0 | 326.969 s | 11.74 pages/min |
| Batch | 32 pages per remote job | 64 | 0 | 160.250 s | 23.96 pages/min |

No real-time request received HTTP 429. At maximum configured concurrency, the
application reached only 9.31% of its 1,100 pages/minute safety target (88% of
the supplied 1,250 pages/minute account ceiling). The account rate limit is
therefore not the current throughput bottleneck on this machine.

Batch size 32 halved the time of size 8 by amortizing upload and provider queue
overhead, but real-time at maximum concurrency was still approximately 2.35
times faster. Batch may remain useful for its provider-side cost discount, but
it is not a speed optimization for this measured workload.

## Decisions resulting from the benchmark

- Adaptive mode no longer assumes that a large queue makes Batch faster.
- Real-time remains selected until a forced Batch calibration has produced
  durable, end-to-end telemetry and its predicted completion time is at least
  10% better.
- Batch telemetry includes local page preparation, upload, provider queue,
  processing, and result download. Comparing provider time alone with
  end-to-end real-time latency would be invalid.
- The recommended Batch size is 32 rather than 8 when Batch is deliberately
  enabled.
- Scheduler aggregates persist in SQLite's `ocr_scheduler_metrics` table and
  are restored when Flask starts.
- Batch input is uploaded as a JSONL file and passed through `input_files`, as
  documented in Mistral's
  [OCR Batch cookbook](https://docs.mistral.ai/resources/cookbooks/mistral-ocr-batch_ocr).
  Remote input, output, and error files are deleted after the job.

## Reproducing a bounded live run

The following command sends paid OCR requests:

```powershell
uv run --with-requirements requirements.txt python scripts\benchmark_ocr.py `
  --document tests\long_doc.pdf --pages 64 --mode both --confirm-live
```

To apply maximum real-time pressure without changing application defaults:

```powershell
uv run --with-requirements requirements.txt python scripts\benchmark_ocr.py `
  --document tests\long_doc.pdf --pages 64 --mode realtime `
  --realtime-concurrency 24 --confirm-live
```

The runner creates an isolated SQLite database under `tmp/` and a content-free
JSON summary under `output/benchmarks/`. Both locations are Git-ignored. OCR
Markdown remains inside the isolated benchmark database and is not copied into
the JSON report.
