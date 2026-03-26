from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.models.document import RenderStatus, StageStatus
from app.services.openai_client import OpenAIResponsesClient
from app.services.storage import StorageService, get_storage_service


class Pass1Analyzer:
    def __init__(
        self,
        storage: StorageService | None = None,
        openai_client: OpenAIResponsesClient | None = None,
        max_workers: int = 3,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.openai_client = openai_client or OpenAIResponsesClient()
        self.max_workers = max(1, max_workers)

    def analyze_page(
        self,
        document_id: str,
        page_number: int,
        optional_extracted_text: str | None = None,
    ) -> dict[str, Any]:
        page_record = self.storage.get_page(document_id, page_number)
        if page_record is None:
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message="Page metadata was not found.",
            )

        try:
            self.storage.update_page_pass1_status(
                document_id,
                page_number,
                StageStatus.PENDING,
                error_message=None,
            )
        except ValueError as exc:
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=self._summarize_error_message("Pass1 setup failed.", exc),
            )

        if page_record.render_status is not RenderStatus.RENDERED:
            error_message = (
                f"Page render_status must be 'rendered', got '{page_record.render_status.value}'."
            )
            self.storage.update_page_pass1_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
            )

        image_path = self.storage.resolve_relative_path(page_record.image_path)
        if not image_path.exists():
            error_message = f"Rendered page image is missing: {page_record.image_path}"
            self.storage.update_page_pass1_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
            )

        try:
            envelope = self.openai_client.run_pass1(
                page_image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                optional_extracted_text=optional_extracted_text,
            )
            saved_path = self.storage.save_pass1_result(document_id, page_number, envelope)
            self.storage.update_page_pass1_status(
                document_id,
                page_number,
                StageStatus.COMPLETED,
                error_message=None,
            )
        except Exception as exc:
            error_message = self._summarize_error_message("Pass1 failed.", exc)
            self.storage.update_page_pass1_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
            )

        candidate_anchor_count = len(envelope["result"]["candidate_anchors"])
        qa_warnings: list[str] = []
        if candidate_anchor_count < 8:
            qa_warnings.append(
                f"candidate_anchors count is {candidate_anchor_count}; recommended QA target is 8~15.",
            )

        return {
            "document_id": document_id,
            "page_number": page_number,
            "pass1_status": StageStatus.COMPLETED.value,
            "saved_path": saved_path,
            "candidate_anchor_count": candidate_anchor_count,
            "qa_warnings": qa_warnings,
            "error_message": None,
        }

    def analyze_document(
        self,
        document_id: str,
        page_numbers: list[int] | None = None,
    ) -> dict[str, Any]:
        page_records = self.storage.get_pages(document_id)
        if not page_records:
            raise ValueError(f"No page metadata found for document_id={document_id}.")

        rendered_page_numbers = {
            page.page_number for page in page_records if page.render_status is RenderStatus.RENDERED
        }

        if page_numbers is None:
            selected_page_numbers = sorted(rendered_page_numbers)
        else:
            selected_page_numbers = sorted(set(page_numbers))

        if not selected_page_numbers:
            raise ValueError(f"No rendered pages are available for document_id={document_id}.")

        worker_count = min(self.max_workers, len(selected_page_numbers))
        if worker_count <= 1:
            page_results = [
                self.analyze_page(document_id=document_id, page_number=page_number)
                for page_number in selected_page_numbers
            ]
        else:
            page_results: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(
                        self.analyze_page,
                        document_id=document_id,
                        page_number=page_number,
                    ): page_number
                    for page_number in selected_page_numbers
                }
                for future in as_completed(future_map):
                    page_number = future_map[future]
                    try:
                        page_results.append(future.result())
                    except Exception as exc:
                        error_message = self._summarize_error_message("Pass1 failed.", exc)
                        try:
                            self.storage.update_page_pass1_status(
                                document_id,
                                page_number,
                                StageStatus.FAILED,
                                error_message=error_message,
                            )
                        except Exception:
                            pass
                        page_results.append(
                            self._failed_page_result(
                                document_id=document_id,
                                page_number=page_number,
                                error_message=error_message,
                            )
                        )

        page_results.sort(key=lambda page_result: int(page_result["page_number"]))

        return {
            "document_id": document_id,
            "requested_pages": selected_page_numbers,
            "completed_pages": [
                page_result["page_number"]
                for page_result in page_results
                if page_result["pass1_status"] == StageStatus.COMPLETED.value
            ],
            "failed_pages": [
                {
                    "page_number": page_result["page_number"],
                    "error_message": page_result["error_message"],
                }
                for page_result in page_results
                if page_result["pass1_status"] == StageStatus.FAILED.value
            ],
            "saved_paths": [
                page_result["saved_path"]
                for page_result in page_results
                if page_result["saved_path"] is not None
            ],
            "qa_warnings": [
                {
                    "page_number": page_result["page_number"],
                    "warnings": page_result["qa_warnings"],
                }
                for page_result in page_results
                if page_result["qa_warnings"]
            ],
        }

    def _failed_page_result(
        self,
        *,
        document_id: str,
        page_number: int,
        error_message: str,
    ) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "page_number": page_number,
            "pass1_status": StageStatus.FAILED.value,
            "saved_path": None,
            "candidate_anchor_count": None,
            "qa_warnings": [],
            "error_message": error_message,
        }

    def _summarize_error_message(self, prefix: str, detail: object | None) -> str:
        normalized_prefix = " ".join(str(prefix).split())
        normalized_detail = " ".join(str(detail or "").split())
        if not normalized_detail:
            return normalized_prefix
        if normalized_detail.lower().startswith("traceback"):
            return normalized_prefix
        max_length = 220
        detail_budget = max_length - len(normalized_prefix) - 1
        if detail_budget <= 0:
            return normalized_prefix
        if len(normalized_detail) > detail_budget:
            normalized_detail = normalized_detail[: max(detail_budget - 3, 0)].rstrip()
            if normalized_detail:
                normalized_detail += "..."
        return f"{normalized_prefix} {normalized_detail}".strip()
