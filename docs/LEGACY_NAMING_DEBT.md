# Legacy Naming Debt

Scholium's current MVP is a selected-region explanation viewer. Old anchor-era names are now compatibility debt, not the product model.

This migration keeps old artifacts readable while moving active code and docs toward:

- `page_elements`
- `candidate_regions`
- `selected_regions`
- `SelectionContext`
- `SelectionExplanation`

## Classification

| Class | Meaning |
| --- | --- |
| A. Safe to rename now | Docs, comments, UI labels, helper names, internal frontend variables, and low-risk benchmark labels. |
| B. Rename with compatibility layer | Backend models, API response fields, parser/pass1 artifacts, storage loaders, and frontend API types. |
| C. Defer | Persisted artifact field names, prompt output schemas, legacy pass2/debug artifacts, and analytics fields that need a separate migration. |

## Term Audit

| Legacy term | Current conceptual meaning | Class | Current handling |
| --- | --- | --- | --- |
| `candidate_anchors` | Persisted pass1 field for page elements / candidate regions | B/C | Storage accepts legacy `candidate_anchors` and new `page_elements` / `candidate_regions`, then normalizes to persisted `candidate_anchors` plus a loaded `page_elements` alias. Prompt output schema still uses `candidate_anchors`. |
| `candidate_anchor_count` | Page element count | A/B | Backend pass1 result now emits `page_element_count` and keeps `candidate_anchor_count` as compatibility metadata. |
| `anchor_id` | Element ID, selection ID, or legacy anchor ID depending on artifact | B/C | Public page elements expose `element_id` while keeping `anchor_id`. Selection explanations still mirror `anchor_id = selection_id` for compatibility. |
| `anchor_type` | Element type / region type / legacy anchor type | B/C | Public page elements expose `element_type` while keeping `anchor_type`. Prompt output schemas still use `anchor_type`. |
| `final_anchors` | Legacy precomputed anchor-click explanation output | C | Kept only for `legacy_pass2`; selected-region pages return it empty. |
| `anchor_click` | Legacy precomputed anchor-click analytics event | C | Kept for existing logs/debug viewer compatibility. Current selected-region flow uses `selection_*` events. |
| `AnchorOverlay` | Legacy overlay for precomputed final anchors | A | Renamed to `LegacyAnchorOverlay`; not part of the primary selected-region UI. |
| `selectedAnchor` | Legacy selected precomputed anchor state | A | Renamed in retained legacy/debug panel props to selected legacy region wording. |
| `selectedAnchorId` | Legacy selected precomputed anchor ID | A | Renamed to `legacySelectedAnchorId` in the legacy overlay. |
| `Anchor Details` | Old UI label for precomputed anchor details | A | Replaced by `Legacy Region Details`. |
| `pass2` | Legacy/debug precomputed explanation stage | C | Retained for compatibility and internal debug only. Default selected-region flow does not require it. |
| `legacy_pass2` | Viewer readiness mode for old precomputed artifact path | C | Kept as explicit compatibility mode. |

## Backend Strategy

- Persisted pass1 JSON remains backward compatible with `candidate_anchors`.
- New pass1-like input can provide `page_elements` or `candidate_regions`.
- `StorageService.load_pass1_result()` returns normalized `result.page_elements` so current code can use current naming without rewriting old JSON files.
- `PagePublicResponse.page_elements` now uses the `PageElement` schema with `element_id` / `element_type` plus legacy aliases.
- `SelectionContextBuilder` consumes `page_elements` first and falls back to legacy `candidate_anchors`.
- `final_anchors`, pass2 schemas, and log column names are intentionally deferred.

## Deferred Cleanup

- Split `anchor_id` / `anchor_type` out of selection explanation and analytics contracts.
- Remove the remaining compatibility alias `FinalAnchor` after old callers stop importing it.
- Migrate benchmark CSV/JSON keys such as `matched_element_anchor_ids` only if downstream consumers are updated.
- Consider an artifact v0.3 migration only after selected-region MVP behavior is stable.
