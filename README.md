# Scholium

이번 스프린트 목표는 완성형 서비스가 아니라 내부 테스트 가능한 작동 데모다.

현재 MVP 방향은 precomputed anchor-click viewer가 아니라 selected-region viewer다.
Scholium은 PDF를 먼저 전처리해서 문서/페이지/요소 맥락을 이해해두고, 사용자가 실제로 막힌 영역을 드래그했을 때 그 영역에 붙는 설명을 생성한다.

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
   ```

3. `data/raw_pdfs`에 테스트용 PDF를 넣거나 업로드 화면에서 PDF를 올린다.

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

- `docs/scholium_product_prd_v0_revised.md`
- `docs/scholium_development_prd_v0_revised.md`

## 참고

- `data/raw_pdfs`: 원본 PDF 입력
- `data/rendered_pages`: 페이지 렌더링 결과
- `data/analysis`: 전처리/문서요약/selection explanation 산출물
- `data/logs`: 실행 로그

## Selected-region flow

1. viewer는 PDF 페이지 이미지를 깨끗하게 보여준다.
2. 사용자가 헷갈리는 영역을 드래그한다.
3. frontend가 normalized bbox `[x, y, w, h]`를 보낸다.
4. backend가 pass1 page context와 document synthesis context를 불러온다.
5. Codex CLI가 선택 영역 전용 JSON 설명을 생성한다.
6. schema validation을 통과한 결과만 `data/analysis/<document_id>/pages/<page>/selection_explanations/`에 저장된다.
7. floating academic annotation panel이 선택 영역 옆에 뜬다.

## Codex CLI provider 제한

- 이 구조는 로컬 MVP 개발용이다. production용 model serving 구조가 아니다.
- Codex CLI는 subprocess로 실행되며 stage별 JSON schema 검증을 통과해야 artifact가 저장된다.
- malformed JSON은 한 번만 repair 요청을 시도하고, 그래도 실패하면 해당 stage를 실패 처리한다.
- pass1과 selection explanation은 페이지 이미지가 Codex CLI image attachment로 전달된다.
- selection explanation은 기본적으로 `CODEX_CLI_MODEL=gpt-5.5`, `CODEX_CLI_REASONING=medium`을 사용한다. 설치된 CLI가 지원하지 않으면 가장 가까운 지원 설정으로 바꾸고 이 파일에 기록해야 한다.
- 기존 OpenAI provider 코드는 남아 있지만 기본값이 아니며, `SCHOLIUM_LLM_PROVIDER=openai_api`일 때만 사용된다.
