from __future__ import annotations

import hashlib
import json
from time import perf_counter
from typing import Any

from app.core.config import AppSettings, get_settings
from app.models.document import RenderStatus
from app.services.analysis_client import AnalysisClient, AnalysisClientError
from app.services.llm_provider import get_analysis_client
from app.services.selection_context_builder import SelectionContextBuilder
from app.services.storage import StorageService, get_storage_service


class SelectionExplanationError(RuntimeError):
    """Raised when an on-demand selection explanation cannot be generated."""


class SelectionExplanationService:
    def __init__(
        self,
        storage: StorageService | None = None,
        analysis_client: AnalysisClient | None = None,
        settings: AppSettings | None = None,
        selection_context_builder: SelectionContextBuilder | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.storage = storage or get_storage_service()
        self.analysis_client = analysis_client or get_analysis_client(
            settings=self.settings,
            storage=self.storage,
        )
        self.selection_context_builder = selection_context_builder or SelectionContextBuilder(storage=self.storage)

    def explain_selection(
        self,
        *,
        document_id: str,
        page_number: int,
        selected_bbox: list[float],
        response_language: str | None = None,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        self._validate_selected_bbox(selected_bbox)

        page = self.storage.get_page(document_id, page_number)
        if page is None:
            raise SelectionExplanationError("Page not found.")
        if page.render_status is not RenderStatus.RENDERED:
            raise SelectionExplanationError("Rendered page image is not ready.")

        pass1_artifact = self.storage.load_pass1_result(document_id, page_number)
        if pass1_artifact is None:
            raise SelectionExplanationError("Pass1 preprocessing is required before selection explanation.")

        document_summary_artifact = self.storage.load_document_summary(document_id)

        image_path = self.storage.resolve_relative_path(page.image_path)
        if not image_path.exists():
            raise SelectionExplanationError("Rendered page image is unavailable.")

        selection_context = self.selection_context_builder.build(
            document_id=document_id,
            page_number=page_number,
            selected_bbox=selected_bbox,
            pass1_artifact=dict(pass1_artifact),
            document_summary_artifact=(
                dict(document_summary_artifact) if document_summary_artifact is not None else None
            ),
            response_language=self._response_language(document_id, response_language),
        )
        selection_id = self._build_selection_id(
            document_id=document_id,
            page_number=page_number,
            selected_bbox=selected_bbox,
            selection_context=selection_context,
        )

        cached = self.storage.load_selection_explanation(document_id, page_number, selection_id)
        if cached is not None and self._cache_matches_current_provider(cached, selection_context):
            return self._result_with_selected_bbox(dict(cached["result"]), selected_bbox)

        try:
            envelope = self.analysis_client.run_selection_explanation(
                page_image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                selection_id=selection_id,
                selected_bbox=selected_bbox,
                selection_context=selection_context,
            )
        except AnalysisClientError:
            raise
        except Exception as exc:
            raise SelectionExplanationError(f"Selection explanation provider failed: {exc}") from exc

        envelope = self._attach_selection_cache_meta(
            envelope,
            selection_context,
            latency_seconds=perf_counter() - started_at,
        )
        self.storage.save_selection_explanation(document_id, page_number, selection_id, envelope)
        saved = self.storage.load_selection_explanation(document_id, page_number, selection_id)
        if saved is None:
            raise SelectionExplanationError("Selection explanation was generated but could not be reloaded.")
        return saved["result"]  # type: ignore[return-value]

    def answer_follow_up(
        self,
        *,
        document_id: str,
        page_number: int,
        selection_id: str,
        question: str,
        response_language: str | None = None,
    ) -> dict[str, Any]:
        clean_question = " ".join(question.split())
        if not clean_question:
            raise SelectionExplanationError("Follow-up question is required.")
        if len(clean_question) > 600:
            raise SelectionExplanationError("Follow-up question is too long.")

        page = self.storage.get_page(document_id, page_number)
        if page is None:
            raise SelectionExplanationError("Page not found.")
        if page.render_status is not RenderStatus.RENDERED:
            raise SelectionExplanationError("Rendered page image is not ready.")

        selection_artifact = self.storage.load_selection_explanation(document_id, page_number, selection_id)
        if selection_artifact is None:
            raise SelectionExplanationError("Selection explanation is not available.")

        pass1_artifact = self.storage.load_pass1_result(document_id, page_number)
        if pass1_artifact is None:
            raise SelectionExplanationError("Pass1 preprocessing is required before follow-up answers.")

        document_summary_artifact = self.storage.load_document_summary(document_id)
        document_summary = (
            dict(document_summary_artifact["result"])
            if document_summary_artifact is not None
            else {
                "document_id": document_id,
                "overall_topic": "Document context is still being prepared.",
                "overall_summary": "Only page context and the existing selected-region explanation are available.",
                "sections": [],
                "key_concepts": [],
                "difficult_pages": [],
                "prerequisite_links": [],
            }
        )

        image_path = self.storage.resolve_relative_path(page.image_path)
        if not image_path.exists():
            raise SelectionExplanationError("Rendered page image is unavailable.")

        try:
            envelope = self.analysis_client.run_selection_follow_up(
                page_image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                selection_id=selection_id,
                question=clean_question,
                response_language=self._response_language(document_id, response_language),
                selection_explanation=dict(selection_artifact["result"]),
                pass1_result=dict(pass1_artifact["result"]),
                document_summary=document_summary,
            )
        except AnalysisClientError:
            raise
        except Exception as exc:
            raise SelectionExplanationError(f"Selection follow-up provider failed: {exc}") from exc

        result = dict(envelope["result"])
        result["document_id"] = document_id
        result["page_number"] = page_number
        result["selection_id"] = selection_id
        result["question"] = clean_question
        result.setdefault("source_cues", [])
        return result

    def list_selection_history(
        self,
        *,
        document_id: str,
        page_number: int,
    ) -> list[dict[str, Any]]:
        return self.storage.list_selection_explanations(document_id, page_number)

    def update_selection_state(
        self,
        *,
        document_id: str,
        page_number: int,
        selection_id: str,
        is_important: bool | None = None,
    ) -> dict[str, Any]:
        try:
            return self.storage.update_selection_explanation_state(
                document_id,
                page_number,
                selection_id,
                is_important=is_important,
            )
        except FileNotFoundError as exc:
            raise SelectionExplanationError("Selection explanation is not available.") from exc

    def delete_selection(
        self,
        *,
        document_id: str,
        page_number: int,
        selection_id: str,
    ) -> bool:
        return self.storage.delete_selection_explanation(document_id, page_number, selection_id)

    def _cache_matches_current_provider(
        self,
        envelope: dict[str, Any],
        selection_context: dict[str, Any],
    ) -> bool:
        meta = envelope.get("meta")
        if not isinstance(meta, dict):
            return False

        if meta.get("schema_version") != self.settings.schema_version:
            return False
        if meta.get("prompt_version") != self.settings.stage_config("selection_explanation").prompt_version:
            return False
        if meta.get("provider") != self.settings.llm_provider:
            return False
        if meta.get("model_name") != self._selection_model_name():
            return False
        if meta.get("reasoning_effort") != self._selection_reasoning_effort():
            return False
        if meta.get("context_hash") != selection_context.get("context_hash"):
            return False
        if meta.get("cache_version") != "selection_cache_v1":
            return False
        return True

    def _validate_selected_bbox(self, selected_bbox: list[float]) -> None:
        if len(selected_bbox) != 4:
            raise SelectionExplanationError("selected_bbox must contain exactly four numbers.")
        x, y, width, height = [float(value) for value in selected_bbox]
        if width <= 0 or height <= 0:
            raise SelectionExplanationError("selected_bbox width and height must be greater than 0.")
        if x < 0 or y < 0 or x + width > 1 or y + height > 1:
            raise SelectionExplanationError("selected_bbox must stay inside the page image.")

    def _build_selection_id(
        self,
        *,
        document_id: str,
        page_number: int,
        selected_bbox: list[float],
        selection_context: dict[str, Any],
    ) -> str:
        raw_key = {
            "cache_version": "selection_cache_v1",
            "document_id": document_id,
            "page_number": page_number,
            "rounded_selected_bbox": SelectionContextBuilder.round_bbox(selected_bbox),
            "schema_version": self.settings.schema_version,
            "prompt_version": self.settings.stage_config("selection_explanation").prompt_version,
            "provider": self.settings.llm_provider,
            "model_name": self._selection_model_name(),
            "reasoning_effort": self._selection_reasoning_effort(),
            "context_hash": selection_context.get("context_hash"),
        }
        encoded_key = json.dumps(raw_key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"sel_{hashlib.sha1(encoded_key.encode('utf-8')).hexdigest()[:16]}"

    def _selection_model_name(self) -> str:
        if self.settings.llm_provider == "codex_cli":
            model_name = self.settings.codex_cli_model or "default"
            return f"codex-cli:{model_name}"
        if self.settings.llm_provider == "openai_api":
            return self.settings.stage_config("selection_explanation").model_name
        return "mock-analysis-provider"

    def _selection_reasoning_effort(self) -> str:
        if self.settings.llm_provider == "codex_cli":
            return self.settings.codex_cli_reasoning_effort
        if self.settings.llm_provider == "openai_api":
            return self.settings.stage_config("selection_explanation").reasoning_effort
        return "none"

    def _response_language(self, document_id: str, requested_language: str | None) -> str:
        if requested_language == "en":
            return "en"
        if requested_language == "ko":
            return "ko"
        document = self.storage.get_document(document_id)
        return document.response_language if document is not None else "ko"

    def _attach_selection_cache_meta(
        self,
        envelope: dict[str, Any],
        selection_context: dict[str, Any],
        *,
        latency_seconds: float,
    ) -> dict[str, Any]:
        metrics = dict(selection_context.get("metrics") or {})
        meta = dict(envelope.get("meta") or {})
        meta.update(
            {
                "provider": self.settings.llm_provider,
                "cache_version": "selection_cache_v1",
                "cache_hit": False,
                "context_hash": selection_context.get("context_hash"),
                "selection_explanation_first_latency": round(max(0.0, latency_seconds), 4),
                "selection_context_size_chars": int(metrics.get("selection_context_size_chars", 0)),
                "matched_element_count": int(metrics.get("matched_element_count", 0)),
                "nearby_text_block_count": int(metrics.get("nearby_text_block_count", 0)),
                "source_candidate_count": int(metrics.get("source_candidate_count", 0)),
            }
        )
        if "prompt_payload_size_chars" not in meta:
            meta["prompt_payload_size_chars"] = int(metrics.get("prompt_payload_size_chars", 0))
        return {
            "meta": meta,
            "result": envelope["result"],
        }

    def _result_with_selected_bbox(
        self,
        result: dict[str, Any],
        selected_bbox: list[float],
    ) -> dict[str, Any]:
        result["bbox"] = list(selected_bbox)
        result["selected_bbox"] = list(selected_bbox)
        return result


def get_selection_explanation_service() -> SelectionExplanationService:
    return SelectionExplanationService()
