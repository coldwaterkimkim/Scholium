from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.common import StrictModel
from app.schemas.semantic_guide_schema import DocumentGuide


class DocumentGuideResult(StrictModel):
    document_id: str = Field(min_length=1)
    document_guide: DocumentGuide

    @model_validator(mode="after")
    def validate_document_consistency(self) -> "DocumentGuideResult":
        if self.document_guide.document_id != self.document_id:
            raise ValueError("document_guide.document_id must match document_id.")
        return self
