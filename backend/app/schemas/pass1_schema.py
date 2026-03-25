from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from app.schemas.common import CandidateAnchor, PageResultBase


class Pass1CandidateAnchor(CandidateAnchor):
    @field_validator("bbox")
    @classmethod
    def validate_positive_dimensions(cls, bbox: list[float]) -> list[float]:
        _, _, width, height = bbox
        if width <= 0 or height <= 0:
            raise ValueError("bbox width and height must be greater than 0.")
        return bbox

    @model_validator(mode="after")
    def validate_bbox_bounds(self) -> "Pass1CandidateAnchor":
        x, y, width, height = self.bbox
        if x + width > 1:
            raise ValueError("bbox x + width must be less than or equal to 1.")
        if y + height > 1:
            raise ValueError("bbox y + height must be less than or equal to 1.")
        return self


class Pass1Result(PageResultBase):
    candidate_anchors: list[Pass1CandidateAnchor] = Field(max_length=15)

    @field_validator("candidate_anchors")
    @classmethod
    def validate_unique_anchor_ids(
        cls,
        candidate_anchors: list[Pass1CandidateAnchor],
    ) -> list[Pass1CandidateAnchor]:
        anchor_ids = [anchor.anchor_id for anchor in candidate_anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("candidate_anchors must not contain duplicate anchor_id values.")
        return candidate_anchors
