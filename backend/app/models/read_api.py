from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentStatus
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
