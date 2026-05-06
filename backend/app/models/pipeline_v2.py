from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_non_empty_string(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    return normalized


class RecommendedExecution(StrEnum):
    TEXT_FIRST = "text_first"
    MULTIMODAL = "multimodal"
    SELECTIVE_VISUAL_ENRICHMENT = "selective_visual_enrichment"


class SectionCluster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    page_numbers: list[int] = Field(default_factory=list)
    dominant_route_label: str
    cluster_reason: str

    @field_validator("cluster_id")
    @classmethod
    def validate_cluster_id(cls, value: str) -> str:
        return _validate_non_empty_string(value, "cluster_id")

    @field_validator("dominant_route_label")
    @classmethod
    def validate_dominant_route_label(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"text-rich", "scan-like", "visual-rich"}:
            raise ValueError("dominant_route_label must be a valid route label.")
        return normalized

    @field_validator("cluster_reason")
    @classmethod
    def validate_cluster_reason(cls, value: str) -> str:
        return _validate_non_empty_string(value, "cluster_reason")

    @model_validator(mode="after")
    def validate_page_numbers(self) -> "SectionCluster":
        if not self.page_numbers:
            raise ValueError("page_numbers must not be empty.")
        if self.page_numbers != sorted(self.page_numbers):
            raise ValueError("page_numbers must be sorted ascending.")
        if len(set(self.page_numbers)) != len(self.page_numbers):
            raise ValueError("page_numbers must be unique within a section cluster.")
        if self.page_numbers[0] != self.start_page or self.page_numbers[-1] != self.end_page:
            raise ValueError("page_numbers must align with start_page and end_page.")
        return self


class KeyPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    reason: str

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"document_start", "cluster_start", "high_hard_page_score"}:
            raise ValueError("reason must be a supported key page reason.")
        return normalized


class HardPageCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    hard_page_score: int = Field(ge=0, le=100)
    hard_page_reasons: list[str] = Field(default_factory=list)
    recommended_execution: RecommendedExecution


class RoutingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_rich_pages: int = Field(ge=0)
    visual_rich_pages: int = Field(ge=0)
    scan_like_pages: int = Field(ge=0)
    hard_page_count: int = Field(ge=0)


class DocumentSpineMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    generated_at: str
    pipeline_mode: str
    spine_mode: str
    parser_source: str

    @field_validator("schema_version", "generated_at", "parser_source")
    @classmethod
    def validate_non_empty(cls, value: str, info) -> str:
        return _validate_non_empty_string(value, info.field_name)

    @field_validator("pipeline_mode")
    @classmethod
    def validate_pipeline_mode(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"legacy", "hybrid", "v2_spine"}:
            raise ValueError("pipeline_mode must be a supported value.")
        return normalized

    @field_validator("spine_mode")
    @classmethod
    def validate_spine_mode(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"off", "shadow", "active"}:
            raise ValueError("spine_mode must be a supported value.")
        return normalized


class DocumentSpineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    total_pages: int = Field(ge=0)
    section_clusters: list[SectionCluster] = Field(default_factory=list)
    key_pages: list[KeyPage] = Field(default_factory=list)
    hard_page_candidates: list[HardPageCandidate] = Field(default_factory=list)
    routing_summary: RoutingSummary

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _validate_non_empty_string(value, "document_id")


class DocumentSpineArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: DocumentSpineMeta
    result: DocumentSpineResult


class PageRoutingMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    generated_at: str
    pipeline_mode: str
    spine_mode: str

    @field_validator("schema_version", "generated_at")
    @classmethod
    def validate_non_empty(cls, value: str, info) -> str:
        return _validate_non_empty_string(value, info.field_name)

    @field_validator("pipeline_mode")
    @classmethod
    def validate_pipeline_mode(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"legacy", "hybrid", "v2_spine"}:
            raise ValueError("pipeline_mode must be a supported value.")
        return normalized

    @field_validator("spine_mode")
    @classmethod
    def validate_spine_mode(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"off", "shadow", "active"}:
            raise ValueError("spine_mode must be a supported value.")
        return normalized


class PageRoutingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    base_route_label: str
    base_route_reason: str
    hard_page_score: int = Field(ge=0, le=100)
    hard_page_reasons: list[str] = Field(default_factory=list)
    recommended_execution: RecommendedExecution

    @field_validator("base_route_label")
    @classmethod
    def validate_base_route_label(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"text-rich", "scan-like", "visual-rich"}:
            raise ValueError("base_route_label must be a valid route label.")
        return normalized

    @field_validator("base_route_reason")
    @classmethod
    def validate_base_route_reason(cls, value: str) -> str:
        return _validate_non_empty_string(value, "base_route_reason")


class PageRoutingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    pages: list[PageRoutingEntry] = Field(default_factory=list)

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _validate_non_empty_string(value, "document_id")

    @model_validator(mode="after")
    def validate_pages(self) -> "PageRoutingResult":
        page_numbers = [page.page_number for page in self.pages]
        if page_numbers != sorted(page_numbers):
            raise ValueError("page routing pages must be sorted ascending.")
        if len(set(page_numbers)) != len(page_numbers):
            raise ValueError("page routing pages must be unique.")
        return self


class PageRoutingArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: PageRoutingMeta
    result: PageRoutingResult
