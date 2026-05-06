# System Checkpoint Review

기준 evidence:
- comparison: `docs/perf_runs/20260329T071211565189Z_comparison.json`
- assessment: `docs/perf_runs/20260329T071211565189Z_active_pass2_assessment.md`
- compat tuning QA: `docs/perf_runs/20260329T071211565189Z_qa_samples_after_tuning.json`

## 1. 현재 구현 상태 요약
- baseline pipeline은 `render -> parse/triage -> pass1 -> synthesis -> pass2` 흐름으로 유지되고 있다.
- active planner는 `v2_spine + active + hard_pages_only`에서 `page_routing.json`을 실제 pass2 planner로 써서 llm/compat 페이지를 분기한다.
- compat path는 deterministic `Pass2CompatBuilder` + shared `StorageService.save_pass2_result()` normalize/save 경로를 사용한다.
- evidence pack은 같은 manifest 기준 baseline vs active corpus 비교까지 완료됐다.
- compat tuning은 text-rich/mixed compat artifact의 `long_explanation`, `prerequisite`, `related_pages`를 deterministic하게 보강했고, llm path semantics는 바뀌지 않았다.

## 2. 비용/시간 절감 상태 요약
아래 숫자는 `docs/perf_runs/20260329T071211565189Z_comparison.json`의 `corpus_aggregate_comparison`를 1차 source로 사용했다.

| metric | baseline_hybrid_all_pages | v2_spine_active_hard_pages_only | delta |
| --- | ---: | ---: | ---: |
| completed_docs | 5 | 5 | 0 |
| failed_docs | 0 | 0 | 0 |
| avg_total_processing_time_seconds | 524.8224 | 361.9378 | -162.8846 |
| median_total_processing_time_seconds | 472.7361 | 329.1720 | -143.5641 |
| total_openai_call_count | 156 | 110 | -46 |
| total_openai_pass2_call_count | 75 | 29 | -46 |
| total_pass2_llm_count | 76 | 30 | -46 |
| total_pass2_compat_count | 0 | 46 | +46 |

핵심 해석:
- pass2 fan-out reduction은 이미 강하게 입증됐다. `openai_pass2_call_count`는 `75 -> 29`, `pass2_llm_count`는 `76 -> 30`으로 줄었다.
- 시간 절감도 같이 관찰됐다. `avg_total_processing_time_seconds`는 `524.8224 -> 361.9378`로 줄었다.
- 지금 남은 병목은 compat를 더 늘릴 수 있느냐보다, 어떤 페이지가 아직 llm path에 남는지와 그 이유를 더 정확히 분해하는 쪽이다.

## 3. 아직 큰 병목 후보 3개
1. `text-rich인데 selective_visual_enrichment로 과상승되는 routing`
   - W1, W2, 논문형 outlier에서 `base_route_label=text-rich`인데도 `has_figure`, `image_count>=N` 신호 때문에 llm path로 남는 페이지가 많다.
2. `compat 설명의 정보 압축/반복`
   - tuning 이후 section/prerequisite 맥락은 좋아졌지만, text-rich intro 페이지의 first sentence는 여전히 upstream `short_explanation` 품질에 묶인다.
3. `visual-heavy page cost concentration`
   - W1 visual-heavy slide, W2의 기사/스크린샷, 논문형 figure page처럼 실제 llm 유지가 맞는 페이지들이 비용 병목을 계속 차지한다.

## 4. outlier 문서 3개 분석
outlier 선정 규칙:
- `expected_type in {"text_rich", "mixed"}`인 문서만 본다.
- 각 문서에 대해 아래 3개 신호를 계산한다.
  - active `pass2_llm_count`
  - reduction ratio = `1 - active_pass2_llm_count / baseline_pass2_llm_count`
  - llm-vs-hard gap = `active_pass2_llm_count - document_spine.result.routing_summary.hard_page_count`
- 정렬은 `reduction ratio 오름차순 -> active pass2_llm_count 내림차순 -> llm-vs-hard gap 내림차순`으로 한다.
- 현재 corpus에서 이 규칙으로 나온 top-3는 아래 세 문서다.

| document_id | source_pdf_relpath | rendered_pages | pass1_text_first_pages | pass1_multimodal_pages | pass1_escalated_pages | pass2_llm_count | pass2_compat_count | openai_pass2_call_count | suspected_reason |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `doc_4840bd97746f4f13bfe8fce69923e1ea` | `W1.Lecture01-Financial Management and Firm Value.pdf` | 7 | 6 | 1 | 0 | 7 | 0 | 7 | reduction ratio `0.0`, llm-vs-hard gap `+6`. `document_spine.json`은 `hard_page_count=1`인데 `page_routing.json`은 text-rich 6p까지 전부 `selective_visual_enrichment`로 올렸다. |
| `doc_5d509baf7e5c4119bdd3e5193a3e2afe` | `W2 Tutorial - Financial Management and Firm Value.pdf` | 12 | 8 | 4 | 0 | 10 | 2 | 10 | reduction ratio `0.1667`, llm-vs-hard gap `+6`. text-rich 6p가 `has_figure`, `image_count>=1/3` 신호 때문에 llm 유지됐다. |
| `doc_3cbb9bb19bea4a33bcf83b826e35eeed` | `유우_질병면역학 발표논문.pdf` | 17 | 13 | 4 | 0 | 10 | 7 | 10 | reduction ratio `0.4118`, llm-vs-hard gap `+10`. text-rich corpus인데도 figure/image flags가 넓게 잡혀 `selective_visual_enrichment`가 10p에 남았다. |

outlier 해석:
- W1은 가장 심한 케이스다. active에서도 `pass2_llm_count=7`이 그대로고, spine 쪽 `hard_page_count=1`과 routing 결과가 직접 충돌한다.
- W2는 mixed 문서라 visual-heavy page가 남는 건 자연스럽지만, text-rich 6p까지 같이 끌려 올라간 점이 문제다.
- 논문형 문서는 reduction은 있었지만, text-rich 문서치고 llm page가 여전히 많다.

참고 성공 사례:
- `doc_f9ba1ef0e03446d1bcf11dcc686d1275.pdf` / active `document_id=doc_49f948e346a949b49fee0181eb6f6cdf`는 `pass2_llm_count 23 -> 1`, `openai_pass2_call_count 23 -> 1`로 routing이 잘 맞았다.
- `26_통계학과.pdf` / active `document_id=doc_b6fb79a4bd514f0ea5d6284b26d9d545`도 `pass2_llm_count 17 -> 2`, `openai_pass2_call_count 16 -> 1`로 잘 회수됐다.

## 5. compat 품질 한계
- `text-rich`
  - section/title 맥락은 좋아졌지만, first sentence가 upstream `short_explanation` 절단 품질에 묶이는 경우가 있다.
- `mixed`
  - hallucination은 없지만, 설명이 너무 보수적으로 짧아져서 useful density가 떨어지는 페이지가 남아 있다.
- `visual-heavy`
  - compat가 아니라 llm 유지가 맞는 경우가 많다. 그래서 이 bucket은 품질보다 routing 분류 정확도와 llm cost 관리가 더 중요한 축이다.

## 6. 다음 실험 후보 3개
| candidate | focus | expected upside | main risk |
| --- | --- | --- | --- |
| `routing_audit_for_text_rich_false_positive_escalation` | `text-rich + has_figure/image_count` 패턴에서 `recommended_execution` 과상승 여부를 문서/페이지 단위로 감사 | W1/W2/논문형처럼 아직 llm으로 남는 non-hard page를 compat로 회수해 추가 reduction 가능 | 과보정하면 실제 visual-heavy page까지 compat로 보내 usefulness를 깰 수 있음 |
| `reasoning_effort_ab_for_active_llm_pages` | active에서 끝까지 llm에 남는 page들의 reasoning_effort/call cost를 A/B로 비교 | routing을 안 건드려도 남은 llm page 비용을 줄일 여지가 있는지 확인 가능 | reduction이 아니라 page당 품질 저하 없는 cost down을 입증해야 해서 해석이 더 까다로움 |
| `compat_first_sentence_tuning` | compat first sentence의 절단/반복을 줄여 text-rich intro 페이지의 usefulness를 올림 | 이미 compat로 회수된 page의 readability 개선 | 비용 병목보다는 품질 병목에 가까워, 지금 당장 fan-out 절감에는 덜 직접적임 |

비교 요약:
- reduction 관점에선 `routing audit`이 가장 큰 미회수 여지를 건드린다.
- 비용은 유지한 채 품질만 올리려면 `compat_first_sentence_tuning`이 맞지만, 지금 남은 큰 숫자 병목을 바로 건드리진 못한다.
- `reasoning_effort_ab_for_active_llm_pages`도 의미 있는 가설이지만, 현재 outlier 신호는 llm page 수 자체가 높게 남는 쪽이 더 강하다.

## 7. 최종 추천 1개
추천: `routing audit`

이유:
- reduction 자체는 이미 충분히 크다.
- compat tuning도 일정 부분 효과가 있었지만, 남은 큰 미회수 여지는 여전히 routing 쪽에 있다.
- W1/W2/논문형 text-rich page들이 `has_figure/image_count` 때문에 llm으로 과하게 남아 있다.
- 특히 W1은 `document_spine` hard candidate와 `page_routing` 결과가 크게 어긋난다.
- 따라서 다음 실험은 품질 미세조정보다 “어떤 page를 compat로 보낼지”를 먼저 감사하는 게 맞다.

## 8. evidence limitation / confidence
- 강한 정량 근거는 `20260329T071211565189Z_comparison.json`과 outlier 문서별 `processing_benchmark.json`, `page_routing.json`, `document_spine.json`에서 왔다.
- QA 해석은 `20260329T071211565189Z_qa_samples_after_tuning.* 기반의 qualitative sample interpretation이다.`
- 현재 QA sample은 3개 문서 / 9개 샘플이라서 compat 품질 일반화에는 한계가 있다.
- 따라서 `compat quality is no longer the main bottleneck`는 정량 결론이 아니라, 현재 corpus와 QA sample을 합친 가설 수준으로 봐야 한다.
- 반면 `W1/W2/논문형에서 routing false positive 가능성이 크다`는 비교/benchmark/spine이 동시에 맞물려 있어 confidence가 높다.

blocker fix: 없음
