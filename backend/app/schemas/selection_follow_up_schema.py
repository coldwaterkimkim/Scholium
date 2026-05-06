from __future__ import annotations

from typing import Annotated

from pydantic import Field

from app.schemas.common import SourceCue, StrictModel


class SelectionFollowUpResult(StrictModel):
    answer: str = Field(min_length=1)
    source_cues: list[SourceCue] = Field(max_length=4)
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None
