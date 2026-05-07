# Scholium Performance Baseline Plan

이 문서는 selected-region pipeline v1의 성능 기준을 잡기 위한 실행 계획이다. 핵심 원칙은 간단하다. 숫자를 만들지 말고, 실제로 측정된 것과 측정하지 않은 것을 JSON에 분리해서 남긴다.

## 목표

- selected-region 설명 흐름이 사용자 드래그 이후 어느 지점에서 느린지 확인한다.
- upload 이후 viewer가 언제 처음 readable해지는지와 explanation context가 언제 준비되는지를 분리해서 확인한다.
- parser 후보별 속도와 추출량을 같은 PDF 묶음에서 비교한다.
- 기존 `processing_benchmark.json`과 충돌하지 않고, on-demand selection 단계만 따로 볼 수 있게 한다.
- 캐시 hit, dry-run, 실제 LLM 호출을 결과에서 명확히 구분한다.

## Readiness Model

viewer readiness는 한 덩어리가 아니라 세 단계로 본다.

- `render_only`: page image가 있어서 PDF를 읽을 수 있다. selection explanation은 아직 막는다.
- `page_context_ready`: pass1 page context가 있어서 선택 영역 설명을 만들 수 있다. document synthesis가 아직 없으면 답변은 page 중심으로 제한된다.
- `on_demand`: page context와 document context가 모두 준비되어 full selected-region explanation을 만들 수 있다.
- `legacy_pass2`: `SCHOLIUM_PRECOMPUTE_ANCHORED_EXPLANATIONS=true`일 때만 쓰는 debug/legacy mode다.

`GET /api/documents/{document_id}/processing`에서는 기존 호환용 `ready_for_viewer`와 함께 `render_ready_for_viewer`, `page_context_ready_pages`, `document_context_ready`를 본다.

`GET /api/documents/{document_id}/pages/{page_number}`는 rendered image가 있으면 pass1이 없어도 `render_only` page response를 반환한다.

## 기준 입력

가능하면 같은 corpus를 계속 쓴다.

- `data/raw_pdfs/`의 고정 PDF 묶음
- 이미 처리된 `data/analysis/<document_id>/` artifact
- selected-region benchmark용 selection spec JSON

selection spec 예시:

```json
[
  {
    "label": "page 2 formula area",
    "document_id": "doc_xxx",
    "page_number": 2,
    "selected_bbox": [0.12, 0.18, 0.32, 0.08]
  }
]
```

`selected_bbox`는 viewer와 같은 normalized `[x, y, w, h]` 좌표다.

## Selected-Region Metrics

`backend/scripts/benchmark_selected_region_perf.py`는 selection 단위로 아래 필드를 남긴다.

- 입력 식별: `label`, `document_id`, `page_number`, `selected_bbox`, `selection_id`
- selected-region geometry: `selected_region_width`, `selected_region_height`, `selected_region_area`, `selected_region_center_x`, `selected_region_center_y`, `selected_region_aspect_ratio`
- 준비 상태: `page_record_found`, `rendered_page_ready`, `page_image_exists`, `pass1_available`, `document_summary_available`
- 캐시 상태: `cache_hit_before`, `cache_matches_current_provider_before`, `cache_hit_after`, `selection_artifact_available_after`
- compact context: `selection_context_size_chars`, `selection_context_hash`, `nearby_text_block_count`, `source_candidate_count`
- preprocessed match: `matched_preprocessed_element_count`, `matched_element_anchor_ids`, `matched_element_anchor_types`, `top_match_score`, `top_selection_overlap_ratio`, `top_selection_center_distance`
- 실행 시간: `dry_run_check_seconds`, `service_call_seconds`, `total_wall_time_seconds`
- 결과 품질 proxy: `result_anchor_type`, `result_confidence`, `result_study_importance`, `result_related_pages_count`, `result_source_cues_count`, `result_short_explanation_chars`, `result_long_explanation_chars`
- 실패 설명: `status`, `error_message`

해석 규칙:

- `dry_run: true` 결과는 LLM latency가 아니다. 준비 상태와 bbox 매칭 품질만 보는 값이다.
- `cache_hit_before: true`인 실제 실행은 “생성 속도”가 아니라 “캐시 반환 속도”로 해석한다.
- `cache_matches_current_provider_before: false`면 artifact는 있어도 현재 provider/model/prompt 기준 재생성될 수 있다.
- `selection_context_size_chars`가 커지면 Codex CLI prompt payload가 커져 latency가 늘 수 있다.
- `top_selection_overlap_ratio`가 낮으면 사용자가 드래그한 영역이 pass1 후보와 잘 맞지 않는다는 뜻이다.

## 실행 명령

기존 artifact에서 자동으로 첫 ready selection을 잡아 dry-run:

```bash
cd backend
./.venv/bin/python scripts/benchmark_selected_region_perf.py \
  --auto-first-ready \
  --limit 3 \
  --dry-run \
  --output /tmp/scholium_selected_region_dry_run.json
```

명시 selection spec으로 dry-run:

```bash
cd backend
./.venv/bin/python scripts/benchmark_selected_region_perf.py \
  --selection-file ../docs/perf/selected_region_cases.example.json \
  --dry-run
```

실제 selected-region 설명 호출까지 측정:

```bash
cd backend
./.venv/bin/python scripts/benchmark_selected_region_perf.py \
  --selection-file ../docs/perf/selected_region_cases.example.json \
  --no-dry-run \
  --mode-name selected_region_real_v1
```

PDF parser 후보 비교:

2026-05-07 integration decision: keep `DOCUMENT_PARSER_BACKEND=pymupdf4llm` as the default backend, with the adapter running the enhanced `pymupdf4llm_enhanced+fitz` path. Docling remains a future optional heavy-parser candidate; Marker/MinerU/MarkItDown are not integrated into the default app install.

```bash
cd backend
./.venv/bin/python scripts/benchmark_pdf_parsers.py \
  --pdf-dir ../data/raw_pdfs \
  --limit 5
```

특정 parser만 비교:

```bash
cd backend
./.venv/bin/python scripts/benchmark_pdf_parsers.py \
  ../data/raw_pdfs/sample.pdf \
  --parsers pymupdf4llm_current pymupdf4llm_enhanced
```

pass1 worker 수 비교:

```bash
cd backend
PASS1_MAX_WORKERS=1 ./.venv/bin/python scripts/run_benchmark_corpus.py --pdf-dir ../data/raw_pdfs --limit 1 --mode-name pass1_workers_1
PASS1_MAX_WORKERS=2 ./.venv/bin/python scripts/run_benchmark_corpus.py --pdf-dir ../data/raw_pdfs --limit 1 --mode-name pass1_workers_2
PASS1_MAX_WORKERS=3 ./.venv/bin/python scripts/run_benchmark_corpus.py --pdf-dir ../data/raw_pdfs --limit 1 --mode-name pass1_workers_3
```

실제 corpus 실행 명령은 기존 benchmark runner 옵션에 맞춰 붙인다. `PASS1_MAX_WORKERS`는 기본 `3`이고, Codex CLI subprocess가 병렬로 늘어난다고 항상 빨라지는 건 아니므로 1/2/3을 같은 PDF 묶음으로 비교한다.

## Parser Metrics

`backend/scripts/benchmark_pdf_parsers.py`는 optional parser package가 없으면 실패시키지 않고 `status: skipped`로 남긴다. 출력은 기본적으로 `docs/perf_runs/parser_benchmark_<timestamp>/` 아래에 생성된다.

parser별 주요 필드:

- `parser_name`
- `parser_display_name`
- `status`: `completed | skipped | failed`
- `skip_reason`
- `install_hint`
- `error_message`
- `artifact_path`
- `structural_metrics`
  - `total_parse_time_seconds`
  - `seconds_per_page`
  - `page_count`
  - `block_count`
  - `non_empty_text_block_count`
  - `element_type_counts`
  - `bbox_coverage_ratio`
  - `percentage_of_elements_with_bbox`
  - `reading_order_continuity_proxy`
  - `table_count`
  - `figure_count`
  - `caption_count`
  - `formula_count`
  - `ocr_used_count`
  - `scan_like_page_count`
- `selection_summary`
  - `bbox_matching_possible_ratio`
  - `enough_context_ratio`
  - `mean_best_overlap_score`
  - `mean_matched_element_count`
  - `mean_source_candidate_count`
- `score`
  - selected bbox matching quality: 20
  - reading order: 15
  - layout element detection: 15
  - source cue usefulness: 15
  - related context usefulness: 10
  - OCR/scanned robustness: 10
  - speed: 10
  - integration complexity: 5

해석 규칙:

- JSON artifact는 `PageElementMap` 비슷한 공통 형식이다. production parser-specific output을 직접 비교하지 않는다.
- 추출 글자 수가 많다고 무조건 좋은 parser는 아니다. selected bbox가 어떤 element/source cue로 매칭되는지 같이 봐야 한다.
- unavailable package는 benchmark 실패가 아니라 환경 차이로 기록한다.
- Docling/Marker/MinerU/MarkItDown은 production parser로 교체하는 게 아니라 optional benchmark 후보로만 본다. 설치되어 있지 않으면 `skipped`가 정상이다.
- goldset이 없거나 해당 PDF와 매칭되지 않으면 script가 parser output에서 proxy selection을 만든다. proxy metric은 human accuracy가 아니라 selected-region context 가능성만 보는 지표다.

## 비교 원칙

- 같은 PDF, 같은 branch, 같은 env로 비교한다.
- selected-region 실제 호출 비교는 캐시 hit와 신규 생성 결과를 분리한다.
- OpenAI/Codex CLI provider 설정, model, reasoning, prompt version을 같이 기록한다.
- dry-run 결과와 real-run 결과를 같은 latency chart에 섞지 않는다.
- 실패 케이스도 지우지 않는다. `error_message`와 prerequisite flag가 다음 병목을 알려준다.

## Pass/Fail Gate

초기 v1 gate는 “최적화 성공”보다 “재현 가능한 baseline 생성”이 목표다.

- 스크립트가 `--help`와 dry-run에서 깨지지 않는다.
- unavailable optional parser package는 skip으로 기록된다.
- selected-region result에 bbox, cache, prerequisite, match, latency 필드가 모두 있다.
- output JSON만 보고 dry-run/cache/real-call을 구분할 수 있다.
- 앱 backend/frontend 코드를 수정하지 않아도 benchmark를 돌릴 수 있다.

## Initial Local Observation

기존 `data/analysis/**/processing_benchmark.json`들을 훑은 결과, render/parse는 대체로 작고 pass1/synthesis/pass2/LLM 호출이 큰 비중을 차지했다. 특히 과거 pass2 precompute path는 selected-region MVP 기본값에서 빼는 게 맞다. 이 branch의 목적은 “모델 품질 낮추기”가 아니라 viewer를 먼저 열고, 선택 영역 요청 때 compact context만 보내서 on-demand latency를 줄이는 것이다.
