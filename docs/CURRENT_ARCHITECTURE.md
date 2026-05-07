# Current Architecture

Scholium's current MVP is a selected-region explanation viewer.

The default product flow is:

```text
PDF upload / render / preprocess
-> clean PDF viewer
-> user drag-selects a confusing region
-> backend builds compact SelectionContext
-> Codex CLI generates an on-demand selected-region explanation
-> floating academic annotation panel appears near the selected region
```

## Current Default UX

- The viewer shows a clean rendered PDF page first.
- The user chooses what is confusing by dragging a region on the page.
- Scholium explains that selected region on demand, using page/document context that was prepared earlier.
- The explanation appears in the floating academic annotation panel near the selected region.

Precomputed anchor-click is legacy/debug only. It can still be useful for internal comparison or rollback checks, but it is not the primary user experience.

## Provider Strategy

- Local default provider: Codex CLI (`SCHOLIUM_LLM_PROVIDER=codex_cli`)
- Optional fallback provider: OpenAI API (`SCHOLIUM_LLM_PROVIDER=openai_api`)
- Mock provider: local smoke/testing only

Do not change provider selection logic as part of repo hygiene or naming cleanup.

## Parser Strategy

- Runtime default parser backend remains `DOCUMENT_PARSER_BACKEND=pymupdf4llm`.
- The current default path is enhanced PyMuPDF4LLM plus fitz geometry, reported as `pymupdf4llm_enhanced+fitz`.
- Heavy parsers such as Docling, Marker, MinerU, and MarkItDown are not production dependencies.
- Docling remains a possible future optional heavy-parser candidate.
- Marker/MinerU are deferred.
- MarkItDown is not suitable as a bbox-grounded selected-region parser backend.
- OCR/scanned-PDF handling is deferred and should become an explicit optional lane after cheap scan-like page detection.

## Readiness Modes

Scholium now separates viewer readiness from full explanation readiness.

| Mode | Meaning |
| --- | --- |
| `render_only` | Page image exists. The PDF is readable, but explanation context is not ready. |
| `page_context_ready` | Pass1 page context exists. The user can request a selected-region explanation, but document-wide context may be limited. |
| `on_demand` | Page context and document synthesis are ready. This is the default selected-region MVP mode. |
| `legacy_pass2` | Precomputed anchor-click debug path. Used only when `SCHOLIUM_PRECOMPUTE_ANCHORED_EXPLANATIONS=true`. |

## Runtime Context

`SelectionContext` is the core runtime context object for selected-region explanations.

It is built for one selected bbox and should stay compact. It includes:

- selected bbox and page identity
- matched page elements
- nearby text blocks
- page role and page summary
- brief document context when available
- related page candidates
- source candidates
- a context hash for caching and reproducibility

The backend should not send full pass1 artifacts, full document summaries, or every page element by default when a compact `SelectionContext` is enough.

## Glossary

| Term | Meaning |
| --- | --- |
| `PageElement` | A parsed or model-identified region on a page that can help build `SelectionContext`. |
| `CandidateRegion` | A bbox-grounded region that may be useful for selected-region explanation. |
| `SelectedRegion` | The user-dragged bbox region. |
| `SelectionExplanation` | The generated explanation for a selected region. |
| `SelectionContext` | The compact context built from page/document artifacts for one selected region. |
| `LegacyPrecomputedAnchor` | Old precomputed explanation region used only for legacy/debug/pass2 compatibility. |

## Legacy Naming Compatibility

Some field names still contain `anchor` because earlier MVP work used precomputed clickable anchors.

- `candidate_anchors` is a legacy persisted field name. Conceptually it now means `page_elements` or `candidate_regions` used to ground selected-region explanations.
- `page_elements` is the current public/API-facing name for those normalized page regions.
- `final_anchors` is a legacy field name for precomputed anchor-click output. In current default pages it should usually be empty.
- `anchor_id` and `anchor_type` remain compatibility schema/API fields. In selected-region results, `anchor_id` may mirror `selection_id`.

Do not rename these persisted/API fields casually. Use `docs/LEGACY_NAMING_DEBT.md` for planned schema-safe cleanup.
