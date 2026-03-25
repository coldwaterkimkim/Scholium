from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator

from app.schemas.common import FinalAnchor, PageResultBase


PositivePageNumber = Annotated[int, Field(ge=1)]


class Pass2FinalAnchor(FinalAnchor):
    related_pages: list[PositivePageNumber] = Field(max_length=2)

    @field_validator("related_pages")
    @classmethod
    def validate_unique_related_pages(cls, related_pages: list[int]) -> list[int]:
        if len(related_pages) != len(set(related_pages)):
            raise ValueError("related_pages must not contain duplicate page numbers.")
        return related_pages


class Pass2Result(PageResultBase):
    final_anchors: list[Pass2FinalAnchor] = Field(min_length=3, max_length=5)
    page_risk_note: str = Field(min_length=1)

    @field_validator("final_anchors")
    @classmethod
    def validate_unique_anchor_ids(
        cls,
        final_anchors: list[Pass2FinalAnchor],
    ) -> list[Pass2FinalAnchor]:
        anchor_ids = [anchor.anchor_id for anchor in final_anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("final_anchors must not contain duplicate anchor_id values.")
        return final_anchors
