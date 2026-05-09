from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from app.schemas.common import StrictModel
from app.schemas.semantic_guide_schema import PositivePageNumber, SemanticPageGuide


class PageGuideChunkResult(StrictModel):
    document_id: str = Field(min_length=1)
    chunk_index: int = Field(ge=1)
    page_numbers: list[PositivePageNumber] = Field(min_length=1)
    page_guides: list[SemanticPageGuide] = Field(min_length=1)

    @field_validator("page_numbers")
    @classmethod
    def normalize_page_numbers(cls, value: list[int]) -> list[int]:
        return sorted({int(page) for page in value})

    @model_validator(mode="after")
    def validate_chunk_consistency(self) -> "PageGuideChunkResult":
        requested_pages = set(self.page_numbers)
        seen_pages: set[int] = set()
        for page_guide in self.page_guides:
            if page_guide.document_id != self.document_id:
                raise ValueError("page_guides[].document_id must match document_id.")
            if page_guide.page_number not in requested_pages:
                raise ValueError("page_guides must only contain requested page_numbers.")
            if page_guide.page_number in seen_pages:
                raise ValueError("page_guides must not contain duplicate page_number values.")
            seen_pages.add(page_guide.page_number)
        if seen_pages != requested_pages:
            raise ValueError("page_guides must contain exactly one guide for every requested page.")
        return self
