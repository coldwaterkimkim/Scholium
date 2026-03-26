# Project Audit

## Executive Summary

현재 Scholium은 PRD의 핵심 P0 흐름인 `upload -> render -> pass1 -> document synthesis -> pass2 -> public read API -> viewer -> overlay/click panel -> logs`까지 코드가 실제로 존재한다. 즉, 이전 감사 시점과 달리 지금은 "백엔드 선행 기반 일부"가 아니라, 내부 테스트용 end-to-end 데모가 가능한 상태다.

다만 이 결론은 "기능 코드가 있다"는 뜻이지, 운영 흐름까지 닫혔다는 뜻은 아니다. 현재 파이프라인은 여전히 internal worker 수동 실행 중심이고, `documents.status`를 최종 완료 상태로 집계하는 로직도 없다. 프론트도 viewer는 구현됐지만, 제품 PRD가 요구한 업로드 화면과 처리 상태 화면은 없다. 따라서 현재 상태를 가장 정확히 표현하면 다음과 같다.

- 내부 테스트용 E2E 데모는 가능하다.
- 운영/사용자용 완결 흐름은 아직 아니다.
- 가장 큰 빈칸은 orchestration, 상태 집계, 업로드/처리 상태 UI다.

이 문서는 실제 코드, 실제 SQLite 상태, 실제 JSON artifact 존재 여부를 기준으로 작성했다. 성능 수치, 대용량 문서 운영성, 배포 프록시 동작처럼 실행 기반 계측이 없는 항목은 불확실하다고 명시한다.

## Current Build Status

### 실제 구현 범위

| 항목 | 상태 | 근거 |
| --- | --- | --- |
| upload | 구현됨 | `POST /api/documents`, PDF 헤더 검사, SQLite 저장 |
| render | 구현됨 | PyMuPDF, PNG/RGB/긴 변 1600px, `pages` row 생성 |
| pass1 | 구현됨 | Responses API, schema validation, atomic JSON 저장 |
| document synthesis | 구현됨 | pass1 artifact 기반 입력, coverage threshold, summary artifact 저장 |
| pass2 | 구현됨 | page image + pass1 + summary 입력, subset/rerank+refine, atomic JSON 저장 |
| public read API | 구현됨 | document / summary / page 조회 API |
| viewer | 구현됨 | `/documents/[documentId]`, image viewer 기반 |
| overlay | 구현됨 | final anchor overlay, normalized bbox -> pixel 변환 |
| click panel | 구현됨 | anchor click 시 우측 패널 상세 갱신, related page jump |
| logs | 구현됨 | `POST /api/logs`, SQLite `interaction_logs` 저장 |

### 현재 실행 표면

#### Public API

- `POST /api/documents`
- `GET /api/documents/{document_id}`
- `GET /api/documents/{document_id}/summary`
- `GET /api/documents/{document_id}/pages/{page_number}`
- `POST /api/logs`
- `GET /health`
- `/static/rendered-pages/...`

#### Debug API

- `GET /api/documents/{document_id}/debug/pass1/{page_number}`
- `GET /api/documents/{document_id}/debug/summary`
- `GET /api/documents/{document_id}/debug/pass2/{page_number}`

#### Internal Trigger / Worker

- `python -m app.workers.render_worker <document_id>`
- `python -m app.workers.pass1_worker <document_id> [--page-number ...]`
- `python -m app.workers.document_synthesis_worker <document_id>`
- `python -m app.workers.pass2_worker <document_id> [--page-number ...]`

### 현재 데이터 스냅샷

감사 시점 SQLite 상태:

- `documents.status`
  - `analyzing`: 5
  - `failed`: 1
  - `uploaded`: 3
- `pages.render_status`
  - `rendered`: 13
  - `failed`: 4
- `pages.pass1_status`
  - `completed`: 3
  - `failed`: 1
  - `NULL`: 13
- `pages.pass2_status`
  - `completed`: 3
  - `NULL`: 14
- `interaction_logs`
  - `page_view`: 5
  - `anchor_click`: 1
  - `related_page_jump`: 1

실제 artifact 존재 범위:

- `data/analysis/doc_675b18899dbc4761a76b3bc8249f00b8/`
  - `document_summary.json`
  - page 1~3의 `page_analysis_pass1.json`
  - page 1~3의 `page_analysis_pass2.json`
- `data/analysis/doc_587a1c6450024fc29d178433d04029e2/`
  - `document_summary.json`만 존재

해석:

- 전체 코드 경로는 존재하지만, 실제로 pass1/pass2/viewer까지 끝까지 돌려본 문서는 현재 1개에 가깝다.
- DB와 artifact는 "기능 구현"과 "실제 검증 범위"를 구분해서 봐야 한다.

## PRD Alignment Check

### 제품 PRD와 일치하는 부분

- FR-1 PDF 업로드: 구현됨
- FR-2 페이지 렌더링: 구현됨
- FR-3 작업 상태 저장: 구현됨
- FR-6 페이지 1차 분석: 구현됨
- FR-7 문서 구조 합성: 구현됨
- FR-8 페이지 2차 보정: 구현됨
- FR-12 앵커 렌더링: 구현됨
- FR-13 click-only 인터랙션: 구현됨
- FR-14 2층 설명 구조: 구현됨
- FR-15 뜻 + 역할 설명: 구현됨

근거:

- backend pipeline: `backend/app/services/*`, `backend/app/workers/*`
- public read API: `backend/app/api/documents.py`
- viewer/overlay/panel: `frontend/components/*`

### 제품 PRD와 불일치하거나 아직 비어 있는 부분

#### 1. 화면 단위 요구사항은 일부만 구현됨

제품 PRD의 화면 정의 중:

- 업로드 화면: 없음
- 처리 상태 화면: 없음
- PDF 뷰어 화면: 구현됨
- 내부 QA 디버그 모드: backend debug read API는 있음, 별도 QA 전용 프론트 화면은 없음

즉 사용자 관점의 전체 흐름은 아직 닫히지 않았다. 지금 프론트는 `/documents/{documentId}` viewer 단일 진입만 있다.

#### 2. `documents.status=completed` 흐름이 비어 있음

제품 PRD는 상태 예시로 `uploaded / rendering / analyzing / completed / failed`를 제시하지만, 실제 구현은 render 이후 문서를 `analyzing`으로 두고 pass1/synthesis/pass2가 끝나도 `completed`로 승격하지 않는다.

이건 단순 명칭 문제가 아니라, "이 문서가 정말 사용자 열람 준비가 끝났는지"를 coarse-grained하게 판단할 공식 상태가 아직 없다는 뜻이다.

#### 3. 다중 문서 관리/운영 표면은 없다

제품 PRD의 후속/출시 이후 요구사항은 현재 범위 밖으로 의도적으로 빠져 있다.

- 다중 파일 관리
- 계정 단위 문서 보관
- 사용자 피드백 기반 개선
- 설명 깊이 조절

이 부분은 미구현이 맞고, 현재 단계에서 억지로 넣지 않은 것도 방향상 맞다.

### 개발 PRD와 일치하는 부분

- API 계약 7.1~7.5는 대부분 구현됨
- 프론트 8.1~8.3의 최소 레이아웃, overlay, 우측 패널 요구사항이 구현됨
- Epic B~H의 핵심 뼈대가 실제 코드로 존재함
- OpenAI 연동 원칙(JSON only, schema validation, `meta/result` envelope)은 `docs/api_model_decisions.md`와 실제 구현이 일치함

### 개발 PRD와 불일치하거나 아직 비어 있는 부분

#### 1. interaction log 예시와 실제 구현이 완전히 같진 않다

개발 PRD 6.5 Interaction Log 예시는 `panel_expand`까지 포함하지만, 현재 구현은 다음 3개만 기록한다.

- `page_view`
- `anchor_click`
- `related_page_jump`

이건 최근 구현 의사결정과 일치하지만, 문서 기준으론 "의도적으로 줄인 차이"로 적는 게 맞다.

#### 2. Epic I1 처리 상태 UX는 아직 없다

개발 PRD는:

- 상태 polling
- analyzing/completed/failed 표시
- 완료 시 자동 viewer 진입

을 요구하는데, 현재 프론트에는 업로드/상태 화면 자체가 없다. viewer 안에서 문서 상태 문구는 보이지만, 처리 대기 흐름은 구현되지 않았다.

#### 3. Epic H2 품질 체크 UI 또는 체크리스트 연계는 없다

현재 debug artifact read는 있지만, 페이지별 품질 평가를 남기는 UI/체크리스트 연계는 없다.

#### 4. Epic I3 파일 보존/삭제 유틸은 없다

문서별 파일 경로 추적은 되지만, 수동 삭제 유틸이나 admin script는 없다.

## Architecture Review

### 실제 구조

#### Backend

- 설정/경로/모델 운용
  - `backend/app/core/config.py`
  - `docs/api_model_decisions.md`
- 업로드/저장
  - `backend/app/api/documents.py`
  - `backend/app/services/storage.py`
- 렌더
  - `backend/app/services/pdf_render.py`
  - `backend/app/workers/render_worker.py`
- 분석
  - `backend/app/services/openai_client.py`
  - `backend/app/services/pass1_analyzer.py`
  - `backend/app/services/document_synthesizer.py`
  - `backend/app/services/pass2_refiner.py`
- 로그
  - `backend/app/services/log_store.py`
  - `backend/app/api/logs.py`

#### Frontend

- 라우트
  - `frontend/app/documents/[documentId]/page.tsx`
- 핵심 화면 상태
  - `frontend/components/DocumentViewer.tsx`
- 우측 패널
  - `frontend/components/RightPanel.tsx`
- overlay
  - `frontend/components/AnchorOverlay.tsx`
- API client / rewrite
  - `frontend/lib/api.ts`
  - `frontend/next.config.mjs`

### 현재 파이프라인

실제 동작 흐름은 아래와 같다.

1. `POST /api/documents`
2. 원본 PDF 저장 + SQLite `documents` row 생성
3. internal render worker 실행
4. page PNG 생성 + `pages` row 저장
5. internal pass1 worker 실행
6. pass1 JSON artifact 저장 + `pages.pass1_status` 갱신
7. internal document synthesis worker 실행
8. `document_summary.json` 저장
9. internal pass2 worker 실행
10. pass2 JSON artifact 저장 + `pages.pass2_status` 갱신
11. public read API와 viewer가 결과를 읽음
12. viewer interaction은 `/api/logs`로 저장

### 구조적 평가

#### 강점

- backend와 frontend의 역할 분리가 분명하다.
- 분석 결과의 source of truth를 JSON artifact로 명확히 둔 점은 MVP에 맞다.
- public read API, debug API, internal worker가 섞이지 않고 분리돼 있다.
- bbox, path, envelope 규칙이 비교적 일관적이다.

#### 약점

- orchestration이 없어 실제 운영 흐름은 사람 손에 많이 의존한다.
- `StorageService`에 DB CRUD, path 계산, artifact normalize/validate가 몰려 있다.
- `DocumentViewer.tsx`도 문서 로딩/페이지 로딩/overlay/logging/navigation 상태가 한 파일에 집중돼 있다.

## Data / Schema Review

### SQLite schema

현재 실제 스키마:

#### `documents`

- `document_id`
- `filename`
- `original_path`
- `status`
- `total_pages`
- `created_at`
- `updated_at`
- `error_message`

#### `pages`

- `document_id`
- `page_number`
- `image_path`
- `render_status`
- `width`
- `height`
- `pass1_status`
- `pass2_status`

#### `interaction_logs`

- `event_id`
- `document_id`
- `page_number`
- `anchor_id`
- `event_type`
- `timestamp`

평가:

- 현재 프로토타입에는 충분하다.
- 다만 summary/pass2 완료 여부를 별도 집계하는 document-level read model은 없다.
- logs 테이블은 `documents` foreign key가 없어서 metadata drift가 있어도 저장이 막히지 않게 설계됐다.

### JSON artifact 구조

#### 공통 규칙

- 모든 AI 산출물은 `meta/result` envelope 사용
- 공통 메타:
  - `schema_version`
  - `prompt_version`
  - `model_name`
  - `generated_at`

#### 현재 파일 경로

- `document_summary.json`
  - `data/analysis/{document_id}/document_summary.json`
- pass1
  - `data/analysis/{document_id}/pages/{page_number}/page_analysis_pass1.json`
- pass2
  - `data/analysis/{document_id}/pages/{page_number}/page_analysis_pass2.json`

평가:

- 저장 구조 자체는 일관적이다.
- 다만 제품 PRD/개발 PRD의 용어 `page_analysis_final.json`과 실제 파일명 `page_analysis_pass2.json`은 어긋난다.
- 이 차이는 문서화만 어긋난 게 아니라, 외부 리뷰어가 artifact를 찾을 때 혼란을 줄 수 있다.

### meta/result envelope

현재 구현은 OpenAI 응답을 직접 저장하지 않고, 로컬 validation 이후 normalize된 envelope만 저장한다.

장점:

- `document_id`, `page_number`, `anchor_type`, `bbox`처럼 시스템이 알아야 하는 필드를 서버가 보정할 수 있다.
- malformed/invalid artifact를 저장 전에 걸러낼 수 있다.

주의:

- source of truth가 JSON artifact이기 때문에, artifact 손상 시 DB와 상태가 어긋날 수 있다.

### path policy

- 경로 기준 루트는 repo root
- DB와 artifact 내부 저장은 repo-root-relative POSIX string
- raw PDF: `data/raw_pdfs/...`
- rendered image: `data/rendered_pages/...`
- analysis JSON: `data/analysis/...`
- logs는 SQLite `data/scholium_dev.sqlite3`

평가:

- 현재 구현에서 가장 안정적인 부분 중 하나다.
- public page API는 `image_path`를 직접 노출하지 않고 `/static/rendered-pages/...`로 재매핑한다.

### bbox policy

- bbox는 normalized `[x, y, w, h]`
- 0~1 범위
- `w/h > 0`
- `x+w <= 1`, `y+h <= 1`
- pass2는 pass1 candidate의 `anchor_id`, `anchor_type`, `bbox`를 고정 재사용

평가:

- PRD 방향과 일치한다.
- viewer overlay도 이 정책을 그대로 전제하고 구현돼 있다.

## API / Worker Review

### Public API

현재 실제 public API는 다음과 같다.

- `POST /api/documents`
- `GET /api/documents/{document_id}`
- `GET /api/documents/{document_id}/summary`
- `GET /api/documents/{document_id}/pages/{page_number}`
- `POST /api/logs`
- `GET /health`

평가:

- viewer가 필요한 최소 read API는 모두 있다.
- 문서 목록, 재시도, 삭제, 로그 조회 같은 운영용 API는 없다.

### Debug API

- `GET /api/documents/{document_id}/debug/pass1/{page_number}`
- `GET /api/documents/{document_id}/debug/summary`
- `GET /api/documents/{document_id}/debug/pass2/{page_number}`

평가:

- raw artifact를 확인하기엔 충분하다.
- 내부 QA 전용 프론트 화면은 없고, HTTP read path만 존재한다.

### Internal Trigger

- render, pass1, synthesis, pass2 모두 CLI worker로 분리돼 있다.

평가:

- 구조는 명확하지만 자동 연결이 없다.
- 현재는 "서비스 함수는 재사용 가능, 실행은 수동"이라는 MVP 구조다.

## Frontend Review

### viewer state

핵심 상태는 `DocumentViewer.tsx`에 집중돼 있다.

- `currentPage`
- `totalPages`
- `documentMeta`
- `documentSummary`
- `currentPageData`
- `selectedAnchorId`
- `imageDisplayMetrics`
- `loading`
- `error`
- `summaryError`

평가:

- 현재 범위에서는 이해 가능하다.
- 하지만 다음 단계에서 기능이 더 붙으면 가장 먼저 비대해질 파일이다.

### overlay state

- `selectedAnchorData`를 따로 저장하지 않고 `selectedAnchorId`만 상태로 둔다.
- 현재 페이지의 `final_anchors`에서 derive한다.
- image bbox는 wrapper/img DOMRect 차이로 실측한다.

평가:

- stale state 문제를 잘 피했다.
- normalized bbox -> pixel 변환 경로도 명확하다.

### click interaction

현재 click interaction은 최소 범위로 구현됐다.

- anchor marker click
- related page jump click

의도적으로 빠진 것:

- hover
- drag/edit
- zoom/pan
- keyboard shortcut
- logs dashboard

평가:

- 현재 단계 PRD 범위와 맞다.
- 다만 related page jump는 읽기 전용 viewer navigation에 묶여 있어서, 이후 section jump나 anchor list가 생기면 navigation 로직을 다시 분리할 가능성이 있다.

### error/loading 처리

- document meta 404 또는 first page 404: 전체 에러
- summary 실패: 우측 패널 fallback
- page-level 실패: viewer 전체는 유지, navigation 가능
- 이미지 로드 후에만 `page_view` 로그 기록

평가:

- 내부 테스트용 viewer 기준으론 꽤 탄탄하다.
- 다만 "이미지 URL만 깨진 경우"는 별도 사용자 메시지가 약한 편이다. 이건 실제 배포/프록시 환경에서 문제가 될 수 있다.

## Risks

### 가장 큰 리스크 TOP 5

#### 1. orchestration 부재

현재 업로드 이후 단계가 자동으로 이어지지 않는다. 순서 보장, 재시도, 운영 반복성 모두 사람 손에 많이 의존한다.

#### 2. `documents.status=completed` 집계 부재

render 이후 `analyzing`에 머물고, synthesis/pass2/viewer 준비 완료 상태를 문서 메타만 보고는 판단하기 어렵다.

#### 3. `StorageService` 책임 과밀

DB, path, artifact normalize/save/load/validation이 한 클래스에 몰려 있다. 지금은 단순해서 괜찮지만, 운영 기능이 늘면 가장 먼저 부담이 커질 지점이다.

#### 4. artifact와 DB 메타의 드리프트 가능성

JSON이 분석 결과 source of truth이고 DB는 상태 보조인데, 손상/누락/부분 실패 시 둘 사이 불일치가 생길 수 있다.

#### 5. 프론트 viewer의 구조적 집중

`DocumentViewer.tsx`가 문서 로딩, 페이지 로딩, overlay, click, logging, navigation을 다 들고 있다. 다음 기능 확장 시 프론트 쪽 병목이 될 가능성이 높다.

### 추가 리스크

- 배포/프록시 환경에서 absolute `image_url` 처리
- 큰 문서에서 이미지 기반 viewer의 체감 성능
- logs는 저장만 있고 활용 표면이 없어서, 쌓여도 운영 피드백 루프는 아직 없다

불확실한 점:

- 대용량 문서 성능은 코드상 추정만 가능하고, 실제 계측은 아직 없다.
- reverse proxy/CDN 배포 동작도 현재는 로컬 dev 기준으로만 확인된 상태다.

## Next Priorities

### 다음 3개 우선순위 작업

#### 1. 업로드 이후 자동 파이프라인 orchestration + 완료 상태 집계

가장 먼저 필요한 건 기능 추가가 아니라 흐름 연결이다.

- upload 이후 render/pass1/synthesis/pass2 자동 연결
- 실패/부분 실패 처리 규칙 고정
- `documents.status=completed` 또는 동등한 readiness 집계 추가

이게 없으면 기능은 있어도 사용자 흐름이 계속 반수동으로 남는다.

#### 2. 업로드 화면 + 처리 상태 화면 최소 구현

현재 viewer는 있지만 제품 PRD의 화면 1, 2가 없다.

- 업로드 화면
- 처리 중/실패/완료 상태 화면
- 완료 시 viewer 진입

이 단계가 있어야 내부 테스트가 아니라 실제 사용자 흐름에 가까워진다.

#### 3. 운영/품질 보조 표면 추가

현재 제일 필요한 건 fancy analytics가 아니라 기본 운영성이다.

추천 범위:

- 문서 목록 또는 최근 문서 진입점
- 실패 재시도 표면
- artifact/상태 불일치 점검용 최소 admin/QA 체크리스트
- 로그 조회는 대시보드보다 먼저 "간단 확인 쿼리/스크립트" 수준이면 충분

### 지금 당장 하지 말아야 할 것

- auth / payment
- vector DB
- hover UX
- voice / collaboration
- 모바일 본격 최적화
- PDF.js 전환
- analytics dashboard
- multi-user / multi-file account system
- direct PDF-to-model 경로
- 모델 비교 실험 코드

이건 지금 병목을 풀지 않고 표면적 복잡도만 늘릴 가능성이 크다.

## Files to Share for External Review

### 기준 문서

- `docs/scholium_product_prd_v0_revised.md`
- `docs/scholium_development_prd_v0_revised.md`
- `docs/api_model_decisions.md`

### backend 핵심

- `backend/app/main.py`
- `backend/app/core/config.py`
- `backend/app/api/documents.py`
- `backend/app/api/debug.py`
- `backend/app/api/logs.py`
- `backend/app/services/storage.py`
- `backend/app/services/log_store.py`
- `backend/app/services/openai_client.py`
- `backend/app/services/pass1_analyzer.py`
- `backend/app/services/document_synthesizer.py`
- `backend/app/services/pass2_refiner.py`
- `backend/app/workers/render_worker.py`

### schema / prompt

- `backend/app/schemas/common.py`
- `backend/app/schemas/pass1_schema.py`
- `backend/app/schemas/document_summary_schema.py`
- `backend/app/schemas/pass2_schema.py`
- `docs/prompts/pass1_prompt.md`
- `docs/prompts/document_synthesis_prompt.md`
- `docs/prompts/pass2_prompt.md`

### frontend 핵심

- `frontend/app/documents/[documentId]/page.tsx`
- `frontend/components/DocumentViewer.tsx`
- `frontend/components/AnchorOverlay.tsx`
- `frontend/components/RightPanel.tsx`
- `frontend/lib/api.ts`
- `frontend/utils/bbox.ts`
- `frontend/next.config.mjs`

### 실제 산출물 예시

- `data/analysis/doc_675b18899dbc4761a76b3bc8249f00b8/document_summary.json`
- `data/analysis/doc_675b18899dbc4761a76b3bc8249f00b8/pages/1/page_analysis_pass1.json`
- `data/analysis/doc_675b18899dbc4761a76b3bc8249f00b8/pages/1/page_analysis_pass2.json`

이 묶음이면 외부 ChatGPT에게 "PRD 대비 현재 구현이 어디까지 왔는지"와 "다음 우선순위가 뭔지"를 비교적 정확하게 다시 물어볼 수 있다.
