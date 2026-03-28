from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ParseBlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    OTHER = "other"


class PageRouteLabel(StrEnum):
    TEXT_RICH = "text-rich"
    SCAN_LIKE = "scan-like"
    VISUAL_RICH = "visual-rich"


def _validate_non_empty_string(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    return normalized


def _validate_dimension(value: float, field_name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be a finite positive number.")
    return float(value)


def _validate_bbox(value: list[float]) -> list[float]:
    if len(value) != 4:
        raise ValueError("bbox must contain exactly four normalized values: [x, y, w, h].")

    normalized = [float(component) for component in value]
    x, y, w, h = normalized
    if any(not math.isfinite(component) for component in normalized):
        raise ValueError("bbox values must be finite numbers.")
    if any(component < 0 or component > 1 for component in normalized):
        raise ValueError("bbox values must be between 0 and 1.")
    if x + w > 1:
        raise ValueError("bbox x + w must be <= 1.")
    if y + h > 1:
        raise ValueError("bbox y + h must be <= 1.")
    return normalized


class ParseBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    block_type: ParseBlockType
    text: str
    bbox: list[float]
    reading_order: int = Field(ge=0)

    @field_validator("block_id")
    @classmethod
    def validate_block_id(cls, value: str) -> str:
        return _validate_non_empty_string(value, "block_id")

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float]) -> list[float]:
        return _validate_bbox(value)


class ParsedPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    width: float
    height: float
    ocr_used: bool = False
    blocks: list[ParseBlock] = Field(default_factory=list)

    @field_validator("width")
    @classmethod
    def validate_width(cls, value: float) -> float:
        return _validate_dimension(value, "width")

    @field_validator("height")
    @classmethod
    def validate_height(cls, value: float) -> float:
        return _validate_dimension(value, "height")

    @model_validator(mode="after")
    def validate_page_blocks(self) -> "ParsedPage":
        seen_block_ids: set[str] = set()
        seen_reading_orders: set[int] = set()
        for block in self.blocks:
            if block.block_id in seen_block_ids:
                raise ValueError(f"Duplicate block_id found within page {self.page_number}: {block.block_id}")
            if block.reading_order in seen_reading_orders:
                raise ValueError(
                    f"Duplicate reading_order found within page {self.page_number}: {block.reading_order}"
                )
            seen_block_ids.add(block.block_id)
            seen_reading_orders.add(block.reading_order)
        return self


class PageParseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    parser_source: str
    schema_version: str
    page_number: int = Field(ge=1)
    width: float
    height: float
    ocr_used: bool = False
    blocks: list[ParseBlock] = Field(default_factory=list)

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _validate_non_empty_string(value, "document_id")

    @field_validator("parser_source")
    @classmethod
    def validate_parser_source(cls, value: str) -> str:
        return _validate_non_empty_string(value, "parser_source")

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        return _validate_non_empty_string(value, "schema_version")

    @field_validator("width")
    @classmethod
    def validate_width(cls, value: float) -> float:
        return _validate_dimension(value, "width")

    @field_validator("height")
    @classmethod
    def validate_height(cls, value: float) -> float:
        return _validate_dimension(value, "height")

    @model_validator(mode="after")
    def validate_page_blocks(self) -> "PageParseArtifact":
        ParsedPage(
            page_number=self.page_number,
            width=self.width,
            height=self.height,
            ocr_used=self.ocr_used,
            blocks=self.blocks,
        )
        return self


class DocumentParseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    parser_source: str
    schema_version: str
    pages: list[ParsedPage] = Field(default_factory=list)

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _validate_non_empty_string(value, "document_id")

    @field_validator("parser_source")
    @classmethod
    def validate_parser_source(cls, value: str) -> str:
        return _validate_non_empty_string(value, "parser_source")

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        return _validate_non_empty_string(value, "schema_version")

    @model_validator(mode="after")
    def validate_document_structure(self) -> "DocumentParseArtifact":
        seen_page_numbers: set[int] = set()
        seen_block_ids: set[str] = set()
        for page in self.pages:
            if page.page_number in seen_page_numbers:
                raise ValueError(f"Duplicate page_number found: {page.page_number}")
            seen_page_numbers.add(page.page_number)

            for block in page.blocks:
                if block.block_id in seen_block_ids:
                    raise ValueError(f"Duplicate block_id found within document: {block.block_id}")
                seen_block_ids.add(block.block_id)
        return self


class PageManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    route_label: PageRouteLabel
    route_reason: str
    text_length: int = Field(ge=0)
    block_count: int = Field(ge=0)
    non_empty_text_block_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    has_table: bool
    has_figure: bool
    ocr_used: bool = False

    @field_validator("route_reason")
    @classmethod
    def validate_route_reason(cls, value: str) -> str:
        return _validate_non_empty_string(value, "route_reason")


class DocumentPageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    parser_source: str
    schema_version: str
    pages: list[PageManifestEntry] = Field(default_factory=list)

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _validate_non_empty_string(value, "document_id")

    @field_validator("parser_source")
    @classmethod
    def validate_parser_source(cls, value: str) -> str:
        return _validate_non_empty_string(value, "parser_source")

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        return _validate_non_empty_string(value, "schema_version")

    @model_validator(mode="after")
    def validate_manifest_pages(self) -> "DocumentPageManifest":
        seen_page_numbers: set[int] = set()
        for page in self.pages:
            if page.page_number in seen_page_numbers:
                raise ValueError(f"Duplicate page_number found in page manifest: {page.page_number}")
            seen_page_numbers.add(page.page_number)
        return self
