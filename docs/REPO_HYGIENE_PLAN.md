# Scholium Repo Hygiene Plan

이번 branch에서는 삭제/아카이브를 실행하지 않는다. 이 문서는 다음 hygiene 전용 branch에서 무엇을 지워도 되는지, 무엇은 보관 위치만 바꿀지 정리한 계획이다.

## Safe Delete Candidates

아래는 repo 동작에 필요 없는 로컬/생성물이라 별도 branch에서 삭제해도 안전하다.

- `.DS_Store`
- `__pycache__/`
- `frontend/.next/`
- `frontend/tsconfig.tsbuildinfo`
- root 임시 PNG screenshot: 예를 들어 `localhost-root-check.png`, `parallel-drag-before.png`, `parallel-drag-after.png`

## Archive Candidates

삭제보다는 `docs/archive/` 같은 위치로 옮기는 편이 낫다. 과거 방향성, 투자/사업 문맥, 오래된 진단은 나중에 히스토리로 유용할 수 있다.

- old PRDs: `docs/scholium_*_prd_v0_revised.md`
- old audits: `docs/project_audit.md`, `docs/chat_handoff_2026-03-29.md`
- bizplan docs: `docs/bizplan_*.md`
- old cost/perf diagnosis docs: `docs/COST_PERF_DIAGNOSIS.md`, 오래된 perf/audit report
- old dataset notes: 현재 corpus와 맞지 않는 `docs/test_dataset_manifest.md`

## Keep Current

현재 selected-region MVP와 직접 연결되는 문서는 유지한다.

- `README.md`
- `docs/api_model_decisions.md`
- `docs/prompts/*`
- `docs/perf/PERFORMANCE_BASELINE_PLAN.md`
- 최신 benchmark runner 문서와 실제 실행 명령

## Generated Artifact Policy

생성물은 “재현 가능한 기준선”과 “로컬 runtime 부산물”을 나눠서 관리한다.

- `data/parsed/**`: parser/runtime artifact다. 원칙적으로 git 추적 대상이 아니다. 이미 추적된 샘플은 별도 cleanup branch에서 유지 여부를 판단한다.
- `data/analysis/**`: 로컬 처리 결과다. fixture로 의도한 최소 샘플만 남기고 일반 실행 결과는 추적하지 않는다.
- `docs/perf_runs/**`: benchmark evidence다. 비교 기준으로 쓰는 run만 설명과 함께 남기고, 임시 실행 JSON은 `/tmp`에 저장한다.
- sample fixtures: 작은 PDF/JSON fixture는 이름과 목적이 명확할 때만 둔다.

## Hygiene Branch Rule

정리 branch에서는 앱 동작 변경을 섞지 않는다. 삭제/이동만 하고, 마지막에 `git status`로 삭제 대상이 safe delete/archive 범위를 벗어나지 않았는지 확인한다.
