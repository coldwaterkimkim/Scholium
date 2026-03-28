from __future__ import annotations

import math
from typing import Any

from app.models.document import RenderStatus, StageStatus
from app.services.openai_client import OpenAIResponsesClient
from app.services.storage import StorageService, get_storage_service
from app.utils.validation import validate_payload


class DocumentSynthesizer:
    def __init__(
        self,
        storage: StorageService | None = None,
        openai_client: OpenAIResponsesClient | None = None,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.openai_client = openai_client or OpenAIResponsesClient(storage=self.storage)

    def synthesize_document(self, document_id: str) -> dict[str, Any]:
        page_records = self.storage.get_pages(document_id)
        if not page_records:
            return self._failed_result(
                document_id=document_id,
                error_message=f"No page metadata found for document_id={document_id}.",
            )

        rendered_pages = sorted(
            (page for page in page_records if page.render_status is RenderStatus.RENDERED),
            key=lambda page: page.page_number,
        )
        total_rendered_pages = len(rendered_pages)
        if total_rendered_pages == 0:
            return self._failed_result(
                document_id=document_id,
                error_message=f"No rendered pages are available for document_id={document_id}.",
            )

        usable_summaries: list[dict[str, Any]] = []
        usable_page_numbers: list[int] = []
        missing_pages: list[int] = []

        for page in rendered_pages:
            if page.pass1_status is not StageStatus.COMPLETED:
                missing_pages.append(page.page_number)
                continue

            try:
                artifact = self.storage.load_pass1_result(document_id, page.page_number)
            except ValueError:
                missing_pages.append(page.page_number)
                continue

            if artifact is None:
                missing_pages.append(page.page_number)
                continue

            result = artifact["result"]
            candidate_anchors = result.get("candidate_anchors", [])
            usable_summaries.append(
                {
                    "page_number": result["page_number"],
                    "page_role": result["page_role"],
                    "page_summary": result["page_summary"],
                    "candidate_anchor_summaries": self._build_candidate_anchor_summaries(candidate_anchors),
                }
            )
            usable_page_numbers.append(page.page_number)

        pass1_completed_pages = len(usable_summaries)
        coverage_threshold = max(3, math.ceil(total_rendered_pages * 0.7))
        coverage_ratio = round(pass1_completed_pages / total_rendered_pages, 4)
        missing_pages = sorted(set(missing_pages))
        partial_input_used = pass1_completed_pages < total_rendered_pages

        if pass1_completed_pages < coverage_threshold:
            return self._failed_result(
                document_id=document_id,
                error_message=(
                    "Document synthesis requires more usable pass1 pages. "
                    f"usable={pass1_completed_pages}, total_rendered={total_rendered_pages}, "
                    f"coverage_threshold={coverage_threshold}"
                ),
                total_rendered_pages=total_rendered_pages,
                pass1_completed_pages=pass1_completed_pages,
                missing_pages=missing_pages,
                coverage_ratio=coverage_ratio,
                partial_input_used=partial_input_used,
                coverage_threshold=coverage_threshold,
            )

        try:
            envelope = self.openai_client.run_document_synthesis(
                document_id=document_id,
                total_pages=total_rendered_pages,
                page_analysis_summaries=usable_summaries,
            )
            normalized_envelope = self._normalize_summary_envelope(
                document_id=document_id,
                envelope=envelope,
                allowed_pages=set(usable_page_numbers),
                total_rendered_pages=total_rendered_pages,
                pass1_completed_pages=pass1_completed_pages,
                missing_pages=missing_pages,
                coverage_ratio=coverage_ratio,
                partial_input_used=partial_input_used,
                coverage_threshold=coverage_threshold,
            )
            saved_path = self.storage.save_document_summary(document_id, normalized_envelope)
        except Exception as exc:
            return self._failed_result(
                document_id=document_id,
                error_message=str(exc),
                total_rendered_pages=total_rendered_pages,
                pass1_completed_pages=pass1_completed_pages,
                missing_pages=missing_pages,
                coverage_ratio=coverage_ratio,
                partial_input_used=partial_input_used,
                coverage_threshold=coverage_threshold,
            )

        return {
            "document_id": document_id,
            "synthesis_status": StageStatus.COMPLETED.value,
            "saved_path": saved_path,
            "total_rendered_pages": total_rendered_pages,
            "pass1_completed_pages": pass1_completed_pages,
            "missing_pages": missing_pages,
            "coverage_ratio": coverage_ratio,
            "partial_input_used": partial_input_used,
            "coverage_threshold": coverage_threshold,
            "used_pages": sorted(set(usable_page_numbers)),
            "error_message": None,
        }

    def _build_candidate_anchor_summaries(
        self,
        candidate_anchors: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        summaries: list[dict[str, str]] = []
        for anchor in candidate_anchors:
            label = str(anchor.get("label", "")).strip()
            anchor_type = str(anchor.get("anchor_type", "")).strip()
            if not label or not anchor_type:
                continue
            key = (label, anchor_type)
            if key in seen:
                continue
            seen.add(key)
            summaries.append({"label": label, "anchor_type": anchor_type})
        return summaries

    def _normalize_summary_envelope(
        self,
        *,
        document_id: str,
        envelope: dict[str, Any],
        allowed_pages: set[int],
        total_rendered_pages: int,
        pass1_completed_pages: int,
        missing_pages: list[int],
        coverage_ratio: float,
        partial_input_used: bool,
        coverage_threshold: int,
    ) -> dict[str, Any]:
        if not isinstance(envelope, dict):
            raise ValueError("Document synthesis envelope must be a JSON object.")
        if not isinstance(envelope.get("meta"), dict):
            raise ValueError("Document synthesis envelope must include a meta object.")
        if not isinstance(envelope.get("result"), dict):
            raise ValueError("Document synthesis envelope must include a result object.")

        validated_result = validate_payload(
            "document_synthesis",
            {
                **dict(envelope["result"]),
                "document_id": document_id,
            },
        )
        normalized_result = self._normalize_result_references(validated_result, allowed_pages)
        normalized_result["document_id"] = document_id
        normalized_result = validate_payload("document_synthesis", normalized_result)

        meta = dict(envelope["meta"])
        meta.update(
            {
                "total_rendered_pages": total_rendered_pages,
                "pass1_completed_pages": pass1_completed_pages,
                "missing_pages": missing_pages,
                "coverage_ratio": coverage_ratio,
                "partial_input_used": partial_input_used,
                "coverage_threshold": coverage_threshold,
            }
        )

        return {
            "meta": meta,
            "result": normalized_result,
        }

    def _normalize_result_references(
        self,
        result: dict[str, Any],
        allowed_pages: set[int],
    ) -> dict[str, Any]:
        if not allowed_pages:
            raise ValueError("Document synthesis requires at least one allowed page.")

        normalized_result = dict(result)
        normalized_result["sections"] = [
            {
                **section,
                "pages": self._normalize_page_list(
                    section["pages"],
                    allowed_pages=allowed_pages,
                    field_name=f"sections[{index}].pages",
                ),
            }
            for index, section in enumerate(result["sections"])
        ]
        normalized_result["key_concepts"] = [
            {
                **concept,
                "pages": self._normalize_page_list(
                    concept["pages"],
                    allowed_pages=allowed_pages,
                    field_name=f"key_concepts[{index}].pages",
                ),
            }
            for index, concept in enumerate(result["key_concepts"])
        ]
        normalized_result["difficult_pages"] = self._normalize_page_list(
            result["difficult_pages"],
            allowed_pages=allowed_pages,
            field_name="difficult_pages",
        )
        normalized_result["prerequisite_links"] = self._normalize_prerequisite_links(
            result["prerequisite_links"],
            allowed_pages=allowed_pages,
        )
        return normalized_result

    def _normalize_page_list(
        self,
        pages: list[int],
        *,
        allowed_pages: set[int],
        field_name: str,
    ) -> list[int]:
        normalized_pages = sorted(set(int(page) for page in pages))
        invalid_pages = [page for page in normalized_pages if page not in allowed_pages]
        if invalid_pages:
            raise ValueError(
                f"{field_name} contains pages outside synthesis input: {', '.join(map(str, invalid_pages))}"
            )
        return normalized_pages

    def _normalize_prerequisite_links(
        self,
        links: list[dict[str, Any]],
        *,
        allowed_pages: set[int],
    ) -> list[dict[str, Any]]:
        normalized_links_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
        for index, link in enumerate(links):
            from_page = int(link["from_page"])
            to_page = int(link["to_page"])
            if from_page not in allowed_pages or to_page not in allowed_pages:
                raise ValueError(
                    "prerequisite_links contains pages outside synthesis input: "
                    f"from_page={from_page}, to_page={to_page}"
                )
            if to_page >= from_page:
                raise ValueError(
                    f"prerequisite_links[{index}] must satisfy to_page < from_page, "
                    f"got from_page={from_page}, to_page={to_page}"
                )
            pair = (from_page, to_page)
            normalized_links_by_pair.setdefault(
                pair,
                {
                    "from_page": from_page,
                    "to_page": to_page,
                    "reason": str(link["reason"]).strip(),
                },
            )
        return sorted(
            normalized_links_by_pair.values(),
            key=lambda item: (item["from_page"], item["to_page"], item["reason"]),
        )

    def _failed_result(
        self,
        *,
        document_id: str,
        error_message: str,
        total_rendered_pages: int = 0,
        pass1_completed_pages: int = 0,
        missing_pages: list[int] | None = None,
        coverage_ratio: float = 0.0,
        partial_input_used: bool = False,
        coverage_threshold: int = 0,
    ) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "synthesis_status": StageStatus.FAILED.value,
            "saved_path": None,
            "total_rendered_pages": total_rendered_pages,
            "pass1_completed_pages": pass1_completed_pages,
            "missing_pages": missing_pages or [],
            "coverage_ratio": coverage_ratio,
            "partial_input_used": partial_input_used,
            "coverage_threshold": coverage_threshold,
            "used_pages": [],
            "error_message": error_message,
        }
