from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import (
    AnchorType,
    NormalizedBBox,
    RelatedConceptPage,
    SourceCue,
    StrictModel,
)


ImportanceLevel = Literal["low", "medium", "high"]


class SelectionStudyImportance(StrictModel):
    importance_level: ImportanceLevel
    focus_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_study_importance(cls, data: object) -> object:
        if isinstance(data, str):
            return {
                "importance_level": "medium",
                "focus_type": "background_context",
                "reason": data.strip() or "Useful context for understanding the selected target.",
            }
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        if not normalized.get("importance_level"):
            legacy_level = str(normalized.get("level") or "").strip().lower()
            if legacy_level in {"low", "medium", "high"}:
                normalized["importance_level"] = legacy_level
            else:
                score = normalized.get("score")
                try:
                    numeric_score = int(score)
                except (TypeError, ValueError):
                    numeric_score = 3
                if numeric_score >= 4:
                    normalized["importance_level"] = "high"
                elif numeric_score <= 2:
                    normalized["importance_level"] = "low"
                else:
                    normalized["importance_level"] = "medium"

        if not normalized.get("focus_type"):
            normalized["focus_type"] = "background_context"

        if not normalized.get("reason"):
            normalized["reason"] = "Useful context for understanding the selected target."

        normalized.pop("level", None)
        normalized.pop("score", None)
        return normalized


class SelectionExplanationResult(StrictModel):
    document_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    selection_id: str = Field(min_length=1)
    concept_title: str = Field(min_length=1)
    selected_bbox: NormalizedBBox
    explanation_mode: Literal["selection"] = Field(...)

    study_importance: SelectionStudyImportance
    what_this_is: str = Field(min_length=1)
    what_it_means_here: str = Field(min_length=1)
    omitted_context: str | None = Field(default=None, min_length=1)
    common_confusion: str | None = Field(default=None, min_length=1)
    example_or_application: str | None = Field(default=None, min_length=1)
    related_concepts_and_pages: list[RelatedConceptPage] = Field(default_factory=list, max_length=4)
    source_cues: list[SourceCue] = Field(default_factory=list, max_length=4)

    anchor_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    anchor_type: AnchorType
    bbox: NormalizedBBox
    question: str = Field(min_length=1)
    short_explanation: str = Field(min_length=1)
    long_explanation: str = Field(min_length=1)
    prerequisite: str = Field(min_length=0)
    related_pages: list[Annotated[int, Field(ge=1)]]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_selection_result(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)

        if not normalized.get("concept_title") and normalized.get("label"):
            normalized["concept_title"] = normalized["label"]
        if not normalized.get("label") and normalized.get("concept_title"):
            normalized["label"] = normalized["concept_title"]

        if "selected_bbox" in normalized and "bbox" not in normalized:
            normalized["bbox"] = normalized["selected_bbox"]
        if "bbox" in normalized and "selected_bbox" not in normalized:
            normalized["selected_bbox"] = normalized["bbox"]

        legacy_meaning = normalized.pop("meaning_in_context", None)
        legacy_why = normalized.pop("why_it_matters_here", None)
        legacy_key_detail = normalized.pop("key_concept_detail", None)

        if not normalized.get("what_this_is"):
            normalized["what_this_is"] = (
                cls._clean_text(legacy_key_detail)
                or cls._clean_text(normalized.get("short_explanation"))
                or cls._clean_text(normalized.get("long_explanation"))
                or cls._clean_text(normalized.get("concept_title"))
                or "Selected target"
            )
        if not normalized.get("what_it_means_here"):
            normalized["what_it_means_here"] = (
                cls._clean_text(legacy_meaning)
                or cls._clean_text(normalized.get("long_explanation"))
                or cls._clean_text(normalized.get("short_explanation"))
                or cls._clean_text(normalized.get("what_this_is"))
                or "This is the selected target in the current page context."
            )

        study_importance = normalized.get("study_importance")
        if isinstance(study_importance, dict):
            study_importance = dict(study_importance)
            if not study_importance.get("reason") and legacy_why:
                study_importance["reason"] = legacy_why
            normalized["study_importance"] = study_importance
        elif isinstance(study_importance, str):
            normalized["study_importance"] = {
                "importance_level": "medium",
                "focus_type": "background_context",
                "reason": cls._clean_text(study_importance) or cls._clean_text(legacy_why) or "Useful study context.",
            }
        elif legacy_why:
            normalized["study_importance"] = {
                "importance_level": "medium",
                "focus_type": "background_context",
                "reason": cls._clean_text(legacy_why) or "Useful study context.",
            }

        if not normalized.get("study_importance"):
            normalized["study_importance"] = {
                "importance_level": "medium",
                "focus_type": "background_context",
                "reason": "Useful context for understanding the selected target.",
            }

        for optional_key in ("omitted_context", "common_confusion", "example_or_application"):
            optional_text = cls._clean_text(normalized.get(optional_key))
            normalized[optional_key] = optional_text or None

        if not normalized.get("question"):
            normalized["question"] = f"What does {normalized.get('concept_title', 'this selection')} mean here?"
        if not normalized.get("short_explanation"):
            normalized["short_explanation"] = cls._clean_text(normalized.get("what_this_is")) or "Selected target."
        if not normalized.get("long_explanation"):
            normalized["long_explanation"] = " ".join(
                text
                for text in (
                    cls._clean_text(normalized.get("what_this_is")),
                    cls._clean_text(normalized.get("what_it_means_here")),
                )
                if text
            ) or cls._clean_text(normalized.get("short_explanation")) or "Selected target."
        normalized.setdefault("prerequisite", "")
        normalized.setdefault("related_pages", [])
        normalized.setdefault("related_concepts_and_pages", [])
        normalized.setdefault("source_cues", [])
        normalized.setdefault("confidence", 0.5)
        return normalized

    @staticmethod
    def _clean_text(value: object) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split())
        return text or None

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
