from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus, ProcessingStage
from app.schemas.common import NormalizedBBox, PageResultBase, SourceCue
from app.schemas.document_summary_schema import DocumentSummaryResult
from app.schemas.pass1_schema import Pass1CandidateAnchor
from app.schemas.pass2_schema import Pass2FinalAnchor
from app.schemas.selection_explanation_schema import SelectionExplanationResult
from app.schemas.selection_follow_up_schema import SelectionFollowUpResult


class DocumentPublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    status: DocumentStatus
    total_pages: int | None = None


class DocumentSummaryPublicResponse(DocumentSummaryResult):
    pass


class PagePublicResponse(PageResultBase):
    image_url: str
    final_anchors: list[Pass2FinalAnchor] = Field(default_factory=list)
    page_elements: list[Pass1CandidateAnchor] = Field(default_factory=list)
    page_risk_note: str
    viewer_mode: Literal["on_demand", "legacy_pass2"] = "on_demand"


class SelectionExplanationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_bbox: NormalizedBBox


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
    pass2_completed_pages: int
    pass2_failed_pages: int
    ready_for_viewer: bool
    current_page_number: int | None = None
    error_message: str | None = None
    has_errors: bool
    failed_page_count: int
    completed_page_count: int
    completion_ratio: float
    recent_failures: list[ProcessingFailureSummary] = []
