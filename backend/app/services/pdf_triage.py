from __future__ import annotations

import logging
from pathlib import Path

import fitz

from app.models.parser import (
    DocumentPageManifest,
    DocumentParseArtifact,
    PageManifestEntry,
    PageRouteLabel,
    ParseBlockType,
)


logger = logging.getLogger(__name__)


class PdfTriageService:
    def build_page_manifest(
        self,
        document_id: str,
        parse_artifact: DocumentParseArtifact,
        pdf_path: str | Path | None = None,
    ) -> DocumentPageManifest:
        image_counts = self._extract_image_counts(pdf_path)
        manifest_pages: list[PageManifestEntry] = []

        for page in parse_artifact.pages:
            text_length = sum(len(block.text.strip()) for block in page.blocks)
            non_empty_text_block_count = sum(1 for block in page.blocks if block.text.strip())
            block_count = len(page.blocks)
            has_table = any(block.block_type is ParseBlockType.TABLE for block in page.blocks)
            has_figure = any(block.block_type is ParseBlockType.FIGURE for block in page.blocks)
            image_count = image_counts.get(page.page_number, 0)

            route_label, route_reason = self._classify_page(
                text_length=text_length,
                block_count=block_count,
                non_empty_text_block_count=non_empty_text_block_count,
                image_count=image_count,
                has_table=has_table,
                has_figure=has_figure,
                ocr_used=page.ocr_used,
            )

            manifest_pages.append(
                PageManifestEntry(
                    page_number=page.page_number,
                    route_label=route_label,
                    route_reason=route_reason,
                    text_length=text_length,
                    block_count=block_count,
                    non_empty_text_block_count=non_empty_text_block_count,
                    image_count=image_count,
                    has_table=has_table,
                    has_figure=has_figure,
                    ocr_used=page.ocr_used,
                )
            )

        return DocumentPageManifest(
            document_id=document_id,
            parser_source=parse_artifact.parser_source,
            schema_version=parse_artifact.schema_version,
            pages=manifest_pages,
        )

    def _classify_page(
        self,
        *,
        text_length: int,
        block_count: int,
        non_empty_text_block_count: int,
        image_count: int,
        has_table: bool,
        has_figure: bool,
        ocr_used: bool,
    ) -> tuple[PageRouteLabel, str]:
        if (
            text_length < 80
            and block_count <= 1
            and not has_table
            and not has_figure
            and (ocr_used or non_empty_text_block_count == 0 or image_count > 0)
        ):
            return (
                PageRouteLabel.SCAN_LIKE,
                "low_text_length="
                f"{text_length}; block_count={block_count}; non_empty_text_block_count="
                f"{non_empty_text_block_count}; image_count={image_count}; no_table_or_figure",
            )

        if has_table:
            reasons: list[str] = []
            reasons.append("has_table")
            reasons.append(f"text_length={text_length}")
            return (
                PageRouteLabel.VISUAL_RICH,
                "; ".join(reasons),
            )

        if has_figure and text_length < 250 and non_empty_text_block_count < 4:
            return (
                PageRouteLabel.VISUAL_RICH,
                "has_figure; "
                f"text_length={text_length}; non_empty_text_block_count={non_empty_text_block_count}",
            )

        if image_count >= 2 and text_length < 250:
            return (
                PageRouteLabel.VISUAL_RICH,
                f"image_count={image_count}; text_length={text_length}",
            )

        return (
            PageRouteLabel.TEXT_RICH,
            "text_length="
            f"{text_length}; block_count={block_count}; non_empty_text_block_count="
            f"{non_empty_text_block_count}; no_strong_visual_signal",
        )

    def _extract_image_counts(self, pdf_path: str | Path | None) -> dict[int, int]:
        if pdf_path is None:
            return {}

        resolved_pdf_path = Path(pdf_path)
        try:
            with fitz.open(resolved_pdf_path) as pdf_document:
                return {
                    page_index + 1: len({int(image[0]) for image in page.get_images(full=True)})
                    for page_index, page in enumerate(pdf_document)
                }
        except Exception as exc:
            logger.warning("Image signal extraction failed for %s: %s", resolved_pdf_path, exc)
            return {}


_pdf_triage_service: PdfTriageService | None = None


def get_pdf_triage_service() -> PdfTriageService:
    global _pdf_triage_service
    if _pdf_triage_service is None:
        _pdf_triage_service = PdfTriageService()
    return _pdf_triage_service
