from pydantic import Field

from app.schemas.common import FinalAnchor, PageResultBase


class Pass2Result(PageResultBase):
    final_anchors: list[FinalAnchor] = Field(min_length=3, max_length=5)
    page_risk_note: str = Field(min_length=1)
