# Scholium 12-Month Milestones

작성 기준:
- 이 문서는 현재 repo와 artifact에서 확인된 상태를 출발점으로 만든 12개월 실행계획 초안이다.
- 현재 구현/검증된 것과 앞으로 만들 것을 구분해서 적었다.
- 멀리 있는 확장보다, 입주 후 6개월 안에 확인 가능한 결과를 먼저 두었다.
- 주요 근거:
  - `docs/bizplan_product_fact_pack.md`
  - `docs/bizplan_execution_evidence_pack.md`
  - `docs/bizplan_aihub_fit_pack.md`
  - `docs/bizplan_numbers_pack.md`
  - `docs/perf_runs/20260329T071211565189Z_comparison.md`
  - `docs/perf_runs/20260329T101732981630Z_routing_audit.md`
  - `docs/perf_runs/20260329T104834162180Z_routing_rule_tiebreak.md`
  - `docs/perf_runs/20260329T134743640870Z_rule_a_recovered_pages_qa.md`

## 1. 실행계획 전제

- 현재 이미 있는 것
  - upload -> render -> parse/triage -> pass1 -> synthesis -> pass2 -> viewer E2E 데모
  - same-manifest benchmark pack
  - routing audit / tie-break simulation / recovered page manual QA pack
- 현재 핵심 병목
  - outlier 문서에서 routing false positive가 남아 있음
  - compat 품질은 개선됐지만 아직 `safe but shallow` 한계가 있음
  - viewer/ops 표면은 내부 데모 수준
- 따라서 12개월 계획의 우선순위는 아래 순서로 잡는다.
  1. routing/compat 품질 보정
  2. QA와 benchmark를 운영 루프로 전환
  3. 파일럿 가능한 제품 표면 추가
  4. 초기 기관 검증 반복

## 2. 1~6개월 월별 마일스톤

| 기간 | 핵심 목표 | 목표 산출물 | 검증 지표 | 인력/자원 필요 |
| --- | --- | --- | --- | --- |
| 1개월차 | recovered page QA 완료와 rollout 기준 확정 | rule A recovered page manual QA 결과, routing rule 변경안 1차 spec, rerun benchmark plan | primary review clean set `15페이지` 검토 완료, safe/borderline/unsafe 분류 완료, next rule 결정 | 대표/개발 1, QA 리뷰 시간, 고정 작업공간 |
| 2개월차 | routing false positive 1차 보정 적용 | routing rule patch, same-manifest rerun comparison pack, regression note | outlier 3개 중 최소 2개 문서에서 llm page 감소, 전체 완료 문서 수 유지, new severe misroute 없음 | 대표/개발 1, benchmark 실행 환경 |
| 3개월차 | compat quality 2차 보강 | compat quality tuning patch, QA sample before/after pack, llm regression guard 결과 | text-rich sample QA에서 safe 또는 borderline 비율 상승, llm path 동일성 유지 | 대표/개발 1, manual QA 시간 |
| 4개월차 | pilot용 viewer/ops 표면 보강 | processing/review 화면 개선, review 대상 리스트, benchmark/log 확인용 내부 화면 | 업로드 후 상태 확인과 리뷰 대상 판별 시간을 현재 대비 단축, 내부 데모 1회 이상 완료 | 대표/개발 1, 필요 시 디자인/프론트 보조 |
| 5개월차 | 기관 파일럿 준비 | pilot 소개자료, 데모 시나리오, sample corpus pack, 인터뷰/검증 질문지 | 파일럿 후보 2곳 이상 접촉, 데모 가능한 문서군 1~2개 확정 | 대표, 고객개발/멘토링, 네트워크 지원 |
| 6개월차 | 첫 파일럿 실행 | 파일럿 운영 기록, pilot result memo, 다음 개선 backlog | 기관 파일럿 1건 이상 실행, 실제 업로드/재사용 로그 확보, 결제 의사 또는 후속 PoC 의향 확인 | 대표, 운영/QA 지원, 파일럿 협력처 |

## 3. 7~12개월 분기별 마일스톤

| 기간 | 핵심 목표 | 목표 산출물 | 검증 지표 | 인력/자원 필요 |
| --- | --- | --- | --- | --- |
| 7~9개월 | 파일럿 문서군 확장과 운영 안정화 | 문서군별 benchmark/QA pack, 기관별 PoC 설명자료, 운영형 viewer 개선본 | 기관 파일럿 누적 2~3건, 문서당 처리시간 목표 `300초 이하` 근접 또는 달성, repeat usage baseline 확보 | 대표, 엔지니어 1, 운영/QA 보조 |
| 10~12개월 | 반복 검증 가능한 초기 제품화 단계 도달 | 운영 리포트, 기관용 도입 제안서, 우선 문서군 기준 제품 패키지 | 기관 파일럿 누적 3~5건, 반복 사용률 baseline 확보, 유료 전환 의사 또는 차기 계약 가능성 문서화 | 대표, 엔지니어 1, 제품/운영 1 수준 필요 |

## 4. 단계별 목표 산출물 상세

### 4-1. 제품/기술 산출물

- routing rule patch 1차
  - 목적: outlier text-rich false positive 감소
- compat quality tuning 2차
  - 목적: `safe but shallow` 완화
- benchmark rerun pack
  - 목적: 개선 전후 수치 비교
- review/ops 화면
  - 목적: manual QA와 내부 운영 루프 가속
- pilot corpus pack
  - 목적: 기관 데모/검증용 공통 패키지화

### 4-2. 사업개발 산출물

- 파일럿 제안서
- 파일럿 운영 체크리스트
- 기관 인터뷰 질문지
- 파일럿 결과 요약서
- 후속 도입 또는 유료 PoC 전환 제안서

## 5. 단계별 검증 지표

### 5-1. 기술 지표

| 지표 | 현재 확인값 | 6개월 목표 | 12개월 목표 |
| --- | ---: | ---: | ---: |
| 문서당 처리시간(avg) | `361.9378초` | `300초 이하` 목표 | `240초 이하` 도전 |
| total OpenAI pass2 calls | `29` | same-manifest 기준 추가 절감 확인 | 문서군별 안정화 |
| outlier 문서 개선 수 | 현재 `0~부분 개선` | 3개 중 2개 이상 개선 | 우선 문서군 대부분 개선 |
| recovered page QA 완료율 | `15/18 clean set 분리` | 100% 검토 완료 | 재검토 루프 운영 |

### 5-2. 사업/사용 지표

| 지표 | 현재 상태 | 6개월 목표 | 12개월 목표 |
| --- | --- | --- | --- |
| 업로드 수 | baseline 없음 | pilot 단계 로그 확보 | 누적 추적 가능 상태 |
| 반복 사용률 | baseline 없음 | 첫 기준선 확보 | 개선 추적 가능 |
| 기관 파일럿 수 | baseline 없음 | `1건 이상` | `3~5건` |
| 결제 의사/후속 PoC 의향 | baseline 없음 | `1건 이상 확인` | `복수 기관 문서화` |

## 6. 인력/자원 필요

### 6-1. 최소 인력 구성

| 시점 | 최소 인력 | 역할 |
| --- | --- | --- |
| 1~3개월 | 대표/개발 1 | routing, compat, benchmark, viewer 개선 |
| 4~6개월 | 대표/개발 1 + 운영/QA 지원 | 파일럿 준비, QA 기록, 데모 운영 |
| 7~12개월 | 대표 + 엔지니어 1 + 운영/제품 1 수준 | 파일럿 병행, 운영 화면, 고객 대응 |

### 6-2. 필요한 자원

- 고정 작업 공간
  - recovered page manual QA, benchmark 비교, 파일럿 준비용
- 실제 문서 공급처와 연결되는 네트워크
  - 대학/연구실/교육기관/기업 내부자료 기반 검증
- 멘토링
  - 문서 workflow PoC 설계, 고객 세그먼트 우선순위, 도입 구조
- 개발 예산
  - API/클라우드, 외주 보완, 운영 도구

## 7. 리스크와 대응

| 리스크 | 현재 징후 | 대응 |
| --- | --- | --- |
| routing 보정이 일부 문서에만 듣고 일반화가 약할 수 있음 | W1/W2/논문형 outlier 존재 | same-manifest rerun + recovered page QA를 선행하고, 문서군별로 좁혀 rollout |
| compat 품질이 설명은 안전하지만 여전히 얕을 수 있음 | latest assessment에 `safe but shallow` 기록 | sample QA와 manual review를 유지하고, text-rich 문서부터 우선 보강 |
| viewer/ops 표면이 파일럿 운영에 부족할 수 있음 | 현재 internal demo 수준 | 4개월차에 review/ops 화면 우선 보강 |
| 실제 사용 로그와 반복 사용률 baseline이 부족함 | interaction log는 있으나 KPI 집계 체계 미완성 | 4~6개월에 KPI 집계 기준과 log read surface를 붙임 |
| 파일럿 전환이 느릴 수 있음 | 현재 네트워크/고객 검증 자료 부족 | 성공 사례 문서군부터 좁혀서 pilot corpus pack과 제안서 동시 준비 |

## 8. 사업계획서 표/일정표용 요약본

### 8-1. 한 줄 일정 요약

- 1분기: recovered page QA 완료, routing false positive 1차 보정, same-manifest rerun benchmark
- 2분기: compat quality 2차 보강, review/ops 화면 추가, 첫 기관 파일럿 실행
- 3분기: 파일럿 문서군 확장, 운영 안정화, repeat usage baseline 확보
- 4분기: 기관 파일럿 3~5건 수준 검증, 초기 제품화 패키지 정리

### 8-2. 표 입력용 간단 버전

| 기간 | 핵심 실행 | 핵심 산출물 | 핵심 지표 |
| --- | --- | --- | --- |
| 1~3개월 | routing/compat 품질 보정 | benchmark rerun pack, QA 결과, routing patch | outlier 개선, 처리시간 단축, llm fan-out 감소 |
| 4~6개월 | pilot 준비 및 첫 실행 | review/ops 화면, pilot pack, pilot result memo | 기관 파일럿 1건+, 사용 로그 확보 |
| 7~9개월 | 파일럿 확장 | 문서군별 PoC 자료, 운영 개선본 | 처리시간 `300초 이하` 근접, repeat usage baseline 확보 |
| 10~12개월 | 초기 제품화 단계 | 도입 제안서, 운영 리포트, 제품 패키지 | 기관 파일럿 3~5건, 후속 PoC/유료 의향 문서화 |

## 9. 먼 확장에 대한 짧은 메모

- 글로벌 확장이나 대규모 시장 확장은 이 12개월 계획의 핵심이 아니다.
- 우선순위는 서울/국내 문서 공급처와 파일럿 검증 루프를 먼저 만드는 것이다.
