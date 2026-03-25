from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InteractionEventType(StrEnum):
    PAGE_VIEW = "page_view"
    ANCHOR_CLICK = "anchor_click"
    RELATED_PAGE_JUMP = "related_page_jump"


class InteractionLogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    anchor_id: str | None = None
    event_type: InteractionEventType

    @model_validator(mode="after")
    def validate_anchor_requirement(self) -> "InteractionLogRequest":
        if self.event_type == InteractionEventType.PAGE_VIEW and self.anchor_id is not None:
            raise ValueError("page_view must not include anchor_id.")

        if self.event_type in {
            InteractionEventType.ANCHOR_CLICK,
            InteractionEventType.RELATED_PAGE_JUMP,
        } and not self.anchor_id:
            raise ValueError(f"{self.event_type.value} requires anchor_id.")

        return self


class InteractionLogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    document_id: str
    page_number: int
    anchor_id: str | None = None
    event_type: InteractionEventType
    timestamp: datetime


class InteractionLogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
