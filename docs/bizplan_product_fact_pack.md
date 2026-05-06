# Scholium Product Fact Pack

작성 기준:
- 이 문서는 현재 repo 코드와 이미 생성된 artifact만 기준으로 적었다.
- "현재 구현됨", "실제로 동작 확인됨", "아직 없음", "3개월 내 구현 가능"을 분리해서 적었다.
- 주요 근거:
  - 코드: `backend/app/*`, `frontend/*`
  - 최신 성능/품질 artifact: `docs/perf_runs/20260329T071211565189Z_*`, `docs/perf_runs/20260329T101732981630Z_*`, `docs/perf_runs/20260329T104834162180Z_*`, `docs/perf_runs/20260329T134743640870Z_*`

## 1. 현재 구현된 기능

### 1-1. 업로드
- 단일 PDF 업로드 API가 구현돼 있다.
  - `POST /api/documents`가 PDF 시그니처를 확인한 뒤 원본 PDF를 저장하고 background pipeline을 시작한다.
  - 근거: `backend/app/api/documents.py`, `backend/app/services/storage.py`, `backend/app/services/orchestrator.py`
- 프런트엔드 업로드 폼이 구현돼 있다.
  - 단일 파일 선택, `.pdf` 체크, 업로드 중 버튼 비활성화, 업로드 실패 메시지, 성공 시 processing 화면 이동이 있다.
  - 근거: `frontend/components/UploadForm.tsx`, `frontend/lib/api.ts`
- 현재 범위 밖 기능:
  - 멀티 업로드, 업로드 이력, 사용자 계정 연동, drag-and-drop, 업로드 progress bar는 없다.

### 1-2. 렌더링
- PDF를 페이지별 PNG로 렌더링하는 기능이 구현돼 있다.
  - `fitz` 기반 렌더링이며 long edge 1600px PNG를 생성한다.
  - 결과물은 `data/rendered_pages/{document_id}/{page}.png`에 저장된다.
  - 근거: `backend/app/services/pdf_render.py`, `backend/app/workers/render_worker.py`
- 렌더링 결과를 static path로 서빙한다.
  - `/static/rendered-pages`
  - 근거: `backend/app/main.py`

### 1-3. parse / triage
- parse artifact 생성 경로가 구현돼 있다.
  - 문서 단위 parse 결과를 `document_parse.json`으로 저장하고, page-level triage 입력으로 사용한다.
  - 근거: `backend/app/services/document_parser.py`, `backend/app/services/storage.py`
- parser backend 선택 로직이 구현돼 있다.
  - 기본값은 `pymupdf4llm`, 실패 시 stub parser로 폴백한다.
  - 근거: `backend/app/core/config.py`, `backend/app/services/document_parser.py`, `backend/app/services/pymupdf4llm_adapter.py`
- triage가 page manifest를 만든다.
  - page별 `route_label`, `route_reason`, `text_length`, `block_count`, `non_empty_text_block_count`, `image_count`, `has_table`, `has_figure`, `ocr_used`를 기록한다.
  - 현재 route label은 `text-rich`, `visual-rich`, `scan-like`다.
  - 근거: `backend/app/services/pdf_triage.py`, `backend/app/services/storage.py`

### 1-4. pass1
- page별 anchor extraction pass1이 구현돼 있다.
  - hybrid routing 기준으로 `text-first`와 `multimodal`을 선택한다.
  - 근거: `backend/app/services/pass1_analyzer.py`
- text-first 조건이 코드로 명시돼 있다.
  - `route_label == text-rich`
  - `text_length >= 200`
  - `non_empty_text_block_count >= 4`
  - parsed text block가 충분할 때
  - 근거: `backend/app/services/pass1_analyzer.py`
- text-first 결과가 약하면 multimodal로 escalate한다.
  - candidate anchor 수가 너무 적으면 fallback이 있다.
  - 근거: `backend/app/services/pass1_analyzer.py`
- pass1 결과는 page별 artifact로 저장된다.
  - `data/analysis/{document_id}/pages/{page_number}/page_analysis_pass1.json`
  - 근거: `backend/app/services/storage.py`

### 1-5. synthesis
- 문서 단위 synthesis가 구현돼 있다.
  - pass1 결과를 모아 document summary를 생성한다.
  - 근거: `backend/app/services/document_synthesizer.py`
- summary coverage 최소 조건이 코드에 있다.
  - `max(3, ceil(total_rendered_pages * 0.7))`
  - 근거: `backend/app/services/document_synthesizer.py`
- summary artifact를 저장한다.
  - `document_summary.json`
  - section, prerequisite reference 등 summary 구조를 검증한 뒤 저장한다.
  - 근거: `backend/app/services/document_synthesizer.py`, `backend/app/services/storage.py`

### 1-6. pass2
- 기본 LLM pass2 경로가 구현돼 있다.
  - page image + pass1 + document summary를 넣어 page-level final anchor result를 만든다.
  - 결과는 `page_analysis_pass2.json`으로 저장된다.
  - 근거: `backend/app/services/pass2_refiner.py`, `backend/app/services/storage.py`
- deterministic compat pass2 경로도 구현돼 있다.
  - pass1 anchor와 document summary를 사용해 저장 가능한 compat artifact를 만든다.
  - `pass2_generation_mode = "compat"`가 기록된다.
  - 근거: `backend/app/services/pass2_compat_builder.py`, `backend/app/services/pass2_artifact_builder.py`
- selective pass2 planner 경로가 구현돼 있다.
  - `v2_spine + active + hard_pages_only`일 때 `page_routing.json`을 읽어 일부 page만 compat로 내리고 나머지는 llm으로 유지한다.
  - compat 실패 페이지는 llm으로 승격한다.
  - 근거: `backend/app/services/orchestrator.py`, `backend/app/services/document_spine_builder.py`

### 1-7. viewer
- 내부 문서 viewer가 구현돼 있다.
  - route:
    - `/`
    - `/documents/[documentId]`
    - `/documents/[documentId]/processing`
  - 근거: `frontend/app/page.tsx`, `frontend/app/documents/*`
- processing 화면이 구현돼 있다.
  - 2초 polling, 현재 stage/status, rendered/pass1/pass2 count, recent failures 표시, ready 시 viewer 자동 이동
  - 근거: `frontend/components/ProcessingStatus.tsx`
- page viewer가 구현돼 있다.
  - 왼쪽 page image, 오른쪽 explanation panel, previous/next navigation
  - 근거: `frontend/components/DocumentViewer.tsx`
- anchor overlay가 구현돼 있다.
  - bbox 기반 버튼 overlay, 클릭 시 anchor 선택, related page jump
  - 근거: `frontend/components/AnchorOverlay.tsx`, `frontend/components/RightPanel.tsx`
- viewer가 현재 보여주는 주요 정보:
  - page_role
  - page_summary
  - selected anchor의 label, question, short/long explanation, prerequisite, related pages, confidence
  - 근거: `frontend/components/RightPanel.tsx`

### 1-8. logging
- 사용자 interaction log append 경로가 구현돼 있다.
  - `POST /api/logs`
  - event type 예: `page_view`, `anchor_click`, `related_page_jump`
  - SQLite `interaction_logs` 테이블에 저장
  - 근거: `backend/app/api/logs.py`, `backend/app/services/log_store.py`
- pipeline 내부 benchmark/logging이 구현돼 있다.
  - stage별 시간, openai call count, pass1/pass2 분기 수, planner 상태 등을 `processing_benchmark.json`으로 저장한다.
  - 근거: `backend/app/services/storage.py`

### 1-9. benchmark
- corpus benchmark 실행 스크립트가 있다.
  - `backend/scripts/run_benchmark_corpus.py`
- run 비교 스크립트가 있다.
  - `backend/scripts/compare_pipeline_modes.py`
- QA / audit / simulation export 스크립트가 있다.
  - `backend/scripts/export_pass2_qa_samples.py`
  - `backend/scripts/export_routing_audit.py`
  - `backend/scripts/simulate_routing_rule_tiebreak.py`
  - `backend/scripts/export_recovered_page_qa.py`
- 최신 evidence pack이 존재한다.
  - same-manifest 비교: `docs/perf_runs/20260329T071211565189Z_comparison.json`, `.md`
  - routing audit: `docs/perf_runs/20260329T101732981630Z_routing_audit.json`, `.md`
  - tie-break simulation: `docs/perf_runs/20260329T104834162180Z_routing_rule_tiebreak.json`, `.md`
  - recovered page QA pack: `docs/perf_runs/20260329T134743640870Z_rule_a_recovered_pages_qa.json`, `.md`

## 2. 현재 실제로 동작하는 모드

### 2-1. baseline
- 현재 코드와 perf pack 기준으로 실제 benchmark가 있는 baseline 모드는 `baseline_hybrid_all_pages`다.
- 의미:
  - pipeline mode: `hybrid`
  - spine mode: `shadow`
  - pass2 execution: `all_pages`
  - 결과적으로 pass2는 전 페이지 llm refinement를 돈다.
- 같은 manifest 기준 benchmark artifact가 있다.
  - 근거: `docs/perf_runs/20260329T071211565189Z_comparison.md`, `.json`

### 2-2. v2 spine shadow
- 코드상 지원되는 모드다.
- 의미:
  - parse/page manifest가 있으면 `document_spine.json`, `page_routing.json`을 생성할 수 있다.
  - shadow 성격이라 planner artifact를 만들지만 pass2 selective execution을 강제로 켜는 상태로 쓰이지는 않는다.
- 현재 확인 가능한 사실:
  - 관련 code path와 artifact 저장 경로는 있다.
  - 근거: `backend/app/services/orchestrator.py`, `backend/app/services/document_spine_builder.py`, `backend/app/services/storage.py`
- 현재 확인되지 않은 것:
  - 최신 perf pack에는 shadow mode 단독의 독립 benchmark summary가 없다.
  - 따라서 "성능이 검증된 운용 모드"라고 쓰면 안 되고, "코드 경로와 artifact 생성은 존재"까지가 사실이다.

### 2-3. active + hard_pages_only
- 현재 코드와 perf pack 기준으로 실제 benchmark가 있는 selective mode는 `v2_spine_active_hard_pages_only`다.
- 의미:
  - pipeline mode: `v2_spine`
  - spine mode: `active`
  - pass2 execution: `hard_pages_only`
  - planner가 `page_routing.json`을 기준으로 일부 페이지를 compat로 내리고, 나머지와 fallback은 llm pass2로 유지한다.
- 같은 manifest 기준 benchmark artifact가 있다.
  - 근거: `docs/perf_runs/20260329T071211565189Z_comparison.md`, `.json`
- 최신 same-manifest 비교 결과:
  - completed docs: `5 -> 5`
  - avg total processing time: `524.8224s -> 361.9378s`
  - total OpenAI pass2 calls: `75 -> 29`
  - total pass2 llm pages: `76 -> 30`
  - total pass2 compat pages: `0 -> 46`
  - 근거: `docs/perf_runs/20260329T071211565189Z_comparison.md`

## 3. 아직 미구현/불완전한 기능

### 3-1. 제품 표면
- auth, payment, vector DB, hover, voice, collaboration, mobile optimization은 현재 스프린트 범위 밖으로 명시돼 있다.
  - 근거: `README.md`
- 문서 목록/최근 업로드/검색/공유/사용자별 저장 같은 제품 레이어는 현재 UI에 없다.
  - 근거: `frontend/app/*`, `frontend/components/*`
- viewer에 page thumbnail, zoom/pan, deep link, hover tooltip, anchor type styling은 없다.
  - 근거: `frontend/components/DocumentViewer.tsx`, `AnchorOverlay.tsx`, `RightPanel.tsx`
- processing 화면에 retry/cancel control은 없다.
  - 근거: `frontend/components/ProcessingStatus.tsx`

### 3-2. pipeline / quality
- OCR은 현재 실질적으로 미구현 상태다.
  - `ocr_used` 필드는 있지만 parser 구현에서 실제 OCR path가 붙어 있지 않고 `False`로 기록된다.
  - 근거: `backend/app/services/document_parser.py`, `backend/app/services/pymupdf4llm_adapter.py`
- parse/triage는 best effort다.
  - 이 단계가 실패해도 pipeline 전체를 막지 않는다.
  - 즉 parser 품질이 낮아도 pass1/pass2는 돌아가지만 hybrid text-first 이점은 줄 수 있다.
  - 근거: `backend/app/services/orchestrator.py`, `backend/app/services/pass1_analyzer.py`
- compat 품질은 개선됐지만 아직 품질 한계가 문서화돼 있다.
  - latest assessment 표현 그대로 `safe but shallow`
  - 근거: `docs/perf_runs/20260329T071211565189Z_active_pass2_assessment.md`
- routing false positive 보정은 아직 production rule로 반영되지 않았다.
  - 현재 있는 것은 audit, tie-break simulation, recovered page QA pack까지다.
  - 근거:
    - `docs/perf_runs/20260329T101732981630Z_routing_audit.json`
    - `docs/perf_runs/20260329T104834162180Z_routing_rule_tiebreak.json`
    - `docs/perf_runs/20260329T134743640870Z_rule_a_recovered_pages_qa.json`

### 3-3. 운영/분석 표면
- interaction log는 append-only 저장까지만 있다.
  - log 조회/집계/dashboard API는 현재 코드 기준 없다.
  - 근거: `backend/app/api/logs.py`, `backend/app/services/log_store.py`
- `processing_benchmark.json`은 내부 artifact로 저장되지만 public read API로 직접 노출되지는 않는다.
  - 근거: `backend/app/services/storage.py`, `backend/app/api/*`

## 4. 기술적으로 이미 검증된 것

### 4-1. end-to-end 데모 동작
- upload -> background pipeline -> processing polling -> viewer 조회 흐름이 코드로 연결돼 있다.
- 프런트엔드 route와 백엔드 API가 실제로 맞물려 있다.
- 근거:
  - `frontend/components/UploadForm.tsx`
  - `frontend/components/ProcessingStatus.tsx`
  - `frontend/components/DocumentViewer.tsx`
  - `backend/app/api/documents.py`

### 4-2. same-manifest benchmark
- baseline과 active selective mode가 같은 corpus manifest로 비교됐다.
- unmatched/excluded document가 없다.
- 근거: `docs/perf_runs/20260329T071211565189Z_comparison.md`, `.json`

### 4-3. selective pass2의 비용 절감
- `v2_spine_active_hard_pages_only`는 latest perf pack 기준으로 llm pass2 fan-out을 실제로 줄였다.
- 확인된 수치:
  - `total_openai_pass2_call_count 75 -> 29`
  - `total_pass2_llm_count 76 -> 30`
  - `avg_total_processing_time_seconds 524.8224 -> 361.9378`
- 근거: `docs/perf_runs/20260329T071211565189Z_comparison.md`

### 4-4. compat artifact 대체가 실제로 일어남
- active selective mode run에서 compat artifact가 실제로 저장됐다.
- 확인된 수치:
  - `total_pass2_compat_count = 46`
- 문서별 성공 사례도 있다.
  - `doc_f9ba1ef0e03446d1bcf11dcc686d1275.pdf`: `pass2 llm 23 -> 1`, `compat 22`
  - `26_통계학과.pdf`: `pass2 llm 17 -> 2`, `compat 15`
- 근거: `docs/perf_runs/20260329T071211565189Z_comparison.md`

### 4-5. outlier 분석까지 가능한 상태
- reduction이 잘 안 먹히는 문서를 artifact 기준으로 골라낼 수 있다.
- outlier와 success reference를 분리한 routing audit artifact가 있다.
- tie-break simulation으로 다음 rule change 후보를 문서/페이지 단위로 비교한 결과도 있다.
- recovered page QA pack까지 만들어져 있다.
- 근거:
  - `docs/perf_runs/20260329T101732981630Z_routing_audit.json`
  - `docs/perf_runs/20260329T104834162180Z_routing_rule_tiebreak.json`
  - `docs/perf_runs/20260329T134743640870Z_rule_a_recovered_pages_qa.json`

## 5. 지금 당장 3개월 내 구현 가능한 것

이 섹션은 "현재 구현됨"이 아니라, 현재 코드 구조와 이미 있는 artifact를 기준으로 추가 구현 난도가 낮아 보이는 항목만 적었다.

### 5-1. routing false positive 보정의 실제 반영
- 현재 상태:
  - routing audit, tie-break simulation, recovered page manual QA pack까지 준비돼 있다.
- 3개월 내 가능한 이유:
  - 다음 단계는 완전한 새 시스템이 아니라, 기존 `document_spine_builder` / `orchestrator`의 routing 조건 조정과 QA 반영이다.
- 아직 현재 동작은 아님.

### 5-2. compat 설명 품질 추가 보강
- 현재 상태:
  - compat builder와 QA sample exporter가 이미 있고, 품질 한계도 latest artifact에서 확인돼 있다.
- 3개월 내 가능한 이유:
  - 바꾸는 지점이 `pass2_artifact_builder` 중심으로 좁혀져 있다.
- 아직 현재 동작은 아님.

### 5-3. viewer에서 summary/context 정보 추가 노출
- 현재 상태:
  - backend에는 `document_summary`와 page-level pass2 data가 있고, frontend도 오른쪽 panel 구조가 이미 있다.
- 3개월 내 가능한 이유:
  - sections, key concepts, prerequisite links, QA labels 같은 정보는 새 모델 호출 없이 UI 확장으로 붙일 수 있다.
- 아직 현재 동작은 아님.

### 5-4. processing / QA 운영 표면 보강
- 현재 상태:
  - processing polling, interaction log, benchmark artifact, recovered page QA pack이 이미 있다.
- 3개월 내 가능한 이유:
  - retry/review workflow, log read API, simple internal ops screen은 기존 storage와 API 위에서 확장 가능하다.
- 아직 현재 동작은 아님.

## 6. 제품/기술 측면에서 과장 없이 한 줄 요약 3개

- Scholium은 PDF를 업로드하면 페이지별 렌더링, anchor extraction, 문서 요약, page-level explanation을 생성해 내부 viewer에서 읽을 수 있는 수준까지 구현돼 있다.
- 전 페이지 llm pass2만 도는 baseline뿐 아니라, 일부 페이지를 compat artifact로 대체하는 `v2_spine_active_hard_pages_only` 모드도 same-manifest benchmark로 검증돼 있다.
- 현재의 핵심 미완성 영역은 routing false positive 보정, compat 설명 품질 개선, 그리고 viewer/ops 표면 확장이지, 업로드-분석-뷰어의 기본 골격 자체가 없는 상태는 아니다.
