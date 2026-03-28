from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import pymupdf4llm

from app.core.config import AppSettings, get_settings
from app.models.parser import (
    DocumentParseArtifact,
    ParseBlock,
    ParseBlockType,
    ParsedPage,
)


logger = logging.getLogger(__name__)

_BULLET_LIST_PATTERN = re.compile(r"^\s*[-*+•]\s+")
_ORDERED_LIST_PATTERN = re.compile(r"^\s*\d+[\.\)]\s+")
_CAPTION_PATTERN = re.compile(r"^\s*(figure|fig\.|table|chart|image)\b", re.IGNORECASE)
_HEADING_MARKER_PATTERN = re.compile(r"^\s*#{1,6}\s+")
_TABLE_OVERLAP_THRESHOLD = 0.35
_HEADING_SIZE_DELTA = 2.0
_HEADING_MAX_LINES = 3
_HEADING_MAX_CHARACTERS = 160


@dataclass(frozen=True)
class _BlockCandidate:
    rect: fitz.Rect
    text: str
    block_type: ParseBlockType | None
    max_font_size: float | None = None


@dataclass(frozen=True)
class _FitzTextBlock:
    rect: fitz.Rect
    text: str
    block_type: int
    max_font_size: float | None = None


class PyMuPDF4LLMDocumentParser:
    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        parser_source: str = "pymupdf4llm+fitz",
    ) -> None:
        self.settings = settings or get_settings()
        self.parser_source = parser_source

    def parse_document(
        self,
        document_id: str,
        pdf_path: str | Path,
    ) -> DocumentParseArtifact:
        resolved_pdf_path = Path(pdf_path)

        with fitz.open(resolved_pdf_path) as pdf_document:
            try:
                page_chunks = self._extract_page_chunks(pdf_document)
            except Exception as exc:
                logger.warning(
                    "PyMuPDF4LLM extraction failed for %s, falling back to fitz page-level text only: %s",
                    document_id,
                    exc,
                )
                return self._build_document_with_fitz_fallback(document_id, pdf_document)

            pages: list[ParsedPage] = []
            for page_index in range(pdf_document.page_count):
                page = pdf_document.load_page(page_index)
                page_chunk = page_chunks[page_index] if page_index < len(page_chunks) else None
                pages.append(self._parse_page(page, page_index + 1, page_chunk))

        return DocumentParseArtifact(
            document_id=document_id,
            parser_source=self.parser_source,
            schema_version=self.settings.parser_schema_version,
            pages=pages,
        )

    def _extract_page_chunks(self, pdf_document: fitz.Document) -> list[dict[str, Any]]:
        raw_chunks = pymupdf4llm.to_markdown(
            pdf_document,
            page_chunks=True,
            hdr_info=False,
            write_images=False,
            embed_images=False,
            show_progress=False,
        )
        if not isinstance(raw_chunks, list):
            raise ValueError("PyMuPDF4LLM page_chunks output must be a list.")

        chunks: list[dict[str, Any]] = []
        for index, chunk in enumerate(raw_chunks, start=1):
            if not isinstance(chunk, dict):
                raise ValueError(f"PyMuPDF4LLM page chunk #{index} must be an object.")
            chunks.append(chunk)
        return chunks

    def _build_document_with_fitz_fallback(
        self,
        document_id: str,
        pdf_document: fitz.Document,
    ) -> DocumentParseArtifact:
        pages = [
            self._build_page_fallback(
                page=pdf_document.load_page(page_index),
                page_number=page_index + 1,
                page_chunk=None,
            )
            for page_index in range(pdf_document.page_count)
        ]
        return DocumentParseArtifact(
            document_id=document_id,
            parser_source=self.parser_source,
            schema_version=self.settings.parser_schema_version,
            pages=pages,
        )

    def _parse_page(
        self,
        page: fitz.Page,
        page_number: int,
        page_chunk: dict[str, Any] | None,
    ) -> ParsedPage:
        try:
            return self._normalize_page(page, page_number, page_chunk)
        except Exception as exc:
            logger.warning(
                "Page-level parse normalization failed for page %s; using graceful fallback: %s",
                page_number,
                exc,
            )
            return self._build_page_fallback(page=page, page_number=page_number, page_chunk=page_chunk)

    def _normalize_page(
        self,
        page: fitz.Page,
        page_number: int,
        page_chunk: dict[str, Any] | None,
    ) -> ParsedPage:
        width = float(page.rect.width)
        height = float(page.rect.height)

        raw_blocks = self._extract_fitz_text_blocks(page)
        body_font_size = self._detect_body_font_size(raw_blocks)
        table_rects = self._extract_table_rects(page_chunk)
        candidates: list[_BlockCandidate] = []
        consumed_block_indexes: set[int] = set()

        for table_rect in table_rects:
            overlapping_indexes = [
                index
                for index, block in enumerate(raw_blocks)
                if block.block_type == 0
                and self._rect_overlap_ratio(block.rect, table_rect) >= _TABLE_OVERLAP_THRESHOLD
            ]
            if not overlapping_indexes:
                candidates.append(
                    _BlockCandidate(
                        rect=table_rect,
                        text="",
                        block_type=ParseBlockType.TABLE,
                    )
                )
                continue

            consumed_block_indexes.update(overlapping_indexes)
            table_text = "\n".join(
                block.text for index, block in enumerate(raw_blocks) if index in overlapping_indexes and block.text
            ).strip()
            candidates.append(
                _BlockCandidate(
                    rect=table_rect,
                    text=table_text,
                    block_type=ParseBlockType.TABLE,
                )
            )

        for index, block in enumerate(raw_blocks):
            if index in consumed_block_indexes:
                continue

            if block.block_type == 1:
                candidates.append(
                    _BlockCandidate(
                        rect=block.rect,
                        text="",
                        block_type=ParseBlockType.FIGURE,
                    )
                )
                continue

            if not block.text:
                continue

            candidates.append(
                _BlockCandidate(
                    rect=block.rect,
                    text=block.text,
                    block_type=None,
                    max_font_size=block.max_font_size,
                )
            )

        candidates.sort(key=lambda candidate: (round(candidate.rect.y0, 4), round(candidate.rect.x0, 4)))

        blocks: list[ParseBlock] = []
        previous_block_type: ParseBlockType | None = None
        for reading_order, candidate in enumerate(candidates):
            block_type = candidate.block_type or self._infer_text_block_type(
                text=candidate.text,
                previous_block_type=previous_block_type,
                body_font_size=body_font_size,
                max_font_size=candidate.max_font_size,
            )
            cleaned_text = self._clean_block_text(candidate.text, block_type)
            blocks.append(
                ParseBlock(
                    block_id=f"p{page_number}_b{reading_order}",
                    block_type=block_type,
                    text=cleaned_text,
                    bbox=self._normalize_bbox(candidate.rect, width, height),
                    reading_order=reading_order,
                )
            )
            previous_block_type = block_type

        if not blocks:
            return self._build_page_fallback(page=page, page_number=page_number, page_chunk=page_chunk)

        return ParsedPage(
            page_number=self._resolve_page_number(page_number, page_chunk),
            width=width,
            height=height,
            ocr_used=False,
            blocks=blocks,
        )

    def _build_page_fallback(
        self,
        *,
        page: fitz.Page,
        page_number: int,
        page_chunk: dict[str, Any] | None,
    ) -> ParsedPage:
        width = float(page.rect.width)
        height = float(page.rect.height)
        fallback_text = self._extract_fitz_fallback_text(page).strip()

        if fallback_text:
            blocks = [
                ParseBlock(
                    block_id=f"p{page_number}_b0",
                    block_type=ParseBlockType.PARAGRAPH,
                    text=fallback_text,
                    bbox=[0.0, 0.0, 1.0, 1.0],
                    reading_order=0,
                )
            ]
        else:
            blocks = []

        return ParsedPage(
            page_number=self._resolve_page_number(page_number, page_chunk),
            width=width,
            height=height,
            ocr_used=False,
            blocks=blocks,
        )

    def _extract_fitz_text_blocks(self, page: fitz.Page) -> list[_FitzTextBlock]:
        text_dict = page.get_text("dict", sort=True)
        raw_blocks: list[_FitzTextBlock] = []
        for block in text_dict.get("blocks", []):
            if not isinstance(block, dict):
                continue

            block_type = int(block.get("type", 0))
            rect = fitz.Rect(block.get("bbox", page.rect))

            if block_type == 1:
                raw_blocks.append(
                    _FitzTextBlock(
                        rect=rect,
                        text="",
                        block_type=block_type,
                        max_font_size=None,
                    )
                )
                continue

            if block_type != 0:
                continue

            lines = block.get("lines", [])
            text_lines: list[str] = []
            font_sizes: list[float] = []
            for line in lines:
                if not isinstance(line, dict):
                    continue
                spans = line.get("spans", [])
                span_text_parts: list[str] = []
                for span in spans:
                    if not isinstance(span, dict):
                        continue
                    span_text = str(span.get("text", ""))
                    if span_text:
                        span_text_parts.append(span_text)
                    size = span.get("size")
                    if isinstance(size, (int, float)):
                        font_sizes.append(float(size))
                line_text = "".join(span_text_parts).strip()
                if line_text:
                    text_lines.append(line_text)

            text = "\n".join(text_lines).strip()
            raw_blocks.append(
                _FitzTextBlock(
                    rect=rect,
                    text=text,
                    block_type=block_type,
                    max_font_size=max(font_sizes) if font_sizes else None,
                )
            )

        return raw_blocks

    def _extract_table_rects(self, page_chunk: dict[str, Any] | None) -> list[fitz.Rect]:
        if not page_chunk:
            return []

        rects: list[fitz.Rect] = []
        for table in page_chunk.get("tables", []):
            if not isinstance(table, dict):
                continue
            bbox = table.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            rect = fitz.Rect(bbox)
            if rect.width > 0 and rect.height > 0:
                rects.append(rect)
        rects.sort(key=lambda rect: (round(rect.y0, 4), round(rect.x0, 4)))
        return rects

    def _detect_body_font_size(self, blocks: list[_FitzTextBlock]) -> float | None:
        rounded_sizes = [
            round(block.max_font_size, 1)
            for block in blocks
            if block.block_type == 0 and block.max_font_size is not None and block.text
        ]
        if not rounded_sizes:
            return None
        return float(Counter(rounded_sizes).most_common(1)[0][0])

    def _infer_text_block_type(
        self,
        *,
        text: str,
        previous_block_type: ParseBlockType | None,
        body_font_size: float | None,
        max_font_size: float | None,
    ) -> ParseBlockType:
        stripped = text.strip()
        if not stripped:
            return ParseBlockType.OTHER

        if self._looks_like_list(stripped):
            return ParseBlockType.LIST

        if previous_block_type in {ParseBlockType.FIGURE, ParseBlockType.TABLE} and _CAPTION_PATTERN.match(stripped):
            return ParseBlockType.CAPTION

        if self._looks_like_heading(stripped, body_font_size, max_font_size):
            return ParseBlockType.HEADING

        return ParseBlockType.PARAGRAPH

    def _looks_like_list(self, text: str) -> bool:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        if _BULLET_LIST_PATTERN.match(lines[0]) or _ORDERED_LIST_PATTERN.match(lines[0]):
            return True
        return False

    def _looks_like_heading(
        self,
        text: str,
        body_font_size: float | None,
        max_font_size: float | None,
    ) -> bool:
        if _HEADING_MARKER_PATTERN.match(text):
            return True
        if body_font_size is None or max_font_size is None:
            return False
        if max_font_size < body_font_size + _HEADING_SIZE_DELTA:
            return False
        if len(text.splitlines()) > _HEADING_MAX_LINES:
            return False
        if len(text) > _HEADING_MAX_CHARACTERS:
            return False
        return True

    def _clean_block_text(self, text: str, block_type: ParseBlockType) -> str:
        stripped = text.strip()
        if block_type is ParseBlockType.HEADING:
            return _HEADING_MARKER_PATTERN.sub("", stripped).strip()
        if block_type is ParseBlockType.LIST:
            normalized_lines = []
            for line in stripped.splitlines():
                line = _BULLET_LIST_PATTERN.sub("", line)
                line = _ORDERED_LIST_PATTERN.sub("", line)
                if line.strip():
                    normalized_lines.append(line.strip())
            return "\n".join(normalized_lines)
        return stripped

    def _normalize_bbox(self, rect: fitz.Rect, width: float, height: float) -> list[float]:
        x0 = max(0.0, min(rect.x0 / width, 1.0))
        y0 = max(0.0, min(rect.y0 / height, 1.0))
        x1 = max(0.0, min(rect.x1 / width, 1.0))
        y1 = max(0.0, min(rect.y1 / height, 1.0))
        return [
            round(x0, 6),
            round(y0, 6),
            round(max(0.0, x1 - x0), 6),
            round(max(0.0, y1 - y0), 6),
        ]

    def _rect_overlap_ratio(self, a: fitz.Rect, b: fitz.Rect) -> float:
        intersection = a & b
        if intersection.is_empty:
            return 0.0
        denominator = min(abs(a), abs(b))
        if denominator <= 0:
            return 0.0
        return abs(intersection) / denominator

    def _resolve_page_number(self, fallback_page_number: int, page_chunk: dict[str, Any] | None) -> int:
        if not page_chunk:
            return fallback_page_number
        metadata = page_chunk.get("metadata")
        if not isinstance(metadata, dict):
            return fallback_page_number
        page_number = metadata.get("page")
        if isinstance(page_number, int) and page_number >= 1:
            return page_number
        return fallback_page_number

    def _extract_page_text(self, page_chunk: dict[str, Any] | None, page: fitz.Page) -> str:
        if page_chunk:
            text = page_chunk.get("text")
            if isinstance(text, str) and text.strip():
                return text
        return page.get_text("text")

    def _extract_fitz_fallback_text(self, page: fitz.Page) -> str:
        blocks = self._extract_fitz_text_blocks(page)
        text_parts = [block.text.strip() for block in blocks if block.block_type == 0 and block.text.strip()]
        if text_parts:
            return "\n\n".join(text_parts)
        return page.get_text("text")


def get_pymupdf4llm_document_parser(
    settings: AppSettings | None = None,
) -> PyMuPDF4LLMDocumentParser:
    return PyMuPDF4LLMDocumentParser(settings=settings or get_settings())
