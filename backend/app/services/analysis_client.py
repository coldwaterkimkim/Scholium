from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class AnalysisClientError(RuntimeError):
    """Base error for local analysis providers."""


class AnalysisResponseParseError(AnalysisClientError):
    """Raised when a provider response cannot be parsed as JSON."""


class AnalysisResponseValidationError(AnalysisClientError):
    """Raised when a provider response fails local schema validation."""


class AnalysisClient(Protocol):
    def run_pass1(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        optional_extracted_text: str | None = None,
    ) -> dict[str, Any]:
        ...

    def run_pass1_text_first(
        self,
        *,
        document_id: str,
        page_number: int,
        route_label: str,
        route_reason: str,
        parser_source: str,
        text_length: int,
        non_empty_text_block_count: int,
        page_text: str,
        parsed_blocks: list[dict[str, Any]],
        allowed_anchor_regions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...

    def run_document_synthesis(
        self,
        document_id: str,
        total_pages: int,
        page_analysis_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...

    def run_semantic_guide(
        self,
        document_id: str,
        document_digest: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def run_pass2(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        pass1_result: dict[str, Any],
        document_summary: dict[str, Any],
        extra_guidance: str | None = None,
    ) -> dict[str, Any]:
        ...

    def run_selection_explanation(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        selection_id: str,
        selected_bbox: list[float],
        selection_context: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def run_selection_follow_up(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        selection_id: str,
        question: str,
        selection_explanation: dict[str, Any],
        pass1_result: dict[str, Any],
        document_summary: dict[str, Any],
    ) -> dict[str, Any]:
        ...
