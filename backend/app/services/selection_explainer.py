from __future__ import annotations

import hashlib
from typing import Any

from app.core.config import AppSettings, get_settings
from app.models.document import RenderStatus
from app.services.analysis_client import AnalysisClient, AnalysisClientError
from app.services.llm_provider import get_analysis_client
from app.services.storage import StorageService, get_storage_service


class SelectionExplanationError(RuntimeError):
    """Raised when an on-demand selection explanation cannot be generated."""


class SelectionExplanationService:
    def __init__(
        self,
        storage: StorageService | None = None,
        analysis_client: AnalysisClient | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.storage = storage or get_storage_service()
        self.analysis_client = analysis_client or get_analysis_client(
            settings=self.settings,
            storage=self.storage,
        )

    def explain_selection(
        self,
        *,
        document_id: str,
        page_number: int,
        selected_bbox: list[float],
    ) -> dict[str, Any]:
        self._validate_selected_bbox(selected_bbox)
        selection_id = self._build_selection_id(document_id, page_number, selected_bbox)

        cached = self.storage.load_selection_explanation(document_id, page_number, selection_id)
        if cached is not None and self._cache_matches_current_provider(cached):
            return cached["result"]  # type: ignore[return-value]

        page = self.storage.get_page(document_id, page_number)
        if page is None:
            raise SelectionExplanationError("Page not found.")
        if page.render_status is not RenderStatus.RENDERED:
            raise SelectionExplanationError("Rendered page image is not ready.")

        pass1_artifact = self.storage.load_pass1_result(document_id, page_number)
        if pass1_artifact is None:
            raise SelectionExplanationError("Pass1 preprocessing is required before selection explanation.")

        document_summary_artifact = self.storage.load_document_summary(document_id)
        if document_summary_artifact is None:
            raise SelectionExplanationError("Document synthesis is required before selection explanation.")

        image_path = self.storage.resolve_relative_path(page.image_path)
        if not image_path.exists():
            raise SelectionExplanationError("Rendered page image is unavailable.")

        pass1_result = dict(pass1_artifact["result"])
        document_summary = dict(document_summary_artifact["result"])
        matched_elements = self._rank_preprocessed_elements(pass1_result, selected_bbox)

        try:
            envelope = self.analysis_client.run_selection_explanation(
                page_image_path=image_path,
                document_id=document_id,
                page_number=page_number,
                selection_id=selection_id,
                selected_bbox=selected_bbox,
                pass1_result=pass1_result,
                document_summary=document_summary,
                matched_preprocessed_elements=matched_elements,
            )
        except AnalysisClientError:
            raise
        except Exception as exc:
            raise SelectionExplanationError(f"Selection explanation provider failed: {exc}") from exc

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
        if document_summary_artifact is None:
            raise SelectionExplanationError("Document synthesis is required before follow-up answers.")

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
                selection_explanation=dict(selection_artifact["result"]),
                pass1_result=dict(pass1_artifact["result"]),
                document_summary=dict(document_summary_artifact["result"]),
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

    def _cache_matches_current_provider(self, envelope: dict[str, Any]) -> bool:
        meta = envelope.get("meta")
        if not isinstance(meta, dict):
            return False

        expected_prompt_version = self.settings.stage_config("selection_explanation").prompt_version
        expected_model_name = (
            f"codex-cli:{self.settings.codex_cli_model}"
            if self.settings.llm_provider == "codex_cli"
            else None
        )
        if meta.get("schema_version") != self.settings.schema_version:
            return False
        if meta.get("prompt_version") != expected_prompt_version:
            return False
        if expected_model_name is not None and meta.get("model_name") != expected_model_name:
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

    def _build_selection_id(self, document_id: str, page_number: int, selected_bbox: list[float]) -> str:
        rounded_bbox = [round(float(value), 4) for value in selected_bbox]
        raw_key = f"{document_id}:{page_number}:{rounded_bbox}"
        return f"sel_{hashlib.sha1(raw_key.encode('utf-8')).hexdigest()[:16]}"

    def _rank_preprocessed_elements(
        self,
        pass1_result: dict[str, Any],
        selected_bbox: list[float],
    ) -> list[dict[str, Any]]:
        candidates = pass1_result.get("candidate_anchors")
        if not isinstance(candidates, list):
            return []

        scored_elements = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            bbox = candidate.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue

            overlap = self._intersection_area(selected_bbox, bbox)
            candidate_area = self._area(bbox)
            selection_area = self._area(selected_bbox)
            overlap_ratio = overlap / max(0.0001, min(candidate_area, selection_area))
            distance = self._center_distance(selected_bbox, bbox)
            score = overlap_ratio - distance * 0.15
            scored_elements.append((score, overlap_ratio, distance, candidate))

        scored_elements.sort(key=lambda item: item[0], reverse=True)
        top_elements = []
        for score, overlap_ratio, distance, candidate in scored_elements[:6]:
            top_elements.append(
                {
                    "anchor_id": candidate.get("anchor_id"),
                    "label": candidate.get("label"),
                    "anchor_type": candidate.get("anchor_type"),
                    "bbox": candidate.get("bbox"),
                    "question": candidate.get("question"),
                    "short_explanation": candidate.get("short_explanation"),
                    "confidence": candidate.get("confidence"),
                    "selection_overlap_ratio": round(max(0.0, overlap_ratio), 4),
                    "selection_center_distance": round(max(0.0, distance), 4),
                    "match_score": round(score, 4),
                }
            )
        return top_elements

    def _area(self, bbox: list[float]) -> float:
        return max(0.0, float(bbox[2])) * max(0.0, float(bbox[3]))

    def _intersection_area(self, left: list[float], right: list[float]) -> float:
        left_x2 = float(left[0]) + float(left[2])
        left_y2 = float(left[1]) + float(left[3])
        right_x2 = float(right[0]) + float(right[2])
        right_y2 = float(right[1]) + float(right[3])
        x1 = max(float(left[0]), float(right[0]))
        y1 = max(float(left[1]), float(right[1]))
        x2 = min(left_x2, right_x2)
        y2 = min(left_y2, right_y2)
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)

    def _center_distance(self, left: list[float], right: list[float]) -> float:
        left_x = float(left[0]) + float(left[2]) / 2
        left_y = float(left[1]) + float(left[3]) / 2
        right_x = float(right[0]) + float(right[2]) / 2
        right_y = float(right[1]) + float(right[3]) / 2
        return ((left_x - right_x) ** 2 + (left_y - right_y) ** 2) ** 0.5


def get_selection_explanation_service() -> SelectionExplanationService:
    return SelectionExplanationService()
