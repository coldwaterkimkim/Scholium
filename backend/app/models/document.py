from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    RENDERING = "rendering"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class ProcessingStage(StrEnum):
    RENDER = "render"
    PASS1 = "pass1"
    SYNTHESIS = "synthesis"
    PASS2 = "pass2"


class RenderStatus(StrEnum):
    PENDING = "pending"
    RENDERED = "rendered"
    FAILED = "failed"


class StageStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    original_path: str
    status: DocumentStatus
    total_pages: int | None = None
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None


class PageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    document_id: str
    page_number: int
    image_path: str
    render_status: RenderStatus
    width: int | None = None
    height: int | None = None
    pass1_status: StageStatus | None = None
    pass2_status: StageStatus | None = None
    pass1_error_message: str | None = None
    pass2_error_message: str | None = None


class DocumentUploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    status: DocumentStatus


class RenderedPageArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int
    image_path: str
    width: int
    height: int


class PageRenderFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int
    error_message: str


class DocumentRenderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    status: DocumentStatus
    total_pages: int
    rendered_pages: list[RenderedPageArtifact]
    failed_pages: list[PageRenderFailure]
    error_message: str | None = None
