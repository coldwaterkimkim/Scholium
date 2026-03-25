from pydantic import Field

from app.schemas.common import DocumentSection, KeyConcept, PrerequisiteLink, StrictModel


class DocumentSummaryResult(StrictModel):
    document_id: str
    overall_topic: str = Field(min_length=1)
    overall_summary: str = Field(min_length=1)
    sections: list[DocumentSection]
    key_concepts: list[KeyConcept]
    difficult_pages: list[int]
    prerequisite_links: list[PrerequisiteLink]
