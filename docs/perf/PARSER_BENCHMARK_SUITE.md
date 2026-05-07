# Parser Benchmark Suite

Scholium의 parser 선택 기준은 Markdown 출력의 미관이 아니다.

기준은 “사용자가 임의의 PDF 영역을 드래그했을 때, 그 영역을 설명할 수 있을 만큼 page/document/element/source context를 안정적으로 제공하는가”다.

## Current Architecture

- Runtime parser contract: `DocumentParser.parse_document(document_id, pdf_path) -> DocumentParseArtifact`
- Current normalized runtime artifact: `DocumentParseArtifact -> ParsedPage -> ParseBlock`
- Current default backend: `DOCUMENT_PARSER_BACKEND=pymupdf4llm`
- Current adapter: enhanced PyMuPDF4LLM page chunks + fitz block geometry, reported as `pymupdf4llm_enhanced+fitz`
- Current fallback: if PyMuPDF4LLM extraction fails, fitz page text fallback creates a full-page paragraph bbox
- Production default must not be switched by benchmark results alone.
- 2026-05-07 decision: keep PyMuPDF4LLM enhanced as default, keep Docling as a future optional heavy-parser candidate, and do not integrate Marker/MinerU/MarkItDown into production default install.

## Benchmark Normalized Format

The benchmark writes a PageElementMap-like artifact per parser/PDF:

- `element_id`
- `page_number`
- `element_type`: `heading | paragraph | figure | table | formula | caption | list | other`
- `text`
- `bbox`: normalized `[x, y, w, h]`, when available
- `reading_order`
- `source_parser`
- `confidence`
- `quality_notes`
- `relations`: parent/caption relations when inferred

This is intentionally close to the eventual selected-region context layer. Parser-specific raw output should stay behind adapters.

## Commands

Run from the repo root:

```bash
backend/.venv/bin/python scripts/benchmark_pdf_parsers.py \
  data/raw_pdfs/doc_f9ba1ef0e03446d1bcf11dcc686d1275.pdf \
  data/raw_pdfs/W1.Lecture01-Financial\ Management\ and\ Firm\ Value.pdf
```

Run from backend:

```bash
cd backend
./.venv/bin/python scripts/benchmark_pdf_parsers.py \
  --pdf-dir ../data/raw_pdfs \
  --limit 5
```

Compare only the two installed PyMuPDF-based adapters:

```bash
cd backend
./.venv/bin/python scripts/benchmark_pdf_parsers.py \
  ../data/raw_pdfs/doc_f9ba1ef0e03446d1bcf11dcc686d1275.pdf \
  --parsers pymupdf4llm_current pymupdf4llm_enhanced
```

## Outputs

Default output directory:

```text
docs/perf_runs/parser_benchmark_<timestamp>/
```

Files:

- `parser_benchmark_results.json`
- `parser_benchmark_summary.md`
- `artifacts/*.json`

## Optional Dependencies

The benchmark detects optional parser packages and skips missing ones.

Recommended isolated install pattern:

```bash
python3.12 -m venv /tmp/scholium-parser-probe
source /tmp/scholium-parser-probe/bin/activate
python -m pip install -U pip
python -m pip install docling marker-pdf mineru markitdown PyMuPDF pymupdf4llm
```

Do not add these heavy parser packages to `backend/requirements.txt` until a later production integration decision.

## Goldset

Starter file:

```text
benchmarks/parser_selection_goldset.yaml
```

The file is intentionally a starter set. Treat it as human-review-needed until the user verifies coordinates in the viewer. If no matching gold selection exists for a PDF, the benchmark creates proxy selections from parser-detected elements. Proxy selections are useful for bbox/context plumbing, not true explanation accuracy.

## Rubric

- selected bbox matching quality: 20
- reading order: 15
- layout element detection: 15
- source cue usefulness: 15
- related context usefulness: 10
- OCR/scanned robustness: 10
- speed: 10
- integration complexity: 5

Skipped or failed parsers are not assigned invented scores.
