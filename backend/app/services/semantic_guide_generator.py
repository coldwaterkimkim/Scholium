from __future__ import annotations

import json
from typing import Any

from app.models.document import RenderStatus, StageStatus
from app.services.analysis_client import AnalysisClient
from app.services.codex_cli_client import CodexCLIClient
from app.services.llm_provider import get_analysis_client
from app.services.page_context_builder import PageContextBuilder
from app.services.storage import StorageService, get_storage_service


class SemanticGuideGenerator:
    """Generate document/page semantic guides from compact parser digest."""

    def __init__(
        self,
        storage: StorageService | None = None,
        analysis_client: AnalysisClient | None = None,
        page_context_builder: PageContextBuilder | None = None,
    ) -> None:
        self.storage = storage or get_storage_service()
        if analysis_client is not None:
            self.analysis_client = analysis_client
        elif self.storage.settings.llm_provider == "mock":
            self.analysis_client = get_analysis_client(storage=self.storage)
        else:
            self.analysis_client = CodexCLIClient(settings=self.storage.settings, storage=self.storage)
        self.page_context_builder = page_context_builder or PageContextBuilder(settings=self.storage.settings)

    def generate_document(self, document_id: str) -> dict[str, Any]:
        document = self.storage.get_document(document_id)
        response_language = document.response_language if document is not None else "ko"
        page_records = self.storage.get_pages(document_id)
        rendered_pages = [
            page for page in page_records if page.render_status is RenderStatus.RENDERED
        ]
        if not rendered_pages:
            return self._failed_result(document_id, "No rendered pages are available.")

        page_contexts: list[dict[str, Any]] = []
        missing_pages: list[int] = []
        for page in sorted(rendered_pages, key=lambda item: item.page_number):
            if page.pass1_status is not StageStatus.COMPLETED:
                missing_pages.append(page.page_number)
                continue
            try:
                page_context = self.storage.load_page_context(document_id, page.page_number)
            except ValueError:
                page_context = None
            if page_context is None:
                missing_pages.append(page.page_number)
                continue
            page_contexts.append(dict(page_context))

        if not page_contexts:
            return self._failed_result(document_id, "No parser-first page contexts are available.")

        digest = self.page_context_builder.build_document_digest(
            document_id=document_id,
            page_contexts=page_contexts,
        )
        digest["response_language"] = response_language
        digest["response_language_instruction"] = (
            "Write all student-facing DocumentGuide and PageGuide fields in English."
            if response_language == "en"
            else "Write all student-facing DocumentGuide and PageGuide fields in Korean."
        )
        digest_size_chars = len(json.dumps(digest, ensure_ascii=False, separators=(",", ":")))

        try:
            envelope = self.analysis_client.run_semantic_guide(
                document_id=document_id,
                document_digest=digest,
            )
            semantic_envelope = self._with_semantic_meta(
                envelope=envelope,
                total_rendered_pages=len(rendered_pages),
                page_context_completed_pages=len(page_contexts),
                digest_size_chars=digest_size_chars,
                response_language=response_language,
            )
            semantic_saved_path = self.storage.save_semantic_guide(document_id, semantic_envelope)
            normalized_semantic = self.storage.load_semantic_guide(document_id)
            if normalized_semantic is None:
                raise ValueError("Semantic guide was saved but could not be reloaded.")

            summary_saved_path = self.storage.save_document_summary(
                document_id,
                self._document_summary_from_semantic(
                    document_id=document_id,
                    semantic_artifact=normalized_semantic,
                    total_rendered_pages=len(rendered_pages),
                    page_context_completed_pages=len(page_contexts),
                    missing_pages=missing_pages,
                ),
            )
            updated_page_guides = self._apply_page_guides_to_pass1(
                document_id=document_id,
                semantic_artifact=normalized_semantic,
            )
        except Exception as exc:
            return self._failed_result(document_id, str(exc))

        return {
            "document_id": document_id,
            "semantic_guide_status": StageStatus.COMPLETED.value,
            "synthesis_status": StageStatus.COMPLETED.value,
            "saved_path": summary_saved_path,
            "semantic_guide_path": semantic_saved_path,
            "total_rendered_pages": len(rendered_pages),
            "pass1_completed_pages": len(page_contexts),
            "page_context_completed_pages": len(page_contexts),
            "missing_pages": sorted(set(missing_pages)),
            "coverage_ratio": round(len(page_contexts) / max(1, len(rendered_pages)), 4),
            "partial_input_used": bool(missing_pages),
            "coverage_threshold": 1,
            "used_pages": [int(page_context["page_number"]) for page_context in page_contexts],
            "page_guide_count": updated_page_guides,
            "semantic_guide_call_count": 1,
            "digest_size_chars": digest_size_chars,
            "error_message": None,
        }

    def _with_semantic_meta(
        self,
        *,
        envelope: dict[str, Any],
        total_rendered_pages: int,
        page_context_completed_pages: int,
        digest_size_chars: int,
        response_language: str,
    ) -> dict[str, Any]:
        meta = dict(envelope.get("meta") or {})
        meta.update(
            {
                "total_rendered_pages": total_rendered_pages,
                "page_context_completed_pages": page_context_completed_pages,
                "semantic_guide_call_count": 1,
                "digest_size_chars": digest_size_chars,
                "response_language": response_language,
            }
        )
        return {
            "meta": meta,
            "result": dict(envelope.get("result") or {}),
        }

    def _document_summary_from_semantic(
        self,
        *,
        document_id: str,
        semantic_artifact: dict[str, Any],
        total_rendered_pages: int,
        page_context_completed_pages: int,
        missing_pages: list[int],
    ) -> dict[str, Any]:
        semantic_meta = dict(semantic_artifact.get("meta") or {})
        result = dict(semantic_artifact["result"])
        document_guide = dict(result["document_guide"])
        sections = [
            {
                "section_id": str(section.get("section_id") or f"section-{index + 1}"),
                "title": str(section.get("title") or f"Section {index + 1}"),
                "pages": [int(page) for page in section.get("pages", [])],
            }
            for index, section in enumerate(document_guide.get("section_structure", []))
            if isinstance(section, dict) and section.get("pages")
        ]
        if not sections:
            used_pages = [
                int(page_guide["page_number"])
                for page_guide in result.get("page_guides", [])
                if isinstance(page_guide, dict) and page_guide.get("page_number")
            ]
            sections = [{"section_id": "section-1", "title": "Document overview", "pages": used_pages or [1]}]

        key_concepts = []
        for concept in document_guide.get("key_concepts", []):
            if not isinstance(concept, dict):
                continue
            term = str(concept.get("concept") or "").strip()
            description = str(concept.get("description") or "").strip()
            pages = [int(page) for page in concept.get("pages", [])]
            if term and description and pages:
                key_concepts.append(
                    {
                        "term": term,
                        "description": description,
                        "pages": pages,
                    }
                )
        if not key_concepts:
            key_concepts = [
                {
                    "term": str(document_guide.get("overall_topic") or "Document topic"),
                    "description": str(document_guide.get("overall_summary") or "Semantic guide topic."),
                    "pages": sections[0]["pages"][:3],
                }
            ]

        return {
            "meta": {
                "schema_version": str(semantic_meta["schema_version"]),
                "prompt_version": str(semantic_meta["prompt_version"]),
                "model_name": str(semantic_meta["model_name"]),
                "generated_at": str(semantic_meta["generated_at"]),
                "total_rendered_pages": total_rendered_pages,
                "pass1_completed_pages": page_context_completed_pages,
                "missing_pages": sorted(set(missing_pages)),
                "coverage_ratio": round(page_context_completed_pages / max(1, total_rendered_pages), 4),
                "partial_input_used": bool(missing_pages),
                "coverage_threshold": 1,
                "response_language": semantic_meta.get("response_language", "ko"),
            },
            "result": {
                "document_id": document_id,
                "overall_topic": document_guide["overall_topic"],
                "overall_summary": document_guide["overall_summary"],
                "sections": sections,
                "key_concepts": key_concepts,
                "difficult_pages": [int(page) for page in document_guide.get("difficult_pages", [])],
                "prerequisite_links": [
                    {
                        "from_page": int(link["from_page"]),
                        "to_page": int(link["to_page"]),
                        "reason": str(link["reason"]),
                    }
                    for link in document_guide.get("prerequisite_links", [])
                    if isinstance(link, dict)
                ],
            },
        }

    def _apply_page_guides_to_pass1(
        self,
        *,
        document_id: str,
        semantic_artifact: dict[str, Any],
    ) -> int:
        result = dict(semantic_artifact["result"])
        page_guides = {
            int(page_guide["page_number"]): dict(page_guide)
            for page_guide in result.get("page_guides", [])
            if isinstance(page_guide, dict) and page_guide.get("page_number")
        }
        updated_count = 0
        for page_number, page_guide in page_guides.items():
            pass1_artifact = self.storage.load_pass1_result(document_id, page_number)
            if pass1_artifact is None:
                continue
            pass1_result = dict(pass1_artifact["result"])
            pass1_result["page_role"] = str(page_guide.get("page_role") or pass1_result["page_role"])
            pass1_result["page_summary"] = str(
                page_guide.get("one_line_thesis") or pass1_result["page_summary"]
            )
            pass1_result["page_guide"] = self._page_guide_for_pass1(page_guide)
            self.storage.save_pass1_result(
                document_id,
                page_number,
                {
                    "meta": dict(pass1_artifact["meta"]),
                    "result": pass1_result,
                },
            )
            updated_count += 1
        return updated_count

    def _page_guide_for_pass1(self, page_guide: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in page_guide.items()
            if key not in {"document_id", "page_number"}
        }

    def _failed_result(self, document_id: str, error_message: str) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "semantic_guide_status": StageStatus.FAILED.value,
            "synthesis_status": StageStatus.FAILED.value,
            "saved_path": None,
            "semantic_guide_path": None,
            "total_rendered_pages": 0,
            "pass1_completed_pages": 0,
            "page_context_completed_pages": 0,
            "missing_pages": [],
            "coverage_ratio": 0.0,
            "partial_input_used": False,
            "coverage_threshold": 1,
            "used_pages": [],
            "page_guide_count": 0,
            "semantic_guide_call_count": 0,
            "digest_size_chars": 0,
            "error_message": error_message,
        }
