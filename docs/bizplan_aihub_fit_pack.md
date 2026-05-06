# Scholium AI Hub Fit Pack

작성 기준:
- 이 문서는 현재 repo와 이미 생성된 artifact를 기준으로, Scholium에 왜 AI 허브형 지원 환경이 필요한지 정리한 문서다.
- 현재 구현/검증된 사실과, 입주 시 기대 효과를 분리해서 적었다.
- 주요 근거:
  - `docs/bizplan_product_fact_pack.md`
  - `docs/bizplan_execution_evidence_pack.md`
  - `docs/perf_runs/20260329T071211565189Z_comparison.md`
  - `docs/perf_runs/20260329T071211565189Z_active_pass2_assessment.md`
  - `docs/perf_runs/20260329T101732981630Z_routing_audit.md`
  - `docs/perf_runs/20260329T104834162180Z_routing_rule_tiebreak.md`
  - `docs/perf_runs/20260329T134743640870Z_rule_a_recovered_pages_qa.md`

## 1. 왜 지금 Scholium에 AI 허브형 환경이 필요한가

- Scholium은 현재 "아이디어 단계"가 아니라, 업로드-렌더링-parse/triage-pass1-synthesis-pass2-viewer까지 실제로 연결된 작동 데모를 가진 상태다.
- same-manifest benchmark 기준으로 selective pass2를 적용했을 때 OpenAI pass2 호출은 `75 -> 29`, 평균 총 처리시간은 `524.8초 -> 361.9초`로 줄었다.
- 반대로, reduction이 잘 안 먹히는 outlier도 이미 확인됐다.
  - `W1`은 `pass2 llm 7 -> 7`, `compat 0`
  - routing audit에서는 outlier 3개 문서에서 `18`개 페이지가 text-rich임에도 visual signal 때문에 llm path에 남아 있었다.
- 즉 현재 병목은 "제품을 만들 수 있느냐"가 아니라 아래 두 가지다.
  - 실험 속도를 유지하면서 outlier routing을 줄이는 것
  - compat 품질을 manual QA 기반으로 안전하게 확장하는 것
- AI 허브형 환경이 지금 필요한 이유는, 이 단계가 개인 개발 환경만으로 장기 버티는 탐색이 아니라, 반복 실험, 문서 QA, 도메인 피드백, PoC 연결이 동시에 필요한 단계이기 때문이다.

## 2. 현재 부족한 자원

### 2-1. 공간
- 현재 repo와 artifact 구조상 Scholium의 핵심 작업은 "개발"만이 아니라 "문서 세트 확보 -> benchmark 실행 -> recovered page QA -> viewer 확인"의 반복이다.
- 이 작업은 코드 작성 공간만으로 끝나지 않고, 실제 PDF 문서 검토와 수동 QA를 병행할 수 있는 지속적인 작업 공간이 필요하다.
- 특히 recovered page QA pack은 `18`페이지를 current llm 결과와 compat preview로 나눠 검토하도록 설계돼 있어, 집중 검토와 기록이 가능한 고정 작업 환경이 필요하다.

### 2-2. 네트워크
- 현재 artifact 기준으로 다음 단계 병목은 모델 호출 자체보다 "어떤 문서/페이지를 우선 검증하고 PoC로 연결할지"에 있다.
- Scholium은 PDF 기반 문서 AI라서, 실제 적용 가능성 검증에는 문서 공급처와 초기 사용자 피드백 네트워크가 중요하다.
- 지금 부족한 것은 단순 온라인 노출보다, 대학/연구실/교육기관/기업 내부자료 같은 실제 문서 흐름과 닿는 검증 네트워크다.

### 2-3. 멘토링
- 현재 기술적으로는 selective pass2 reduction, routing audit, tie-break simulation, recovered page manual QA까지는 해냈다.
- 하지만 이 다음 단계는 모델 프롬프트 문제만이 아니라 "어디서부터 rollout해야 하는가", "어떤 문서군을 먼저 고객 문제로 정의할 것인가", "manual QA를 어떤 운영 프로세스로 전환할 것인가"의 문제다.
- 즉 지금 필요한 멘토링은 일반적인 창업 조언보다, B2B/B2G 문서 workflow PoC 설계와 초기 검증 범위를 줄여주는 멘토링이다.

### 2-4. 기술/사업화 지원
- repo 기준 현재 미구현 항목도 명확하다.
  - auth
  - payment
  - collaboration
  - ops/dashboard
  - viewer 고도화
- 그러나 지금 당장 가장 필요한 지원은 전체 제품을 넓히는 게 아니라, 이미 작동 중인 파이프라인을 "재현 가능한 실험 -> 고객 검증 가능한 PoC"로 전환하는 지원이다.
- 따라서 필요한 기술/사업화 지원은 다음 성격에 가깝다.
  - benchmark/QA 체계를 고객 검증 패키지로 바꾸는 지원
  - 문서군별 파일럿 설계 지원
  - 초기 PoC 도입처 연결 지원

## 3. AI 허브 입주 시 6개월 내 얻을 효과

- 첫째, routing false positive 보정을 더 빠르게 검증할 수 있다.
  - 현재는 rule A recovered page `18`개 중 primary review clean set `15`개를 사람이 직접 검토해야 한다.
  - 고정 공간과 QA 협업 환경이 있으면 이 검토를 단발성이 아니라 운영 루프로 바꿀 수 있다.
- 둘째, benchmark 중심 개발을 PoC 중심 개발로 전환할 수 있다.
  - 지금은 same-manifest perf pack과 QA artifact가 잘 쌓여 있지만, 이걸 실제 고객 검증 자료로 연결하는 네트워크가 부족하다.
- 셋째, outlier 문서군을 먼저 좁혀서 rollout할 수 있다.
  - 현재 성공 사례와 outlier가 이미 분리돼 있기 때문에, 허브 내 멘토링/네트워크를 통해 "어떤 문서 유형부터 실제 도입이 가능한지"를 빠르게 정할 수 있다.
- 넷째, viewer/ops 기능을 우선순위에 맞게 붙일 수 있다.
  - 현재는 internal demo 수준 viewer이지만, processing/review/logging artifact는 이미 있다.
  - 허브 지원이 있으면 이를 PoC 운영 화면 수준으로 확장하는 속도가 빨라진다.

## 4. AI 허브 입주 시 12개월 내 만들 성과

- 현재 구조를 기준으로 12개월 내 현실적으로 만들 수 있는 성과는 아래와 같다.
- selective pass2를 특정 문서군에서 더 안정적으로 적용한 운영형 버전
  - 전제: routing false positive rule 보정 + recovered page QA 결과 반영
- 문서군별 PoC 패키지
  - 현재 benchmark pack, routing audit, QA pack을 기반으로, 도입처별로 "처리시간/비용/품질"을 설명할 수 있는 자료 체계화
- 내부 데모 수준 viewer를 넘어선 초기 운영 화면
  - processing 상태, review 대상, 로그/QA 상태를 한 화면에서 다루는 운영형 표면
- 초기 협력처 기준의 반복 검증 체계
  - 현재 artifact 중심 개발을 고객 문서 기준 검증 루프로 바꾸는 것

## 5. 허브에서 특히 필요한 지원 3개

- 문서 AI PoC 연결이 가능한 네트워크 지원
  - 지금 병목은 모델 성능 자체보다, 어떤 실제 문서군에서 먼저 검증할지와 도입처 연결이다.
- 반복 QA와 실험을 지속할 수 있는 고정 작업 공간
  - recovered page manual QA, benchmark 비교, viewer 확인은 집중도 높은 반복 작업이라 공간 지원 효과가 직접적이다.
- 문서 workflow 제품의 초기 사업화 멘토링
  - 현재는 "기술 구현"보다 "어떤 문제 정의와 도입 구조로 좁힐지"가 더 큰 병목이다.

## 6. 서울 소재/서울 생태계와 연결되는 논리

- Scholium의 다음 단계는 불특정 다수 대상 확산보다, 밀도 높은 문서 공급처와 빠른 피드백 루프를 가진 환경에서 초기 PoC를 반복하는 것이다.
- 이런 제품은 초기 사용자군이 넓게 흩어져 있을 때보다, 대학, 연구기관, 교육기관, 스타트업, 중소기업 문서 workflow가 가까이 모여 있는 생태계에서 검증 속도가 빠르다.
- 서울 AI 허브형 환경은 이 제품에 필요한 자원을 한 군데에 모아준다.
  - 작업 공간
  - 기술/사업화 멘토링
  - 초기 수요처/협력 네트워크
- 즉 서울과의 연결 논리는 "서울에 있으니 지원받겠다"가 아니라, 현재 Scholium의 병목이 문서 기반 PoC의 반복 실험과 네트워크 확보에 있고, 그 병목을 가장 직접적으로 줄여줄 수 있는 환경이 서울 AI 허브형 지원 구조라는 점에 있다.

## 신청배경 및 의지용 요약 문단

Scholium은 현재 PDF 업로드부터 렌더링, 문서 분석, page-level explanation, 내부 viewer까지 연결된 작동 데모를 이미 구현했고, same-manifest benchmark 기준으로 selective pass2를 적용해 OpenAI pass2 호출을 `75`회에서 `29`회로 줄인 상태다. 반면 outlier 문서에서는 reduction이 잘 작동하지 않는 원인도 routing audit과 tie-break simulation으로 확인했고, recovered page `18`개에 대한 manual QA pack까지 만들어 다음 개선 범위를 구체화했다. 지금 필요한 것은 추상적인 성장 지원이 아니라, 이 반복 실험과 수동 QA, 초기 PoC 연결을 동시에 밀어줄 수 있는 공간, 네트워크, 멘토링이다. 서울 AI 허브형 환경은 Scholium이 현재 가진 benchmark 중심 개발을 실제 문서 workflow 기반 PoC와 초기 사업화 단계로 전환하는 데 가장 직접적으로 필요한 인프라다.
