from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator, model_validator

from app.schemas.common import PageGuide, StrictModel


PositivePageNumber = Annotated[int, Field(ge=1)]


class SemanticGuideSection(StrictModel):
    section_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    pages: list[PositivePageNumber] = Field(min_length=1)

    @field_validator("pages")
    @classmethod
    def normalize_pages(cls, value: list[int]) -> list[int]:
        return sorted({int(page) for page in value})


class SemanticGuideKeyConcept(StrictModel):
    concept: str = Field(min_length=1)
    description: str = Field(min_length=1)
    pages: list[PositivePageNumber] = Field(default_factory=list)

    @field_validator("pages")
    @classmethod
    def normalize_pages(cls, value: list[int]) -> list[int]:
        return sorted({int(page) for page in value})


class SemanticGuidePrerequisiteLink(StrictModel):
    from_page: PositivePageNumber
    to_page: PositivePageNumber
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_page_order(self) -> "SemanticGuidePrerequisiteLink":
        if self.to_page >= self.from_page:
            raise ValueError("prerequisite_links must satisfy to_page < from_page.")
        return self


class DocumentGuide(StrictModel):
    document_id: str = Field(min_length=1)
    overall_topic: str = Field(min_length=1)
    overall_summary: str = Field(min_length=1)
    section_structure: list[SemanticGuideSection] = Field(default_factory=list)
    key_concepts: list[SemanticGuideKeyConcept] = Field(default_factory=list)
    page_sequence_overview: list[str] = Field(default_factory=list, max_length=20)
    prerequisite_links: list[SemanticGuidePrerequisiteLink] = Field(default_factory=list)
    difficult_pages: list[PositivePageNumber] = Field(default_factory=list)
    study_strategy_notes: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("difficult_pages")
    @classmethod
    def normalize_pages(cls, value: list[int]) -> list[int]:
        return sorted({int(page) for page in value})


class SemanticPageGuide(PageGuide):
    document_id: str = Field(min_length=1)
    page_number: PositivePageNumber
    page_role: str = Field(min_length=1)


class SemanticGuideResult(StrictModel):
    document_id: str = Field(min_length=1)
    document_guide: DocumentGuide
    page_guides: list[SemanticPageGuide] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document_consistency(self) -> "SemanticGuideResult":
        if self.document_guide.document_id != self.document_id:
            raise ValueError("document_guide.document_id must match document_id.")

        seen_pages: set[int] = set()
        for page_guide in self.page_guides:
            if page_guide.document_id != self.document_id:
                raise ValueError("page_guides[].document_id must match document_id.")
            if page_guide.page_number in seen_pages:
                raise ValueError("page_guides must not contain duplicate page_number values.")
            seen_pages.add(page_guide.page_number)
        return self
