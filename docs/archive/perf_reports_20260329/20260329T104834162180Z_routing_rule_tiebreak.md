# Routing Rule Tie-Break Simulation

## Audit scope and source artifacts

- routing_audit_json: `/Users/chansukim/Documents/개인/6-1. codex/Scholium/docs/perf_runs/20260329T101732981630Z_routing_audit.json`
- comparison_json: `/Users/chansukim/Documents/개인/6-1. codex/Scholium/docs/perf_runs/20260329T071211565189Z_comparison.json`
- corpus_manifest: `/Users/chansukim/Documents/개인/6-1. codex/Scholium/docs/perf_runs/20260329T071211565189Z_corpus_manifest.json`
- raw artifacts: processing_benchmark.json, document_spine.json, page_routing.json, page_manifest.json, page_analysis_pass1.json, page_analysis_pass2.json

## Selected document set

| role | document_id | source_pdf_relpath | expected_type | rendered_pages | active_llm | active_compat |
| --- | --- | --- | --- | ---: | ---: | ---: |
| outlier | doc_3cbb9bb19bea4a33bcf83b826e35eeed | 유우_질병면역학 발표논문.pdf | text_rich | 17 | 10 | 7 |
| outlier | doc_4840bd97746f4f13bfe8fce69923e1ea | W1.Lecture01-Financial Management and Firm Value.pdf | mixed | 7 | 7 | 0 |
| success_reference | doc_49f948e346a949b49fee0181eb6f6cdf | doc_f9ba1ef0e03446d1bcf11dcc686d1275.pdf | text_rich | 23 | 1 | 22 |
| outlier | doc_5d509baf7e5c4119bdd3e5193a3e2afe | W2 Tutorial - Financial Management and Firm Value.pdf | mixed | 12 | 10 | 2 |
| success_reference | doc_b6fb79a4bd514f0ea5d6284b26d9d545 | 26_통계학과.pdf | graph_heavy | 17 | 2 | 15 |

## Rule A simulation

### Overall

| metric | value |
| --- | ---: |
| eligible_pages_total | 61 |
| eligible_current_llm_pages | 18 |
| eligible_current_compat_pages | 43 |
| llm_pages_recovered | 18 |
| recovery_rate_over_current_llm | 1.0 |
| pages_still_left_on_llm | 0 |
| pages_with_visual_risk_signal | 19 |
| recovered_pages_with_visual_risk_signal | 18 |
| path_mismatch_page_count | 0 |

### Outlier only

| metric | value |
| --- | ---: |
| eligible_pages_total | 27 |
| eligible_current_llm_pages | 18 |
| eligible_current_compat_pages | 9 |
| llm_pages_recovered | 18 |
| recovery_rate_over_current_llm | 1.0 |
| pages_still_left_on_llm | 0 |
| pages_with_visual_risk_signal | 18 |
| recovered_pages_with_visual_risk_signal | 18 |
| path_mismatch_page_count | 0 |

### Success reference only

| metric | value |
| --- | ---: |
| eligible_pages_total | 34 |
| eligible_current_llm_pages | 0 |
| eligible_current_compat_pages | 34 |
| llm_pages_recovered | 0 |
| recovery_rate_over_current_llm | None |
| pages_still_left_on_llm | 0 |
| pages_with_visual_risk_signal | 1 |
| recovered_pages_with_visual_risk_signal | 0 |
| path_mismatch_page_count | 0 |

## Rule B simulation

### Overall

| metric | value |
| --- | ---: |
| eligible_pages_total | 61 |
| eligible_current_llm_pages | 18 |
| eligible_current_compat_pages | 43 |
| llm_pages_recovered | 18 |
| recovery_rate_over_current_llm | 1.0 |
| pages_still_left_on_llm | 0 |
| pages_with_visual_risk_signal | 19 |
| recovered_pages_with_visual_risk_signal | 18 |
| path_mismatch_page_count | 0 |

### Outlier only

| metric | value |
| --- | ---: |
| eligible_pages_total | 27 |
| eligible_current_llm_pages | 18 |
| eligible_current_compat_pages | 9 |
| llm_pages_recovered | 18 |
| recovery_rate_over_current_llm | 1.0 |
| pages_still_left_on_llm | 0 |
| pages_with_visual_risk_signal | 18 |
| recovered_pages_with_visual_risk_signal | 18 |
| path_mismatch_page_count | 0 |

### Success reference only

| metric | value |
| --- | ---: |
| eligible_pages_total | 34 |
| eligible_current_llm_pages | 0 |
| eligible_current_compat_pages | 34 |
| llm_pages_recovered | 0 |
| recovery_rate_over_current_llm | None |
| pages_still_left_on_llm | 0 |
| pages_with_visual_risk_signal | 1 |
| recovered_pages_with_visual_risk_signal | 0 |
| path_mismatch_page_count | 0 |

## Outlier impact comparison

- 유우_질병면역학 발표논문.pdf: 두 규칙 모두 6페이지를 회수해 차이가 없다.
- W1.Lecture01-Financial Management and Firm Value.pdf: 두 규칙 모두 6페이지를 회수해 차이가 없다.
- W2 Tutorial - Financial Management and Firm Value.pdf: 두 규칙 모두 6페이지를 회수해 차이가 없다.

## Success reference impact comparison

- doc_f9ba1ef0e03446d1bcf11dcc686d1275.pdf: success reference 기준 visual-risk recovered page는 동률이고 recovered count는 0 vs 0다.
- 26_통계학과.pdf: success reference 기준 visual-risk recovered page는 동률이고 recovered count는 0 vs 0다.

## Risk comparison

| rule | overall recovered visual-risk pages | success recovered visual-risk pages | recovered spine-hard pages |
| --- | ---: | ---: | ---: |
| rule_a | 18 | 0 | 0 |
| rule_b | 18 | 0 | 0 |

## Recommended first rule

- recommended_first_rule: `rule_a`
- recommendation_confidence: `low`
- Outlier recover potential: rule_a=18, rule_b=18.
- Success reference visual-risk recovered pages: rule_a=0, rule_b=0.
- Overall visual-risk recovered pages: rule_a=18, rule_b=18.
- Candidate scope: rule_a=61, rule_b=61.
- Recommendation confidence: low.

## Evidence limitations

- This simulation uses existing artifacts only and does not rerun the pipeline.
- current_effective_path mismatch pages: 0, unknown pages: 0.
- processing_benchmark.json remains the primary ground truth for current llm/compat path.
- pass2 meta mismatch or missing pages are counted as limitations and kept visible in per-document summaries.
