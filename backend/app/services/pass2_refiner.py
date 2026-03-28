from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.models.document import RenderStatus, StageStatus
from app.services.openai_client import OpenAIClientError, OpenAIResponsesClient
from app.services.storage import StorageService, get_storage_service
from app.utils.validation import validate_payload


class Pass2Refiner:
    def __init__(
        self,
        storage: StorageService | None = None,
        openai_client: OpenAIResponsesClient | None = None,
        max_workers: int = 2,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.openai_client = openai_client or OpenAIResponsesClient()
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

        candidate_map = self._build_candidate_map(pass1_artifact["result"]["candidate_anchors"])
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

        valid_pass1_page_numbers = self._get_valid_pass1_page_numbers(document_id)

        try:
            envelope = self._run_pass2_with_timeout_retry(
                image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                pass1_result=pass1_artifact["result"],
                document_summary_result=document_summary["result"],
            )
            normalized_envelope, initial_warnings, needs_diversity_retry = self._normalize_envelope(
                document_id=document_id,
                page_number=page_number,
                envelope=envelope,
                candidate_map=candidate_map,
                valid_pass1_page_numbers=valid_pass1_page_numbers,
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
            return self.openai_client.run_pass2(
                page_image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                pass1_result=pass1_result,
                document_summary=document_summary_result,
                extra_guidance=extra_guidance,
            )
        except OpenAIClientError as exc:
            if "timed out" not in str(exc).lower():
                raise
            retry_guidance = self._build_timeout_retry_guidance(extra_guidance)
            return self.openai_client.run_pass2(
                page_image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                pass1_result=pass1_result,
                document_summary=document_summary_result,
                extra_guidance=retry_guidance,
            )

    def _build_candidate_map(self, candidate_anchors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            str(candidate["anchor_id"]): dict(candidate)
            for candidate in candidate_anchors
        }

    def _get_valid_pass1_page_numbers(self, document_id: str) -> set[int]:
        valid_page_numbers: set[int] = set()
        for page in self.storage.get_pages(document_id):
            try:
                artifact = self.storage.load_pass1_result(document_id, page.page_number)
            except ValueError:
                continue
            if artifact is not None:
                valid_page_numbers.add(page.page_number)
        return valid_page_numbers

    def _normalize_envelope(
        self,
        *,
        document_id: str,
        page_number: int,
        envelope: dict[str, Any],
        candidate_map: dict[str, dict[str, Any]],
        valid_pass1_page_numbers: set[int],
        document_summary_result: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str], bool]:
        if not isinstance(envelope, dict):
            raise ValueError("Pass2 envelope must be a JSON object.")
        if not isinstance(envelope.get("meta"), dict):
            raise ValueError("Pass2 envelope must include a meta object.")
        if not isinstance(envelope.get("result"), dict):
            raise ValueError("Pass2 envelope must include a result object.")

        validated_result = validate_payload(
            "pass2",
            {
                **dict(envelope["result"]),
                "document_id": document_id,
                "page_number": page_number,
            },
        )

        normalized_final_anchors = [
            self._normalize_final_anchor(
                anchor=anchor,
                candidate_map=candidate_map,
                current_page_number=page_number,
                valid_pass1_page_numbers=valid_pass1_page_numbers,
                document_summary_result=document_summary_result,
            )
            for anchor in validated_result["final_anchors"]
        ]

        normalized_result = {
            **validated_result,
            "document_id": document_id,
            "page_number": page_number,
            "final_anchors": normalized_final_anchors,
        }
        normalized_result = validate_payload("pass2", normalized_result)

        final_types = {anchor["anchor_type"] for anchor in normalized_result["final_anchors"]}
        candidate_types = {candidate["anchor_type"] for candidate in candidate_map.values()}
        needs_diversity_retry = len(final_types) == 1 and len(candidate_types) > 1

        return {
            "meta": {
                "schema_version": str(envelope["meta"]["schema_version"]),
                "prompt_version": str(envelope["meta"]["prompt_version"]),
                "model_name": str(envelope["meta"]["model_name"]),
                "generated_at": str(envelope["meta"]["generated_at"]),
            },
            "result": normalized_result,
        }, [], needs_diversity_retry

    def _normalize_final_anchor(
        self,
        *,
        anchor: dict[str, Any],
        candidate_map: dict[str, dict[str, Any]],
        current_page_number: int,
        valid_pass1_page_numbers: set[int],
        document_summary_result: dict[str, Any],
    ) -> dict[str, Any]:
        anchor_id = str(anchor["anchor_id"])
        if anchor_id not in candidate_map:
            raise ValueError(f"final_anchors contains anchor_id not found in pass1 candidates: {anchor_id}")

        candidate = candidate_map[anchor_id]
        return {
            **anchor,
            "anchor_id": candidate["anchor_id"],
            "anchor_type": candidate["anchor_type"],
            "bbox": candidate["bbox"],
            "related_pages": self._normalize_related_pages(
                related_pages=anchor["related_pages"],
                current_page_number=current_page_number,
                valid_pass1_page_numbers=valid_pass1_page_numbers,
                document_summary_result=document_summary_result,
            ),
        }

    def _normalize_related_pages(
        self,
        *,
        related_pages: list[int],
        current_page_number: int,
        valid_pass1_page_numbers: set[int],
        document_summary_result: dict[str, Any],
    ) -> list[int]:
        normalized_pages = sorted(set(int(page) for page in related_pages))
        if any(page == current_page_number for page in normalized_pages):
            raise ValueError("related_pages must not include the current page.")

        invalid_pages = [page for page in normalized_pages if page not in valid_pass1_page_numbers]
        if invalid_pages:
            raise ValueError(
                "related_pages contains pages without valid pass1 artifacts: "
                + ", ".join(map(str, invalid_pages))
            )

        return sorted(
            normalized_pages,
            key=lambda page: (
                -self._related_page_priority_score(
                    current_page_number=current_page_number,
                    related_page=page,
                    document_summary_result=document_summary_result,
                ),
                abs(page - current_page_number),
                page,
            ),
        )

    def _related_page_priority_score(
        self,
        *,
        current_page_number: int,
        related_page: int,
        document_summary_result: dict[str, Any],
    ) -> int:
        score = 0

        for section in document_summary_result.get("sections", []):
            pages = section.get("pages", [])
            if current_page_number in pages and related_page in pages:
                score += 3

        for link in document_summary_result.get("prerequisite_links", []):
            from_page = int(link["from_page"])
            to_page = int(link["to_page"])
            if from_page == current_page_number and to_page == related_page:
                score += 4
            elif to_page == current_page_number and from_page == related_page:
                score += 4

        difficult_pages = {int(page) for page in document_summary_result.get("difficult_pages", [])}
        if related_page in difficult_pages:
            score += 1

        return score

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
