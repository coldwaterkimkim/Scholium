from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, conlist, field_validator, model_validator


NormalizedFloat = Annotated[float, Field(ge=0.0, le=1.0)]
NormalizedBBox = conlist(NormalizedFloat, min_length=4, max_length=4)
AnchorType = Literal["text", "formula", "chart", "table", "diagram", "image", "flow", "other"]
StudyImportanceLevel = Literal["low", "medium", "high"]
SourceCueType = Literal[
    "this_slide",
    "caption",
    "related_page",
    "transcript",
    "document_context",
    "other",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageResultBase(StrictModel):
    document_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    page_role: str = Field(min_length=1)
    page_summary: str = Field(min_length=1)


class PageGuide(StrictModel):
    page_role: str | None = Field(default=None, min_length=1)
    previous_slide_connection: str | None = Field(default=None, min_length=1)
    one_line_thesis: str | None = Field(default=None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_page_guide(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        connection = normalized.get("before_next_connection")
        if isinstance(connection, dict) and not normalized.get("previous_slide_connection"):
            normalized["previous_slide_connection"] = connection.get("previous")

        for legacy_key in (
            "document_id",
            "page_number",
            "key_question",
            "reading_path",
            "logic_flow",
            "key_concepts",
            "omitted_context",
            "study_focus",
            "common_confusions",
            "example_or_application",
            "must_remember",
            "self_check_questions",
            "before_next_connection",
            "next_slide_connection",
            "wrap_up",
        ):
            normalized.pop(legacy_key, None)
        return normalized

    @field_validator(
        "page_role",
        "previous_slide_connection",
        "one_line_thesis",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class PageWrapUp(StrictModel):
    logic_flow: list[str] = Field(default_factory=list, max_length=4)
    study_focus: str | None = Field(default=None, min_length=1)
    must_remember: list[str] = Field(default_factory=list, max_length=3)
    next_slide_connection: str | None = Field(default=None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_wrap_up(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        connection = normalized.get("before_next_connection")
        if isinstance(connection, dict) and not normalized.get("next_slide_connection"):
            normalized["next_slide_connection"] = connection.get("next")

        for legacy_key in (
            "document_id",
            "page_number",
            "page_role",
            "previous_slide_connection",
            "one_line_thesis",
            "key_question",
            "reading_path",
            "key_concepts",
            "omitted_context",
            "common_confusions",
            "example_or_application",
            "self_check_questions",
            "before_next_connection",
            "page_guide",
        ):
            normalized.pop(legacy_key, None)
        return normalized

    @field_validator("study_focus", "next_slide_connection", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, list):
            joined = " ".join(str(item).strip() for item in value if str(item).strip())
            return joined or None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("logic_flow", "must_remember", mode="before")
    @classmethod
    def normalize_text_list(cls, value: object, info: ValidationInfo) -> object:
        max_items = 3 if info.field_name == "must_remember" else 4
        if value is None:
            return []
        if isinstance(value, str):
            normalized = value.strip()
            return [normalized] if normalized else []
        if isinstance(value, list):
            normalized_items = []
            for item in value:
                if not isinstance(item, str):
                    normalized_items.append(item)
                    continue
                normalized = item.strip()
                if normalized:
                    normalized_items.append(normalized)
            return normalized_items[:max_items]
        return value


class CandidateAnchor(StrictModel):
    anchor_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    anchor_type: AnchorType
    bbox: NormalizedBBox
    question: str = Field(min_length=1)
    short_explanation: str = Field(min_length=1)
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class PageElement(StrictModel):
    element_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    element_type: AnchorType
    bbox: NormalizedBBox
    question: str = Field(min_length=1)
    short_explanation: str = Field(min_length=1)
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    anchor_id: str = Field(min_length=1)
    anchor_type: AnchorType

    @model_validator(mode="before")
    @classmethod
    def fill_legacy_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        if not normalized.get("element_id") and normalized.get("anchor_id"):
            normalized["element_id"] = normalized["anchor_id"]
        if not normalized.get("element_id") and normalized.get("region_id"):
            normalized["element_id"] = normalized["region_id"]
        if not normalized.get("anchor_id") and normalized.get("element_id"):
            normalized["anchor_id"] = normalized["element_id"]
        if not normalized.get("element_type") and normalized.get("anchor_type"):
            normalized["element_type"] = normalized["anchor_type"]
        if not normalized.get("element_type") and normalized.get("region_type"):
            normalized["element_type"] = normalized["region_type"]
        if not normalized.get("anchor_type") and normalized.get("element_type"):
            normalized["anchor_type"] = normalized["element_type"]
        normalized.pop("region_id", None)
        normalized.pop("region_type", None)
        return normalized


class CandidateRegion(PageElement):
    pass


class StudyImportance(StrictModel):
    level: StudyImportanceLevel
    score: Annotated[int, Field(ge=1, le=5)]
    reason: str | None = Field(...)


class RelatedConceptPage(StrictModel):
    concept: str = Field(min_length=1)
    page_number: int | None = Field(..., ge=1)
    relation_reason: str = Field(min_length=1)


class SourceCue(StrictModel):
    source_type: SourceCueType
    label: str = Field(min_length=1)
    page_number: int | None = Field(..., ge=1)
    snippet: str | None = Field(...)


class FinalAnchor(CandidateAnchor):
    long_explanation: str = Field(min_length=1)
    prerequisite: str = Field(min_length=0)
    related_pages: list[int]
    study_importance: StudyImportance | None = Field(...)
    meaning_in_context: str | None = Field(..., min_length=1)
    why_it_matters_here: str | None = Field(..., min_length=1)
    related_concepts_and_pages: list[RelatedConceptPage] | None = Field(..., max_length=4)
    source_cues: list[SourceCue] | None = Field(..., max_length=4)


class LegacyPrecomputedAnchor(FinalAnchor):
    pass


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
