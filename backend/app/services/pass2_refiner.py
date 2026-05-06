from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.models.document import RenderStatus, StageStatus
from app.services.analysis_client import AnalysisClient, AnalysisClientError
from app.services.llm_provider import get_analysis_client
from app.services.pass2_artifact_builder import Pass2ArtifactBuilder
from app.services.storage import StorageService, get_storage_service


class Pass2Refiner:
    def __init__(
        self,
        storage: StorageService | None = None,
        analysis_client: AnalysisClient | None = None,
        artifact_builder: Pass2ArtifactBuilder | None = None,
        max_workers: int = 2,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.analysis_client = analysis_client or get_analysis_client(storage=self.storage)
        self.artifact_builder = artifact_builder or Pass2ArtifactBuilder(storage=self.storage)
        self.max_workers = max(1, max_workers)

    def refine_page(self, document_id: str, page_number: int) -> dict[str, Any]:
        page_record = self.storage.get_page(document_id, page_number)
        if page_record is None:
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message="Page metadata was not found.",
            )

        qa_warnings: list[str] = []

        try:
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.PENDING,
                error_message=None,
            )
        except ValueError as exc:
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=self._summarize_error_message("Pass2 setup failed.", exc),
                qa_warnings=qa_warnings,
            )

        if page_record.render_status is not RenderStatus.RENDERED:
            error_message = (
                f"Page render_status must be 'rendered', got '{page_record.render_status.value}'."
            )
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                qa_warnings=qa_warnings,
            )

        image_path = self.storage.resolve_relative_path(page_record.image_path)
        if not image_path.exists():
            error_message = f"Rendered page image is missing: {page_record.image_path}"
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                qa_warnings=qa_warnings,
            )

        if page_record.pass1_status is not StageStatus.COMPLETED:
            error_message = "Pass1 must be completed before pass2 can run."
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                qa_warnings=qa_warnings,
            )

        try:
            pass1_artifact = self.storage.load_pass1_result(document_id, page_number)
        except ValueError as exc:
            error_message = self._summarize_error_message("Pass2 failed.", exc)
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                qa_warnings=qa_warnings,
            )

        if pass1_artifact is None:
            error_message = "Pass1 artifact was not found for the requested page."
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                qa_warnings=qa_warnings,
            )

        try:
            document_summary = self.storage.load_document_summary(document_id)
        except ValueError as exc:
            error_message = self._summarize_error_message("Pass2 failed.", exc)
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                qa_warnings=qa_warnings,
            )

        if document_summary is None:
            error_message = "Document summary artifact was not found for the requested document."
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                qa_warnings=qa_warnings,
            )

        candidate_map = self.artifact_builder.build_candidate_map(
            pass1_artifact["result"]["candidate_anchors"]
        )
        candidate_types = {candidate["anchor_type"] for candidate in candidate_map.values()}
        if len(candidate_map) < 3:
            error_message = (
                "Pass1 candidate anchor pool has fewer than 3 candidates, so pass2 cannot select 3~5 final anchors."
            )
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                qa_warnings=[
                    "Pass1 candidate pool is insufficient for pass2 selection.",
                ],
            )

        try:
            envelope = self._run_pass2_with_timeout_retry(
                image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                pass1_result=pass1_artifact["result"],
                document_summary_result=document_summary["result"],
            )
            (
                normalized_envelope,
                initial_warnings,
                needs_diversity_retry,
            ) = self.artifact_builder.normalize_llm_envelope(
                document_id=document_id,
                page_number=page_number,
                envelope=envelope,
                pass1_result=pass1_artifact["result"],
                document_summary_result=document_summary["result"],
            )
            qa_warnings.extend(initial_warnings)

            if needs_diversity_retry:
                qa_warnings.append(
                    "Final anchors remained monotype even though the pass1 candidate pool included multiple anchor types.",
                )

            if len(candidate_types) == 1:
                qa_warnings.append(
                    "Pass1 candidate pool was monotype, so anchor type diversity was not possible on this page.",
                )

            saved_path = self.storage.save_pass2_result(document_id, page_number, normalized_envelope)
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.COMPLETED,
                error_message=None,
            )
        except Exception as exc:
            error_message = self._summarize_error_message("Pass2 failed.", exc)
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                qa_warnings=qa_warnings,
            )

        final_anchors = normalized_envelope["result"]["final_anchors"]
        anchor_types = sorted({anchor["anchor_type"] for anchor in final_anchors})

        return {
            "document_id": document_id,
            "page_number": page_number,
            "pass2_status": StageStatus.COMPLETED.value,
            "saved_path": saved_path,
            "final_anchor_count": len(final_anchors),
            "anchor_types": anchor_types,
            "qa_warnings": qa_warnings,
            "error_message": None,
        }

    def refine_document(
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
                self.refine_page(document_id=document_id, page_number=page_number)
                for page_number in selected_page_numbers
            ]
        else:
            page_results: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(
                        self.refine_page,
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
                        error_message = self._summarize_error_message("Pass2 failed.", exc)
                        try:
                            self.storage.update_page_pass2_status(
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
                                qa_warnings=[],
                            )
                        )

        page_results.sort(key=lambda page_result: int(page_result["page_number"]))

        return {
            "document_id": document_id,
            "requested_pages": selected_page_numbers,
            "completed_pages": [
                page_result["page_number"]
                for page_result in page_results
                if page_result["pass2_status"] == StageStatus.COMPLETED.value
            ],
            "failed_pages": [
                {
                    "page_number": page_result["page_number"],
                    "error_message": page_result["error_message"],
                }
                for page_result in page_results
                if page_result["pass2_status"] == StageStatus.FAILED.value
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

    def _run_pass2_with_timeout_retry(
        self,
        *,
        image_path: str | Any,
        document_id: str,
        page_number: int,
        pass1_result: dict[str, Any],
        document_summary_result: dict[str, Any],
        extra_guidance: str | None = None,
    ) -> dict[str, Any]:
        try:
            return self.analysis_client.run_pass2(
                page_image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                pass1_result=pass1_result,
                document_summary=document_summary_result,
                extra_guidance=extra_guidance,
            )
        except AnalysisClientError as exc:
            if "timed out" not in str(exc).lower():
                raise
            retry_guidance = self._build_timeout_retry_guidance(extra_guidance)
            return self.analysis_client.run_pass2(
                page_image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                pass1_result=pass1_result,
                document_summary=document_summary_result,
                extra_guidance=retry_guidance,
            )

    def _build_timeout_retry_guidance(self, existing_guidance: str | None = None) -> str:
        timeout_guidance = (
            "Retry pass2 with a faster, more concise output. Prefer 3 final anchors if that is enough. "
            "Keep short_explanation to 1 sentence, keep long_explanation to 2 concise sentences, "
            "keep prerequisite very short, keep page_risk_note to 1 sentence, "
            "and use at most 1 related_page if the connection value is strong. Do not create new anchors."
        )
        if existing_guidance:
            return f"{existing_guidance}\n\n{timeout_guidance}"
        return timeout_guidance

    def _failed_page_result(
        self,
        *,
        document_id: str,
        page_number: int,
        error_message: str,
        qa_warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "page_number": page_number,
            "pass2_status": StageStatus.FAILED.value,
            "saved_path": None,
            "final_anchor_count": None,
            "anchor_types": [],
            "qa_warnings": qa_warnings,
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
