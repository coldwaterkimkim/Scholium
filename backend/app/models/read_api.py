from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus, ProcessingStage
from app.schemas.common import NormalizedBBox, PageElement, PageGuide, PageResultBase, PageWrapUp, SourceCue
from app.schemas.document_summary_schema import DocumentSummaryResult
from app.schemas.pass2_schema import Pass2FinalAnchor
from app.schemas.selection_explanation_schema import SelectionExplanationResult
from app.schemas.selection_follow_up_schema import SelectionFollowUpResult


class DocumentPublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    status: DocumentStatus
    total_pages: int | None = None
    response_language: Literal["ko", "en"] = "ko"


class DocumentListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    status: DocumentStatus
    total_pages: int | None = None
    response_language: Literal["ko", "en"] = "ko"
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None


class DocumentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentListItem] = Field(default_factory=list)


class DocumentSummaryPublicResponse(DocumentSummaryResult):
    pass


class PagePublicResponse(PageResultBase):
    image_url: str
    final_anchors: list[Pass2FinalAnchor] = Field(default_factory=list)
    page_elements: list[PageElement] = Field(default_factory=list)
    page_guide: PageGuide | None = None
    wrap_up: PageWrapUp | None = None
    document_guide_summary: dict[str, object] | None = None
    page_risk_note: str
    viewer_mode: Literal["render_only", "page_context_ready", "on_demand", "legacy_pass2"] = "on_demand"


class SelectionExplanationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_bbox: NormalizedBBox
    response_language: Literal["ko", "en"] | None = None


class SelectionExplanationPublicResponse(SelectionExplanationResult):
    pass


class SelectionExplanationHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explanation: SelectionExplanationResult
    is_important: bool = False


class SelectionExplanationHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SelectionExplanationHistoryItem] = Field(default_factory=list)


class SelectionExplanationStatePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_important: bool | None = None


class SelectionExplanationStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_id: str
    is_important: bool = False


class SelectionFollowUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=600)
    response_language: Literal["ko", "en"] | None = None


class SelectionFollowUpPublicResponse(SelectionFollowUpResult):
    document_id: str
    page_number: int
    selection_id: str
    question: str
    source_cues: list[SourceCue] = Field(default_factory=list, max_length=4)


class ProcessingFailureSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int
    stage: ProcessingStage
    error_message: str


class DocumentProcessingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    status: DocumentStatus
    stage: ProcessingStage | None = None
    current_stage: ProcessingStage | None = None
    total_pages: int | None = None
    rendered_pages: int
    pass1_completed_pages: int
    pass1_failed_pages: int
    pass1_processed_pages: int
    synthesis_ready: bool
    semantic_guide_ready: bool = False
    pass2_completed_pages: int
    pass2_failed_pages: int
    render_ready_for_viewer: bool
    page_context_ready_pages: int
    parser_map_ready_pages: int = 0
    document_context_ready: bool
    viewer_ready: bool = False
    ready_for_viewer: bool
    page_guide_count: int = 0
    semantic_guide_stage: str = "not_started"
    semantic_guide_completed_chunks: int = 0
    semantic_guide_total_chunks: int = 0
    semantic_guide_failed_chunks: int = 0
    current_page_number: int | None = None
    error_message: str | None = None
    has_errors: bool
    failed_page_count: int
    completed_page_count: int
    completion_ratio: float
    recent_failures: list[ProcessingFailureSummary] = []
