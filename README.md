# Scholium

이번 스프린트 목표는 완성형 서비스가 아니라 내부 테스트 가능한 작동 데모다.

현재 MVP 방향은 precomputed anchor-click viewer가 아니라 selected-region viewer다.
Scholium은 PDF를 먼저 전처리해서 문서/페이지/요소 맥락을 이해해두고, 페이지 상단 Page Guide로 먼저 읽는 방향을 잡아준 뒤, 사용자가 실제로 막힌 영역을 드래그했을 때 그 영역에 붙는 설명을 생성한다.

## 이번 스프린트 범위

- P0만 구현한다.
- P1/P2는 이번 스프린트에서 건드리지 않는다.
- 아래 항목은 금지한다.
  - auth
  - payment
  - vector DB
  - hover
  - voice
  - collaboration
  - mobile optimization

## 리포 구조

```text
scholium/
  frontend/
  backend/
  data/
    raw_pdfs/
    rendered_pages/
    parsed/
    analysis/
    logs/
  docs/
  scripts/
  .env.example
  README.md
```

## 필수 준비물

- Codex CLI 로그인 상태
- Node.js
- Python 3.11+
- pnpm 또는 npm
- 테스트용 PDF 10~12개
- Git 저장소
- `.env.example`

OpenAI API 키는 기본 실행에 필요하지 않다. 로컬 MVP 분석 provider 기본값은 Codex CLI다.

## 빠른 시작

1. Codex CLI가 동작하는지 확인한다.

   ```bash
   codex --version
   ```

2. `.env.example`을 참고해서 `.env`를 만든다. 기본값은 아래처럼 Codex CLI provider다.

   ```bash
   SCHOLIUM_LLM_PROVIDER=codex_cli
   CODEX_CLI_BIN=codex
   CODEX_CLI_MODEL=gpt-5.5
   CODEX_CLI_REASONING=medium
   CODEX_CLI_TIMEOUT_SECONDS=300
   SCHOLIUM_PRECOMPUTE_ANCHORED_EXPLANATIONS=false
   PASS1_MAX_WORKERS=3
   ```

3. `data/raw_pdfs`에 테스트용 PDF를 넣거나 업로드 화면에서 PDF를 올린다.
   업로드한 PDF는 바로 viewer로 이동하지 않고 작업 목록에 추가된다. 같은 파일명을 다시 올리면 기존 작업을 덮어쓰고 새로 준비한다.

4. 백엔드와 프런트엔드를 실행한다.

   ```bash
   cd backend
   python3 -m uvicorn app.main:app --reload --port 8000
   ```

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

5. worker를 수동 실행할 때는 아래 순서로 돌린다. 기본 MVP에서는 pass2/final anchor 생성을 선제 실행하지 않는다.

   ```bash
   cd backend
   python3 -m app.workers.render_worker <document_id>
   python3 -m app.workers.pass1_worker <document_id>
   python3 -m app.workers.document_synthesis_worker <document_id>
   ```

   legacy precomputed anchor-click artifact를 내부 비교용으로 다시 만들고 싶을 때만 아래 값을 켠 뒤 pass2 worker를 실행한다.

   ```bash
   SCHOLIUM_PRECOMPUTE_ANCHORED_EXPLANATIONS=true
   python3 -m app.workers.pass2_worker <document_id>
   ```

6. OpenAI API fallback을 명시적으로 쓰고 싶을 때만 `.env`를 바꾼다.

   ```bash
   SCHOLIUM_LLM_PROVIDER=openai_api
   OPENAI_API_KEY=...
   ```

## 기준 문서

- `docs/DEVELOPMENT_SOURCE_OF_TRUTH.md`
- `docs/CURRENT_ARCHITECTURE.md`
- `docs/LEGACY_NAMING_DEBT.md`
- `docs/api_model_decisions.md`
- `docs/perf/PARSER_DECISION_20260507.md`
- `docs/perf/PERFORMANCE_BASELINE_PLAN.md`

`docs/archive/` 아래 문서는 historical reference다. 현재 selected-region architecture를 덮어쓰는 기준 문서로 쓰지 않는다.

## 참고

- `data/raw_pdfs`: 원본 PDF 입력
- `data/rendered_pages`: 페이지 렌더링 결과
- `data/parsed`: parser/page manifest runtime 산출물
- `data/analysis`: 전처리/문서요약/selection explanation 산출물
- `data/logs`: 실행 로그

## Selected-region flow

1. viewer는 PDF 페이지 이미지를 깨끗하게 보여준다.
2. 홈의 작업 목록은 저장된 문서, 준비 상태, 준비 시간, 삭제/처리상태/viewer 진입 버튼을 보여준다.
3. page image만 준비된 상태면 viewer는 `render_only`로 먼저 열린다.
4. pass1 page context가 준비되면 top-edge `Page Guide`가 페이지 역할, 핵심 질문, 읽는 순서, 논리 흐름, study focus를 보여준다.
5. 사용자가 헷갈리는 영역을 드래그하면 frontend가 normalized bbox `[x, y, w, h]`를 보낸다.
6. document synthesis까지 준비되면 `on_demand`가 되고, 문서 전체 맥락이 포함된 full selected-region explanation을 만든다.
7. backend가 full pass1/document artifact를 그대로 보내지 않고 compact `SelectionContext`를 만든다.
8. Codex CLI가 선택 영역 전용 JSON 설명을 생성한다.
9. schema validation을 통과한 결과만 `data/analysis/<document_id>/pages/<page>/selection_explanations/`에 저장된다.
10. floating academic annotation panel이 선택 영역 옆에 뜬다.

Scholium의 설명 UI는 두 레이어로 나뉜다.

- `Page Guide`: 페이지 단위, proactive, macro orientation. "이 페이지를 어떻게 읽어야 하지?"에 답한다.
- `Selected Explanation Panel`: 선택 영역 단위, reactive, micro explanation. "내가 드래그한 이 부분은 무슨 뜻이지?"에 답한다.

Pass1 artifact의 persisted field는 legacy 호환 때문에 아직 `candidate_anchors`지만, 현재 제품 의미와 public page API 이름은 `page_elements`다. 새 코드에서는 `page_elements` / `element_id` / `element_type`을 우선 쓰고, `candidate_anchors` / `anchor_id` / `anchor_type`은 저장 artifact와 legacy/debug 호환용으로만 취급한다.
`page_guide`는 pass1의 page-level artifact로 저장되며, 오래된 artifact에 없으면 API가 `page_role`과 `page_summary` 기반의 최소 fallback만 제공한다.

## Readiness modes

- `render_only`: PDF page image만 준비됐다. 사용자는 읽을 수 있지만 selection explanation은 막힌다.
- `page_context_ready`: pass1 page context가 준비됐다. selection explanation을 만들 수 있고, document context가 아직 없으면 page 중심으로 제한된다.
- `on_demand`: page context와 document context가 모두 준비됐다. 기본 selected-region MVP 모드다.
- `legacy_pass2`: precomputed anchor-click debug path다. 기본값에서는 쓰지 않는다.

## Codex CLI provider 제한

- 이 구조는 로컬 MVP 개발용이다. production용 model serving 구조가 아니다.
- Codex CLI는 subprocess로 실행되며 stage별 JSON schema 검증을 통과해야 artifact가 저장된다.
- malformed JSON은 한 번만 repair 요청을 시도하고, 그래도 실패하면 해당 stage를 실패 처리한다.
- pass1과 selection explanation은 페이지 이미지가 Codex CLI image attachment로 전달된다.
- selection explanation은 기본적으로 `CODEX_CLI_MODEL=gpt-5.5`, `CODEX_CLI_REASONING=medium`을 사용한다. 설치된 CLI가 지원하지 않으면 가장 가까운 지원 설정으로 바꾸고 이 파일에 기록해야 한다.
- 기존 OpenAI provider 코드는 남아 있지만 기본값이 아니며, `SCHOLIUM_LLM_PROVIDER=openai_api`일 때만 사용된다.

## Performance benchmarks

selected-region dry-run:

```bash
cd backend
./.venv/bin/python scripts/benchmark_selected_region_perf.py \
  --auto-first-ready \
  --limit 3 \
  --dry-run
```

실제 selection explanation latency 측정:

```bash
cd backend
./.venv/bin/python scripts/benchmark_selected_region_perf.py \
  --selection-file ../docs/perf/selected_region_cases.example.json \
  --no-dry-run
```

parser 후보 비교:

```bash
cd backend
./.venv/bin/python scripts/benchmark_pdf_parsers.py \
  --pdf-dir ../data/raw_pdfs \
  --limit 5
```

full-product parser benchmark는 모든 parser output을 `PageElementMap` 형태로 정규화한 뒤 비교한다. 기준은 Markdown 예쁨이 아니라 selected-region explanation에 필요한 bbox 매칭, reading order, layout element, source cue, OCR/scan robustness, speed다. 기본 parser backend는 계속 `DOCUMENT_PARSER_BACKEND=pymupdf4llm`이고, 이 adapter가 현재는 `pymupdf4llm_enhanced+fitz` path로 동작한다. Docling/Marker/MinerU/MarkItDown은 production default install에 포함하지 않는다.

gold selection starter는 `benchmarks/parser_selection_goldset.yaml`에 있다. 사람이 아직 확정 검수하지 않은 좌표는 proxy/gold seed로만 보고, 최종 parser 선택 전 viewer에서 직접 확인해야 한다.

`PASS1_MAX_WORKERS=1|2|3`을 붙여 pass1 병렬도도 비교할 수 있다. 기본값은 `3`이고, Codex CLI subprocess 병렬도는 실제 PDF 묶음에서 측정한 뒤 조정한다.
