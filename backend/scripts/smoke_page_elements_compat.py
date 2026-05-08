#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
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
from app.utils.validation import get_json_schema


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


def _ensure_interaction_log_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS interaction_logs (
            event_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            anchor_id TEXT NULL,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
        """
    )


def _insert_interaction_log(storage: StorageService, document_id: str, event_id: str) -> None:
    with storage._connect() as connection:
        _ensure_interaction_log_table(connection)
        connection.execute(
            """
            INSERT INTO interaction_logs (
                event_id,
                document_id,
                page_number,
                anchor_id,
                event_type,
                timestamp
            )
            VALUES (?, ?, 1, NULL, 'selection_created', '2026-05-08T00:00:00Z')
            """,
            (event_id, document_id),
        )


def _interaction_log_count(storage: StorageService, document_id: str) -> int:
    with storage._connect() as connection:
        _ensure_interaction_log_table(connection)
        row = connection.execute(
            "SELECT COUNT(*) FROM interaction_logs WHERE document_id = ?",
            (document_id,),
        ).fetchone()
    return int(row[0])


def _assert_delete_clears_runtime_state_and_logs(storage: StorageService) -> None:
    document = storage.save_uploaded_document("delete-smoke.pdf", b"%PDF-1.4\n%delete smoke\n")
    document_id = document.document_id

    for directory in (
        storage.analysis_dir / document_id,
        storage.parsed_dir / document_id,
        storage.rendered_pages_dir / document_id,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "artifact.json").write_text("{}", encoding="utf-8")
    _insert_interaction_log(storage, document_id, "evt_delete_smoke")

    assert storage.delete_document(document_id)
    assert storage.get_document(document_id) is None
    assert not (storage.raw_pdfs_dir / f"{document_id}.pdf").exists()
    assert not (storage.analysis_dir / document_id).exists()
    assert not (storage.parsed_dir / document_id).exists()
    assert not (storage.rendered_pages_dir / document_id).exists()
    assert _interaction_log_count(storage, document_id) == 0

    orphan_id = "doc_orphan_smoke"
    for path, _owner_dir in storage._runtime_paths_for_document_id(orphan_id):
        if path.suffix == ".pdf":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF-1.4\n%orphan smoke\n")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "artifact.json").write_text("{}", encoding="utf-8")
    _insert_interaction_log(storage, orphan_id, "evt_orphan_smoke")

    summary = storage.prune_orphan_document_state()
    assert orphan_id in summary["removed_runtime_document_ids"]
    assert orphan_id in summary["removed_log_document_ids"]
    assert summary["removed_log_count"] == 1
    assert _interaction_log_count(storage, orphan_id) == 0
    for path, _owner_dir in storage._runtime_paths_for_document_id(orphan_id):
        assert not path.exists()


def _assert_strict_pass1_schema_requires_page_guide_fields() -> None:
    schema = get_json_schema("pass1")
    page_guide_schema = schema["$defs"]["PageGuide"]
    page_guide_properties = set(page_guide_schema["properties"].keys())
    assert set(page_guide_schema["required"]) == page_guide_properties
    assert "default" not in page_guide_schema["properties"]["page_role"]
    assert "default" not in page_guide_schema["properties"]["reading_path"]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="scholium-page-elements-") as raw_tempdir:
        storage = StorageService(settings=_settings_for_tempdir(Path(raw_tempdir)))
        pass1_artifact = _assert_pass1_aliases(storage)
        _assert_public_response_accepts_page_elements(pass1_artifact)
        _assert_selection_context_uses_page_elements(storage, pass1_artifact)
        _assert_delete_clears_runtime_state_and_logs(storage)
        _assert_strict_pass1_schema_requires_page_guide_fields()

    print("page-elements compatibility smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
