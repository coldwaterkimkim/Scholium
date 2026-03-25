from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, conlist


NormalizedFloat = Annotated[float, Field(ge=0.0, le=1.0)]
NormalizedBBox = conlist(NormalizedFloat, min_length=4, max_length=4)
AnchorType = Literal["text", "formula", "chart", "table", "diagram", "image", "flow", "other"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageResultBase(StrictModel):
    document_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    page_role: str = Field(min_length=1)
    page_summary: str = Field(min_length=1)


class CandidateAnchor(StrictModel):
    anchor_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    anchor_type: AnchorType
    bbox: NormalizedBBox
    question: str = Field(min_length=1)
    short_explanation: str = Field(min_length=1)
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class FinalAnchor(CandidateAnchor):
    long_explanation: str = Field(min_length=1)
    prerequisite: str = Field(min_length=0)
    related_pages: list[int]


class DocumentSection(StrictModel):
    section_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    pages: list[int]


class KeyConcept(StrictModel):
    term: str = Field(min_length=1)
    description: str = Field(min_length=1)
    pages: list[int]


class PrerequisiteLink(StrictModel):
    from_page: int = Field(ge=1)
    to_page: int = Field(ge=1)
    reason: str = Field(min_length=1)
