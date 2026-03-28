from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Protocol

import fitz

from app.core.config import AppSettings, get_settings
from app.models.parser import (
    DocumentParseArtifact,
    ParseBlock,
    ParseBlockType,
    ParsedPage,
)


StubTextMode = Literal["empty", "page_text"]
logger = logging.getLogger(__name__)


class DocumentParser(Protocol):
    def parse_document(
        self,
        document_id: str,
        pdf_path: str | Path,
    ) -> DocumentParseArtifact: ...


class StubDocumentParser:
    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        parser_source: str = "stub",
        text_mode: StubTextMode = "empty",
    ) -> None:
        self.settings = settings or get_settings()
        self.parser_source = parser_source
        self.text_mode = text_mode

    def parse_document(
        self,
        document_id: str,
        pdf_path: str | Path,
    ) -> DocumentParseArtifact:
        resolved_pdf_path = Path(pdf_path)
        pages: list[ParsedPage] = []

        with fitz.open(resolved_pdf_path) as pdf_document:
            for page_index in range(pdf_document.page_count):
                page = pdf_document.load_page(page_index)
                page_number = page_index + 1
                pages.append(
                    ParsedPage(
                        page_number=page_number,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        ocr_used=False,
                        blocks=self._build_blocks(page_number, page),
                    )
                )

        return DocumentParseArtifact(
            document_id=document_id,
            parser_source=self.parser_source,
            schema_version=self.settings.parser_schema_version,
            pages=pages,
        )

    def _build_blocks(self, page_number: int, page: fitz.Page) -> list[ParseBlock]:
        if self.text_mode != "page_text":
            return []

        page_text = page.get_text("text").strip()
        if not page_text:
            return []

        return [
            ParseBlock(
                block_id=f"p{page_number}_b0",
                block_type=ParseBlockType.PARAGRAPH,
                text=page_text,
                bbox=[0.0, 0.0, 1.0, 1.0],
                reading_order=0,
            )
        ]


def get_default_document_parser(settings: AppSettings | None = None) -> DocumentParser:
    resolved_settings = settings or get_settings()
    if resolved_settings.document_parser_backend == "stub":
        return StubDocumentParser(settings=resolved_settings, text_mode="empty")

    try:
        from app.services.pymupdf4llm_adapter import PyMuPDF4LLMDocumentParser

        return PyMuPDF4LLMDocumentParser(settings=resolved_settings)
    except Exception as exc:
        logger.warning(
            "Falling back to stub document parser because the PyMuPDF4LLM backend is unavailable: %s",
            exc,
        )
        return StubDocumentParser(settings=resolved_settings, text_mode="page_text")
