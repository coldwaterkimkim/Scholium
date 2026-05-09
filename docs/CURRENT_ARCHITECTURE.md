# Current Architecture

Scholium's current MVP is a selected-region explanation viewer.

The default product flow is:

```text
PDF upload / render / parser-first preprocess / semantic guide
-> document worklist with status, elapsed time, delete, and processing entry
-> clean PDF viewer
-> top-edge Page Guide gives page-level reading orientation
-> user drag-selects a confusing region
-> backend builds compact SelectionContext
-> Codex CLI generates an on-demand selected-region explanation
-> floating academic annotation panel appears near the selected region
```

## Current Default UX

- The home screen is the document worklist. Uploads stay in the list while they prepare, and duplicate filenames overwrite the existing document job.
- The user-facing flow is not upload -> empty viewer. Viewer entry is enabled after the document is usable.
- The viewer shows a clean rendered PDF page after parser map and minimum semantic guide readiness.
- When pass1 page context exists, the viewer shows a top-edge Page Guide attached to the learning material area. This is a proactive page-level reading guide, not a chatbot and not a side rail.
- The user chooses what is confusing by dragging a region on the page.
- Scholium explains that selected region on demand, using page/document context that was prepared earlier.
- The explanation appears in the floating academic annotation panel near the selected region.

## Explanation Layers

Scholium now has two complementary explanation layers.

| Layer | Timing | Scope | Placement | Answers |
| --- | --- | --- | --- | --- |
| `Page Guide` | proactive | page-level macro orientation | top edge of the viewer surface | "How should I read this page?" |
| `Selected Explanation Panel` | reactive | selected-region micro explanation | floating near the selected bbox | "What does this exact selected part mean?" |

The Page Guide should reconstruct the page's role, thesis, reading path, logic flow, concepts, omitted context, study focus, confusions, takeaways, self-check questions, and optional before/next connection. It should not repeat the whole slide or replace selected-region explanations.

Precomputed anchor-click is legacy/debug only. It can still be useful for internal comparison or rollback checks, but it is not the primary user experience.

## Provider Strategy

- Local default provider: Codex CLI (`SCHOLIUM_LLM_PROVIDER=codex_cli`)
- Optional fallback provider: OpenAI API (`SCHOLIUM_LLM_PROVIDER=openai_api`)
- Mock provider: local smoke/testing only
- Current core path does not use native PDF provider APIs, Files API, or prompt caching.
- OpenAI/Claude/Gemini native PDF input and caching are future optional cloud-provider paths, not current MVP infrastructure.

Do not change provider selection logic as part of repo hygiene or naming cleanup.

## Parser Strategy

- Runtime default parser backend remains `DOCUMENT_PARSER_BACKEND=pymupdf4llm`.
- The current default path is enhanced PyMuPDF4LLM plus fitz geometry, reported as `pymupdf4llm_enhanced+fitz`.
- Parser owns bbox and page elements. LLM output must not be the source of truth for bbox in the default path.
- `PASS1_MODE=parser_first` builds deterministic `page_context.json` / PageElementMap without page-by-page LLM calls.
- Existing `page_analysis_pass1.json` remains as a compatibility envelope for page API and SelectionContextBuilder; its persisted `candidate_anchors` are parser-derived in parser_first mode.
- Heavy parsers such as Docling, Marker, MinerU, and MarkItDown are not production dependencies.
- Docling remains a possible future optional heavy-parser candidate.
- Marker/MinerU are deferred.
- MarkItDown is not suitable as a bbox-grounded selected-region parser backend.
- OCR/scanned-PDF handling is deferred and should become an explicit optional lane after cheap scan-like page detection.

## Semantic Guide Strategy

- Semantic Guide is the document/page meaning layer.
- It consumes compact parser-generated document digest, not full raw artifacts for every page.
- It is generated through local Codex CLI using one document-level call in the current implementation.
- `DocumentGuide` captures overall topic, summary, section structure, key concepts, page sequence, prerequisite links, difficult pages, and study strategy notes.
- `PageGuide` remains proactive macro information. It does not replace selected-region explanations and does not pre-explain every visual element.
- A compatibility `document_summary.json` is still saved so existing summary APIs, follow-up, and old artifact loaders keep working.

## Readiness Modes

Scholium now separates viewer readiness from full explanation readiness.

| Mode | Meaning |
| --- | --- |
| `render_only` | Page image exists, but this is not the main user-facing viewer destination after upload. |
| `parser_map_ready` | Deterministic PageContext/PageElementMap exists. |
| `semantic_guide_ready` | Minimum DocumentGuide/PageGuides exist. |
| `viewer_ready` | Parser map plus minimum Semantic Guide exist. This is the worklist/processing gate for viewer entry. |
| `page_context_ready` | Existing API compatibility name for parser map/page context ready. |
| `on_demand` | Page context and semantic/document context are ready. This is the default selected-region MVP mode. |
| `legacy_pass2` | Precomputed anchor-click debug path. Used only when `SCHOLIUM_PRECOMPUTE_ANCHORED_EXPLANATIONS=true`. |

The API may still return `render_only` for direct page requests while processing, but worklist/processing viewer entry is gated by `viewer_ready` / `ready_for_viewer`. Old pass1 artifacts without `page_guide` are loaded with a minimal fallback from `page_role` and `page_summary`.

## Runtime Context

`SelectionContext` is the core runtime context object for selected-region explanations.

It is built for one selected bbox and should stay compact. It includes:

- selected bbox and page identity
- matched page elements
- nearby text blocks
- page role and page summary
- compact PageGuide subset when available
- brief document context when available
- Semantic Guide DocumentGuide brief when available
- related page candidates
- source candidates
- a context hash for caching and reproducibility

The backend should not send full pass1 artifacts, full document summaries, or every page element by default when a compact `SelectionContext` is enough.

## Key Artifacts

| Artifact | Role |
| --- | --- |
| `data/parsed/{document_id}/document_parse.json` | Parser-owned blocks with bbox, text, type, and reading order. |
| `data/parsed/{document_id}/page_manifest.json` | Parser/triage signal for text-rich, visual-rich, and scan-like pages. |
| `data/analysis/{document_id}/pages/{page}/page_context.json` | Deterministic parser-first PageContext/PageElementMap. |
| `data/analysis/{document_id}/pages/{page}/page_analysis_pass1.json` | Compatibility envelope. In parser_first mode it contains parser-derived `candidate_anchors` and loaded `page_elements`. |
| `data/analysis/{document_id}/semantic_guide.json` | Semantic Guide artifact with DocumentGuide and PageGuides. |
| `data/analysis/{document_id}/document_summary.json` | Compatibility summary generated from Semantic Guide for existing APIs/follow-up. |

## Glossary

| Term | Meaning |
| --- | --- |
| `PageElement` | A parsed or model-identified region on a page that can help build `SelectionContext`. |
| `CandidateRegion` | A bbox-grounded region that may be useful for selected-region explanation. |
| `SelectedRegion` | The user-dragged bbox region. |
| `SelectionExplanation` | The generated explanation for a selected region. |
| `SelectionContext` | The compact context built from page/document artifacts for one selected region. |
| `PageGuide` | Proactive page-level reading orientation generated during pass1 and exposed on the page API. |
| `LegacyPrecomputedAnchor` | Old precomputed explanation region used only for legacy/debug/pass2 compatibility. |

## Legacy Naming Compatibility

Some field names still contain `anchor` because earlier MVP work used precomputed clickable anchors.

- `candidate_anchors` is a legacy persisted field name. Conceptually it now means `page_elements` or `candidate_regions` used to ground selected-region explanations.
- `page_elements` is the current public/API-facing name for those normalized page regions.
- `final_anchors` is a legacy field name for precomputed anchor-click output. In current default pages it should usually be empty.
- `anchor_id` and `anchor_type` remain compatibility schema/API fields. In selected-region results, `anchor_id` may mirror `selection_id`.

Do not rename these persisted/API fields casually. Use `docs/LEGACY_NAMING_DEBT.md` for planned schema-safe cleanup.
