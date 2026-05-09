# Development Source Of Truth

Use these files as the current source of truth for Scholium development.

## Product And Architecture

- `README.md`
- `docs/CURRENT_ARCHITECTURE.md`
- `docs/api_model_decisions.md`
- `docs/LEGACY_NAMING_DEBT.md`

Old archived docs are historical reference and should not override current architecture.

## Performance And Parser Decisions

- `docs/perf/PARSER_DECISION_20260507.md`
- `docs/perf/PERFORMANCE_BASELINE_PLAN.md`
- `docs/perf/PARSER_BENCHMARK_SUITE.md`
- `docs/perf_runs/README.md`
- `benchmarks/parser_selection_goldset.yaml`

Raw generated benchmark runs belong in ignored local `docs/perf_runs/` outputs, not in merge-ready source.

## Prompts

- `docs/prompts/pass1_prompt.md`
- `docs/prompts/document_guide_prompt.md`
- `docs/prompts/page_guide_chunk_prompt.md`
- `docs/prompts/semantic_guide_prompt.md`
- `docs/prompts/document_synthesis_prompt.md` only for legacy compatibility/debug
- `docs/prompts/selection_explanation_prompt.md`
- `docs/prompts/selection_follow_up_prompt.md`
- `docs/prompts/pass2_prompt.md` only for legacy/debug precomputed anchor-click mode

## Backend Selected-Region Flow

- `backend/app/api/documents.py`
- `backend/app/core/config.py`
- `backend/app/models/read_api.py`
- `backend/app/schemas/selection_explanation_schema.py`
- `backend/app/services/selection_context_builder.py`
- `backend/app/services/selection_explainer.py`
- `backend/app/services/codex_cli_client.py`
- `backend/app/services/openai_client.py`
- `backend/app/services/storage.py`
- `backend/app/services/document_parser.py`
- `backend/app/services/pymupdf4llm_adapter.py`
- `backend/app/services/page_context_builder.py`
- `backend/app/services/semantic_guide_generator.py`
- `backend/app/services/orchestrator.py`
- `backend/app/workers/render_worker.py`
- `backend/app/workers/pass1_worker.py`
- `backend/app/workers/document_synthesis_worker.py`
- `backend/app/workers/pass2_worker.py` only for legacy/debug precomputed anchor-click mode

## Frontend Selected-Region Flow

- `frontend/lib/api.ts`
- `frontend/utils/bbox.ts`
- `frontend/components/UploadForm.tsx`
- `frontend/components/ProcessingStatus.tsx`
- `frontend/components/DocumentViewer.tsx`
- `frontend/components/PageGuidePanel.tsx`
- `frontend/components/SelectedExplanationPanel.tsx`
- `frontend/components/LegacyAnchorOverlay.tsx` only for legacy/debug precomputed anchor-click mode
- `frontend/components/RightPanel.tsx` only for legacy/debug precomputed anchor-click mode

## Benchmarks And Scripts

- `backend/scripts/benchmark_selected_region_perf.py`
- `backend/scripts/benchmark_pass1_modes.py`
- `backend/scripts/benchmark_pdf_parsers.py`
- `backend/scripts/benchmark_parser_selection_explanations.py`
- `backend/scripts/export_parser_goldset_review.py`

Older business plans, old PRDs, old audits, old handoff notes, old cost/perf diagnosis docs, and March 2026 perf run summaries live under `docs/archive/`.
