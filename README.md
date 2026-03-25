# Scholium

이번 스프린트 목표는 완성형 서비스가 아니라 내부 테스트 가능한 작동 데모다.

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

- OpenAI API 키
- Node.js
- Python 3.11+
- pnpm 또는 npm
- 테스트용 PDF 10~12개
- Git 저장소
- `.env.example`

## 빠른 시작

1. `.env.example`을 참고해서 `.env`를 만든다.
2. `data/raw_pdfs`에 테스트용 PDF 10~12개를 넣는다.
3. 프런트엔드와 백엔드 구현은 P0 데모 범위 안에서만 시작한다.

## 기준 문서

- `docs/scholium_product_prd_v0_revised.md`
- `docs/scholium_development_prd_v0_revised.md`

## 참고

- `data/raw_pdfs`: 원본 PDF 입력
- `data/rendered_pages`: 페이지 렌더링 결과
- `data/analysis`: 분석 산출물
- `data/logs`: 실행 로그
