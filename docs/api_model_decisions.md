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

## canonical parse artifact layer

- parser 엔진별 raw output은 코드 전역에 직접 퍼뜨리지 않고, `DocumentParser` adapter 뒤에서 canonical parse artifact로 정규화한다.
- 이유: 이후 PyMuPDF4LLM, Unstructured, 기타 외부 parser를 붙여도 pass1/pass2/viewer 쪽이 parser별 출력 차이를 직접 알 필요가 없게 만들기 위해서다.
- canonical parse artifact는 AI stage의 `meta` / `result` envelope와 별도 계층으로 관리한다.
- 이유: parse artifact는 모델 응답 저장물이 아니라 전처리 source of truth이기 때문이다.
- parser artifact schema version은 AI stage용 `SCHEMA_VERSION`과 분리해서 `PARSER_SCHEMA_VERSION`으로 관리한다.
- canonical 문서 artifact 경로는 `data/parsed/{document_id}/document_parse.json`으로 고정한다.
- 페이지 단위 `pages/{page_number}.json`은 기본 강제 저장이 아니라 optional mirror로 두고, 필요할 때만 lazy materialization 한다.
- 이유: 지금 단계에서 diff와 저장 복잡도를 키우지 않으면서도, 이후 page-level adapter/debug 소비 지점을 열어두기 위해서다.
- stub parser는 최소 모드(`blocks=[]`)와 lightweight happy path(`page 전체 텍스트를 paragraph 1개로 저장`)를 모두 지원한다.
- 이유: 실제 parser 연동 전에도 저장/검증 경로와 간단한 텍스트 흐름을 빠르게 smoke test할 수 있게 하기 위해서다.
- 기본 실제 parser adapter는 `pymupdf4llm==0.3.4`에 맞춰 구현한다.
- 이유: 현재 canonical normalization이 `page_chunks`의 실제 shape(`metadata`, `text`, `tables`, `images`, `graphics`, `words`)와 PyMuPDF low-level block API 조합에 의존하므로, broad range보다 재현 가능한 exact pinning이 더 중요하다.
- parser backend는 `DOCUMENT_PARSER_BACKEND=stub|pymupdf4llm`로 선택 가능하게 둔다.
- 이유: 로컬 환경 차이, dependency 미설치, parser import/runtime failure가 있어도 시스템 전체가 죽지 않고 stub fallback으로 이어질 수 있게 하기 위해서다.
- 실제 1차 adapter는 PyMuPDF4LLM raw output을 segmentation source로 직접 저장하지 않고, `fitz` low-level text/image block을 block/bbox source로 사용하며 PyMuPDF4LLM은 page-level markdown/table 힌트로만 사용한다.
- 이유: 현재 사용한 PyMuPDF4LLM legacy page chunk shape만으로는 안정적인 block-level bbox/class 정보를 항상 보장하지 않기 때문이다.
- page-level canonicalization은 hard fail 대신 graceful fallback을 사용한다.
- 순서: block normalization 시도 → 실패 시 full-page paragraph block 1개 fallback → 그것도 불가능하면 `blocks=[]`.
- caption 분류는 보수적으로 한다.
- 이유: classifier 정교화보다 canonical artifact 일관성이 이번 단계 목표이기 때문이다. 명백한 경우만 `caption`으로 두고, 애매하면 `paragraph` 또는 `other`로 남긴다.
- OCR은 이번 단계에서 사용하지 않고 `ocr_used=false`로 고정한다.
- 이번 단계에서는 canonical parse artifact를 도입하지만, pass1/pass2/viewer는 아직 이 artifact를 소비하지 않는다.
- 이후 PyMuPDF4LLM / Unstructured / 기타 parser는 `DocumentParser` adapter 구현체로 붙인다.

## additive parse integration and page manifest

- parse 단계는 기존 문서 처리 파이프라인에 additive integration으로 붙인다.
- 이유: canonical parse artifact와 routing signal을 자동 생성하되, 현재 pass1이 아직 이를 소비하지 않으므로 기존 render/image 기반 파이프라인을 깨지 않기 위해서다.
- 이번 단계의 orchestrator 연결 방식은 pass1 앞 blocking step이 아니라 non-blocking best-effort side step으로 둔다.
- 이유: 이번 단계가 단독으로 머지돼도 parse/triage 때문에 기존 문서 처리 시간이 늘어나거나 실패 경로가 늘어나지 않게 하기 위해서다.
- parse 결과는 `data/parsed/{document_id}/document_parse.json`에 저장한다.
- routing 결과는 `data/parsed/{document_id}/page_manifest.json`에 저장한다.
- `page_manifest`는 다음 단계 pass1 routing을 위한 lightweight signal artifact다.
- 목적: pass1 text-first cheap path 전환 시 parse artifact 전체를 다시 뜯지 않고, page별 route label과 핵심 signal만 보고 분기할 수 있게 하기 위해서다.
- page manifest route label은 `text-rich`, `scan-like`, `visual-rich` 세 가지로 제한한다.
- `scan-like` 분류는 false positive를 줄이기 위해 보수적으로 둔다.
- 즉 low text/block 조건만으로는 부족하고, `ocr_used`, `non_empty_text_block_count == 0`, `image_count > 0` 중 하나 이상이 같이 맞아야 한다.
- parse 또는 routing 실패는 best-effort warning으로만 처리하고, 기존 `pass1 -> synthesis -> pass2` 흐름을 실패시키지 않는다.
- 이유: 이번 단계의 목적은 pass1 소비 전 준비 작업이지, 문서 처리의 새 hard dependency를 만드는 것이 아니기 때문이다.

## pass1 manifest routing

- `PASS1_ROUTING_MODE=hybrid|legacy`로 pass1 routing을 제어한다.
- 기본값은 `hybrid`다.
- `legacy`는 기존 full-page multimodal pass1로 즉시 rollback하기 위한 안전 스위치다.
- `hybrid`에서는 parse/triage가 pass1 직전 best-effort precondition이 된다.
- 단, 매번 강제 재실행하지 않고 현재 `document_parse.json`과 `page_manifest.json`이 유효하면 재사용을 우선한다.
- 이유: 이번 단계의 목적은 parse/triage를 계속 다시 돌리는 것이 아니라, pass1이 cheap path에 필요한 입력을 안정적으로 확보하는 것이기 때문이다.
- `page_manifest` route label은 pass1에서 직접 소비한다.
  - `text-rich` → text-first cheap path 우선
  - `scan-like`, `visual-rich` → 기존 multimodal path 유지
- text-first cheap path는 image multimodal을 쓰지 않고 text-only LLM path를 사용한다.
- 이유: `page_role`, `page_summary`, `candidate_anchors` 품질을 유지하면서도 가장 직접적으로 multimodal 호출 수를 줄일 수 있기 때문이다.
- text-first bbox는 자유 생성이 아니라 선택/조합으로 제한한다.
  - 허용: parsed block 1개의 bbox
  - 허용: reading_order상 인접한 2개 block union bbox
  - 그 외 bbox는 cheap path 실패로 보고 multimodal로 escalate한다.
- cheap path가 실패하거나 품질 신호가 약하면 multimodal fallback을 유지한다.
- 기준 예시: text 부족, non-empty block 부족, bbox grounding 실패, candidate anchor 수 부족.
- 이번 단계의 candidate anchor 최소 성공 기준은 `< 6`이면 cheap path 성공으로 보지 않고 fallback한다.
- pass1 artifact `meta`에는 `pass1_path`, `route_label`, `route_reason`, `parser_source`를 optional로 남긴다.
- 이유: 실제로 multimodal 호출 수가 줄었는지와 어떤 페이지가 escalate됐는지 검증 가능하게 하기 위해서다.

## pass1 text-first routing

- pass1은 `page_manifest.json`을 읽어서 page-level route를 실제로 소비한다.
- `text-rich` 페이지는 image multimodal 대신 text-first cheap path를 먼저 시도한다.
- 이유: 이번 단계의 핵심 성공 기준은 비용 절감이 실제로 시작되는 것이고, text-rich 페이지에서 full-page image multimodal을 줄이는 것이 가장 직접적인 절감 경로이기 때문이다.
- `visual-rich`, `scan-like` 페이지는 기존 multimodal pass1 경로를 유지한다.
- 이유: 표, 그림, 스캔성 페이지는 시각 정보 손실 위험이 더 커서 현재 단계에서는 보수적으로 유지하는 편이 성공률 측면에서 안전하다.
- parse/triage는 default 모드에서 pass1 시작 전 best-effort precondition으로 실행한다.
- 이유: pass1이 manifest와 canonical parse artifact를 실제로 읽으려면, 최소한 시작 전에 입력 artifact가 준비되어 있어야 하기 때문이다.
- parser/triage 실패 시 문서 전체 처리는 계속 진행하고, pass1은 기존 multimodal fallback 경로로 내려간다.
- 이유: cheap path보다 문서 처리 성공률 유지가 우선이기 때문이다.
- text-first cheap path는 deterministic heuristic이 아니라 text-only LLM path를 사용한다.
- 이유: `page_role`, `page_summary`, `question`, `short_explanation` 품질을 유지하면서도 image multimodal만 제거할 수 있기 때문이다.
- cheap path가 실패하거나 candidate anchor 수가 너무 적으면 해당 페이지만 multimodal로 escalate 한다.
- `pass1` artifact `meta`에는 `pass1_path`, `route_label`, `route_reason`, `parser_source`를 남긴다.
- 목적: 실제로 multimodal pass1 호출 수가 얼마나 줄었는지 사후 검증 가능하게 하기 위함이다.
- 이번 단계에서는 triage를 text-first friendly하게 보정해서, 텍스트가 충분한 슬라이드가 `has_figure` 하나 때문에 무조건 `visual-rich`로 떨어지지 않게 한다.

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
