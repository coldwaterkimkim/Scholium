from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.config import StageName
from app.schemas.document_summary_schema import DocumentSummaryResult
from app.schemas.pass1_schema import Pass1Result
from app.schemas.pass2_schema import Pass2Result
from app.schemas.selection_explanation_schema import SelectionExplanationResult
from app.schemas.selection_follow_up_schema import SelectionFollowUpResult
from app.schemas.semantic_guide_schema import SemanticGuideResult


STAGE_MODEL_REGISTRY = {
    "pass1": Pass1Result,
    "semantic_guide": SemanticGuideResult,
    "document_synthesis": DocumentSummaryResult,
    "pass2": Pass2Result,
    "selection_explanation": SelectionExplanationResult,
    "selection_follow_up": SelectionFollowUpResult,
}


def validate_payload(stage: StageName, payload: dict[str, Any]) -> dict[str, Any]:
    model = STAGE_MODEL_REGISTRY[stage]
    validated = model.model_validate(payload)
    return validated.model_dump(mode="json")


def get_json_schema(stage: StageName) -> dict[str, Any]:
    model = STAGE_MODEL_REGISTRY[stage]
    return _to_strict_json_schema(model.model_json_schema())


def _to_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize Pydantic schema for OpenAI/Codex strict structured outputs."""

    strict_schema = deepcopy(schema)

    def visit(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return

        if not isinstance(node, dict):
            return

        node.pop("default", None)

        properties = node.get("properties")
        if isinstance(properties, dict):
            node["required"] = list(properties.keys())
            node.setdefault("additionalProperties", False)
            for property_schema in properties.values():
                visit(property_schema)

        defs = node.get("$defs")
        if isinstance(defs, dict):
            for definition in defs.values():
                visit(definition)

        for keyword in ("anyOf", "oneOf", "allOf", "prefixItems"):
            visit(node.get(keyword))

        visit(node.get("items"))

    visit(strict_schema)
    return strict_schema
