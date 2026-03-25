from __future__ import annotations

import argparse

from app.models.document import (
    DocumentRenderResult,
    DocumentStatus,
    PageRecord,
    PageRenderFailure,
    RenderStatus,
)
from app.services.pdf_render import PDFRenderService
from app.services.storage import StorageService, get_storage_service


class RenderWorker:
    def __init__(
        self,
        storage: StorageService | None = None,
        renderer: PDFRenderService | None = None,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.renderer = renderer or PDFRenderService()

    def render_document(self, document_id: str) -> DocumentRenderResult:
        document = self.storage.get_document(document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")

        source_pdf_path = self.storage.resolve_relative_path(document.original_path)
        if not source_pdf_path.exists():
            error_message = f"Source PDF not found: {document.original_path}"
            self.storage.update_document(
                document_id,
                status=DocumentStatus.FAILED,
                total_pages=None,
                error_message=error_message,
            )
            return DocumentRenderResult(
                document_id=document_id,
                status=DocumentStatus.FAILED,
                total_pages=0,
                rendered_pages=[],
                failed_pages=[],
                error_message=error_message,
            )

        self.storage.update_document(
            document_id,
            status=DocumentStatus.RENDERING,
            total_pages=None,
            error_message=None,
        )

        try:
            with self.renderer.open_pdf_document(source_pdf_path) as pdf_document:
                total_pages = pdf_document.page_count
                if total_pages <= 0:
                    raise ValueError("PDF has no pages to render.")

                self.renderer.reset_output_dir(document_id)
                pending_pages = self._build_pending_pages(document_id, total_pages)
                self.storage.replace_pages(document_id, pending_pages)
                self.storage.update_document(
                    document_id,
                    status=DocumentStatus.RENDERING,
                    total_pages=total_pages,
                    error_message=None,
                )

                rendered_pages = []
                failed_pages = []

                for page_number in range(1, total_pages + 1):
                    try:
                        rendered_page = self.renderer.render_page(pdf_document, document_id, page_number)
                    except Exception as exc:
                        failed_pages.append(
                            PageRenderFailure(
                                page_number=page_number,
                                error_message=str(exc),
                            )
                        )
                        self.storage.update_page_render(
                            document_id,
                            page_number,
                            render_status=RenderStatus.FAILED,
                            width=None,
                            height=None,
                        )
                        continue

                    rendered_pages.append(rendered_page)
                    self.storage.update_page_render(
                        document_id,
                        page_number,
                        render_status=RenderStatus.RENDERED,
                        width=rendered_page.width,
                        height=rendered_page.height,
                    )
        except Exception as exc:
            error_message = f"Render failed: {exc}"
            self.storage.update_document(
                document_id,
                status=DocumentStatus.FAILED,
                total_pages=None,
                error_message=error_message,
            )
            return DocumentRenderResult(
                document_id=document_id,
                status=DocumentStatus.FAILED,
                total_pages=0,
                rendered_pages=[],
                failed_pages=[],
                error_message=error_message,
            )

        final_status = DocumentStatus.ANALYZING if rendered_pages else DocumentStatus.FAILED
        error_message = self._build_error_message(total_pages, failed_pages)
        self.storage.update_document(
            document_id,
            status=final_status,
            total_pages=total_pages,
            error_message=error_message,
        )

        return DocumentRenderResult(
            document_id=document_id,
            status=final_status,
            total_pages=total_pages,
            rendered_pages=rendered_pages,
            failed_pages=failed_pages,
            error_message=error_message,
        )

    def _build_pending_pages(self, document_id: str, total_pages: int) -> list[PageRecord]:
        pending_pages: list[PageRecord] = []
        for page_number in range(1, total_pages + 1):
            _, relative_image_path = self.renderer.page_output_path(document_id, page_number)
            pending_pages.append(
                PageRecord(
                    document_id=document_id,
                    page_number=page_number,
                    image_path=relative_image_path,
                    render_status=RenderStatus.PENDING,
                    width=None,
                    height=None,
                    pass1_status=None,
                    pass2_status=None,
                )
            )
        return pending_pages

    def _build_error_message(
        self,
        total_pages: int,
        failed_pages: list[PageRenderFailure],
    ) -> str | None:
        if not failed_pages:
            return None

        failed_page_numbers = ", ".join(str(page.page_number) for page in failed_pages)
        if len(failed_pages) == total_pages:
            return f"Render failed for all pages: {failed_page_numbers}"
        return f"Render failed for pages: {failed_page_numbers}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a stored PDF into page PNG images.")
    parser.add_argument("document_id", help="Document identifier returned by POST /api/documents")
    args = parser.parse_args()

    result = RenderWorker().render_document(args.document_id)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
