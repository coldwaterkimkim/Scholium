from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.services.storage import StorageService, get_storage_service


_BBOX_PRECISION = 3
_MAX_MATCHED_ELEMENTS = 5
_MAX_NEARBY_TEXT_BLOCKS = 5
_MAX_RELATED_PAGES = 3
_MAX_SOURCE_CANDIDATES = 4
_MAX_TEXT_SNIPPET_CHARS = 360
_MAX_SUMMARY_CHARS = 700
_CONTEXT_VERSION = "selection_context_v2_source_text_policy"


class SelectionContextBuilder:
    """Build the compact context sent to the selection explanation provider."""

    def __init__(self, storage: StorageService | None = None) -> None:
        self.storage = storage or get_storage_service()

    def build(
        self,
        *,
        document_id: str,
        page_number: int,
        selected_bbox: list[float],
        pass1_artifact: dict[str, Any],
        document_summary_artifact: dict[str, Any] | None,
        response_language: str = "ko",
    ) -> dict[str, Any]:
        pass1_result = dict(pass1_artifact.get("result") or {})
        pass1_meta = dict(pass1_artifact.get("meta") or {})
        document_summary = (
            dict(document_summary_artifact.get("result") or {})
            if isinstance(document_summary_artifact, dict)
            else None
        )
        document_summary_meta = (
            dict(document_summary_artifact.get("meta") or {})
            if isinstance(document_summary_artifact, dict)
            else {}
        )
        page_parse = self._load_page_parse_artifact(document_id, page_number)
        page_manifest_entry = self._load_page_manifest_entry(document_id, page_number)
        semantic_guide_artifact = self._load_semantic_guide_artifact(document_id)
        semantic_result = (
            dict(semantic_guide_artifact.get("result") or {})
            if isinstance(semantic_guide_artifact, dict)
            else None
        )
        semantic_meta = (
            dict(semantic_guide_artifact.get("meta") or {})
            if isinstance(semantic_guide_artifact, dict)
            else {}
        )

        rounded_bbox = self.round_bbox(selected_bbox)
        matched_page_elements = self._rank_page_elements(
            self._page_elements_from_pass1_result(pass1_result),
            rounded_bbox,
        )
        nearby_text_blocks = self._rank_nearby_text_blocks(page_parse, rounded_bbox)
        page_guide_brief = self._build_page_guide_brief(
            pass1_result.get("page_guide"),
            semantic_result,
            page_number,
        )
        document_context_brief = self._build_document_context_brief(
            document_summary,
            semantic_result,
            page_number,
            matched_page_elements,
        )
        related_page_candidates = self._build_related_page_candidates(
            document_id,
            document_summary,
            semantic_result,
            page_number,
            matched_page_elements,
        )
        source_candidates = self._build_source_candidates(
            page_number,
            matched_page_elements,
            nearby_text_blocks,
            document_context_brief,
        )

        context: dict[str, Any] = {
            "context_version": _CONTEXT_VERSION,
            "document_id": document_id,
            "page_number": page_number,
            "response_language": "en" if response_language == "en" else "ko",
            "source_text_policy": {
                "explanation_language": "en" if response_language == "en" else "ko",
                "preserve_source_terms": True,
                "preserve_fields": [
                    "concept_title",
                    "label",
                    "related_concepts_and_pages[].concept",
                    "source_cues[].label",
                    "source_cues[].snippet",
                ],
                "rule": (
                    "Write explanatory prose in response_language, but keep concept names, page titles, "
                    "selected text, labels, and snippets in the exact language/wording found in the PDF."
                ),
            },
            "selected_bbox": rounded_bbox,
            "readiness": {
                "page_context_ready": True,
                "document_context_ready": document_summary is not None or semantic_result is not None,
                "semantic_guide_ready": semantic_result is not None,
            },
            "matched_page_elements": matched_page_elements,
            "nearby_text_blocks": nearby_text_blocks,
            "page_role": self._compact_text(pass1_result.get("page_role"), max_chars=220),
            "page_summary": self._compact_text(pass1_result.get("page_summary"), max_chars=_MAX_SUMMARY_CHARS),
            "page_guide_brief": page_guide_brief,
            "document_context_brief": document_context_brief,
            "related_page_candidates": related_page_candidates,
            "source_candidates": source_candidates,
            "parser_source": self._parser_source(pass1_meta, page_parse, page_manifest_entry),
            "artifact_versions": {
                "schema_version": self.storage.settings.schema_version,
                "parser_schema_version": self.storage.settings.parser_schema_version,
                "pass1_prompt_version": pass1_meta.get("prompt_version"),
                "pass1_model_name": pass1_meta.get("model_name"),
                "pass1_path": pass1_meta.get("pass1_path"),
                "document_summary_prompt_version": document_summary_meta.get("prompt_version"),
                "document_summary_model_name": document_summary_meta.get("model_name"),
                "semantic_guide_prompt_version": semantic_meta.get("prompt_version"),
                "semantic_guide_model_name": semantic_meta.get("model_name"),
            },
        }

        core_json = self._stable_json(context)
        context["context_hash"] = hashlib.sha1(core_json.encode("utf-8")).hexdigest()
        context["metrics"] = {
            "selection_context_size_chars": len(core_json),
            "matched_element_count": len(matched_page_elements),
            "nearby_text_block_count": len(nearby_text_blocks),
            "source_candidate_count": len(source_candidates),
        }
        return context

    @staticmethod
    def round_bbox(bbox: list[float]) -> list[float]:
        return [round(float(value), _BBOX_PRECISION) for value in bbox]

    def _load_page_parse_artifact(self, document_id: str, page_number: int) -> dict[str, Any] | None:
        try:
            payload = self.storage.load_page_parse_artifact(document_id, page_number)
        except ValueError:
            return None
        return dict(payload) if isinstance(payload, dict) else None

    def _load_page_manifest_entry(self, document_id: str, page_number: int) -> dict[str, Any] | None:
        try:
            payload = self.storage.load_page_manifest(document_id)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        for page_payload in payload.get("pages", []):
            if isinstance(page_payload, dict) and int(page_payload.get("page_number", 0)) == page_number:
                return dict(page_payload)
        return None

    def _load_semantic_guide_artifact(self, document_id: str) -> dict[str, Any] | None:
        try:
            payload = self.storage.load_semantic_guide(document_id)
        except ValueError:
            return None
        return dict(payload) if isinstance(payload, dict) else None

    def _rank_page_elements(
        self,
        page_elements: object,
        selected_bbox: list[float],
    ) -> list[dict[str, Any]]:
        if not isinstance(page_elements, list):
            return []

        scored_elements: list[tuple[float, float, float, dict[str, Any]]] = []
        for element in page_elements:
            if not isinstance(element, dict):
                continue
            bbox = self._normalized_bbox(element.get("bbox"))
            if bbox is None:
                continue

            overlap = self._intersection_area(selected_bbox, bbox)
            candidate_area = self._area(bbox)
            selection_area = self._area(selected_bbox)
            overlap_ratio = overlap / max(0.0001, min(candidate_area, selection_area))
            distance = self._center_distance(selected_bbox, bbox)
            score = overlap_ratio - distance * 0.15
            scored_elements.append((score, overlap_ratio, distance, element))

        scored_elements.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "element_id": str(element.get("element_id") or element.get("anchor_id") or ""),
                "label": self._compact_text(element.get("label"), max_chars=120),
                "element_type": str(element.get("element_type") or element.get("anchor_type") or "other"),
                "bbox": self.round_bbox(list(element.get("bbox") or [])),
                "question": self._compact_text(element.get("question"), max_chars=180),
                "short_explanation": self._compact_text(element.get("short_explanation"), max_chars=260),
                "confidence": element.get("confidence"),
                "selection_overlap_ratio": round(max(0.0, overlap_ratio), 4),
                "selection_center_distance": round(max(0.0, distance), 4),
                "match_score": round(score, 4),
            }
            for score, overlap_ratio, distance, element in scored_elements[:_MAX_MATCHED_ELEMENTS]
        ]

    def _page_elements_from_pass1_result(self, pass1_result: dict[str, Any]) -> list[dict[str, Any]]:
        page_elements = pass1_result.get("page_elements")
        if isinstance(page_elements, list):
            return [element for element in page_elements if isinstance(element, dict)]

        legacy_candidates = pass1_result.get("candidate_anchors")
        if not isinstance(legacy_candidates, list):
            return []

        normalized_elements: list[dict[str, Any]] = []
        for candidate in legacy_candidates:
            if not isinstance(candidate, dict):
                continue
            normalized_elements.append(
                {
                    **candidate,
                    "element_id": candidate.get("element_id") or candidate.get("region_id") or candidate.get("anchor_id"),
                    "element_type": candidate.get("element_type") or candidate.get("region_type") or candidate.get("anchor_type"),
                }
            )
        return normalized_elements

    def _rank_nearby_text_blocks(
        self,
        page_parse: dict[str, Any] | None,
        selected_bbox: list[float],
    ) -> list[dict[str, Any]]:
        if not page_parse:
            return []
        blocks = page_parse.get("blocks")
        if not isinstance(blocks, list):
            return []

        scored_blocks: list[tuple[float, float, float, dict[str, Any]]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = self._compact_text(block.get("text"), max_chars=_MAX_TEXT_SNIPPET_CHARS)
            bbox = self._normalized_bbox(block.get("bbox"))
            if not text or bbox is None:
                continue

            overlap = self._intersection_area(selected_bbox, bbox)
            block_area = self._area(bbox)
            selection_area = self._area(selected_bbox)
            overlap_ratio = overlap / max(0.0001, min(block_area, selection_area))
            distance = self._center_distance(selected_bbox, bbox)
            type_bonus = 0.08 if str(block.get("block_type")) in {"caption", "table", "figure"} else 0.0
            score = overlap_ratio - distance * 0.12 + type_bonus
            scored_blocks.append((score, overlap_ratio, distance, block))

        scored_blocks.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "block_id": str(block.get("block_id") or ""),
                "block_type": str(block.get("block_type") or "other"),
                "bbox": self.round_bbox(list(block.get("bbox") or [])),
                "text": self._compact_text(block.get("text"), max_chars=_MAX_TEXT_SNIPPET_CHARS),
                "reading_order": int(block.get("reading_order", 0)),
                "selection_overlap_ratio": round(max(0.0, overlap_ratio), 4),
                "selection_center_distance": round(max(0.0, distance), 4),
                "match_score": round(score, 4),
            }
            for score, overlap_ratio, distance, block in scored_blocks[:_MAX_NEARBY_TEXT_BLOCKS]
        ]

    def _build_document_context_brief(
        self,
        document_summary: dict[str, Any] | None,
        semantic_result: dict[str, Any] | None,
        page_number: int,
        matched_page_elements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        semantic_document_guide = self._semantic_document_guide(semantic_result)
        if document_summary is None and semantic_document_guide is not None:
            document_summary = self._document_summary_from_semantic(semantic_document_guide)

        if document_summary is None:
            return {
                "available": False,
                "semantic_guide_available": False,
                "overall_topic": None,
                "overall_summary": None,
                "sections": [],
                "key_concepts": [],
                "difficult_pages": [],
            }

        matched_text = " ".join(
            str(element.get("label") or element.get("short_explanation") or "")
            for element in matched_page_elements
        ).lower()
        sections = [
            {
                "title": self._compact_text(section.get("title"), max_chars=140),
                "pages": section.get("pages", []),
            }
            for section in document_summary.get("sections", [])
            if isinstance(section, dict)
            and (
                page_number in [int(page) for page in section.get("pages", [])]
                or len(matched_page_elements) == 0
            )
        ][:3]
        key_concepts = []
        for concept in document_summary.get("key_concepts", []):
            if not isinstance(concept, dict):
                continue
            pages = [int(page) for page in concept.get("pages", [])]
            term = str(concept.get("term") or "")
            if page_number not in pages and term.lower() not in matched_text and len(key_concepts) >= 3:
                continue
            key_concepts.append(
                {
                    "term": self._compact_text(term, max_chars=120),
                    "description": self._compact_text(concept.get("description"), max_chars=220),
                    "pages": pages[:6],
                }
            )
            if len(key_concepts) >= 6:
                break

        return {
            "available": True,
            "semantic_guide_available": semantic_document_guide is not None,
            "overall_topic": self._compact_text(document_summary.get("overall_topic"), max_chars=180),
            "overall_summary": self._compact_text(document_summary.get("overall_summary"), max_chars=_MAX_SUMMARY_CHARS),
            "sections": sections,
            "key_concepts": key_concepts,
            "difficult_pages": [int(page) for page in document_summary.get("difficult_pages", [])][:5],
        }

    def _build_related_page_candidates(
        self,
        document_id: str,
        document_summary: dict[str, Any] | None,
        semantic_result: dict[str, Any] | None,
        page_number: int,
        matched_page_elements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        semantic_document_guide = self._semantic_document_guide(semantic_result)
        if document_summary is None and semantic_document_guide is not None:
            document_summary = self._document_summary_from_semantic(semantic_document_guide)

        if document_summary is None:
            return []

        matched_text = " ".join(
            str(element.get("label") or element.get("short_explanation") or "")
            for element in matched_page_elements
        ).lower()
        candidates: list[dict[str, Any]] = []

        for concept in document_summary.get("key_concepts", []):
            if not isinstance(concept, dict):
                continue
            pages = [int(page) for page in concept.get("pages", [])]
            term = str(concept.get("term") or "")
            if page_number not in pages and term.lower() not in matched_text:
                continue
            for related_page in pages:
                if related_page == page_number:
                    continue
                candidates.append(
                    {
                        "page_number": related_page,
                        "concept": self._compact_text(term, max_chars=120),
                        "reason": self._compact_text(concept.get("description"), max_chars=220),
                    }
                )

        for link in document_summary.get("prerequisite_links", []):
            if not isinstance(link, dict):
                continue
            from_page = int(link.get("from_page", 0))
            to_page = int(link.get("to_page", 0))
            if from_page == page_number:
                candidates.append(
                    {
                        "page_number": to_page,
                        "concept": "Prerequisite page",
                        "reason": self._compact_text(link.get("reason"), max_chars=220),
                    }
                )
            elif to_page == page_number:
                candidates.append(
                    {
                        "page_number": from_page,
                        "concept": "Later related page",
                        "reason": self._compact_text(link.get("reason"), max_chars=220),
                    }
                )

        seen_pages: set[int] = set()
        deduped_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            related_page = int(candidate.get("page_number", 0))
            if related_page <= 0 or related_page == page_number or related_page in seen_pages:
                continue
            seen_pages.add(related_page)
            deduped_candidates.append(
                self._with_related_page_source_labels(document_id, related_page, candidate)
            )
            if len(deduped_candidates) >= _MAX_RELATED_PAGES:
                break
        return deduped_candidates

    def _with_related_page_source_labels(
        self,
        document_id: str,
        page_number: int,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        source_labels = self._related_page_source_labels(document_id, page_number)
        source_label = self._best_source_label(source_labels)
        semantic_concept = self._compact_text(candidate.get("concept"), max_chars=120)
        source_preserved_concept = self._source_preserved_concept(
            semantic_concept,
            source_label,
            source_labels,
        )

        enriched = dict(candidate)
        enriched["semantic_concept"] = semantic_concept
        enriched["concept"] = source_preserved_concept or semantic_concept
        enriched["source_label"] = source_label
        enriched["source_labels"] = source_labels[:5]
        enriched["source_text_policy"] = "preserve_pdf_wording_for_concept_labels"
        return enriched

    def _related_page_source_labels(self, document_id: str, page_number: int) -> list[str]:
        labels: list[str] = []
        try:
            page_context = self.storage.load_page_context(document_id, page_number)
        except ValueError:
            page_context = None
        if isinstance(page_context, dict):
            for heading in page_context.get("heading_chain", []):
                self._append_source_label(labels, heading)
            for element in page_context.get("page_elements", [])[:10]:
                if not isinstance(element, dict):
                    continue
                self._append_source_label(labels, element.get("label"))
                self._append_source_label(labels, element.get("text"))
            for candidate in page_context.get("source_candidates", [])[:6]:
                if not isinstance(candidate, dict):
                    continue
                self._append_source_label(labels, candidate.get("label"))
                self._append_source_label(labels, candidate.get("snippet"))

        if labels:
            return labels

        try:
            pass1_artifact = self.storage.load_pass1_result(document_id, page_number)
        except ValueError:
            pass1_artifact = None
        pass1_result = dict(pass1_artifact.get("result") or {}) if isinstance(pass1_artifact, dict) else {}
        for element in self._page_elements_from_pass1_result(pass1_result)[:10]:
            self._append_source_label(labels, element.get("label"))
            self._append_source_label(labels, element.get("text"))
        return labels

    def _append_source_label(self, labels: list[str], value: object) -> None:
        label = self._compact_text(value, max_chars=140)
        if not label:
            return
        normalized_label = " ".join(label.split()).casefold()
        if normalized_label in {"text", "paragraph", "heading", "formula", "image", "table", "figure"}:
            return
        if any(" ".join(existing.split()).casefold() == normalized_label for existing in labels):
            return
        labels.append(label)

    def _best_source_label(self, labels: list[str]) -> str | None:
        if not labels:
            return None

        def score(label: str) -> tuple[int, int]:
            stripped = label.strip()
            score_value = 0
            if ":" in stripped:
                score_value += 4
            if self._contains_source_acronym(stripped):
                score_value += 3
            if 8 <= len(stripped) <= 96:
                score_value += 2
            if stripped.startswith(("•", "-", "→")):
                score_value -= 2
            if len(stripped) > 120:
                score_value -= 2
            return (score_value, -len(stripped))

        return max(labels, key=score)

    @staticmethod
    def _contains_source_acronym(value: str) -> bool:
        tokens = {token.upper() for token in re.findall(r"[A-Za-z0-9]+", value)}
        return bool(tokens & {"AI", "ML", "DL", "LLM", "AIBT"})

    def _source_preserved_concept(
        self,
        concept: str | None,
        source_label: str | None,
        source_labels: list[str],
    ) -> str | None:
        clean_concept = self._compact_text(concept, max_chars=120)
        if not source_label:
            return clean_concept
        if not clean_concept:
            return source_label

        concept_key = clean_concept.casefold()
        source_keys = [label.casefold() for label in source_labels]
        if any(concept_key in source_key or source_key in concept_key for source_key in source_keys):
            return clean_concept

        concept_has_hangul = self._has_hangul(clean_concept)
        source_has_hangul = any(self._has_hangul(label) for label in source_labels)
        source_has_ascii_words = any(self._has_ascii_letters(label) for label in source_labels)
        concept_has_ascii_words = self._has_ascii_letters(clean_concept)

        if concept_has_hangul and source_has_ascii_words:
            return source_label
        if concept_has_ascii_words and source_has_hangul and not source_has_ascii_words:
            return source_label
        if clean_concept in {"Prerequisite page", "Later related page", "Related page", "Mock concept"}:
            return source_label
        return clean_concept

    @staticmethod
    def _has_hangul(value: str) -> bool:
        return any("\uac00" <= character <= "\ud7a3" for character in value)

    @staticmethod
    def _has_ascii_letters(value: str) -> bool:
        return any(("a" <= character.lower() <= "z") for character in value)

    def _semantic_document_guide(self, semantic_result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(semantic_result, dict):
            return None
        document_guide = semantic_result.get("document_guide")
        return dict(document_guide) if isinstance(document_guide, dict) else None

    def _document_summary_from_semantic(
        self,
        semantic_document_guide: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "overall_topic": semantic_document_guide.get("overall_topic"),
            "overall_summary": semantic_document_guide.get("overall_summary"),
            "sections": semantic_document_guide.get("section_structure", []),
            "key_concepts": [
                {
                    "term": concept.get("concept"),
                    "description": concept.get("description"),
                    "pages": concept.get("pages", []),
                }
                for concept in semantic_document_guide.get("key_concepts", [])
                if isinstance(concept, dict)
            ],
            "difficult_pages": semantic_document_guide.get("difficult_pages", []),
            "prerequisite_links": semantic_document_guide.get("prerequisite_links", []),
        }

    def _build_page_guide_brief(
        self,
        pass1_page_guide: object,
        semantic_result: dict[str, Any] | None,
        page_number: int,
    ) -> dict[str, Any]:
        page_guide = dict(pass1_page_guide) if isinstance(pass1_page_guide, dict) else {}
        if isinstance(semantic_result, dict):
            for candidate in semantic_result.get("page_guides", []):
                if not isinstance(candidate, dict) or int(candidate.get("page_number", 0)) != page_number:
                    continue
                page_guide.update(
                    {
                        key: value
                        for key, value in candidate.items()
                        if key not in {"document_id", "page_number"}
                    }
                )
                break

        return {
            "available": bool(page_guide),
            "page_role": self._compact_text(page_guide.get("page_role"), max_chars=160),
            "one_line_thesis": self._compact_text(page_guide.get("one_line_thesis"), max_chars=260),
            "key_question": self._compact_text(page_guide.get("key_question"), max_chars=200),
            "logic_flow": self._compact_text_list(page_guide.get("logic_flow"), max_items=3, max_chars=180),
            "study_focus": self._compact_text_list(page_guide.get("study_focus"), max_items=3, max_chars=180),
            "common_confusions": self._compact_text_list(
                page_guide.get("common_confusions"),
                max_items=3,
                max_chars=180,
            ),
            "must_remember": self._compact_text_list(page_guide.get("must_remember"), max_items=3, max_chars=180),
        }

    def _build_source_candidates(
        self,
        page_number: int,
        matched_page_elements: list[dict[str, Any]],
        nearby_text_blocks: list[dict[str, Any]],
        document_context_brief: dict[str, Any],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for element in matched_page_elements:
            candidates.append(
                {
                    "source_type": "this_slide",
                    "label": element.get("label") or "Matched page element",
                    "page_number": page_number,
                    "snippet": element.get("short_explanation") or element.get("question"),
                    "bbox": element.get("bbox"),
                }
            )
        for block in nearby_text_blocks:
            source_type = "caption" if block.get("block_type") == "caption" else "this_slide"
            candidates.append(
                {
                    "source_type": source_type,
                    "label": str(block.get("block_type") or "text block"),
                    "page_number": page_number,
                    "snippet": block.get("text"),
                    "bbox": block.get("bbox"),
                }
            )
        if document_context_brief.get("available"):
            candidates.append(
                {
                    "source_type": "document_context",
                    "label": document_context_brief.get("overall_topic") or "Document context",
                    "page_number": None,
                    "snippet": document_context_brief.get("overall_summary"),
                }
            )

        compact_candidates: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for candidate in candidates:
            label = self._compact_text(candidate.get("label"), max_chars=120) or "Source cue"
            snippet = self._compact_text(candidate.get("snippet"), max_chars=220)
            key = (str(candidate.get("source_type")), label, snippet or "")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            compact_candidates.append(
                {
                    **candidate,
                    "label": label,
                    "snippet": snippet,
                }
            )
            if len(compact_candidates) >= _MAX_SOURCE_CANDIDATES:
                break
        return compact_candidates

    def _parser_source(
        self,
        pass1_meta: dict[str, Any],
        page_parse: dict[str, Any] | None,
        page_manifest_entry: dict[str, Any] | None,
    ) -> str:
        if pass1_meta.get("parser_source"):
            return str(pass1_meta["parser_source"])
        if page_parse and page_parse.get("parser_source"):
            return str(page_parse["parser_source"])
        if page_manifest_entry and page_manifest_entry.get("parser_source"):
            return str(page_manifest_entry["parser_source"])
        return self.storage.settings.document_parser_backend

    def _normalized_bbox(self, value: object) -> list[float] | None:
        if not isinstance(value, list) or len(value) != 4:
            return None
        try:
            bbox = [float(component) for component in value]
        except (TypeError, ValueError):
            return None
        x, y, width, height = bbox
        if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > 1 or y + height > 1:
            return None
        return self.round_bbox(bbox)

    def _area(self, bbox: list[float]) -> float:
        return max(0.0, float(bbox[2])) * max(0.0, float(bbox[3]))

    def _intersection_area(self, first_bbox: list[float], second_bbox: list[float]) -> float:
        first_right = first_bbox[0] + first_bbox[2]
        first_bottom = first_bbox[1] + first_bbox[3]
        second_right = second_bbox[0] + second_bbox[2]
        second_bottom = second_bbox[1] + second_bbox[3]
        width = min(first_right, second_right) - max(first_bbox[0], second_bbox[0])
        height = min(first_bottom, second_bottom) - max(first_bbox[1], second_bbox[1])
        return max(0.0, width) * max(0.0, height)

    def _center_distance(self, first_bbox: list[float], second_bbox: list[float]) -> float:
        first_center_x = first_bbox[0] + first_bbox[2] / 2
        first_center_y = first_bbox[1] + first_bbox[3] / 2
        second_center_x = second_bbox[0] + second_bbox[2] / 2
        second_center_y = second_bbox[1] + second_bbox[3] / 2
        return ((first_center_x - second_center_x) ** 2 + (first_center_y - second_center_y) ** 2) ** 0.5

    def _compact_text(self, value: object, *, max_chars: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 1)].rstrip() + "..."

    def _compact_text_list(
        self,
        value: object,
        *,
        max_items: int,
        max_chars: int,
    ) -> list[str]:
        if not isinstance(value, list):
            return []
        compacted: list[str] = []
        for item in value:
            text = self._compact_text(item, max_chars=max_chars)
            if not text:
                continue
            compacted.append(text)
            if len(compacted) >= max_items:
                break
        return compacted

    def _stable_json(self, value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
