from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentStatus, ProcessingStage
from app.schemas.document_summary_schema import DocumentSummaryResult
from app.schemas.pass2_schema import Pass2Result


class DocumentPublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    status: DocumentStatus
    total_pages: int | None = None


class DocumentSummaryPublicResponse(DocumentSummaryResult):
    pass


class PagePublicResponse(Pass2Result):
    image_url: str


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
