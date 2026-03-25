from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from app.schemas.common import DocumentSection, KeyConcept, PrerequisiteLink, StrictModel


PositivePageNumber = Annotated[int, Field(ge=1)]


class DocumentSummarySection(DocumentSection):
    pages: list[PositivePageNumber] = Field(min_length=1)


class DocumentSummaryKeyConcept(KeyConcept):
    pages: list[PositivePageNumber] = Field(min_length=1)


class DocumentSummaryPrerequisiteLink(PrerequisiteLink):
    @model_validator(mode="after")
    def validate_page_order(self) -> "DocumentSummaryPrerequisiteLink":
        if self.to_page >= self.from_page:
            raise ValueError("prerequisite_links must satisfy to_page < from_page.")
        return self


class DocumentSummaryResult(StrictModel):
    document_id: str = Field(min_length=1)
    overall_topic: str = Field(min_length=1)
    overall_summary: str = Field(min_length=1)
    sections: list[DocumentSummarySection] = Field(min_length=1)
    key_concepts: list[DocumentSummaryKeyConcept] = Field(min_length=1)
    difficult_pages: list[PositivePageNumber]
    prerequisite_links: list[DocumentSummaryPrerequisiteLink]
