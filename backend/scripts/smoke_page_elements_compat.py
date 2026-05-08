#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings
from app.models.read_api import PagePublicResponse
from app.services.selection_context_builder import SelectionContextBuilder
from app.services.storage import StorageService


def _settings_for_tempdir(tempdir: Path):
    settings = get_settings()
    return replace(
        settings,
        document_db_path=str(tempdir / "scholium.sqlite3"),
        raw_pdfs_dir=str(tempdir / "raw_pdfs"),
        rendered_pages_dir=str(tempdir / "rendered_pages"),
        analysis_dir=str(tempdir / "analysis"),
        logs_dir=str(tempdir / "logs"),
    )


def _base_envelope(result: dict[str, object]) -> dict[str, object]:
    return {
        "meta": {
            "schema_version": "0.2",
            "prompt_version": "pass1_v0_1",
            "model_name": "compat-smoke",
            "generated_at": "2026-05-07T00:00:00Z",
        },
        "result": {
            "document_id": "doc_compat",
            "page_number": 1,
            "page_role": "Concept page",
            "page_summary": "A compact page summary.",
            **result,
        },
    }


def _assert_pass1_aliases(storage: StorageService) -> dict[str, object]:
    legacy_candidate_payload = _base_envelope(
        {
            "candidate_anchors": [
                {
                    "anchor_id": "legacy_anchor_1",
                    "label": "Legacy stored element",
                    "anchor_type": "text",
                    "bbox": [0.05, 0.08, 0.3, 0.08],
                    "question": "What did this old artifact mean?",
                    "short_explanation": "It is an old candidate_anchors entry.",
                    "confidence": 0.7,
                }
            ]
        }
    )
    normalized_from_legacy = storage._normalize_pass1_artifact(
        "doc_compat",
        1,
        legacy_candidate_payload,
    )
    legacy_result = normalized_from_legacy["result"]
    assert legacy_result["candidate_anchors"][0]["anchor_id"] == "legacy_anchor_1"
    assert legacy_result["page_elements"][0]["element_id"] == "legacy_anchor_1"
    assert legacy_result["page_guide"]["page_role"] == "Concept page"
    assert legacy_result["page_guide"]["one_line_thesis"] == "A compact page summary."

    page_elements_payload = _base_envelope(
        {
            "page_guide": {
                "page_role": "Concept bridge",
                "one_line_thesis": "The page connects a diagram to the core idea.",
                "key_question": "How should this diagram be read?",
                "reading_path": ["Start from the title", "Follow the diagram labels"],
                "logic_flow": ["Claim", "Diagram evidence", "Takeaway"],
                "key_concepts": [
                    {
                        "concept": "Band gap",
                        "brief_description": "A compact concept description.",
                        "role_on_page": "Connects the diagram to the page thesis.",
                    }
                ],
                "omitted_context": ["Prior definition is assumed."],
                "study_focus": ["Diagram-to-concept mapping."],
                "common_confusions": ["Do not confuse axis labels with the measured quantity."],
                "example_or_application": "Use the diagram to explain a material comparison.",
                "must_remember": ["Band gap frames the page interpretation."],
                "self_check_questions": ["Can you explain why the diagram matters?"],
                "before_next_connection": {"previous": None, "next": None},
            },
            "page_elements": [
                {
                    "element_id": "element_1",
                    "label": "Band gap",
                    "element_type": "diagram",
                    "bbox": [0.1, 0.2, 0.3, 0.2],
                    "question": "What does this region mean?",
                    "short_explanation": "It marks the key visual region.",
                    "confidence": 0.9,
                }
            ]
        }
    )
    normalized_from_page_elements = storage._normalize_pass1_artifact(
        "doc_compat",
        1,
        page_elements_payload,
    )
    result = normalized_from_page_elements["result"]
    assert result["candidate_anchors"][0]["anchor_id"] == "element_1"
    assert result["candidate_anchors"][0]["anchor_type"] == "diagram"
    assert result["page_elements"][0]["element_id"] == "element_1"
    assert result["page_elements"][0]["element_type"] == "diagram"
    assert result["page_guide"]["key_question"] == "How should this diagram be read?"

    candidate_regions_payload = _base_envelope(
        {
            "candidate_regions": [
                {
                    "region_id": "region_1",
                    "label": "Formula region",
                    "region_type": "formula",
                    "bbox": [0.2, 0.3, 0.2, 0.1],
                    "question": "Why is this formula here?",
                    "short_explanation": "It summarizes the local relation.",
                    "confidence": 0.8,
                }
            ]
        }
    )
    normalized_from_regions = storage._normalize_pass1_artifact(
        "doc_compat",
        1,
        candidate_regions_payload,
    )
    region_result = normalized_from_regions["result"]
    assert region_result["candidate_anchors"][0]["anchor_id"] == "region_1"
    assert region_result["page_elements"][0]["element_id"] == "region_1"
    return normalized_from_page_elements


def _assert_public_response_accepts_page_elements(pass1_artifact: dict[str, object]) -> None:
    result = pass1_artifact["result"]
    response = PagePublicResponse(
        document_id="doc_compat",
        page_number=1,
        image_url="http://example.test/page.png",
        page_role=result["page_role"],
        page_summary=result["page_summary"],
        final_anchors=[],
        page_elements=result["page_elements"],
        page_guide=result["page_guide"],
        page_risk_note="Ready for selected-region explanations.",
        viewer_mode="on_demand",
    )
    assert response.page_elements[0].element_id == "element_1"
    assert response.page_elements[0].anchor_id == "element_1"
    assert response.page_guide is not None
    assert response.page_guide.key_question == "How should this diagram be read?"


def _assert_selection_context_uses_page_elements(
    storage: StorageService,
    pass1_artifact: dict[str, object],
) -> None:
    context = SelectionContextBuilder(storage=storage).build(
        document_id="doc_compat",
        page_number=1,
        selected_bbox=[0.12, 0.22, 0.12, 0.12],
        pass1_artifact=pass1_artifact,
        document_summary_artifact=None,
    )
    assert context["matched_page_elements"][0]["element_id"] == "element_1"
    assert context["matched_page_elements"][0]["element_type"] == "diagram"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="scholium-page-elements-") as raw_tempdir:
        storage = StorageService(settings=_settings_for_tempdir(Path(raw_tempdir)))
        pass1_artifact = _assert_pass1_aliases(storage)
        _assert_public_response_accepts_page_elements(pass1_artifact)
        _assert_selection_context_uses_page_elements(storage, pass1_artifact)

    print("page-elements compatibility smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
