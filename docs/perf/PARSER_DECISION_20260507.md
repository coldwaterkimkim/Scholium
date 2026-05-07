# Parser Decision - 2026-05-07

## Decision

Use the enhanced PyMuPDF4LLM + fitz path as Scholium's default selected-region parser path.

Runtime default remains:

```bash
DOCUMENT_PARSER_BACKEND=pymupdf4llm
```

The production adapter reports its source as `pymupdf4llm_enhanced+fitz`.

## Why

Second-round isolated parser benchmarks compared parser output as selected-region context, not as pretty Markdown.

| Parser | Result |
| --- | --- |
| PyMuPDF4LLM enhanced | Best current default candidate. Fastest successful path, stable bbox coverage, strongest goldset selection matching. |
| PyMuPDF4LLM current | Close to enhanced, but slightly weaker in proxy scoring and classification. |
| Docling | Interesting future heavy-parser candidate. Useful native bbox/page elements, but slower and not ready as default. |
| MarkItDown | Not suitable for bbox-grounded selected-region parsing because the benchmark path had no bbox coverage. |
| Marker | Deferred. Installed in isolation, but first-run model downloads blocked a clean parser-quality run. |
| MinerU | Deferred. Current CLI/runtime dependencies and adapter contract need a separate isolated integration pass. |

## Production Strategy

- Default parser: `pymupdf4llm_enhanced+fitz` through the existing `pymupdf4llm` backend.
- Fallback: existing fitz fallback inside the same adapter.
- Heavy parser dependencies are not part of the default app install.
- Docling remains documented as a possible optional heavy parser candidate.
- Marker/MinerU are deferred.
- MarkItDown is not a selected-region parser backend.
- OCR/scanned-PDF lane is deferred and should be added as an explicit optional path after cheap scan-like page detection.

## Benchmark Evidence

The concise second-round report was generated at:

```text
docs/perf_runs/parser_benchmark_20260507T_second_round_isolated_v1/SECOND_ROUND_REPORT.md
```

That generated run directory is intentionally not part of the merge-ready source tree. Keep benchmark runs reproducible through scripts and docs rather than committing large generated dumps.
