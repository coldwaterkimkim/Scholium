# Active Pass2 Assessment

## 1. corpus 개요
- manifest 기반 문서 수: `5`
- curated corpus dir: `/Users/chansukim/Documents/개인/6-1. codex/Scholium/data/raw_pdfs/_active_pass2_eval_20260329T071211565189Z`
- corpus manifest: `docs/perf_runs/20260329T071211565189Z_corpus_manifest.json`
- corpus manifest sha256: `d34a9168c0517522dd6dfc4ee66e050b8878e5b85a88c9d375ebeed97636e20d`
- 타입 분포: `text_rich 2`, `mixed 2`, `graph_heavy 1`
- 같은 manifest 기반 run 여부: baseline과 active 모두 동일한 `corpus_manifest_sha256`를 기록했고 comparison에서도 unmatched/excluded가 없었다.

## 2. baseline vs active 핵심 수치
| metric | baseline | active | delta | change |
| --- | --- | --- | --- | --- |
| completed_docs | 5 | 5 | 0 | +0.0% |
| failed_docs | 0 | 0 | 0 | N/A |
| avg_total_processing_time_seconds | 524.8224 | 361.9378 | -162.88460000000003 | -31.0% |
| total_openai_call_count | 156 | 110 | -46 | -29.5% |
| total_openai_pass2_call_count | 75 | 29 | -46 | -61.3% |
| total_pass2_llm_count | 76 | 30 | -46 | -60.5% |
| total_pass2_compat_count | 0 | 46 | 46 | N/A |

핵심 해석:
- `total_openai_pass2_call_count`는 `75 -> 29`로 `-61.3%` 감소했다.
- `total_pass2_llm_count`는 `76 -> 30`로 `-60.5%` 감소했다.
- `avg_total_processing_time_seconds`는 `524.8224 -> 361.9378`로 `-31.0%` 줄었다.
- active는 `total_pass2_compat_count=46`를 기록해 non-hard page를 실제 compat artifact로 대체했다.

## 3. 문서별 특이사항
- active가 특히 잘 먹힌 문서: `doc_f9ba1ef0e03446d1bcf11dcc686d1275.pdf`는 `openai_pass2 23 -> 1`, `pass2_llm 23 -> 1`, `total_time 745.4967 -> 321.4916`으로 가장 큰 감소를 보였다.
- 두 번째로 reduction이 큰 문서: `26_통계학과.pdf`는 `openai_pass2 16 -> 1`, `pass2_llm 17 -> 2`로 graph-heavy 문서에서도 planner가 잘 작동했다.
- llm page가 많이 남는 문서: `W1.Lecture01-Financial Management and Firm Value.pdf`는 `pass2_llm 7 -> 7`, `pass2_compat 0`으로 reduction이 거의 없었다. 이 문서는 mixed지만 visual-heavy 성격이 강한 것으로 보인다.
- compat 비중이 과도하게 높은 문서: `doc_f9...`는 active에서 `pass2_compat_count=22`, `text-first` 중심 구조라 compat 대체가 극단적으로 많이 발생했다.
- fallback/실패/비교 제외 문서: 없음. `matched_document_count == expected_doc_count`이고 `unmatched_documents_by_run`, `excluded_documents_missing_key` 모두 비어 있었다.

## 4. compat 품질 관찰 메모
- 전체적으로 compat는 `safe but shallow` 쪽이다. anchor 선택과 related_pages는 대체로 안정적이고, 명백히 이상한 artifact는 샘플에서 보이지 않았다.
- `related_pages`는 text-rich 문서에서 꽤 유용했고, 페이지 위치나 섹션 맥락을 짚는 데 도움이 됐다.
- 반면 `long_explanation`은 text-rich 전환 페이지나 제목/도입 페이지에서 다소 밋밋하고 템플릿 느낌이 남는다. 특히 `doc_f9...`의 초반 슬라이드와 논문형 문서의 서론 페이지에서 이 경향이 보였다.
- LLM이 여전히 필요한 페이지 유형은 시각 구조가 강한 페이지다. 예: `W1`의 개념도/프레임 페이지, `W2`의 WSJ 스크린샷, `26_통계학과`의 회귀 진단 그래프, 논문형 문서의 현미경+bar chart 페이지.

## 5. 의사결정 제안
추천: `tune compat quality first`
- reduction 자체는 충분히 크고 same-manifest 비교도 성공했다.
- 다만 compat가 많이 쓰이는 text-rich 문서에서 설명 톤이 안전하지만 얕은 편이라, 바로 broad rollout 전에 설명 품질을 한 번 다듬는 게 합리적이다.
- routing/scoring 실패 신호는 이번 corpus에선 크지 않았으므로 우선순위는 품질 보강 쪽이 더 높다.

## 6. 다음 slice 제안
추천 구현 단계: `compat 품질 개선`
- long_explanation/prerequisite/related_pages 조립 규칙을 조금 더 문서 유형별로 다듬고, text-rich compat artifact의 설명 밀도를 보강하는 slice가 가장 직접적이다.

## 7. blocker fix
- 있음
- 바뀐 파일: `backend/scripts/run_benchmark_corpus.py`, `backend/scripts/compare_pipeline_modes.py`, `backend/scripts/export_pass2_qa_samples.py`
- 막힌 이유: shared stamp로 pack 전체를 묶으려면 compare/export에 명시적 output prefix가 필요했고, same-manifest evidence를 증명하려면 run metadata에 `corpus_manifest_path`/`sha256`를 남겨야 했다. QA sample도 문서 유형/분기 이유를 같이 봐야 해서 `expected_type`과 `planner_reason` enrichment가 필요했다.
- 결과 해석 영향: pipeline semantics는 바뀌지 않았고, evidence pack의 재현성과 비교 신뢰도만 올라갔다.
