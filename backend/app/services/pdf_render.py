from __future__ import annotations

import shutil
from pathlib import Path

import fitz

from app.core.config import PROJECT_ROOT, AppSettings, get_settings
from app.models.document import RenderedPageArtifact


RENDER_OUTPUT_FORMAT = "png"
RENDER_COLORSPACE_NAME = "RGB"
RENDER_COLORSPACE = fitz.csRGB
RENDER_LONG_EDGE_PIXELS = 1600


class PDFRenderService:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.rendered_pages_dir = self._resolve_project_path(self.settings.rendered_pages_dir)

    def open_pdf_document(self, pdf_path: Path) -> fitz.Document:
        return fitz.open(pdf_path)

    def reset_output_dir(self, document_id: str) -> Path:
        output_dir = self.rendered_pages_dir / document_id
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def page_output_path(self, document_id: str, page_number: int) -> tuple[Path, str]:
        absolute_path = self.rendered_pages_dir / document_id / f"{page_number}.{RENDER_OUTPUT_FORMAT}"
        relative_path = absolute_path.relative_to(PROJECT_ROOT).as_posix()
        return absolute_path, relative_path

    def render_page(
        self,
        pdf_document: fitz.Document,
        document_id: str,
        page_number: int,
    ) -> RenderedPageArtifact:
        if page_number < 1 or page_number > pdf_document.page_count:
            raise ValueError(f"Page number out of range: {page_number}")

        absolute_path, relative_path = self.page_output_path(document_id, page_number)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)

        page = pdf_document.load_page(page_number - 1)
        pixmap = self._render_page_pixmap(page)
        pixmap.save(absolute_path)

        return RenderedPageArtifact(
            page_number=page_number,
            image_path=relative_path,
            width=pixmap.width,
            height=pixmap.height,
        )

    def _render_page_pixmap(self, page: fitz.Page) -> fitz.Pixmap:
        source_rect = self._preferred_source_rect(page)
        long_edge_points = max(source_rect.width, source_rect.height, 1.0)
        scale = RENDER_LONG_EDGE_PIXELS / long_edge_points
        matrix = fitz.Matrix(scale, scale)

        return page.get_pixmap(
            matrix=matrix,
            clip=source_rect,
            colorspace=RENDER_COLORSPACE,
            alpha=False,
        )

    def _preferred_source_rect(self, page: fitz.Page) -> fitz.Rect:
        crop_box = page.cropbox
        if crop_box.width > 0 and crop_box.height > 0:
            return fitz.Rect(crop_box)
        return fitz.Rect(page.mediabox)

    def _resolve_project_path(self, configured_path: str) -> Path:
        return (PROJECT_ROOT / configured_path).resolve()
