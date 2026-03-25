# API / Model Decisions

## 목적

이 문서는 Scholium MVP v0 Step 2에서 OpenAI 연동과 모델 운용 방식을 고정하기 위한 결정 기록이다.

## 고정 결정

- API: OpenAI Responses API 사용
- 기준 모델: `gpt-5.4`
- 입력 전략: direct PDF input 미사용, page image 기반 입력
- 출력 전략: JSON only + local schema validation 필수
- 저장 메타: `model_name`, `schema_version`, `prompt_version`, `generated_at`
- 응답 구조: `meta` / `result` 분리
- result 본문만 schema validation 대상

## stage별 기본값

| stage | model | reasoning.effort | prompt_version |
| --- | --- | --- | --- |
| pass1 | `gpt-5.4` | `high` | `pass1_v0_1` |
| document_synthesis | `gpt-5.4` | `medium` | `synthesis_v0_1` |
| pass2 | `gpt-5.4` | `high` | `pass2_v0_1` |

## timeout / retry

- request timeout: 60초
- API/network retry: 최대 2회
- local validation 실패 시: repair instruction을 붙여 1회 재시도

## schema validation

- Python 쪽 검증은 `pydantic` 기반으로 처리한다.
- stage별 placeholder schema는 `backend/app/schemas/` 아래에 둔다.
- OpenAI Structured Outputs의 JSON schema와 로컬 pydantic validation을 함께 사용한다.
- 결과 envelope는 아래처럼 저장한다.

```json
{
  "meta": {
    "schema_version": "0.1",
    "prompt_version": "pass1_v0_1",
    "model_name": "gpt-5.4",
    "generated_at": "ISO8601"
  },
  "result": {}
}
```

## 선택 이유

### Responses API
- reasoning 모델과 구조화 출력에 맞는 현재 기준 API다.
- page image 입력과 text-only 입력을 같은 인터페이스로 다룰 수 있다.

### 단일 모델 고정
- 이번 단계는 인프라 정리 단계라서 모델 비교 실험보다 일관성이 중요하다.
- 후속 결과 JSON에 `model_name`을 남겨 재분석 비교 여지를 확보한다.

### page image 기준
- PRD 방향과 일치한다.
- direct PDF-to-model 경로를 지금 붙이지 않아도, 이후 render 단계와 자연스럽게 연결된다.

## 이번 단계 out of scope

- direct PDF-to-model 처리
- Gemini 연동
- 여러 모델 비교 실험 코드
- auth
- payment
- vector DB
- hover
- voice
- collaboration
- mobile optimization
