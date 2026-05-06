from __future__ import annotations

from typing import Any

from app.models.document import StageStatus
from app.services.pass2_artifact_builder import Pass2ArtifactBuilder
from app.services.storage import StorageService, get_storage_service


class Pass2CompatBuilder:
    def __init__(
        self,
        storage: StorageService | None = None,
        artifact_builder: Pass2ArtifactBuilder | None = None,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.artifact_builder = artifact_builder or Pass2ArtifactBuilder(storage=self.storage)

    def build_page(
        self,
        document_id: str,
        page_number: int,
        *,
        planner_reason: str | None = None,
        page_routing_entry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        page_record = self.storage.get_page(document_id, page_number)
        if page_record is None:
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message="Page metadata was not found.",
            )

        if page_record.pass1_status is not StageStatus.COMPLETED:
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message="Pass1 must be completed before compat pass2 can run.",
            )

        try:
            pass1_artifact = self.storage.load_pass1_result(document_id, page_number)
            if pass1_artifact is None:
                raise ValueError("Pass1 artifact was not found for the requested page.")
            document_summary = self.storage.load_document_summary(document_id)
            if document_summary is None:
                raise ValueError("Document summary artifact was not found for the requested document.")

            envelope = self.artifact_builder.build_compat_envelope(
                document_id=document_id,
                page_number=page_number,
                pass1_result=pass1_artifact["result"],
                pass1_meta=pass1_artifact.get("meta"),
                document_summary_result=document_summary["result"],
                planner_reason=planner_reason,
                page_routing_entry=page_routing_entry,
            )
            saved_path = self.storage.save_pass2_result(document_id, page_number, envelope)
            self.storage.update_page_pass2_status(
                document_id,
                page_number,
                StageStatus.COMPLETED,
                error_message=None,
            )
            final_anchors = envelope["result"]["final_anchors"]
            return {
                "document_id": document_id,
                "page_number": page_number,
                "pass2_status": StageStatus.COMPLETED.value,
                "saved_path": saved_path,
                "final_anchor_count": len(final_anchors),
                "anchor_types": sorted({anchor["anchor_type"] for anchor in final_anchors}),
                "qa_warnings": [],
                "error_message": None,
            }
        except Exception as exc:
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=self._summarize_error_message("Compat pass2 failed.", exc),
            )

    def build_document(
        self,
        document_id: str,
        page_numbers: list[int],
        *,
        planner_reason_by_page: dict[int, str] | None = None,
        page_routing_by_page: dict[int, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        selected_page_numbers = sorted(set(page_numbers))
        page_results = [
            self.build_page(
                document_id=document_id,
                page_number=page_number,
                planner_reason=(planner_reason_by_page or {}).get(page_number),
                page_routing_entry=(page_routing_by_page or {}).get(page_number),
            )
            for page_number in selected_page_numbers
        ]
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
            "pass2_status": StageStatus.FAILED.value,
            "saved_path": None,
            "final_anchor_count": None,
            "anchor_types": [],
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
