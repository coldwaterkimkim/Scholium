from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.core.config import AppSettings, get_settings
from app.models.document import PageRecord


_BBOX_PRECISION = 4
_MAX_ELEMENT_TEXT_CHARS = 360
_MAX_PAGE_SUMMARY_CHARS = 520
_MAX_PAGE_TEXT_CHARS = 2600
_MAX_HEADING_CHAIN = 4
_FORMULA_LIKE_PATTERN = re.compile(r"(?:=|<=|>=|≤|≥|∑|∫|√|\b(?:npv|pbr|roe|eps|roi|r\\^2)\b)", re.IGNORECASE)
_ELEMENT_TYPE_MAP = {
    "heading": "text",
    "paragraph": "text",
    "list": "text",
    "table": "table",
    "figure": "diagram",
    "formula": "formula",
    "caption": "text",
    "other": "other",
}


class PageContextBuilder:
    """Build deterministic parser-first page context without model calls."""

    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def build_page_context(
        self,
        *,
        document_id: str,
        page_record: PageRecord,
        parsed_page: dict[str, Any] | None,
        page_manifest_entry: dict[str, Any] | None,
        parser_source: str | None,
    ) -> dict[str, Any]:
        page_number = page_record.page_number
        blocks = self._sorted_blocks(parsed_page)
        parser_had_no_blocks = not blocks
        if parser_had_no_blocks:
            blocks = [
                {
                    "block_id": f"p{page_number}_fallback_region",
                    "block_type": "other",
                    "text": "",
                    "bbox": [0.0, 0.0, 1.0, 1.0],
                    "reading_order": 0,
                }
            ]
        manifest = dict(page_manifest_entry or {})
        source = self._parser_source(parser_source, parsed_page, manifest)
        text_blocks = [block for block in blocks if self._block_text(block)]
        visual_blocks = [
            block
            for block in blocks
            if self._block_type(block) in {"figure", "table"} or not self._block_text(block)
        ]
        table_blocks = [block for block in blocks if self._block_type(block) == "table"]
        figure_blocks = [block for block in blocks if self._block_type(block) == "figure"]
        caption_blocks = [block for block in blocks if self._block_type(block) == "caption"]
        formula_like_blocks = [
            block
            for block in blocks
            if self._block_type(block) == "formula" or _FORMULA_LIKE_PATTERN.search(self._block_text(block))
        ]

        page_elements = [
            self._page_element_from_block(
                document_id=document_id,
                page_number=page_number,
                block=block,
                parser_source=source,
                index=index,
            )
            for index, block in enumerate(blocks)
            if self._valid_bbox(block.get("bbox"))
        ]

        text_length = int(manifest.get("text_length", sum(len(self._block_text(block)) for block in blocks)))
        image_count = int(manifest.get("image_count", 0))
        block_count = int(manifest.get("block_count", len(blocks)))
        non_empty_text_block_count = int(
            manifest.get("non_empty_text_block_count", len(text_blocks))
        )
        text_density = self._ratio(min(text_length, _MAX_PAGE_TEXT_CHARS), _MAX_PAGE_TEXT_CHARS)
        image_coverage = self._estimate_coverage(visual_blocks)
        table_like_score = self._ratio(len(table_blocks), max(block_count, 1))
        figure_like_score = self._ratio(len(figure_blocks) + image_count, max(block_count + image_count, 1))
        scan_like_score = self._scan_like_score(
            text_length=text_length,
            block_count=block_count,
            image_count=image_count,
            has_visual=bool(visual_blocks),
        )

        quality_notes = self._quality_notes(
            page_elements=page_elements,
            blocks=blocks,
            parser_had_no_blocks=parser_had_no_blocks,
            text_length=text_length,
            scan_like_score=scan_like_score,
            manifest=manifest,
        )

        return {
            "document_id": document_id,
            "page_number": page_number,
            "parser_source": source,
            "schema_version": self.settings.schema_version,
            "parser_schema_version": self.settings.parser_schema_version,
            "page_elements": page_elements,
            "text_blocks": self._compact_blocks(text_blocks),
            "visual_blocks": self._compact_blocks(visual_blocks),
            "table_blocks": self._compact_blocks(table_blocks),
            "figure_blocks": self._compact_blocks(figure_blocks),
            "caption_blocks": self._compact_blocks(caption_blocks),
            "formula_like_blocks": self._compact_blocks(formula_like_blocks),
            "heading_chain": self._heading_chain(blocks),
            "reading_order_quality": self._reading_order_quality(blocks),
            "text_density": round(text_density, 4),
            "image_coverage": round(image_coverage, 4),
            "scan_like_score": round(scan_like_score, 4),
            "table_like_score": round(table_like_score, 4),
            "figure_like_score": round(figure_like_score, 4),
            "source_candidates": self._source_candidates(page_elements, text_blocks),
            "parser_quality_notes": quality_notes,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def build_pass1_envelope(self, page_context: dict[str, Any]) -> dict[str, Any]:
        page_elements = list(page_context.get("page_elements") or [])
        page_role = self._deterministic_page_role(page_context)
        page_summary = self._deterministic_page_summary(page_context)
        return {
            "meta": {
                "schema_version": self.settings.schema_version,
                "prompt_version": "parser_first_page_context_v0_1",
                "model_name": "deterministic-parser",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "pass1_path": "parser_first",
                "route_label": self._route_label(page_context),
                "route_reason": self._route_reason(page_context),
                "parser_source": str(page_context.get("parser_source") or "unknown"),
            },
            "result": {
                "document_id": page_context["document_id"],
                "page_number": page_context["page_number"],
                "page_role": page_role,
                "page_summary": page_summary,
                "page_guide": self._placeholder_page_guide(page_role, page_summary, page_context),
                "wrap_up": self._placeholder_wrap_up(page_context),
                "candidate_anchors": self._legacy_candidates_from_page_elements(page_elements),
            },
        }

    def build_document_digest(
        self,
        *,
        document_id: str,
        page_contexts: list[dict[str, Any]],
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        selected_contexts = sorted(page_contexts, key=lambda item: int(item["page_number"]))
        if max_pages is not None:
            selected_contexts = selected_contexts[:max_pages]

        pages: list[dict[str, Any]] = []
        for page_context in selected_contexts:
            text_snippets = [
                str(block.get("text") or "").strip()
                for block in page_context.get("text_blocks", [])
                if isinstance(block, dict) and str(block.get("text") or "").strip()
            ]
            element_summary = [
                {
                    "element_id": element.get("element_id"),
                    "element_type": element.get("element_type"),
                    "text": self._compact_text(element.get("text"), max_chars=160),
                    "reading_order": element.get("reading_order"),
                }
                for element in list(page_context.get("page_elements") or [])[:14]
                if isinstance(element, dict)
            ]
            pages.append(
                {
                    "page_number": int(page_context["page_number"]),
                    "heading_chain": page_context.get("heading_chain", []),
                    "text_snippets": self._clip_snippets(text_snippets),
                    "element_summary": element_summary,
                    "parser_metrics": {
                        "text_density": page_context.get("text_density"),
                        "image_coverage": page_context.get("image_coverage"),
                        "scan_like_score": page_context.get("scan_like_score"),
                        "table_like_score": page_context.get("table_like_score"),
                        "figure_like_score": page_context.get("figure_like_score"),
                        "reading_order_quality": page_context.get("reading_order_quality"),
                    },
                    "parser_quality_notes": page_context.get("parser_quality_notes", []),
                }
            )

        return {
            "document_id": document_id,
            "schema_version": self.settings.schema_version,
            "parser_schema_version": self.settings.parser_schema_version,
            "digest_version": "parser_document_digest_v0_1",
            "page_count": len(pages),
            "pages": pages,
        }

    def _page_element_from_block(
        self,
        *,
        document_id: str,
        page_number: int,
        block: dict[str, Any],
        parser_source: str,
        index: int,
    ) -> dict[str, Any]:
        block_type = self._block_type(block)
        element_type = _ELEMENT_TYPE_MAP.get(block_type, "other")
        text = self._block_text(block)
        element_id = str(block.get("block_id") or f"p{page_number}_e{index}")
        label = self._label_for_block(block_type, text, index)
        quality_notes: list[str] = []
        if not text:
            quality_notes.append("parser_bbox_without_text")
        return {
            "element_id": element_id,
            "element_type": element_type,
            "anchor_id": element_id,
            "anchor_type": element_type,
            "bbox": self._round_bbox(list(block.get("bbox") or [0, 0, 1, 1])),
            "text": self._compact_text(text, max_chars=_MAX_ELEMENT_TEXT_CHARS),
            "label": label,
            "question": self._question_for_block(block_type, label),
            "short_explanation": self._short_explanation_for_block(block_type, text, label),
            "reading_order": int(block.get("reading_order", index)),
            "source_parser": parser_source,
            "confidence": self._confidence_for_block(block_type, text, block.get("bbox")),
            "quality_notes": quality_notes,
        }

    def _legacy_candidates_from_page_elements(
        self,
        page_elements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for element in page_elements:
            if not isinstance(element, dict):
                continue
            candidates.append(
                {
                    "anchor_id": str(element.get("element_id") or element.get("anchor_id") or ""),
                    "label": self._compact_text(element.get("label"), max_chars=120),
                    "anchor_type": str(element.get("element_type") or element.get("anchor_type") or "other"),
                    "bbox": list(element.get("bbox") or [0.0, 0.0, 1.0, 1.0]),
                    "question": self._compact_text(element.get("question"), max_chars=180)
                    or "What does this parser region mean?",
                    "short_explanation": self._compact_text(element.get("short_explanation"), max_chars=260)
                    or "Parser-generated bbox region.",
                    "confidence": float(element.get("confidence") or 0.5),
                }
            )
        return candidates[:80]

    def _sorted_blocks(self, parsed_page: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(parsed_page, dict):
            return []
        blocks = parsed_page.get("blocks")
        if not isinstance(blocks, list):
            return []
        return sorted(
            [dict(block) for block in blocks if isinstance(block, dict)],
            key=lambda block: int(block.get("reading_order", 0)),
        )

    def _compact_blocks(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "block_id": str(block.get("block_id") or ""),
                "block_type": self._block_type(block),
                "bbox": self._round_bbox(list(block.get("bbox") or [0, 0, 1, 1])),
                "text": self._compact_text(self._block_text(block), max_chars=_MAX_ELEMENT_TEXT_CHARS),
                "reading_order": int(block.get("reading_order", 0)),
            }
            for block in blocks
            if self._valid_bbox(block.get("bbox"))
        ]

    def _heading_chain(self, blocks: list[dict[str, Any]]) -> list[str]:
        headings = [
            self._compact_text(self._block_text(block), max_chars=160)
            for block in blocks
            if self._block_type(block) == "heading" and self._block_text(block)
        ]
        if headings:
            return headings[:_MAX_HEADING_CHAIN]

        first_text = [
            self._compact_text(self._block_text(block), max_chars=160)
            for block in blocks[:2]
            if self._block_text(block)
        ]
        return first_text[:1]

    def _source_candidates(
        self,
        page_elements: list[dict[str, Any]],
        text_blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for element in page_elements[:5]:
            if not isinstance(element, dict):
                continue
            candidates.append(
                {
                    "source_type": "parser_page_element",
                    "element_id": element.get("element_id"),
                    "label": element.get("label"),
                    "bbox": element.get("bbox"),
                    "snippet": self._compact_text(element.get("text"), max_chars=180),
                }
            )
        for block in text_blocks[:3]:
            candidates.append(
                {
                    "source_type": "parser_text_block",
                    "block_id": block.get("block_id"),
                    "label": block.get("block_type") or "text",
                    "bbox": block.get("bbox"),
                    "snippet": self._compact_text(block.get("text"), max_chars=180),
                }
            )
        return candidates[:8]

    def _placeholder_page_guide(
        self,
        page_role: str,
        page_summary: str,
        page_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "page_role": page_role,
            "previous_slide_connection": None,
            "one_line_thesis": page_summary,
        }

    def _placeholder_wrap_up(self, page_context: dict[str, Any]) -> dict[str, Any]:
        notes = [str(item) for item in page_context.get("parser_quality_notes", []) if str(item).strip()]
        return {
            "logic_flow": [],
            "study_focus": " ".join(notes[:2]) if notes else None,
            "must_remember": [],
            "next_slide_connection": None,
        }

    def _deterministic_page_role(self, page_context: dict[str, Any]) -> str:
        if float(page_context.get("scan_like_score") or 0.0) >= 0.65:
            return "Parser-detected scan-like page"
        if float(page_context.get("table_like_score") or 0.0) > 0:
            return "Table or structured data page"
        if float(page_context.get("figure_like_score") or 0.0) > 0.2:
            return "Visual explanation page"
        if page_context.get("heading_chain"):
            return "Text-rich page with parser headings"
        return "Parser-mapped PDF page"

    def _deterministic_page_summary(self, page_context: dict[str, Any]) -> str:
        snippets = [
            str(block.get("text") or "").strip()
            for block in page_context.get("text_blocks", [])
            if isinstance(block, dict) and str(block.get("text") or "").strip()
        ]
        if snippets:
            joined = " ".join(snippets[:3])
            return self._compact_text(joined, max_chars=_MAX_PAGE_SUMMARY_CHARS)
        notes = page_context.get("parser_quality_notes", [])
        if notes:
            return self._compact_text("; ".join(str(note) for note in notes), max_chars=_MAX_PAGE_SUMMARY_CHARS)
        return "Parser generated a geometry map for this rendered page, but no reliable text was extracted."

    def _quality_notes(
        self,
        *,
        page_elements: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        parser_had_no_blocks: bool,
        text_length: int,
        scan_like_score: float,
        manifest: dict[str, Any],
    ) -> list[str]:
        notes: list[str] = []
        if parser_had_no_blocks:
            notes.append("parser_produced_no_blocks")
        if not page_elements:
            notes.append("parser_produced_no_bbox_elements")
        if text_length < 80:
            notes.append("low_text_extraction")
        if scan_like_score >= 0.65:
            notes.append("scan_like_or_image_heavy_page")
        route_reason = str(manifest.get("route_reason") or "").strip()
        if route_reason:
            notes.append(f"triage:{route_reason}"[:180])
        return notes[:8]

    def _reading_order_quality(self, blocks: list[dict[str, Any]]) -> str:
        if not blocks:
            return "missing"
        orders = [int(block.get("reading_order", 0)) for block in blocks]
        if orders == sorted(set(orders)) and len(orders) == len(set(orders)):
            return "parser_ordered"
        return "parser_order_ambiguous"

    def _route_label(self, page_context: dict[str, Any]) -> str:
        scan_like = float(page_context.get("scan_like_score") or 0.0)
        if scan_like >= 0.65:
            return "scan-like"
        if float(page_context.get("table_like_score") or 0.0) > 0 or float(page_context.get("figure_like_score") or 0.0) > 0.2:
            return "visual-rich"
        return "text-rich"

    def _route_reason(self, page_context: dict[str, Any]) -> str:
        notes = page_context.get("parser_quality_notes")
        if isinstance(notes, list) and notes:
            return "; ".join(str(note) for note in notes[:3])
        return "parser_first_page_context"

    def _parser_source(
        self,
        parser_source: str | None,
        parsed_page: dict[str, Any] | None,
        manifest: dict[str, Any],
    ) -> str:
        if parser_source:
            return parser_source
        if isinstance(parsed_page, dict) and parsed_page.get("parser_source"):
            return str(parsed_page["parser_source"])
        if manifest.get("parser_source"):
            return str(manifest["parser_source"])
        return self.settings.document_parser_backend

    def _block_type(self, block: dict[str, Any]) -> str:
        return str(block.get("block_type") or "other")

    def _block_text(self, block: dict[str, Any]) -> str:
        return " ".join(str(block.get("text") or "").split())

    def _label_for_block(self, block_type: str, text: str, index: int) -> str:
        if text:
            return self._compact_text(text, max_chars=90)
        if block_type == "table":
            return f"Table region {index + 1}"
        if block_type == "figure":
            return f"Figure region {index + 1}"
        return f"Parser region {index + 1}"

    def _question_for_block(self, block_type: str, label: str) -> str:
        if block_type == "table":
            return "What is this table showing?"
        if block_type == "figure":
            return "What does this visual region mean?"
        if block_type == "formula":
            return "What does this formula or symbol mean?"
        return f"What does this part mean: {label}?"

    def _short_explanation_for_block(self, block_type: str, text: str, label: str) -> str:
        if text:
            return self._compact_text(text, max_chars=220)
        if block_type == "table":
            return "Parser detected a table-like region; use the selected-region explanation for its local meaning."
        if block_type == "figure":
            return "Parser detected a visual region; use the selected-region explanation for its local meaning."
        return f"Parser detected a bbox-grounded region labeled {label}."

    def _confidence_for_block(self, block_type: str, text: str, bbox: object) -> float:
        if not self._valid_bbox(bbox):
            return 0.35
        if text and block_type in {"heading", "paragraph", "list", "caption", "formula"}:
            return 0.82
        if text and block_type == "table":
            return 0.76
        if block_type in {"figure", "table"}:
            return 0.64
        return 0.58

    def _valid_bbox(self, value: object) -> bool:
        if not isinstance(value, list) or len(value) != 4:
            return False
        try:
            x, y, width, height = [float(component) for component in value]
        except (TypeError, ValueError):
            return False
        return width > 0 and height > 0 and x >= 0 and y >= 0 and x + width <= 1 and y + height <= 1

    def _round_bbox(self, bbox: list[Any]) -> list[float]:
        x, y, width, height = [float(component) for component in bbox]
        x = min(max(x, 0.0), 1.0)
        y = min(max(y, 0.0), 1.0)
        width = min(max(width, 0.0001), 1.0 - x)
        height = min(max(height, 0.0001), 1.0 - y)
        return [round(value, _BBOX_PRECISION) for value in (x, y, width, height)]

    def _estimate_coverage(self, blocks: list[dict[str, Any]]) -> float:
        return min(1.0, sum(self._bbox_area(block.get("bbox")) for block in blocks))

    def _bbox_area(self, value: object) -> float:
        if not self._valid_bbox(value):
            return 0.0
        _, _, width, height = [float(component) for component in value]  # type: ignore[arg-type]
        return max(0.0, width) * max(0.0, height)

    def _scan_like_score(
        self,
        *,
        text_length: int,
        block_count: int,
        image_count: int,
        has_visual: bool,
    ) -> float:
        if text_length == 0 and (image_count > 0 or has_visual or block_count <= 1):
            return 0.95
        score = 0.0
        if text_length < 80:
            score += 0.45
        if block_count <= 1:
            score += 0.25
        if image_count > 0 or has_visual:
            score += 0.2
        return min(1.0, score)

    def _ratio(self, numerator: int | float, denominator: int | float) -> float:
        if denominator <= 0:
            return 0.0
        return max(0.0, min(1.0, float(numerator) / float(denominator)))

    def _clip_snippets(self, snippets: list[str]) -> list[str]:
        clipped: list[str] = []
        for snippet in snippets:
            compact = self._compact_text(snippet, max_chars=320)
            if compact:
                clipped.append(compact)
            if len(clipped) >= 5:
                break
        return clipped

    def _compact_text(self, value: object, *, max_chars: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 1)].rstrip() + "..."
