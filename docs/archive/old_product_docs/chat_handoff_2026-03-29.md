# Scholium New Chat Handoff

Scholium은 강의 PDF/슬라이드 안에서 사용자가 막히는 지점을 바로 풀어주는 클릭형 해설 레이어를 만드는 프로젝트야. 지금 repo는 P0 데모의 핵심 흐름은 거의 붙어 있고, 최근 관심사는 기능 추가 자체보다 운영 가능한 처리 흐름, 비용/성능 절감, 그리고 baseline 비교 체계를 만드는 쪽으로 옮겨간 상태야.

## 현재 핵심 문제 / 지금 제일 먼저 할 일 / 지금 하지 말아야 할 일

### 현재 핵심 문제
- 문서/코드 드리프트가 있어서 오래된 PRD나 audit를 최신 사실처럼 읽으면 판단이 어긋날 수 있어.
- pass1 text-first 최적화는 이미 들어갔지만, 전체 tail latency는 여전히 pass2 비중이 크고 usable corpus baseline도 아직 약해.
- benchmark/corpus 체계는 생겼지만, 지금 커밋된 perf run은 interrupted 샘플이라 “비교 가능한 기준선”이라고 보기 어렵다.

### 지금 제일 먼저 할 일
1. 이 handoff 문서와 현재 코드 기준으로 상태를 다시 맞춰.
2. 실제 PDF corpus 5~10개로 usable baseline을 다시 수집해.
3. text-first가 실제로 multimodal 호출 수와 비용을 얼마나 줄였는지 수치로 확인해.

### 지금 하지 말아야 할 일
- `docs/project_audit.md`나 오래된 PRD만 읽고 현재 상태를 단정하지 마.
- baseline 없이 viewer/public API를 크게 갈아엎지 마.
- pass2 병목을 확인하기 전에 대규모 리팩터링부터 시작하지 마.

## 프로젝트 한 줄 요약

- 제품 정의: PDF 안에서 막힘을 푸는 클릭형 해설 레이어.
- 핵심 경험: dense extraction은 내부에서 하고, 사용자에게는 sparse surfacing으로 3~5개 핵심 앵커만 보여준다.
- 처리 철학: direct PDF-to-chat보다 page image 기반 처리에 가깝고, 파이프라인은 `pass1 -> document synthesis -> pass2` 구조다.
- 출력 철학: markdown 임시 텍스트가 아니라 구조화된 JSON artifact를 source of truth로 쓴다.

## 지금 구현된 상태

### 프론트 / 사용자 흐름
- 업로드 화면이 있다. `/`에서 PDF 업로드 후 바로 processing 화면으로 간다.
- processing 상태 화면이 있다. `/documents/{documentId}/processing`에서 2초 폴링으로 상태를 보고, 완료되면 viewer로 자동 이동한다.
- viewer가 있다. `/documents/{documentId}`에서 백엔드가 렌더한 PNG 이미지를 기반으로 overlay를 그리고, 우측 패널에 `page_role`, `page_summary`, anchor 설명, related page jump를 보여준다.
- interaction log도 붙어 있다. `page_view`, `anchor_click`, `related_page_jump`가 저장된다.

### 백엔드 / 처리 흐름
- 현재 자동 orchestration은 `upload -> render -> parse/triage -> pass1 -> synthesis -> pass2 -> public read API`까지 연결돼 있다.
- parse layer가 있다. canonical parse artifact는 `data/parsed/{document_id}/document_parse.json`에 저장된다.
- page-level routing artifact도 있다. `data/parsed/{document_id}/page_manifest.json`에 저장된다.
- pass1은 `page_manifest`를 읽고 `text-first`와 `multimodal`을 분기한다.
- benchmark harness도 있다. 문서 단위로 `data/analysis/{document_id}/processing_benchmark.json`이 생성된다.
- corpus runner 스크립트도 로컬엔 있다. 다만 지금 워킹트리 기준으로는 `backend/scripts/run_benchmark_corpus.py`가 untracked라서, “repo에 있는 로컬 작업”과 “main 커밋 반영 상태”를 분리해서 봐야 한다.

## 대표 benchmark 예시

대표 예시로 로컬 benchmark artifact `data/analysis/doc_f9ba1ef0e03446d1bcf11dcc686d1275/processing_benchmark.json`에서는 아래 수치가 나와 있어.

- `rendered_pages = 23`
- `pass1_text_first_pages = 21`
- `pass1_multimodal_pages = 2`
- `pass1_escalated_pages = 0`
- `openai_call_count_total = 47`

이 숫자는 “text-first 최적화가 실제로 일부가 아니라 많이 적용되고 있다”는 근거로 쓸 수 있어. 다만 이걸 바로 corpus baseline으로 일반화하면 안 되고, 꼭 여러 PDF로 다시 묶어서 비교해야 해.

## 이전까지 진행한 일

### 1차 구현 단계
- `pass1` 구조와 JSON artifact 뼈대가 먼저 잡혔다.
- 이어서 `document synthesis`, `pass2`, `viewer`, overlay, logs가 빠르게 붙으면서 P0 데모의 end-to-end 흐름이 닫혔다.
- 이 시기의 핵심은 “작동하는 데모를 빨리 만든다”였고, 제품 완성도보다 핵심 경험 확인이 우선이었다.

### 2차 운영 정리 단계
- 업로드 화면과 processing 상태 화면이 추가됐다.
- orchestrator가 들어가서 업로드 후 background pipeline이 자동으로 이어지게 됐다.
- 이 단계부터 관심사가 기능 존재 여부보다 “실제로 돌릴 수 있는가”로 옮겨갔다.

### 3차 비용/성능 대응 단계
- 비용/성능 병목 분석 문서가 추가됐다.
- reasoning effort 하향, pass2 diversity retry 제거, timeout/parallelism 조정이 들어갔다.
- parser abstraction, canonical parse artifact, `page_manifest`, pass1 text-first routing이 들어갔다.
- benchmark harness와 corpus runner가 추가되면서, 감이 아니라 수치로 전/후 비교할 준비가 생겼다.

## 수정했던 사항과 수정 이유

### pass2 diversity retry 제거
- 이유: 비용 대비 효과가 불안정했고, 추가 호출이 tail latency를 더 키웠기 때문이야.

### reasoning downshift
- 이유: pass1/pass2는 fan-out 구조라 reasoning effort가 높을수록 누적 비용과 지연이 빠르게 커졌기 때문이야.

### parse abstraction 도입
- 이유: parser raw output을 코드 전역에 퍼뜨리지 않고, canonical schema 뒤에 숨겨야 이후 parser를 바꾸거나 추가해도 영향이 작기 때문이야.

### `page_manifest` 도입
- 이유: pass1이 parse artifact 전체를 다시 뜯지 않고도 page-level routing 결정을 할 수 있게 만들기 위해서야.

### `PASS1_ROUTING_MODE` 도입
- 이유: hybrid routing 품질 문제가 생기면 기존 multimodal 전체 경로로 즉시 rollback할 수 있어야 했기 때문이야.

### text-first cheap path 도입
- 이유: 비용 절감이 “언젠가”가 아니라 지금 당장 실제로 시작돼야 했고, text-rich 페이지에서 image multimodal을 줄이는 게 가장 직접적인 절감 포인트였기 때문이야.

### benchmark / telemetry 도입
- 이유: 처리 시간이 길고 비용이 큰 상황에서 감으로 최적화하면 방향을 잃기 쉬워서, 문서 단위 수치가 필요했기 때문이야.

### corpus runner 도입
- 이유: 문서 1개만 보고 최적화 전/후를 판단하면 편향이 커서, corpus 단위 baseline 비교가 필요했기 때문이야.

## 현재 중요한 설정과 토글

- `DOCUMENT_PARSER_BACKEND=stub|pymupdf4llm`
  - 기본은 `pymupdf4llm`
  - parser import/runtime 문제가 있으면 stub fallback 경로도 준비돼 있다.
- `PASS1_ROUTING_MODE=hybrid|legacy`
  - `hybrid`: parse/page_manifest 기반 text-first + selective multimodal
  - `legacy`: 기존 full-page multimodal pass1
- 모델 기본값
  - pass1: `gpt-5.4`, reasoning `medium`
  - synthesis: `gpt-5.4`, reasoning `medium`
  - pass2: `gpt-5.4`, reasoning `medium`
- benchmark 비교 시 같이 봐야 할 조건
  - parser backend
  - pass1 routing mode
  - stage별 model / reasoning effort
  - OpenAI timeout / retries
  - render image long edge
- 주의
  - `.env.example`에는 아직 `DOCUMENT_PARSER_BACKEND`, `PASS1_ROUTING_MODE`, `PARSER_SCHEMA_VERSION` 같은 최신 스위치가 반영돼 있지 않다.

## 현재 artifact / 데이터 구조

- `data/raw_pdfs/{document_id}.pdf`
  - 업로드된 원본 PDF 저장 위치
- `data/rendered_pages/{document_id}/{page}.png`
  - viewer와 multimodal 입력에 쓰는 페이지 이미지
- `data/analysis/{document_id}/`
  - pass1 / synthesis / pass2 artifact 저장 위치
  - viewer용 최종 page 결과는 여기 있는 pass2 artifact 기준
- `data/parsed/{document_id}/document_parse.json`
  - canonical parse source of truth
- `data/parsed/{document_id}/page_manifest.json`
  - page-level routing artifact
- `data/parsed/{document_id}/pages/{page}.json`
  - page parse mirror
  - 기본 강제 저장이 아니라 lazy materialization
- `data/analysis/{document_id}/processing_benchmark.json`
  - 문서 단위 benchmark / telemetry artifact
- `docs/perf_runs/*.json`
  - corpus benchmark run 결과

## 새 챗방이 먼저 읽어야 할 파일

### A. 지금 당장 필수 3개
1. `docs/chat_handoff_2026-03-29.md`
2. `docs/api_model_decisions.md`
3. `backend/README.md`

### B. 필요 시 추가 읽기 파일들
1. `docs/perf_baseline.md`
2. `backend/app/services/orchestrator.py`
3. `backend/app/services/pass1_analyzer.py`
4. `backend/app/services/storage.py`
5. `frontend/components/DocumentViewer.tsx`
6. `frontend/components/ProcessingStatus.tsx`
7. `frontend/lib/api.ts`
8. `docs/COST_PERF_DIAGNOSIS.md`
9. `docs/project_audit.md`
10. `docs/scholium_product_prd_v0_revised.md`
11. `docs/scholium_development_prd_v0_revised.md`

## stale 문서 / source of truth

### historical reference only
- `docs/project_audit.md`
  - 리스크 지도나 과거 상태를 보는 데는 유용하지만, 현재 HEAD와 어긋나는 항목이 있다.
  - 예: 업로드 화면/processing 화면/orchestration 부재처럼 지금은 더 이상 사실이 아닌 서술이 있다.
- `docs/scholium_product_prd_v0_revised.md`
  - 제품 의도와 원칙을 보는 용도
  - 최신 구현 상태를 알려주는 문서는 아니다.
- `docs/scholium_development_prd_v0_revised.md`
  - 구현 스펙의 출발점으로는 유용하지만, 지금 실제 코드의 최신 결정과는 차이가 있다.

### 부분적으로 stale 가능
- `docs/api_model_decisions.md`
  - 가장 중요한 운영/구조 결정 문서이긴 하지만, additive side-step처럼 일부 설명은 현재 hybrid 기본 동작과 완전히 같지 않다.
  - 읽을 때는 항상 현재 코드와 같이 봐야 한다.
- `docs/perf_baseline.md`
  - baseline 체계 설명은 최신에 가깝다.
  - 하지만 커밋된 perf run 샘플은 interrupted run이라 usable baseline 숫자라고 보면 안 된다.

### 지금 source of truth
- 1순위: main 브랜치의 현재 코드
- 2순위: 최신 artifact 구조와 실제 benchmark artifact
- 3순위: 이 handoff 문서
- 4순위: 나머지 PRD / audit / 진단 문서

즉, 새 챗방은 오래된 PRD나 audit를 “현재 사실”로 읽으면 안 되고, 현재 코드 + artifact + handoff 문서를 먼저 봐야 해.

## 주의: 문서와 코드가 어긋나는 부분

- `docs/project_audit.md`의 업로드/processing/orchestration 부재 진술은 현재 코드와 다르다.
- `docs/api_model_decisions.md`의 additive/non-blocking side-step 설명도 현재 hybrid 기본 동작과 완전히 같지 않다. 지금은 hybrid에서 parse/triage가 pass1 직전 precondition으로 inline 수행된다.
- baseline 문서는 체계 설명에는 유용하지만, 현재 커밋된 perf run 샘플은 interrupted run이라 usable baseline이 아니다.

## 현재 리스크 / 미완성

- synthesis hard gate가 강해서 partial success라도 viewer-ready로 못 가는 경우가 있다.
- pass2가 아직 전체 지연의 큰 비중을 차지한다.
- `scan-like` evidence와 page parse mirror evidence가 약하다.
- benchmark 재사용 경로는 코드상 있지만 실제 저장 evidence는 아직 약하다.
- automated tests가 부족하다.
- 프론트는 CORS보다 Next rewrite 전제에 기대고 있다.
- summary API는 풍부하지만 viewer는 아직 일부만 소비한다.
- corpus runner는 로컬엔 있지만 현재 워킹트리 기준 untracked라 commit 상태와 분리해서 봐야 한다.

## 앞으로 진행할 내용

1. 최신 상태 기준 handoff / audit 문서를 다시 정리해서 문서 드리프트를 줄이기
2. 실제 corpus 5~10개로 usable baseline 다시 수집하기
3. text-first가 실제 비용/호출 수를 얼마나 줄였는지 검증하기
4. pass2 지연/비용 절감 아이디어를 검증하기
5. partial viewer 또는 synthesis gate 재검토하기
6. artifact-DB drift / `StorageService` 과밀 / `DocumentViewer` 집중을 리팩터링 후보로 정리하기

## 현재 워킹트리 상태

- tracked 수정 중
  - `backend/README.md`
  - `docs/perf_baseline.md`
- untracked 로컬 작업 / 산출물
  - `backend/scripts/`
  - `docs/perf_runs/`
  - 여러 `data/parsed/doc_*`
- 의미
  - “main에 커밋된 상태”와 “로컬 실험 상태”를 분리해서 봐야 한다.
  - 특히 corpus runner와 perf run 일부는 로컬 작업 흔적로 봐야 하고, handoff 시에는 이 점을 같이 넘겨야 한다.

## 빠른 사실 체크용 메모

- 로컬 DB 기준 문서 상태 수
  - `completed = 12`
  - `failed = 15`
  - `analyzing = 6`
  - `uploaded = 3`
- interaction log 누적
  - `page_view = 72`
  - `anchor_click = 19`
  - `related_page_jump = 3`

이 숫자는 “완전 미검증 repo”가 아니라, 적어도 upload / processing / viewer까지 실제로 여러 번 만져본 로컬 상태라는 정도의 참고치로 쓰면 돼.
