from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import (
    FinalAnchor,
    NormalizedBBox,
    RelatedConceptPage,
    SourceCue,
    StudyImportance,
)


class SelectionExplanationResult(FinalAnchor):
    document_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    selection_id: str = Field(min_length=1)
    concept_title: str = Field(min_length=1)
    selected_bbox: NormalizedBBox
    explanation_mode: Literal["selection"] = Field(...)
    study_importance: StudyImportance
    meaning_in_context: str = Field(min_length=1)
    why_it_matters_here: str = Field(min_length=1)
    related_concepts_and_pages: list[RelatedConceptPage] = Field(max_length=4)
    source_cues: list[SourceCue] = Field(max_length=4)

    @field_validator("related_pages")
    @classmethod
    def validate_unique_related_pages(cls, related_pages: list[int]) -> list[int]:
        if len(related_pages) != len(set(related_pages)):
            raise ValueError("related_pages must not contain duplicate page numbers.")
        return related_pages

    @model_validator(mode="after")
    def validate_bbox_bounds(self) -> "SelectionExplanationResult":
        for field_name, bbox in {"bbox": self.bbox, "selected_bbox": self.selected_bbox}.items():
            x, y, width, height = bbox
            if width <= 0 or height <= 0:
                raise ValueError(f"{field_name} width and height must be greater than 0.")
            if x + width > 1:
                raise ValueError(f"{field_name} x + width must be less than or equal to 1.")
            if y + height > 1:
                raise ValueError(f"{field_name} y + height must be less than or equal to 1.")
        return self
