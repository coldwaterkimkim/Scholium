from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services.storage import StorageService, get_storage_service


_BBOX_PRECISION = 3
_MAX_MATCHED_ELEMENTS = 5
_MAX_NEARBY_TEXT_BLOCKS = 5
_MAX_RELATED_PAGES = 3
_MAX_SOURCE_CANDIDATES = 4
_MAX_TEXT_SNIPPET_CHARS = 360
_MAX_SUMMARY_CHARS = 700
_CONTEXT_VERSION = "selection_context_v1"


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

        rounded_bbox = self.round_bbox(selected_bbox)
        matched_page_elements = self._rank_page_elements(
            pass1_result.get("candidate_anchors"),
            rounded_bbox,
        )
        nearby_text_blocks = self._rank_nearby_text_blocks(page_parse, rounded_bbox)
        document_context_brief = self._build_document_context_brief(
            document_summary,
            page_number,
            matched_page_elements,
        )
        related_page_candidates = self._build_related_page_candidates(
            document_summary,
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
            "selected_bbox": rounded_bbox,
            "readiness": {
                "page_context_ready": True,
                "document_context_ready": document_summary is not None,
            },
            "matched_page_elements": matched_page_elements,
            "nearby_text_blocks": nearby_text_blocks,
            "page_role": self._compact_text(pass1_result.get("page_role"), max_chars=220),
            "page_summary": self._compact_text(pass1_result.get("page_summary"), max_chars=_MAX_SUMMARY_CHARS),
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

    def _rank_page_elements(
        self,
        candidates: object,
        selected_bbox: list[float],
    ) -> list[dict[str, Any]]:
        if not isinstance(candidates, list):
            return []

        scored_elements: list[tuple[float, float, float, dict[str, Any]]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            bbox = self._normalized_bbox(candidate.get("bbox"))
            if bbox is None:
                continue

            overlap = self._intersection_area(selected_bbox, bbox)
            candidate_area = self._area(bbox)
            selection_area = self._area(selected_bbox)
            overlap_ratio = overlap / max(0.0001, min(candidate_area, selection_area))
            distance = self._center_distance(selected_bbox, bbox)
            score = overlap_ratio - distance * 0.15
            scored_elements.append((score, overlap_ratio, distance, candidate))

        scored_elements.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "element_id": str(candidate.get("anchor_id") or ""),
                "label": self._compact_text(candidate.get("label"), max_chars=120),
                "element_type": str(candidate.get("anchor_type") or "other"),
                "bbox": self.round_bbox(list(candidate.get("bbox") or [])),
                "question": self._compact_text(candidate.get("question"), max_chars=180),
                "short_explanation": self._compact_text(candidate.get("short_explanation"), max_chars=260),
                "confidence": candidate.get("confidence"),
                "selection_overlap_ratio": round(max(0.0, overlap_ratio), 4),
                "selection_center_distance": round(max(0.0, distance), 4),
                "match_score": round(score, 4),
            }
            for score, overlap_ratio, distance, candidate in scored_elements[:_MAX_MATCHED_ELEMENTS]
        ]

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
        page_number: int,
        matched_page_elements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if document_summary is None:
            return {
                "available": False,
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
            "overall_topic": self._compact_text(document_summary.get("overall_topic"), max_chars=180),
            "overall_summary": self._compact_text(document_summary.get("overall_summary"), max_chars=_MAX_SUMMARY_CHARS),
            "sections": sections,
            "key_concepts": key_concepts,
            "difficult_pages": [int(page) for page in document_summary.get("difficult_pages", [])][:5],
        }

    def _build_related_page_candidates(
        self,
        document_summary: dict[str, Any] | None,
        page_number: int,
        matched_page_elements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
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
            deduped_candidates.append(candidate)
            if len(deduped_candidates) >= _MAX_RELATED_PAGES:
                break
        return deduped_candidates

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

    def _stable_json(self, value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
