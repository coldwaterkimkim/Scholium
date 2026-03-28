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
| pass1 | `gpt-5.4` | `medium` | `pass1_v0_1` |
| document_synthesis | `gpt-5.4` | `medium` | `synthesis_v0_1` |
| pass2 | `gpt-5.4` | `medium` | `pass2_v0_1` |

## timeout / retry

- request timeout: 기본 60초
- pass2 request timeout: 120초
- API/network retry: 최대 2회
- local validation 실패 시: repair instruction을 붙여 1회 재시도
- pass2 diversity 보정용 추가 모델 호출: 사용하지 않음

## P0-A 비용/성능 절감

- pass1 기본 reasoning effort는 `high`에서 `medium`으로 낮춘다.
- 이유: pass1은 페이지 수만큼 호출이 fan-out되므로 reasoning effort를 낮추는 편이 누적 비용과 지연을 가장 직접적으로 줄인다.
- pass2는 diversity 부족을 이유로 모델을 다시 호출하지 않고, 결과의 다양성 부족은 `qa_warnings`에만 남긴다.
- 이유: diversity 보정용 추가 호출은 비용 증가 대비 효과가 불안정하므로, 이번 단계에서는 warning으로만 관찰하고 재호출은 생략한다.
- pass2 기본 reasoning effort는 `high`에서 `medium`으로 낮춘다.
- 이유: pass2는 이미지 + pass1 결과 + 문서 요약을 함께 넣는 무거운 stage라서 timeout과 connection error를 줄이기 위해 호출당 사고량을 낮춘다.
- pass2 기본 병렬도는 `3`에서 `2`로 낮춘다.
- 이유: 문서당 동시 호출 burst를 줄여 timeout/connection error 확률을 낮춘다.
- pass2 기본 timeout은 `60초`가 아니라 `120초`를 사용한다.
- 이유: pass2는 stage 특성상 payload와 출력이 가장 무거워서, 60초 제한보다 1회 성공을 우선하는 편이 재시도/실패 비용까지 포함하면 더 안정적이다.
- 모델 입력에 넣는 stage payload JSON은 compact form으로 직렬화한다.
- 이유: 모델 입력 토큰과 전송량을 줄이기 위해서고, 사람용 artifact 저장 포맷은 그대로 유지한다.
- artifact 저장 JSON과 `meta` / `result` envelope 구조는 그대로 유지한다.

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
