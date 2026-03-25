# Project Audit

## Executive Summary

현재 Scholium은 `upload -> render -> pass1`까지는 실제 코드와 데이터가 존재하는 상태다. 반면 PRD가 P0로 요구하는 `document synthesis`, `pass2`, `viewer`, `overlay`, `click panel`, `page result API`는 아직 구현되지 않았다. 즉, "백엔드 핵심 파이프라인의 앞 3단계 + 내부 debug 확인 경로"까지만 닫혀 있고, 사용자가 체감하는 P0 데모 경험은 아직 완성되지 않았다.

이 감사는 실제 repo 파일, FastAPI route 표면, SQLite schema, 저장된 JSON artifact, 현재 데이터 디렉터리 상태를 기준으로 작성했다. 숨겨진 브랜치나 외부 폴더는 보지 못했으므로, 이 작업공간 기준으로만 판단한다.

## Current Build Status

### 실제 구현 상태 요약

| 항목 | 상태 | 근거 |
| --- | --- | --- |
| 업로드 | 구현됨 | `POST /api/documents`, PDF 헤더 검사, SQLite 저장 |
| 렌더링 | 구현됨 | CLI worker, PNG/RGB/1600px, `pages` row 생성 |
| 상태 저장 | 구현됨 | SQLite `documents`, `pages` 테이블 |
| pass1 | 구현됨 | Responses API, schema validation, JSON artifact 저장, debug read |
| synthesis | 부분만 있음 | prompt/schema/client wrapper만 있음, worker/API/persistence 없음 |
| pass2 | 부분만 있음 | prompt/schema/client wrapper만 있음, worker/API/persistence 없음 |
| viewer | 미구현 | `frontend/.gitkeep`만 존재 |
| overlay | 미구현 | 프론트 코드 없음, final anchor serve도 없음 |
| click panel | 미구현 | 프론트 코드 없음, pass2 artifact도 없음 |
| logs | 미구현 | `data/logs/.gitkeep`만 있고 code 없음 |

### 현재 실행 표면

- FastAPI public/debug route
  - `GET /health`
  - `POST /api/documents`
  - `GET /api/documents/{document_id}/debug/pass1/{page_number}`
- Internal worker
  - `python -m app.workers.render_worker <document_id>`
  - `python -m app.workers.pass1_worker <document_id> [--page-number ...]`

### 현재 데이터 스냅샷

감사 시점 SQLite 상태:

- `documents` 상태 분포
  - `analyzing`: 5
  - `failed`: 1
  - `uploaded`: 3
- `pages.render_status` 분포
  - `rendered`: 13
  - `failed`: 4
- `pages.pass1_status` 분포
  - `completed`: 3
  - `failed`: 1
  - `NULL`: 13

현재 실제 JSON artifact는 `page_analysis_pass1.json` 3개만 존재한다.

## PRD Alignment Check

### PRD와 일치하는 부분

- 업로드 API는 P0 요구와 대체로 맞다.
  - multipart PDF 업로드
  - `document_id`, `status=uploaded` 반환
  - SQLite metadata 저장
- 렌더링 표준은 PRD와 맞는다.
  - PNG
  - RGB
  - 긴 변 1600px
  - repo-root-relative path 정책
  - `total_pages`, `pages` row 생성
- pass1 방향도 PRD와 맞는다.
  - page image 중심 입력
  - Responses API
  - JSON only + schema validation
  - `meta/result` envelope
  - normalized bbox
  - decorative exclusion rule 반영
  - `model_name`, `prompt_version`, `schema_version` 저장

### PRD와 불일치하거나 아직 비어 있는 부분

- PRD P0 항목 중 아래는 아직 실제 동작 코드가 없다.
  - document synthesis
  - pass2
  - page result API
  - viewer
  - overlay
  - click panel
- PRD는 `GET /api/documents/{document_id}`, `GET /summary`, `GET /pages/{page_number}`, `POST /api/logs`를 요구하지만 현재 없다.
- PRD는 "사용자가 PDF 업로드 후 viewer 진입"을 목표로 하지만, 지금은 viewer 자체가 없다.
- PRD는 `document_summary.json`, `page_analysis_final.json`, logs를 저장 대상으로 두지만, 실제 저장된 artifact는 pass1만 있다.
- PRD는 background job enqueue 문맥이 있지만 실제 실행은 CLI worker 수동 호출이다.

### 현재 단계 판단

현재 구현은 PRD 전체 P0 기준으로 보면 완료가 아니라 "P0의 백엔드 선행 기반 일부 완료" 상태다. 엄격히 보면 P0 핵심 사용자 경험은 아직 시작 전 단계에 가깝다.

## Architecture Review

### 실제 구조

- 설정
  - `backend/app/core/config.py`
  - plain dataclass 기반 설정
  - stage별 model / reasoning / prompt_version 중앙화
- 업로드/저장
  - `backend/app/api/documents.py`
  - `backend/app/services/storage.py`
- 렌더
  - `backend/app/services/pdf_render.py`
  - `backend/app/workers/render_worker.py`
- pass1
  - `backend/app/services/openai_client.py`
  - `backend/app/services/pass1_analyzer.py`
  - `backend/app/workers/pass1_worker.py`
  - `backend/app/api/debug.py`

### 현재 파이프라인

1. `POST /api/documents`로 원본 PDF 저장
2. `render_worker`가 PDF를 페이지 PNG로 렌더
3. `pass1_worker`가 렌더된 페이지를 OpenAI로 분석
4. 결과를 JSON artifact로 저장
5. debug GET으로 내부 확인

### 아직 없는 구조

- render 이후 자동으로 pass1이 이어지는 orchestrator
- synthesis orchestrator
- pass2 orchestrator
- viewer가 소비할 read model API
- usage log 수집 경로

### 구조적 평가

- 좋은 점
  - 설정과 OpenAI stage 기본값은 중앙화되어 있다.
  - 경로 기준은 repo root로 대체로 통일되어 있다.
  - artifact envelope와 bbox 정책은 비교적 일관적이다.
- 문제점
  - `StorageService`에 DB CRUD, path 계산, JSON 저장, artifact validation이 몰려 있다.
  - worker는 있으나 서비스 간 자동 연결이 없어 운영 흐름이 수동이다.
  - 문서 상태와 페이지 상태의 aggregate 규칙이 아직 약하다.

## Data / Schema Review

### SQLite schema

실제 스키마:

- `documents`
  - `document_id`
  - `filename`
  - `original_path`
  - `status`
  - `total_pages`
  - `created_at`
  - `updated_at`
  - `error_message`
- `pages`
  - `id`
  - `document_id`
  - `page_number`
  - `image_path`
  - `render_status`
  - `width`
  - `height`
  - `pass1_status`
  - `pass2_status`

평가:

- upload/render/pass1 단계까지는 충분하다.
- synthesis/pass2/logs를 붙이기 위한 별도 aggregate 메타 구조는 아직 없다.
- `documents.status`는 coarse-grained 상태이고, render 이후 계속 `analyzing`에 머문다.

### JSON artifact 구조

현재 실제 artifact:

- `data/analysis/{document_id}/pages/{page_number}/page_analysis_pass1.json`

구조:

```json
{
  "meta": {
    "schema_version": "0.1",
    "prompt_version": "pass1_v0_1",
    "model_name": "gpt-5.4-2026-03-05",
    "generated_at": "ISO8601"
  },
  "result": {
    "document_id": "doc_xxx",
    "page_number": 1,
    "page_role": "string",
    "page_summary": "string",
    "candidate_anchors": []
  }
}
```

평가:

- PRD의 `meta/result` envelope와 맞는다.
- `document_summary.json`, `page_analysis_final.json`는 아직 없다.

### bbox / path policy

- bbox
  - normalized `[x, y, w, h]`
  - 0~1 범위
  - `w/h > 0`
  - `x+w<=1`, `y+h<=1`
- path
  - DB에는 repo-root-relative POSIX 문자열 저장
  - raw PDF: `data/raw_pdfs/...`
  - rendered pages: `data/rendered_pages/...`
  - analysis artifact: `data/analysis/...`

평가:

- 이 부분은 현재 구현 품질이 가장 안정적이다.

## API / Worker Review

### Public API

현재 있는 것:

- `POST /api/documents`
- `GET /health`

현재 없는 것:

- `GET /api/documents/{document_id}`
- `GET /api/documents/{document_id}/summary`
- `GET /api/documents/{document_id}/pages/{page_number}`
- `POST /api/logs`

평가:

- 업로드만 public으로 열려 있고, viewer가 소비할 조회 API가 없다.
- 지금 상태에선 프론트가 있어도 데이터를 받아올 공식 read path가 부족하다.

### Debug API

현재 있는 것:

- `GET /api/documents/{document_id}/debug/pass1/{page_number}`

없는 것:

- pass2 debug
- synthesis debug
- QA aggregate endpoints

평가:

- 내부 확인 경로는 pass1 한정으로만 있음.

### Internal trigger / worker

현재 있는 것:

- `render_worker`
- `pass1_worker`

없는 것:

- synthesis worker
- pass2 worker
- end-to-end pipeline runner

평가:

- worker 구조는 나쁘지 않지만, 아직 문서 단위 전체 파이프라인이 이어지지 않는다.

### OpenAI integration

현재 확인된 원칙:

- Responses API 사용
- stage config 중앙화
- strict json_schema
- local pydantic validation
- validation/parsing 실패 시 repair retry 1회
- `document_id`, `page_number`는 서버가 보정

리스크:

- 모든 이미지를 data URL base64로 직접 올리는 방식이라 페이지 수가 늘면 비용/메모리/지연이 커질 수 있다.
- synthesis/pass2는 wrapper만 있고 실제 orchestration이 없다.

## Risks

### 가장 위험한 부분 TOP 5

1. **프론트엔드/viewer가 아예 없음**
   - P0 핵심 사용자 경험이 아직 전혀 구현되지 않았다.
   - 현재 repo 기준으론 `frontend/.gitkeep`만 있다.

2. **viewer가 소비할 public read API 부재**
   - 문서 상태, summary, page final result API가 없다.
   - viewer를 바로 붙일 수 있는 데이터 계약이 비어 있다.

3. **synthesis / pass2 미구현**
   - pass1까지만 있으므로 final anchors 3~5개, related pages, click panel 데이터가 나오지 않는다.
   - PRD가 말하는 “유용한 클릭형 해설 경험”을 아직 만들 수 없다.

4. **실행 흐름이 수동 CLI 기반**
   - 업로드 후 render/pass1이 자동 연결되지 않는다.
   - 내부 데모를 돌릴 때도 운영 실수가 생기기 쉽다.

5. **`StorageService` 책임 집중**
   - DB, artifact path, JSON write, validation까지 한 군데에 몰려 있다.
   - 다음 단계에서 synthesis/pass2/logs가 붙으면 변경 폭이 커질 가능성이 높다.

### 추가 리스크

- README와 실제 구현 상태가 완전히 맞지 않는다.
- 자동화된 테스트 파일이 없다.
- logs는 P1이지만, 내부 데모 QA 근거가 약해질 수 있다.

## Next Priorities

### 다음 3개 우선순위 작업

1. **document synthesis 구현**
   - worker + persistence + artifact 저장
   - 결과물: `document_summary.json`
   - 이유: pass2와 related pages의 입력이 비어 있기 때문

2. **pass2 구현**
   - worker + persistence + final anchor artifact 저장
   - 결과물: `page_analysis_final.json`
   - 이유: viewer/overlay/click panel이 쓸 최종 데이터가 아직 없기 때문

3. **viewer가 소비할 public read API + 최소 viewer 구현**
   - backend: document/page/summary read API
   - frontend: 최소 viewer + overlay + click panel
   - 이유: 지금 구현 상태를 실제 데모 경험으로 연결하는 마지막 고리이기 때문

### 지금 당장 하지 말아야 할 것

- auth
- payment
- vector DB
- hover interaction
- voice
- collaboration
- mobile optimization
- Postgres 확장
- queue/broker 인프라 추가
- 성능 최적화
- dense extraction 비교 툴
- 로그 대시보드

즉, 지금은 P0 경험을 닫는 데 직접 기여하지 않는 인프라/운영/확장 작업을 붙이면 안 된다.

## Files to Share for External Review

외부 ChatGPT나 다른 리뷰어에게 추가 검토를 받을 때는 아래 파일을 같이 주는 게 좋다.

### 제품/개발 기준 문서

- `docs/scholium_product_prd_v0_revised.md`
- `docs/scholium_development_prd_v0_revised.md`
- `docs/api_model_decisions.md`

### 설정 / 앱 진입점

- `backend/app/core/config.py`
- `backend/app/main.py`
- `.env.example`

### 업로드 / 저장 / 렌더 / pass1 핵심 코드

- `backend/app/api/documents.py`
- `backend/app/api/debug.py`
- `backend/app/services/storage.py`
- `backend/app/services/pdf_render.py`
- `backend/app/services/openai_client.py`
- `backend/app/services/pass1_analyzer.py`
- `backend/app/workers/render_worker.py`
- `backend/app/workers/pass1_worker.py`

### 스키마 / 데이터 모델

- `backend/app/models/document.py`
- `backend/app/schemas/common.py`
- `backend/app/schemas/pass1_schema.py`
- `backend/app/schemas/document_summary.py`
- `backend/app/schemas/pass2.py`
- `backend/app/utils/validation.py`

### 프롬프트

- `docs/prompts/pass1_prompt.md`
- `docs/prompts/document_synthesis_prompt.md`
- `docs/prompts/pass2_prompt.md`

### 실제 산출물 예시

- `data/analysis/{sample_document_id}/pages/1/page_analysis_pass1.json`
- `data/scholium_dev.sqlite3`의 `documents`, `pages` schema 및 샘플 row

