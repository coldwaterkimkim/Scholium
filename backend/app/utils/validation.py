from __future__ import annotations

from typing import Any

from app.core.config import StageName
from app.schemas.document_summary_schema import DocumentSummaryResult
from app.schemas.pass1_schema import Pass1Result
from app.schemas.pass2_schema import Pass2Result


STAGE_MODEL_REGISTRY = {
    "pass1": Pass1Result,
    "document_synthesis": DocumentSummaryResult,
    "pass2": Pass2Result,
}


def validate_payload(stage: StageName, payload: dict[str, Any]) -> dict[str, Any]:
    model = STAGE_MODEL_REGISTRY[stage]
    validated = model.model_validate(payload)
    return validated.model_dump(mode="json")


def get_json_schema(stage: StageName) -> dict[str, Any]:
    model = STAGE_MODEL_REGISTRY[stage]
    return model.model_json_schema()
