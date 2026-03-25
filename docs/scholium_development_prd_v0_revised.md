좋아. 아래는 **코덱스/개발용 전달 패키지**로 바로 써도 되는 수준으로 정리한 **개발용 PRD + 시스템 설계 + API 계약 + 데이터 모델 + 백로그 + 테스트 계획**이다.
이번 문서는 일부러 “기획 문서” 말투보다 **구현 문서** 말투로 썼다. 코덱스한테 넣었을 때 바로 작업 단위로 떨어지게 하려고.

------

# Scholium 개발용 PRD / Implementation Spec v0.1

문서 목적:

- 10일 내 내부 테스트 가능한 MVP v0 구현
- Codex/개발 에이전트가 바로 작업 가능한 수준의 명세 제공
- 범위 통제, 데이터 구조 통일, API 및 백로그 정의

문서 대상:

- Codex
- 개발 에이전트
- 본인(파운더/PM/빌더)

------

# 0. 이번 스프린트의 절대 목표

이번 스프린트의 목표는 **“작동하는 핵심 경험”**이다.
완성형 서비스가 아니다.

## 성공 기준

사용자가 PDF를 업로드하면:

1. 페이지별로 렌더링되고
2. 각 페이지에 3~5개의 클릭형 해설 앵커가 보이고
3. 클릭 시 짧은 설명/긴 설명이 열리며
4. 페이지 역할과 관련 페이지가 표시되는 상태

즉, 이번 스프린트는 아래 질문에 답하기 위한 것이다.

**“PDF 위에 자동 생성된 클릭형 해설 앵커가 실제로 유용한가?”**

------

# 1. 범위 정의

## [v0 필수]

- PDF 업로드
- 페이지별 렌더링
- 페이지 1차 분석
- 문서 전체 구조 합성
- 페이지 2차 보정
- 최종 앵커 3~5개 노출
- 클릭형 설명 패널
- 페이지 역할 표시
- 관련 페이지 연결 1~2개 표시
- 분석 결과 JSON 저장
- 내부 테스트 가능한 viewer

## [후속 확장]

- 텍스트 선택 후 즉시 설명
- dense extraction 품질 비교 도구
- 복잡한 종합 추론 앵커
- 성능 최적화
- 에러 회복성 개선
- 처리량 향상
- 요약/복습 모드

## [출시 이후 필요]

- 로그인
- 결제
- 사용자 문서 관리
- 공유 링크
- 교수/기관용 authoring/admin
- 클래스 대시보드
- 장기 로그 분석 및 추천

------

# 2. 제품 핵심 개념

## 2.1 문서 처리 철학

- direct PDF-to-chat 제품이 아니다
- page image 기반 multimodal understanding 제품이다
- 내부적으로는 많은 후보 앵커를 생성하되, 사용자에겐 적게 노출한다
- 기본값은 “뜻 + 역할” 설명이다
- 종합 추론은 후순위다

## 2.2 앵커 철학

- 내부: dense extraction
- 외부: sparse surfacing
- 기본 노출: 3~5개
- 후보 생성: 8~15개
- bbox는 정규화 좌표
- click only, hover 없음


## 2.3 공통 메타데이터 및 처리 표준

### [v0 필수] 공통 메타데이터 규칙

- 모든 JSON 산출물의 `meta`에는 `schema_version`과 `generated_at`를 포함한다.
- AI가 직접 생성한 산출물의 `meta`에는 `prompt_version`과 `model_name`을 포함한다.
- 동일 문서 재분석 시 어떤 prompt/model 조합으로 생성된 결과인지 추적 가능해야 한다.

### [v0 필수] 렌더링 표준

- 분석용 페이지 이미지는 PNG, RGB 기준으로 생성한다.
- 분석에 사용되는 페이지 이미지는 긴 변 기준 표준 해상도로 정규화한 뒤 사용한다.
- 페이지 회전 정보는 렌더 단계에서 정규화하여 항상 읽기 쉬운 방향으로 맞춘다.
- bbox는 분석에 사용된 렌더 결과물 기준으로 계산한다.

### [v0 필수] bbox 좌표계 표준

- 좌표 원점은 페이지 이미지 좌상단 `(0, 0)`이다.
- bbox는 normalized `[x, y, w, h]` 형식으로 저장한다.
- 기본 박스 기준은 CropBox 우선, CropBox가 없거나 비정상일 경우 MediaBox fallback을 사용한다.
- 회전 페이지는 회전을 정규화한 뒤 bbox를 계산하고, 원본 페이지 번호는 유지한다.

### [v0 필수] 장식 요소 제외 규칙

- 기본 제외: 학교/기관 로고, 워터마크, 단순 배경 도형, 장식선, 반복 페이지 번호, 단순 브랜딩 아이콘
- 기본 포함: 축 라벨, 범례, 단위, 캡션, 표 헤더, 의미 있는 번호/기호
- 장식처럼 보이더라도 페이지 의미를 바꾸는 경우에는 후보 요소로 포함할 수 있다.

------

# 3. 사용자 플로우

## 3.1 v0 메인 플로우

1. 사용자 PDF 업로드
2. 문서 상태가 “분석 중”으로 표시
3. 완료 시 viewer 진입
4. PDF 각 페이지 위에 앵커 노출
5. 사용자가 앵커 클릭
6. 오른쪽 패널에 짧은 설명/긴 설명/관련 페이지 표시
7. 사용자가 관련 페이지로 이동
8. 사용 로그 저장

## 3.2 내부 테스트 플로우

1. 테스트용 PDF 업로드
2. 분석 완료 확인
3. 품질 체크리스트 기반 평가
4. bbox/설명/관련 페이지 점검
5. 피드백 기록

------

# 4. 시스템 아키텍처

## 4.1 전체 구조

프론트엔드:

- Next.js
- PDF.js viewer
- overlay layer
- 우측 설명 패널

백엔드:

- FastAPI
- 비동기 작업 실행
- PDF 렌더링
- AI API 호출
- JSON 저장
- 상태 조회 API

저장:

- 파일 저장소: PDF, 페이지 이미지, 결과 JSON
- 메타 DB: SQLite 또는 Postgres
- 로그 저장: DB 또는 JSON append

AI:

- OpenAI 계열 멀티모달 API 우선
- 입력: 페이지 이미지 + 필요시 추출 텍스트
- 출력: 구조화된 JSON

## 4.2 처리 단계

1. Ingest
2. Render
3. Page Analysis Pass 1
4. Document Synthesis
5. Page Refinement Pass 2
6. Persist
7. Serve to Viewer

------

# 5. 파이프라인 상세 설계

## 5.1 Step 1. Ingest

### 입력

- PDF 파일

### 처리

- 파일 유효성 검사
- document_id 생성
- 원본 파일 저장
- 문서 상태 `uploaded`
- background job enqueue

### 출력

- document_id
- 상태 응답

### 실패 조건

- 파일 형식 오류
- 저장 실패

------

## 5.2 Step 2. Render

### 입력

- PDF 파일 경로

### 처리

- 페이지 수 추출
- 각 페이지를 PNG/JPG로 렌더링
- 썸네일/표준 렌더 버전 생성 가능
- 페이지별 메타 저장

### 출력

- pages/{page_number}.png
- total_pages
- status = `rendered`

### 비고

- PyMuPDF 권장
- 페이지 이미지 크기는 bbox 품질과 비용 사이 절충 필요
- 초기엔 해상도보다 일관성 우선


### [v0 필수] 렌더링 표준

- 출력 포맷: PNG
- 색 공간: RGB
- 분석용 페이지 이미지는 긴 변 기준 표준 해상도로 정규화한다.
- 페이지 회전 정보는 렌더 단계에서 보정한다.
- bbox 계산 기준은 분석에 사용된 렌더 결과물과 동일해야 한다.
- 박스 기준은 CropBox 우선, MediaBox fallback이다.

------

## 5.3 Step 3. Page Analysis Pass 1

### 목적

문서 전체 맥락을 과하게 넣지 않고, 각 페이지를 페이지 자체로 잘 읽는다.

### 입력

- page image
- optional extracted text
- prompt template v1

### 출력

- page_role draft
- page_summary draft
- candidate_anchors 8~15개
- bbox
- short_explanation
- confidence

### 내부 규칙

- 의미 없는 로고, 장식 요소는 제외
- candidate anchor는 최대한 많이 뽑되 무의미한 것 제거
- 텍스트·기호 / 구조화 시각 요소를 모두 후보에 포함
- 종합 추론은 초안 수준만 가능

- decorative exclusion rules를 적용한다.
  - 제외: 학교/기관 로고, 워터마크, 단순 배경 도형, 장식선, 반복 페이지 번호, 단순 브랜딩 아이콘
  - 포함: 축 라벨, 범례, 단위, 캡션, 표 헤더, 의미 있는 번호/기호

### 기대 출력 포맷

JSON only

------

## 5.4 Step 4. Document Synthesis

### 목적

페이지 요약들의 집합으로 문서 전체 구조를 이해한다.

### 입력

- 모든 page_role
- 모든 page_summary
- anchor label/anchor type 요약

### 출력

- overall_topic
- overall_summary
- sections
- key_concepts
- difficult_pages
- prerequisite links
- page dependency hints

### 설계 원칙

- 원본 전체 PDF를 다시 통째로 모델에 넣지 않는다
- 페이지 분석 결과를 바탕으로 문서 구조를 합성한다
- 긴 문서에 대해서도 context 폭발을 막는다

------

## 5.5 Step 5. Page Refinement Pass 2

### 목적

문서 전체 구조를 참조해 페이지의 최종 노출 앵커를 정한다.

### 입력

- page image
- page pass1 result
- document synthesis result

### 출력

- final_anchors 3~5개
- long_explanation
- related_pages 1~2개
- prerequisite
- page_risk_note

### 핵심 로직

candidate anchors를 rerank한다.

rerank 기준:

- 중요도
- 혼란 가능성
- 맥락 의존도
- 비자명성
- 후속 영향도

다양성 제약:

- 개념형만 몰리지 않게
- 시각요소형 1개 이상 가능
- 흐름/연결형 1개 포함 가능

------

## 5.6 Step 6. Persist

### 저장 대상

- document_summary.json
- page_analysis_pass1.json
- page_analysis_final.json
- document metadata
- usage logs

### 저장 원칙

- markdown 아님
- 구조화된 JSON
- bbox는 normalized
- 후속 파이프라인 재사용 가능해야 함

------

## 5.7 Step 7. Viewer Delivery

### 프론트 전달 정보

- 문서 메타
- 페이지 이미지 URL
- final anchors
- page_role
- page summary
- related pages
- document overview 일부

------



## 5.8 실패 처리 정책

### [v0 필수]
- Render 전체 실패 시 document status는 `failed`로 처리한다.
- Pass 1 / Pass 2에서 일부 페이지만 실패한 경우, 성공한 페이지 결과는 유지하고 실패 페이지는 unavailable 상태로 노출한다.
- Document synthesis가 실패하더라도 page-level 결과가 존재하면 viewer는 page-level 결과를 우선 제공한다.
- silent failure는 허용하지 않는다. 실패 단계와 원인을 최소한 내부 로그로 남긴다.

### [후속 확장]
- 자동 재시도 정책
- 단계별 fallback 모델 정책
- degraded output 정책 고도화

# 6. 데이터 모델


## 6.0 공통 메타데이터 규칙

### [v0 필수]
- 모든 JSON 산출물의 `meta`는 `schema_version`과 `generated_at` 규칙을 따른다. 단, 순수 메타 테이블/문서 레코드는 `created_at` / `updated_at`를 사용해도 된다.
- AI가 직접 생성한 산출물의 `meta`는 반드시 `prompt_version`과 `model_name`을 포함한다.
- 동일 문서 재분석 시 버전 비교가 가능해야 한다.

## 6.1 Document

```json
{
  "schema_version": "0.1",
  "document_id": "doc_xxx",
  "filename": "sample.pdf",
  "title": "optional",
  "status": "uploaded|rendering|analyzing|completed|failed",
  "total_pages": 18,
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

## 6.2 Document Summary

```json
{
  "meta": {
    "schema_version": "0.1",
    "generated_at": "ISO8601",
    "prompt_version": "synthesis_v0_1",
    "model_name": "gpt-5.4"
  },
  "result": {
    "document_id": "doc_xxx",
    "overall_topic": "string",
    "overall_summary": "string",
    "sections": [
      {
        "section_id": "sec_1",
        "title": "string",
        "pages": [1,2,3]
      }
    ],
    "key_concepts": [
      {
        "term": "string",
        "description": "string",
        "pages": [2,5,7]
      }
    ],
    "difficult_pages": [4, 9, 12],
    "prerequisite_links": [
      {
        "from_page": 5,
        "to_page": 3,
        "reason": "page 5 relies on concept introduced in page 3"
      }
    ]
  }
}
```

## 6.3 Page Analysis Pass 1

```json
{
  "meta": {
    "schema_version": "0.1",
    "generated_at": "ISO8601",
    "prompt_version": "pass1_v0_1",
    "model_name": "gpt-5.4"
  },
  "result": {
    "document_id": "doc_xxx",
    "page_number": 3,
    "page_role": "string",
    "page_summary": "string",
    "candidate_anchors": [
      {
        "anchor_id": "p3_c1",
        "label": "string",
        "anchor_type": "text|formula|chart|table|diagram|image|flow|other",
        "bbox": [0.12, 0.18, 0.30, 0.09],
        "question": "string",
        "short_explanation": "string",
        "confidence": 0.84
      }
    ]
  }
}
```

## 6.4 Page Analysis Final

```json
{
  "meta": {
    "schema_version": "0.1",
    "generated_at": "ISO8601",
    "prompt_version": "pass2_v0_1",
    "model_name": "gpt-5.4"
  },
  "result": {
    "document_id": "doc_xxx",
    "page_number": 3,
    "page_role": "string",
    "page_summary": "string",
    "final_anchors": [
      {
        "anchor_id": "p3_f1",
        "label": "string",
        "anchor_type": "text|formula|chart|table|diagram|image|flow|other",
        "bbox": [0.10, 0.20, 0.28, 0.08],
        "question": "string",
        "short_explanation": "string",
        "long_explanation": "string",
        "prerequisite": "string",
        "related_pages": [2, 4],
        "confidence": 0.87
      }
    ],
    "page_risk_note": "string"
  }
}
```

## 6.5 Interaction Log

```json
{
  "schema_version": "0.1",
  "event_id": "evt_xxx",
  "document_id": "doc_xxx",
  "page_number": 3,
  "anchor_id": "p3_f1",
  "event_type": "anchor_click|page_view|related_page_jump|panel_expand",
  "timestamp": "ISO8601"
}
```

------

# 7. API 계약

## 7.1 POST /api/documents

PDF 업로드

### request

multipart/form-data

- file: pdf

### response

```json
{
  "document_id": "doc_xxx",
  "status": "uploaded"
}
```

------

## 7.2 GET /api/documents/{document_id}

문서 상태 및 기본 메타 조회

### response

```json
{
  "document_id": "doc_xxx",
  "status": "analyzing",
  "filename": "sample.pdf",
  "total_pages": 18
}
```

------

## 7.3 GET /api/documents/{document_id}/summary

문서 전체 구조 조회

### response

document_summary.json 형태

------

## 7.4 GET /api/documents/{document_id}/pages/{page_number}

페이지 최종 결과 조회

### response

```json
{
  "document_id": "doc_xxx",
  "page_number": 3,
  "image_url": "/files/doc_xxx/pages/3.png",
  "page_role": "string",
  "page_summary": "string",
  "final_anchors": [...]
}
```

------

## 7.5 POST /api/logs

사용자 interaction 로그 저장

### request

```json
{
  "document_id": "doc_xxx",
  "page_number": 3,
  "anchor_id": "p3_f1",
  "event_type": "anchor_click"
}
```

### response

```json
{
  "ok": true
}
```

------

## 7.6 GET /api/documents/{document_id}/debug/pass1/{page_number}

### [v0 필수, 내부 디버그 전용]

후보 앵커 8~15개 확인용

이건 외부 공개용이 아니라 내부 품질 확인용이다.

------

# 8. 프론트엔드 명세

## 8.1 레이아웃

- 왼쪽/중앙: PDF viewer
- 오른쪽: context panel
- 상단: 문서 정보 + 상태 + 페이지 네비게이션

## 8.2 Overlay 앵커 UI

### [v0 필수]

- 페이지 위에 3~5개 마커
- click 가능
- 선택된 마커만 active state
- bbox overlay는 subtle highlight 또는 small badge
- hover 기반 동작 없음

## 8.3 우측 설명 패널

### [v0 필수]

표시 정보:

- 현재 페이지 역할 1줄
- 현재 페이지 핵심 설명
- 선택한 앵커 label
- short explanation
- long explanation
- prerequisite
- related pages

### [후속 확장]

- 설명 타입 표시
- 더 보기
- 관련 개념 이동


## 8.4 내부 QA 디버그 모드

### [v0 필수, 내부 전용]
- final anchors only / candidate anchors 포함 보기 toggle
- bbox overlay on/off toggle
- confidence 표시
- pass1 / pass2 결과 비교
- 내부 품질 평가 체크 포인트 확인

------

# 9. AI 프롬프트 출력 원칙

## 9.1 공통 원칙

- 무조건 JSON 출력
- 추측과 확실한 내용 구분
- bbox는 normalized
- 로고/장식은 제외
- 불확실하면 confidence 낮게
- “뜻”만이 아니라 최소 “이 페이지에서의 역할”까지 설명
- overly long explanation 금지

## 9.2 설명 타입의 기본 층위

### [v0 필수]

- short_explanation: 1~2문장
- long_explanation: 3~5문장

### [후속 확장]

- explanation_type: lexical|object|relational

------

# 10. 품질 전략

## 10.1 내부 품질 평가 모드

페이지당 많은 candidate anchor를 생성하고, coverage와 질을 본다.

### 목적

- 모델이 실제로 어떤 요소를 포착하는지 확인
- 의미 없는 앵커/좌표 오류 파악
- surfacing 이전 내부 품질 확보

## 10.2 사용자 노출 모드

페이지당 3~5개 final anchor만 노출한다.

### 목적

- 인지 과부하 줄이기
- 신뢰도 확보
- 핵심 막힘 포인트만 보여주기

------

# 11. 백로그

이제부터 진짜 중요하다. 코덱스한테 던질 수 있게 **에픽 → 스토리 → 수용 기준**으로 쪼갠다.

------

## Epic A. 프로젝트 부트스트랩

### Story A1. 레포 구조 생성

**목표**: 프론트/백/데이터 디렉터리 구조 확정

#### 작업

- Next.js 앱 생성
- FastAPI 앱 생성
- shared schema 디렉터리 생성
- local file storage 구조 설계

#### 수용 기준

- `frontend/`, `backend/`, `data/`, `docs/` 구조 존재
- 로컬 실행 가능
- README 존재

------

### Story A2. 환경 설정

**목표**: 로컬에서 서버와 프론트 둘 다 실행 가능

#### 작업

- `.env.example` 작성
- OpenAI API key 환경 변수화
- dev script 추가

#### 수용 기준

- `npm run dev` 또는 equivalent로 프론트 실행
- `uvicorn` 또는 equivalent로 백엔드 실행
- 환경변수 누락 시 친절한 에러

------

## Epic B. PDF 업로드 및 렌더링

### Story B1. PDF 업로드 API

**목표**: PDF를 업로드하고 document_id 반환

#### 작업

- POST `/api/documents`
- 파일 유효성 검사
- 파일 저장

#### 수용 기준

- PDF 업로드 성공
- document_id 반환
- 비-PDF 업로드 시 400

------

### Story B2. 문서 상태 저장

**목표**: 문서 처리 상태 관리

#### 작업

- document metadata 저장
- 상태 값 enum 정의

#### 수용 기준

- uploaded / rendering / analyzing / completed / failed 상태 관리 가능

------

### Story B3. 페이지 렌더링

**목표**: PDF를 페이지 이미지로 저장

#### 작업

- PyMuPDF 렌더링
- 페이지 수 기록
- 이미지 저장 경로 생성

#### 수용 기준

- 1개 PDF 업로드 후 pages/*.png 생성
- total_pages 저장
- 일부 페이지 실패 시 에러 핸들링

------

## Epic C. 분석 엔진 Pass 1

### Story C1. 페이지 분석 prompt schema 정의

**목표**: 모델이 candidate anchor JSON을 안정적으로 반환

#### 작업

- prompt 템플릿 작성
- JSON schema 정의
- anchor_type enum 정의

#### 수용 기준

- 최소 3개 테스트 페이지에서 schema-valid JSON 반환

------

### Story C2. 페이지 분석 worker 구현

**목표**: 각 페이지에 대해 pass1 결과 생성

#### 작업

- 페이지 이미지 로드
- 모델 호출
- candidate_anchors 저장

#### 수용 기준

- page_role/page_summary/candidate_anchors 생성
- bbox normalized format
- candidate 8~15개 생성 가능

------

### Story C3. pass1 디버그 API

**목표**: 내부에서 candidate anchor를 바로 확인 가능

#### 작업

- GET `/debug/pass1/{page}`
- 원본 JSON 노출

#### 수용 기준

- 특정 페이지의 pass1 결과 확인 가능

------

## Epic D. 문서 구조 합성

### Story D1. 문서 요약 입력 포맷 설계

**목표**: pass1 결과를 문서 합성에 사용할 구조로 정리

#### 작업

- page summaries aggregate
- key concept 후보 추출
- section prompt 입력 포맷 구성

#### 수용 기준

- 문서 합성 prompt input 파일 생성 가능

------

### Story D2. document synthesis worker

**목표**: 문서 전체 구조 생성

#### 작업

- overall topic 생성
- sections 생성
- key concepts 생성
- prerequisite links 생성

#### 수용 기준

- document_summary.json 생성
- 전체 문서 구조 읽을 수 있음

------

## Epic E. 분석 엔진 Pass 2

### Story E1. rerank rule 설계

**목표**: candidate anchor에서 final anchor를 고르는 기준 적용

#### 작업

- 중요도/혼란 가능성/맥락 의존도/비자명성/후속 영향도 정의
- 다양성 제약 정의

#### 수용 기준

- rerank 결과가 전부 같은 타입 앵커로 몰리지 않음

------

### Story E2. pass2 worker 구현

**목표**: final anchors 생성

#### 작업

- pass1 결과 로드
- document summary 로드
- 모델 호출 또는 로직 보정
- final anchors 3~5개 생성

#### 수용 기준

- long explanation 생성
- related pages 1~2개 생성
- page_risk_note 생성

------

## Epic F. 데이터 저장 및 조회

### Story F1. JSON persistence 구조 구현

**목표**: pass1/pass2/document_summary 결과 저장

#### 작업

- JSON file naming 규칙
- 저장/불러오기 유틸 작성

#### 수용 기준

- 문서 단위 재조회 가능
- 재분석 없이 viewer에서 결과 불러오기 가능

------

### Story F2. 문서/페이지 조회 API

**목표**: 프론트에서 viewer 데이터 읽기

#### 작업

- GET document meta
- GET summary
- GET page result

#### 수용 기준

- 프론트에서 문서/페이지 결과 호출 가능

------

## Epic G. Viewer 및 Overlay

### Story G1. PDF viewer 화면 구현

**목표**: 페이지 이미지 또는 PDF viewer 표시

#### 작업

- viewer container
- page navigation
- current page state

#### 수용 기준

- 페이지 이동 가능
- 이미지 또는 PDF 렌더 정상 표시

------

### Story G2. overlay anchor 렌더링

**목표**: final anchor bbox를 페이지 위에 표시

#### 작업

- normalized bbox → px 변환
- clickable marker UI
- active state

#### 수용 기준

- 3~5개 앵커 노출
- 위치가 심각하게 깨지지 않음
- 페이지 변경 시 올바른 앵커 표시

------

### Story G3. 설명 패널 구현

**목표**: 앵커 클릭 시 우측 설명 패널 표시

#### 작업

- short explanation 표시
- long explanation 표시
- prerequisite 표시
- related pages 표시

#### 수용 기준

- anchor click → 설명 열림
- related page 클릭 시 페이지 이동 가능

------


### Story G4. 내부 QA 디버그 모드 구현

**목표**: 내부 테스트 시 candidate/final anchor를 비교 확인할 수 있어야 함

#### 작업

- final only / candidate 포함 보기 toggle
- bbox on/off toggle
- confidence 표시
- pass1/pass2 비교 정보 노출

#### 수용 기준

- 내부 QA 모드에서 candidate anchors 확인 가능
- 외부 사용자 기본 화면에는 노출되지 않음

------

## Epic H. 로그 및 내부 테스트 도구

### Story H1. interaction logging

**목표**: 클릭/페이지 이동 로그 저장

#### 작업

- POST `/api/logs`
- anchor click 로그
- related page jump 로그
- page view 로그

#### 수용 기준

- 로그 저장 확인 가능

------

### Story H2. 품질 체크 UI 또는 체크리스트 연계

**목표**: 내부 테스트 시 품질 평가 가능

#### 작업

- 간단한 평가 포맷 제공
- 또는 외부 문서 연계용 export

#### 수용 기준

- 각 페이지에 대해 품질 평가 기록 가능

------

## Epic I. 에러 및 운영성

### Story I1. 분석 상태 표시

**목표**: 사용자가 현재 상태를 알 수 있어야 함

#### 작업

- 상태 polling
- analyzing/completed/failed 표시

#### 수용 기준

- 처리 실패 시 무한 로딩 아님
- 완료 시 자동 viewer 진입 가능

------

### Story I2. 부분 실패 허용

**목표**: 일부 페이지 실패해도 전체 문서를 버리지 않음

#### 작업

- 페이지별 실패 상태
- fallback 메시지

#### 수용 기준

- 일부 페이지만 실패해도 나머지는 열람 가능

------


### Story I3. 파일 보존/삭제 기본 정책 구현

**목표**: 내부 테스트용 파일 보존 및 수동 삭제 기준 마련

#### 작업

- 문서별 파일 경로 추적
- 수동 삭제 유틸 또는 admin script 준비
- 보관 기간 기준 문서화

#### 수용 기준

- document_id 기준 수동 삭제 가능
- 테스트 종료 후 파일 정리 절차 존재

------

# 12. 구현 우선순위

## P0 무조건 해야 함

- 업로드
- PDF 렌더링
- pass1
- document synthesis
- pass2
- JSON 저장
- page result API
- viewer
- overlay
- click panel

## P1 있으면 좋음

- 로그 저장
- debug API
- 상태 표시 개선
- related page jump polish

## P2 나중에

- 텍스트 선택 설명
- 고급 분석
- 성능 최적화
- 운영 자동화

------

# 13. 테스트 계획


## 13.0 테스트 데이터셋 관리

### [v0 필수]
`dataset_manifest.json`을 유지한다.

권장 필드:
- dataset_id
- document_id
- domain
- subject
- pdf_type
- element_mix
- difficulty
- expected_failure_modes
- notes

모든 테스트 PDF는 최소한 유형, 난이도, 예상 실패 포인트가 태깅되어야 한다.

## 13.1 기술 테스트

테스트 PDF 10~12개 준비

- 잘 될 것 같은 것
- 애매한 것
- 어려운 것

### 확인 항목

- 업로드 성공
- 페이지 렌더링 성공
- candidate anchors 생성
- final anchors 생성
- bbox usable 여부
- click panel 표시
- related pages 동작

## 13.2 내부 품질 테스트

각 페이지별 1~5점 평가

- 위치 적절성
- 의미 정확성
- 설명 유용성
- 막힘 해소력
- 페이지 연결 적절성

통과 기준:

- 평균 3.5 이상
- 치명적 오류 20% 이하

## 13.3 외부 기능 테스트 준비

외부 테스트 전 필수 체크:

- 최소 3개 PDF 유형에서 안정 동작
- 대표 데모 PDF 2~3개 확보
- 화면 녹화 가능
- 설명 패널 UX 최소한 usable

------

# 14. 이번 스프린트에서 하지 말아야 할 것

이건 반드시 코덱스에게도 전달해.

- 모든 텍스트를 미리 전부 앵커로 노출
- hover UX
- 로그인
- 결제 붙이기
- vector DB부터 붙이기
- n8n 붙이기
- 모바일 최적화
- 음성 업로드
- 협업 기능
- 정교한 authoring 도구

------

# 15. 코드/구현 가이드라인

## 일반 원칙

- 결과는 항상 재현 가능해야 함
- LLM 출력은 항상 schema validation 거칠 것
- 실패 시 silent failure 금지
- JSON 저장 구조는 나중에 재사용 가능해야 함
- bbox는 normalized only
- 프론트는 overlay positioning 로직을 별도 유틸로 분리

## 프롬프트 엔지니어링 원칙

- 자유 텍스트 금지
- JSON only
- candidate와 final을 분리
- “뜻 + 역할”을 항상 포함
- 확신도 낮으면 confidence 낮게
- 불확실한 요소는 aggressive하게 final surfacing 하지 말 것

------

# 16. 10일 마일스톤

## Day 1

- 레포/환경 세팅
- 스키마 고정
- PDF 세트 확보

## Day 2

- 업로드 API
- 렌더링

## Day 3

- 상태 관리
- 파일 저장 구조

## Day 4

- pass1 JSON 생성

## Day 5

- pass1 품질 1차 확인

## Day 6

- document synthesis

## Day 7

- pass2 및 final anchor 생성

## Day 8

- viewer + overlay

## Day 9

- 설명 패널 + related page 이동
- 로그 저장

## Day 10

- 내부 테스트
- 대표 데모 PDF 확보

------

# 17. Codex에 줄 실행 지시문 초안

아래는 코덱스에 그대로 붙여도 되는 수준으로 적어준다.

```text
너는 Scholium MVP v0 구현을 맡은 개발 에이전트다.

목표:
10일 내 내부 테스트 가능한 MVP를 구현한다.

핵심 기능:
1. PDF 업로드
2. 페이지별 렌더링
3. 페이지 1차 분석 (candidate anchors 8~15개)
4. 문서 구조 합성
5. 페이지 2차 보정 (final anchors 3~5개)
6. PDF viewer 위 overlay 앵커 표시
7. 클릭 시 short/long explanation 패널 표시
8. related pages 표시 및 이동

기술 원칙:
- code-first
- n8n 사용하지 않음
- PDF는 페이지 이미지 기반으로 처리
- LLM 출력은 JSON only
- bbox는 normalized coordinates
- schema_version / prompt_version / model_name 추적을 유지
- hover UX 없음, click only
- 내부적으로는 dense extraction, 사용자에겐 sparse surfacing

스택 권장:
- frontend: Next.js + PDF.js
- backend: FastAPI
- PDF rendering: PyMuPDF
- persistence: JSON files + SQLite/Postgres
- AI: OpenAI multimodal API

데이터 모델:
- document_summary.json
- page_analysis_pass1.json
- page_analysis_final.json
- interaction logs
- dataset_manifest.json

우선순위:
P0:
- upload
- render
- pass1
- doc synthesis
- pass2
- viewer
- overlay
- click panel

P1:
- logs
- debug APIs
- failure handling

P2:
- text selection
- performance optimization
- ops automation

절대 이번 스프린트에서 하지 말 것:
- auth
- payments
- n8n
- vector DB
- hover UX
- voice
- collaboration
- mobile optimization

중요:
제품 완성도가 아니라, 작동하는 핵심 경험이 목표다.
```

------

# 18. 최종 압축

이 문서의 요점은 이거다.

- 내부적으로는 많이 뽑고
- 사용자에겐 적게 보여주고
- 뜻 + 역할까지만 안정적으로 붙이고
- 문서/페이지 분석 결과를 JSON으로 남기고
- 10일 안에 작동 데모를 만들고
- 바로 테스트한다

이 정도면 코덱스한테 던졌을 때 **“뭘 만들지 모르겠다”** 상태는 아니다.
이제 진짜 필요한 건 더 많은 문서가 아니라 **첫 구현 결과**다.

원하면 다음 턴에서 내가 이걸 더 잘게 쪼개서 **GitHub issue 템플릿 형식**이나 **체크리스트형 개발 보드**로도 바꿔줄게.
