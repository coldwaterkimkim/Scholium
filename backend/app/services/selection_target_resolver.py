from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz


NEAR_EXACT_IOU_THRESHOLD = 0.65
NEAR_EXACT_SELECTION_COVERAGE_THRESHOLD = 0.85
NEAR_EXACT_ELEMENT_COVERAGE_THRESHOLD = 0.65
INSIDE_LARGE_ELEMENT_SELECTION_COVERAGE_THRESHOLD = 0.85
TEXT_CONFIDENT_INSIDE_LARGE_SELECTION_COVERAGE_THRESHOLD = 0.65
INSIDE_LARGE_ELEMENT_MAX_ELEMENT_COVERAGE = 0.45
MEANINGFUL_ELEMENT_SELECTION_COVERAGE = 0.12
MEANINGFUL_ELEMENT_COVERAGE = 0.08
WORD_OVERLAP_THRESHOLD = 0.45
WORD_CENTER_TOLERANCE = 0.004
MAX_MATCHED_WORDS = 80
VISUAL_ELEMENT_TYPES = {"chart", "diagram", "figure", "image", "table", "flow"}
FORMULA_PATTERN = re.compile(r"(?:=|≤|≥|∑|∫|√|\b(?:pbr|npv|roe|eps|roi)\b)", re.IGNORECASE)


@dataclass(frozen=True)
class _WordBox:
    text: str
    bbox: list[float]
    reading_order: int
    block_no: int | None = None
    line_no: int | None = None
    word_no: int | None = None


@dataclass(frozen=True)
class _ElementMatch:
    element: dict[str, Any]
    element_id: str
    element_type: str
    bbox: list[float]
    intersection_area: float
    iou: float
    selection_covered_by_element: float
    element_covered_by_selection: float


class SelectionTargetResolver:
    """Resolve the user's selected bbox into the most exact target available.

    This resolver is deterministic by design: it never calls an LLM and never
    re-runs full document parsing. Parser page elements are useful context, but
    the selected bbox and word-level text inside it are the primary signal.
    """

    def resolve(
        self,
        *,
        document_id: str,
        page_number: int,
        selected_bbox: list[float],
        page_parse: dict[str, Any] | None = None,
        page_elements: list[dict[str, Any]] | None = None,
        pdf_path: str | Path | None = None,
        rendered_page_image_path: str | Path | None = None,
    ) -> dict[str, Any]:
        rounded_selection = self._round_bbox(selected_bbox)
        elements = self._normalize_elements(page_elements or [])
        element_matches = self._rank_element_matches(elements, rounded_selection)
        matched_words = self._match_words(
            selected_bbox=rounded_selection,
            page_parse=page_parse,
            pdf_path=pdf_path,
            page_number=page_number,
        )
        selected_text_exact = self._selected_text_from_words(matched_words)
        selected_text_confidence = self._selected_text_confidence(matched_words, rounded_selection)
        visual_matches = [
            match
            for match in element_matches
            if match.element_type in VISUAL_ELEMENT_TYPES and self._is_meaningful_element_match(match)
        ]
        meaningful_element_matches = [
            match for match in element_matches if self._is_meaningful_element_match(match)
        ]
        primary_match = element_matches[0] if element_matches else None
        near_exact_match = next(
            (match for match in element_matches if self._is_near_exact_element_match(match)),
            None,
        )
        inside_large_match = next(
            (match for match in element_matches if self._is_exact_text_inside_large_element(match)),
            None,
        )
        if inside_large_match is None and selected_text_exact and selected_text_confidence >= 0.7:
            inside_large_match = next(
                (
                    match
                    for match in element_matches
                    if match.selection_covered_by_element
                    >= TEXT_CONFIDENT_INSIDE_LARGE_SELECTION_COVERAGE_THRESHOLD
                    and match.element_covered_by_selection < INSIDE_LARGE_ELEMENT_MAX_ELEMENT_COVERAGE
                ),
                None,
            )
        enclosing_block_text = self._enclosing_block_text(
            selected_bbox=rounded_selection,
            page_parse=page_parse,
            primary_match=inside_large_match or primary_match,
        )

        bbox_match_mode = "unknown"
        target_kind = "unknown"
        routing_notes: list[str] = []

        if near_exact_match is not None:
            bbox_match_mode = "near_exact_element_match"
            target_kind = "page_element"
            routing_notes.append("Selection nearly matches a parser page element; element can support the target.")
        elif selected_text_exact and inside_large_match is not None:
            bbox_match_mode = "exact_text_inside_large_element"
            target_kind = "exact_text"
            routing_notes.append(
                "Exact selected text is inside a larger parser element; keep the selected text as primary target."
            )
        elif len(meaningful_element_matches) >= 2:
            bbox_match_mode = "multi_element_selection"
            target_kind = "mixed" if selected_text_exact and visual_matches else "multi_element"
            routing_notes.append("Selection overlaps multiple meaningful page elements.")
        elif selected_text_exact:
            bbox_match_mode = "exact_text_inside_large_element" if primary_match else "unknown"
            target_kind = "exact_text"
            routing_notes.append("Word-level text inside the selected bbox is the primary target.")
        elif visual_matches:
            bbox_match_mode = "visual_crop"
            target_kind = "visual_crop"
            routing_notes.append("No reliable selected text was found; visual/table/diagram crop metadata is needed.")
        elif primary_match and self._is_meaningful_element_match(primary_match):
            bbox_match_mode = "near_exact_element_match" if self._is_near_exact_element_match(primary_match) else "unknown"
            target_kind = "page_element"
            routing_notes.append("Selection has no exact words, but overlaps a parser page element.")
        else:
            bbox_match_mode = "visual_crop" if not selected_text_exact else "unknown"
            target_kind = "unknown"
            routing_notes.append("Selection could not be resolved confidently from available parser artifacts.")

        if selected_text_exact and visual_matches and target_kind not in {"multi_element", "mixed"}:
            target_kind = "mixed"
            bbox_match_mode = "mixed"
            routing_notes.append("Selection includes both exact text and visual context.")

        target_type = self._target_type(
            target_kind=target_kind,
            selected_text_exact=selected_text_exact,
            primary_match=near_exact_match or inside_large_match or primary_match,
            visual_matches=visual_matches,
        )
        crop_needed = target_kind in {"visual_crop", "mixed"} or bool(
            visual_matches and target_type in {"table", "diagram", "figure"}
        )
        crop_bbox = rounded_selection if crop_needed else None
        primary_element_id = (
            (near_exact_match or inside_large_match or primary_match).element_id
            if (near_exact_match or inside_large_match or primary_match) is not None
            else None
        )
        matched_element_ids = [match.element_id for match in meaningful_element_matches[:5]]

        if rendered_page_image_path:
            routing_notes.append("Rendered page image is available for provider-side visual grounding.")
        if pdf_path and not matched_words:
            routing_notes.append("Resolver attempted page-local PyMuPDF word extraction.")

        return {
            "target_kind": target_kind,
            "target_type": target_type,
            "selected_text_exact": selected_text_exact,
            "selected_text_confidence": round(selected_text_confidence, 4),
            "enclosing_block_text": enclosing_block_text,
            "matched_words": [
                {
                    "text": word.text,
                    "bbox": self._round_bbox(word.bbox),
                    "reading_order": word.reading_order,
                }
                for word in matched_words[:MAX_MATCHED_WORDS]
            ],
            "matched_word_count": len(matched_words),
            "primary_element_id": primary_element_id,
            "matched_element_ids": matched_element_ids,
            "bbox_match_mode": bbox_match_mode,
            "crop_needed": crop_needed,
            "crop_bbox": crop_bbox,
            "confidence": round(
                self._confidence(
                    target_kind=target_kind,
                    selected_text_confidence=selected_text_confidence,
                    near_exact_match=near_exact_match,
                    visual_matches=visual_matches,
                    meaningful_element_matches=meaningful_element_matches,
                ),
                4,
            ),
            "routing_notes": routing_notes,
        }

    def _match_words(
        self,
        *,
        selected_bbox: list[float],
        page_parse: dict[str, Any] | None,
        pdf_path: str | Path | None,
        page_number: int,
    ) -> list[_WordBox]:
        words = self._words_from_page_parse(page_parse)
        if not words and pdf_path is not None:
            words = self._words_from_pdf_page(pdf_path, page_number)

        matched: list[_WordBox] = []
        for word in words:
            overlap = self._intersection_area(selected_bbox, word.bbox)
            if overlap <= 0:
                continue
            word_area = self._area(word.bbox)
            if word_area <= 0:
                continue
            if overlap / word_area >= WORD_OVERLAP_THRESHOLD or self._center_inside(word.bbox, selected_bbox):
                matched.append(word)

        matched.sort(key=self._word_sort_key)
        return matched[:MAX_MATCHED_WORDS]

    def _words_from_page_parse(self, page_parse: dict[str, Any] | None) -> list[_WordBox]:
        if not isinstance(page_parse, dict):
            return []

        words: list[_WordBox] = []
        for raw_word in page_parse.get("words", []) if isinstance(page_parse.get("words"), list) else []:
            word = self._word_from_payload(raw_word, len(words))
            if word is not None:
                words.append(word)

        blocks = page_parse.get("blocks")
        if not isinstance(blocks, list):
            return words

        for block_index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            for raw_word in block.get("words", []) if isinstance(block.get("words"), list) else []:
                word = self._word_from_payload(raw_word, len(words), block_no=block_index)
                if word is not None:
                    words.append(word)
            for raw_span in block.get("spans", []) if isinstance(block.get("spans"), list) else []:
                span_word = self._word_from_payload(raw_span, len(words), block_no=block_index)
                if span_word is not None:
                    words.append(span_word)
        return words

    def _word_from_payload(
        self,
        payload: object,
        reading_order: int,
        *,
        block_no: int | None = None,
    ) -> _WordBox | None:
        if not isinstance(payload, dict):
            return None
        text = " ".join(str(payload.get("text") or payload.get("word") or "").split())
        bbox = self._normalized_bbox(payload.get("bbox"))
        if not text or bbox is None:
            return None
        return _WordBox(
            text=text,
            bbox=bbox,
            reading_order=int(payload.get("reading_order") or reading_order),
            block_no=int(payload.get("block_no")) if payload.get("block_no") is not None else block_no,
            line_no=int(payload.get("line_no")) if payload.get("line_no") is not None else None,
            word_no=int(payload.get("word_no")) if payload.get("word_no") is not None else None,
        )

    def _words_from_pdf_page(self, pdf_path: str | Path, page_number: int) -> list[_WordBox]:
        resolved_pdf_path = Path(pdf_path)
        if not resolved_pdf_path.exists():
            return []

        words: list[_WordBox] = []
        try:
            with fitz.open(resolved_pdf_path) as pdf_document:
                if page_number < 1 or page_number > pdf_document.page_count:
                    return []
                page = pdf_document.load_page(page_number - 1)
                width = float(page.rect.width)
                height = float(page.rect.height)
                for index, raw_word in enumerate(page.get_text("words")):
                    if len(raw_word) < 5:
                        continue
                    x0, y0, x1, y1, text = raw_word[:5]
                    block_no = int(raw_word[5]) if len(raw_word) > 5 else None
                    line_no = int(raw_word[6]) if len(raw_word) > 6 else None
                    word_no = int(raw_word[7]) if len(raw_word) > 7 else None
                    clean_text = " ".join(str(text).split())
                    if not clean_text:
                        continue
                    bbox = [
                        float(x0) / width,
                        float(y0) / height,
                        max(0.0, float(x1) - float(x0)) / width,
                        max(0.0, float(y1) - float(y0)) / height,
                    ]
                    normalized_bbox = self._normalized_bbox(bbox)
                    if normalized_bbox is None:
                        continue
                    words.append(
                        _WordBox(
                            text=clean_text,
                            bbox=normalized_bbox,
                            reading_order=index,
                            block_no=block_no,
                            line_no=line_no,
                            word_no=word_no,
                        )
                    )
        except Exception:
            return []
        return words

    def _selected_text_from_words(self, matched_words: list[_WordBox]) -> str | None:
        if not matched_words:
            return None
        return " ".join(word.text for word in matched_words).strip() or None

    def _selected_text_confidence(self, matched_words: list[_WordBox], selected_bbox: list[float]) -> float:
        if not matched_words:
            return 0.0
        word_coverages = [
            self._intersection_area(selected_bbox, word.bbox) / max(0.0001, self._area(word.bbox))
            for word in matched_words
        ]
        avg_word_coverage = sum(word_coverages) / len(word_coverages)
        count_bonus = min(0.18, len(matched_words) * 0.03)
        return min(0.98, max(0.2, avg_word_coverage + count_bonus))

    def _rank_element_matches(
        self,
        elements: list[dict[str, Any]],
        selected_bbox: list[float],
    ) -> list[_ElementMatch]:
        matches: list[_ElementMatch] = []
        selection_area = self._area(selected_bbox)
        for element in elements:
            bbox = self._normalized_bbox(element.get("bbox"))
            if bbox is None:
                continue
            element_area = self._area(bbox)
            intersection_area = self._intersection_area(selected_bbox, bbox)
            union_area = selection_area + element_area - intersection_area
            element_id = str(
                element.get("element_id")
                or element.get("anchor_id")
                or element.get("region_id")
                or ""
            )
            if not element_id:
                continue
            element_type = str(
                element.get("element_type")
                or element.get("anchor_type")
                or element.get("region_type")
                or "unknown"
            ).strip().lower()
            matches.append(
                _ElementMatch(
                    element=element,
                    element_id=element_id,
                    element_type=element_type,
                    bbox=bbox,
                    intersection_area=intersection_area,
                    iou=intersection_area / max(0.0001, union_area),
                    selection_covered_by_element=intersection_area / max(0.0001, selection_area),
                    element_covered_by_selection=intersection_area / max(0.0001, element_area),
                )
            )

        matches.sort(
            key=lambda match: (
                match.selection_covered_by_element,
                match.element_covered_by_selection,
                match.iou,
                -self._center_distance(selected_bbox, match.bbox),
            ),
            reverse=True,
        )
        return matches

    def _normalize_elements(self, page_elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for element in page_elements:
            if not isinstance(element, dict):
                continue
            normalized.append(dict(element))
        return normalized

    def _enclosing_block_text(
        self,
        *,
        selected_bbox: list[float],
        page_parse: dict[str, Any] | None,
        primary_match: _ElementMatch | None,
    ) -> str | None:
        candidates: list[tuple[float, str]] = []
        if isinstance(page_parse, dict) and isinstance(page_parse.get("blocks"), list):
            for block in page_parse["blocks"]:
                if not isinstance(block, dict):
                    continue
                text = " ".join(str(block.get("text") or "").split())
                bbox = self._normalized_bbox(block.get("bbox"))
                if not text or bbox is None:
                    continue
                overlap = self._intersection_area(selected_bbox, bbox)
                if overlap <= 0:
                    continue
                coverage = overlap / max(0.0001, self._area(selected_bbox))
                candidates.append((coverage, text))

        if candidates:
            candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
            return candidates[0][1]

        if primary_match is not None:
            label = primary_match.element.get("text") or primary_match.element.get("label")
            compact = " ".join(str(label or "").split())
            return compact or None
        return None

    def _target_type(
        self,
        *,
        target_kind: str,
        selected_text_exact: str | None,
        primary_match: _ElementMatch | None,
        visual_matches: list[_ElementMatch],
    ) -> str:
        if target_kind == "mixed":
            return "mixed"
        element_type = (
            primary_match.element_type
            if primary_match is not None
            else visual_matches[0].element_type
            if visual_matches
            else ""
        )
        if element_type == "formula" or (selected_text_exact and FORMULA_PATTERN.search(selected_text_exact)):
            return "formula"
        if element_type == "table":
            return "table"
        if element_type in {"diagram", "chart", "flow"}:
            return "diagram"
        if element_type == "figure":
            return "figure"
        if element_type == "caption":
            return "caption"
        if selected_text_exact:
            word_count = len(selected_text_exact.split())
            if word_count <= 4:
                return "concept_term"
            if word_count <= 12:
                return "phrase"
            return "paragraph"
        if visual_matches:
            return "figure"
        return "unknown"

    def _confidence(
        self,
        *,
        target_kind: str,
        selected_text_confidence: float,
        near_exact_match: _ElementMatch | None,
        visual_matches: list[_ElementMatch],
        meaningful_element_matches: list[_ElementMatch],
    ) -> float:
        if target_kind == "exact_text":
            return min(0.96, max(0.72, selected_text_confidence))
        if target_kind == "page_element" and near_exact_match is not None:
            return min(0.94, max(0.78, near_exact_match.iou + 0.2))
        if target_kind in {"multi_element", "mixed"}:
            return min(0.88, max(0.62, selected_text_confidence + 0.12))
        if target_kind == "visual_crop":
            best_visual = max((match.selection_covered_by_element for match in visual_matches), default=0.0)
            return min(0.78, max(0.48, best_visual))
        if meaningful_element_matches:
            return 0.52
        return 0.25

    def _is_near_exact_element_match(self, match: _ElementMatch) -> bool:
        return match.iou >= NEAR_EXACT_IOU_THRESHOLD or (
            match.selection_covered_by_element >= NEAR_EXACT_SELECTION_COVERAGE_THRESHOLD
            and match.element_covered_by_selection >= NEAR_EXACT_ELEMENT_COVERAGE_THRESHOLD
        )

    def _is_exact_text_inside_large_element(self, match: _ElementMatch) -> bool:
        return (
            match.selection_covered_by_element >= INSIDE_LARGE_ELEMENT_SELECTION_COVERAGE_THRESHOLD
            and match.element_covered_by_selection < INSIDE_LARGE_ELEMENT_MAX_ELEMENT_COVERAGE
        )

    def _is_meaningful_element_match(self, match: _ElementMatch) -> bool:
        return (
            match.selection_covered_by_element >= MEANINGFUL_ELEMENT_SELECTION_COVERAGE
            or match.element_covered_by_selection >= MEANINGFUL_ELEMENT_COVERAGE
            or match.iou >= 0.03
        )

    def _word_sort_key(self, word: _WordBox) -> tuple[int, int, float, float, int]:
        if word.block_no is not None and word.line_no is not None and word.word_no is not None:
            return (word.block_no, word.line_no, 0.0, 0.0, word.word_no)
        return (
            word.block_no if word.block_no is not None else 9999,
            word.line_no if word.line_no is not None else int(round(word.bbox[1] / 0.015)),
            round(word.bbox[1], 4),
            round(word.bbox[0], 4),
            word.reading_order,
        )

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
        return self._round_bbox(bbox)

    def _round_bbox(self, bbox: list[float]) -> list[float]:
        return [round(float(value), 4) for value in bbox]

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

    def _center_inside(self, word_bbox: list[float], selected_bbox: list[float]) -> bool:
        center_x = word_bbox[0] + word_bbox[2] / 2
        center_y = word_bbox[1] + word_bbox[3] / 2
        return (
            selected_bbox[0] - WORD_CENTER_TOLERANCE
            <= center_x
            <= selected_bbox[0] + selected_bbox[2] + WORD_CENTER_TOLERANCE
            and selected_bbox[1] - WORD_CENTER_TOLERANCE
            <= center_y
            <= selected_bbox[1] + selected_bbox[3] + WORD_CENTER_TOLERANCE
        )
