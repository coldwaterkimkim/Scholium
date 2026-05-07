# Scholium Execution Evidence Pack

작성 기준:
- 이 문서는 repo에 남아 있는 최신 benchmark, comparison, routing audit, tie-break simulation, QA artifact만 기준으로 적었다.
- "확인된 수치"와 "해석"을 분리했고, 수치가 약한 부분은 약하다고 적었다.
- 주요 근거:
  - `docs/perf_runs/20260329T071211565189Z_comparison.md`
  - `docs/perf_runs/20260329T071211565189Z_active_pass2_assessment.md`
  - `docs/perf_runs/20260329T101732981630Z_routing_audit.md`
  - `docs/perf_runs/20260329T104834162180Z_routing_rule_tiebreak.md`
  - `docs/perf_runs/20260329T134743640870Z_rule_a_recovered_pages_qa.md`

## 1. baseline vs active 비교 핵심 수치

- 비교 대상은 같은 `corpus_manifest_sha256`를 가진 5개 문서 run이다.
  - `baseline_hybrid_all_pages`
  - `v2_spine_active_hard_pages_only`
- aggregate 기준 확인된 수치:
  - completed docs: `5 -> 5`
  - failed docs: `0 -> 0`
  - avg total processing time: `524.8224s -> 361.9378s`
  - total OpenAI calls: `156 -> 110`
  - total OpenAI pass2 calls: `75 -> 29`
  - total pass2 llm pages: `76 -> 30`
  - total pass2 compat pages: `0 -> 46`
- 문서별 편차도 명확하다.
  - `doc_f9ba1ef0e03446d1bcf11dcc686d1275.pdf`: `pass2 llm 23 -> 1`, `compat 22`
  - `26_통계학과.pdf`: `pass2 llm 17 -> 2`, `compat 15`
  - `W1.Lecture01-Financial Management and Firm Value.pdf`: `pass2 llm 7 -> 7`, `compat 0`

## 2. 비용/시간 절감의 핵심 포인트

- 비용 절감의 중심은 "전 페이지 llm pass2"를 "선별 llm + compat"으로 바꾼 데 있다.
- 확인된 변화:
  - OpenAI pass2 호출 수 `-61.3%` (`75 -> 29`)
  - pass2 llm page 수 `-60.5%` (`76 -> 30`)
  - 평균 총 처리시간 `-31.0%` (`524.8224s -> 361.9378s`)
- 약한 부분도 있다.
  - 모든 문서가 같이 좋아진 건 아니다.
  - W1은 `time +80.8337s`, `openai_pass2 +0`, `pass2_llm +0`으로 reduction이 거의 없었다.
- 즉 현재 근거로 말할 수 있는 건:
  - corpus 전체에선 절감 효과가 확인됐다.
  - 하지만 문서 유형에 따라 성과 편차가 크다.

## 3. routing audit과 tie-break simulation에서 확인된 사실

- routing audit에서 확인된 반복 패턴:
  - outlier 3개 문서에서 `18`개 페이지가 `no-table text-rich body`인데 `has_figure/image_count` 신호 때문에 hard-page score가 올라가 llm path에 남아 있었다.
  - 같은 `18`개 페이지가 `text-first compatible`인데도 spine hard candidate가 아닌 상태였다.
- outlier와 success reference가 artifact 기준으로 분리됐다.
  - outlier:
    - `W1.Lecture01-Financial Management and Firm Value.pdf`
    - `W2 Tutorial - Financial Management and Firm Value.pdf`
    - `유우_질병면역학 발표논문.pdf`
  - success reference:
    - `doc_f9ba1ef0e03446d1bcf11dcc686d1275.pdf`
    - `26_통계학과.pdf`
- tie-break simulation에서 규칙 A/B를 raw artifact 기준으로 다시 계산했다.
  - rule A와 rule B 모두:
    - `eligible_current_llm_pages = 18`
    - `llm_pages_recovered = 18`
    - `recovery_rate_over_current_llm = 1.0`
  - success reference에서 두 규칙 모두 추가 recovered page는 `0`
  - risk 측면에서도 이번 corpus에서는 차이가 거의 없었다.
- 결론:
  - 다음 tuning 후보는 실제 artifact 기준으로 두 개까지 좁혀졌다.
  - 하지만 winner는 강하게 갈리지 않았고, 추천 confidence는 `low`였다.

## 4. compat quality tuning에서 개선된 점과 남은 한계

- 개선된 점:
  - compat artifact가 section/prerequisite/context를 더 반영하도록 조립 규칙이 보강됐다.
  - QA/export용 trace 필드가 추가돼, why-this-output를 읽을 수 있게 됐다.
  - llm path semantics는 separate check에서 유지됐다.
- 확인된 상태:
  - latest assessment는 compat를 `safe but shallow`로 평가했다.
  - routing/tie-break 이후 recovered page QA pack에서는 rule A 기준 recovered page `18`개가 추려졌고, 이 중 primary review clean set은 `15`개, skipped는 `3`개다.
- 남은 한계:
  - text-rich intro/transition page 설명은 여전히 밋밋한 경우가 있다.
  - recovered `18`페이지 모두 visual risk signal이 있어, "회수 가능"과 "즉시 안전"이 같은 뜻은 아니다.
  - 즉 compat 품질은 개선됐지만, 아직 manual QA 없이 broad rollout했다고 말할 수준은 아니다.

## 5. 현재 팀의 실행력/기술 대응력을 보여주는 evidence 5개

- 같은 manifest로 baseline과 active를 직접 비교하는 perf pack이 있다.
  - `comparison.md/json`에 run metadata, corpus sha, aggregate, per-document delta가 남아 있다.
- reduction이 안 먹힌 문서를 따로 audit했다.
  - routing audit에서 outlier/success reference를 규칙 기반으로 분리하고, page-level 원인을 artifact 기준으로 적었다.
- 동률이 나온 규칙 후보를 실제 data로 다시 시뮬레이션했다.
  - tie-break simulation에서 recover potential과 risk를 rule A/B로 나눠 계산했다.
- 추천 규칙을 바로 rollout하지 않고 recovered page manual QA pack까지 만들었다.
  - `rule_a_recovered_pages_qa`에 현재 llm 결과와 compat preview를 나란히 놓고 `safe / borderline / unsafe` 검토가 가능하게 했다.
- 품질 개선과 비용 절감을 분리해서 다뤘다.
  - benchmark로 비용 절감, QA sample과 assessment로 품질 한계를 같이 남겨서, 좋은 숫자만 선택적으로 적지 않았다.

## 6. 사업계획서에 바로 넣을 수 있는 5문장 요약

- Scholium은 같은 5개 문서 corpus에서 baseline 대비 active selective mode 기준 OpenAI pass2 호출을 `75`회에서 `29`회로 줄였고, 평균 총 처리시간도 `524.8초`에서 `361.9초`로 낮췄다.
- 이 절감은 모든 문서에서 균일하게 나온 것은 아니며, `doc_f9...`와 `26_통계학과.pdf`에서는 크게 개선됐지만 `W1`에서는 reduction이 거의 없었다.
- 팀은 reduction이 안 먹힌 문서를 그대로 두지 않고, routing audit로 false positive 패턴을 분리하고, tie-break simulation으로 다음 rule 후보 두 개를 실제 artifact 기준으로 다시 비교했다.
- 이후에도 바로 규칙을 바꾸지 않고 recovered page `18`개에 대한 manual QA pack을 만들어, 현재 llm 결과와 would-be compat 결과를 사람이 직접 검토할 수 있게 했다.
- 현재 상태를 과장 없이 정리하면, Scholium은 업로드-분석-뷰어의 기본 파이프라인과 selective pass2 cost reduction은 이미 증명됐고, 남은 과제는 outlier routing 보정과 compat 품질의 안전한 확대 적용이다.
