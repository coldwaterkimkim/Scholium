#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.pass2_artifact_builder import Pass2ArtifactBuilder
from app.services.storage import StorageService, get_storage_service
from export_routing_audit import (
    _active_and_baseline_run_keys,
    _comparison_run_map,
    _load_json,
    _load_manifest,
    _normalize_relpath,
    _resolve_existing_file,
    _resolve_output_prefix,
    _suspected_false_positive_reason,
)


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "perf_runs"
_RULE_A_KEY = "rule_a"
_LLM_MODE = "llm"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a manual QA pack for rule_a recovered pages.",
    )
    parser.add_argument(
        "--routing-rule-tiebreak-json",
        required=True,
        help="Routing rule tie-break JSON path.",
    )
    parser.add_argument("--comparison-json", required=True, help="Comparison JSON path.")
    parser.add_argument("--corpus-manifest", required=True, help="Corpus manifest JSON path.")
    parser.add_argument(
        "--output-prefix",
        required=True,
        help=(
            "Output prefix path without extension, for example "
            "docs/perf_runs/20260329T000000Z_rule_a_recovered_pages_qa."
        ),
    )
    return parser


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _resolve_document_record(storage: StorageService, document_id: str) -> Any | None:
    return storage.get_document(document_id)


def _resolve_page_record(storage: StorageService, document_id: str, page_number: int) -> Any | None:
    return storage.get_page(document_id, page_number)


def _load_routing_by_page(storage: StorageService, document_id: str) -> dict[int, dict[str, Any]]:
    artifact = storage.load_page_routing(document_id)
    if artifact is None:
        return {}
    return {
        int(page["page_number"]): dict(page)
        for page in artifact["result"].get("pages", [])
        if isinstance(page, dict) and page.get("page_number") is not None
    }


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _dedupe_ints(values: list[int]) -> list[int]:
    deduped: list[int] = []
    for value in values:
        normalized = int(value)
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _current_llm_fields(pass2_artifact: dict[str, Any] | None) -> dict[str, Any]:
    if not pass2_artifact or str(pass2_artifact.get("meta", {}).get("pass2_generation_mode")) != _LLM_MODE:
        return {
            "current_llm_artifact_missing": True,
            "current_llm_page_role": None,
            "current_llm_page_summary": None,
            "current_llm_anchor_labels": [],
            "current_llm_long_explanations": [],
            "current_llm_related_pages": [],
            "current_llm_prerequisites": [],
        }

    final_anchors = list(pass2_artifact["result"].get("final_anchors", []))
    related_pages: list[int] = []
    prerequisites: list[str] = []
    for anchor in final_anchors:
        related_pages.extend(int(page) for page in anchor.get("related_pages", []))
        prerequisite = str(anchor.get("prerequisite") or "").strip()
        if prerequisite:
            prerequisites.append(prerequisite)
    return {
        "current_llm_artifact_missing": False,
        "current_llm_page_role": pass2_artifact["result"].get("page_role"),
        "current_llm_page_summary": pass2_artifact["result"].get("page_summary"),
        "current_llm_anchor_labels": [anchor.get("label") for anchor in final_anchors],
        "current_llm_long_explanations": [anchor.get("long_explanation") for anchor in final_anchors],
        "current_llm_related_pages": sorted(_dedupe_ints(related_pages)),
        "current_llm_prerequisites": _dedupe_strings(prerequisites),
    }


def _preview_compat_fields(
    *,
    storage: StorageService,
    artifact_builder: Pass2ArtifactBuilder,
    document_id: str,
    page_number: int,
    pass1_artifact: dict[str, Any] | None,
    document_summary: dict[str, Any] | None,
    routing_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    if pass1_artifact is None:
        return {
            "preview_compat_anchor_labels": [],
            "preview_compat_long_explanations": [],
            "preview_compat_related_pages": [],
            "preview_compat_prerequisite": None,
            "preview_compat_prerequisites_all": [],
            "preview_compat_page_role": None,
            "preview_compat_page_summary": None,
            "preview_compat_generation_mode": "compat_preview",
            "preview_compat_meta_generation_mode": None,
            "preview_error": "Pass1 artifact missing.",
        }
    if document_summary is None:
        return {
            "preview_compat_anchor_labels": [],
            "preview_compat_long_explanations": [],
            "preview_compat_related_pages": [],
            "preview_compat_prerequisite": None,
            "preview_compat_prerequisites_all": [],
            "preview_compat_page_role": None,
            "preview_compat_page_summary": None,
            "preview_compat_generation_mode": "compat_preview",
            "preview_compat_meta_generation_mode": None,
            "preview_error": "Document summary artifact missing.",
        }

    try:
        preview_envelope = artifact_builder.build_compat_envelope(
            document_id=document_id,
            page_number=page_number,
            pass1_result=pass1_artifact["result"],
            pass1_meta=pass1_artifact.get("meta"),
            document_summary_result=document_summary["result"],
            planner_reason=None,
            page_routing_entry=routing_entry,
        )
        normalized_preview = storage._normalize_pass2_artifact(document_id, page_number, preview_envelope)
        final_anchors = list(normalized_preview["result"].get("final_anchors", []))
        related_pages: list[int] = []
        prerequisites: list[str] = []
        for anchor in final_anchors:
            related_pages.extend(int(page) for page in anchor.get("related_pages", []))
            prerequisite = str(anchor.get("prerequisite") or "").strip()
            if prerequisite:
                prerequisites.append(prerequisite)
        deduped_prerequisites = _dedupe_strings(prerequisites)
        return {
            "preview_compat_anchor_labels": [anchor.get("label") for anchor in final_anchors],
            "preview_compat_long_explanations": [
                anchor.get("long_explanation") for anchor in final_anchors
            ],
            "preview_compat_related_pages": sorted(_dedupe_ints(related_pages)),
            "preview_compat_prerequisite": deduped_prerequisites[0] if deduped_prerequisites else None,
            "preview_compat_prerequisites_all": deduped_prerequisites,
            "preview_compat_page_role": normalized_preview["result"].get("page_role"),
            "preview_compat_page_summary": normalized_preview["result"].get("page_summary"),
            "preview_compat_generation_mode": "compat_preview",
            "preview_compat_meta_generation_mode": normalized_preview["meta"].get("pass2_generation_mode"),
            "preview_error": None,
        }
    except Exception as exc:
        return {
            "preview_compat_anchor_labels": [],
            "preview_compat_long_explanations": [],
            "preview_compat_related_pages": [],
            "preview_compat_prerequisite": None,
            "preview_compat_prerequisites_all": [],
            "preview_compat_page_role": None,
            "preview_compat_page_summary": None,
            "preview_compat_generation_mode": "compat_preview",
            "preview_compat_meta_generation_mode": None,
            "preview_error": " ".join(str(exc).split())[:220],
        }


def _comparison_doc_map(comparison_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    documents = comparison_payload.get("documents")
    if not isinstance(documents, list):
        raise SystemExit("Comparison payload documents must be a list.")
    result: dict[str, dict[str, Any]] = {}
    for document in documents:
        if not isinstance(document, dict):
            continue
        relpath = _normalize_relpath(document.get("source_pdf_relpath"))
        if relpath:
            result[relpath] = dict(document)
    return result


def _load_expected_types(manifest_map: dict[str, dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relpath, entry in manifest_map.items():
        expected_type = entry.get("expected_type")
        if isinstance(expected_type, str) and expected_type.strip():
            result[relpath] = expected_type.strip()
            result[Path(relpath).name] = expected_type.strip()
    return result


def _validate_sources(
    *,
    tiebreak_payload: dict[str, Any],
    comparison_path: Path,
    manifest_path: Path,
) -> Path:
    if tiebreak_payload.get("summary", {}).get("recommended_first_rule") != _RULE_A_KEY:
        raise SystemExit("routing_rule_tiebreak.json recommended_first_rule must be rule_a.")
    recovered_rows = tiebreak_payload.get("rules", {}).get(_RULE_A_KEY, {}).get("recovered_page_rows", [])
    if not isinstance(recovered_rows, list) or not recovered_rows:
        raise SystemExit("routing_rule_tiebreak.json rule_a.recovered_page_rows must not be empty.")

    source_artifacts = tiebreak_payload.get("source_artifacts") or {}
    source_comparison = Path(str(source_artifacts.get("comparison_json") or "")).expanduser().resolve()
    source_manifest = Path(str(source_artifacts.get("corpus_manifest") or "")).expanduser().resolve()
    if source_comparison != comparison_path:
        raise SystemExit("routing_rule_tiebreak source comparison_json does not match --comparison-json.")
    if source_manifest != manifest_path:
        raise SystemExit("routing_rule_tiebreak source corpus_manifest does not match --corpus-manifest.")

    routing_audit_path = Path(str(source_artifacts.get("routing_audit_json") or "")).expanduser().resolve()
    if not routing_audit_path.exists() or not routing_audit_path.is_file():
        raise SystemExit("routing_rule_tiebreak source routing_audit_json is missing.")
    return routing_audit_path


def _build_row(
    *,
    storage: StorageService,
    artifact_builder: Pass2ArtifactBuilder,
    recovered_row: dict[str, Any],
    expected_types: dict[str, str],
    active_run_key: str,
    comparison_doc_map: dict[str, dict[str, Any]],
    manifest_relpaths: set[str],
    routing_by_page: dict[int, dict[str, Any]],
    benchmark_llm_pages: set[int],
    routing_audit_reason_map: dict[tuple[str, int], str | None],
) -> dict[str, Any]:
    document_id = str(recovered_row["document_id"])
    page_number = int(recovered_row["page_number"])
    source_pdf_relpath = _normalize_relpath(recovered_row.get("source_pdf_relpath"))
    if source_pdf_relpath is None:
        raise SystemExit(f"Recovered row missing source_pdf_relpath: {document_id} page {page_number}")
    if str(recovered_row.get("current_effective_path")) != "llm":
        raise SystemExit(
            f"Recovered row must remain on llm path in current state: {document_id} page {page_number}"
        )

    comparison_document = comparison_doc_map.get(source_pdf_relpath)
    if comparison_document is None:
        raise SystemExit(f"Recovered row not found in comparison JSON: {source_pdf_relpath}")
    if source_pdf_relpath not in manifest_relpaths:
        raise SystemExit(f"Recovered row not found in corpus manifest: {source_pdf_relpath}")
    active_run = comparison_document.get("runs", {}).get(active_run_key)
    if not isinstance(active_run, dict) or str(active_run.get("document_id")) != document_id:
        raise SystemExit(
            f"Recovered row document_id mismatch against comparison active run: {source_pdf_relpath}"
        )

    pass1_artifact = storage.load_pass1_result(document_id, page_number)
    document_summary = storage.load_document_summary(document_id)
    current_pass2_artifact = storage.load_pass2_result(document_id, page_number)
    page_record = _resolve_page_record(storage, document_id, page_number)
    document_record = _resolve_document_record(storage, document_id)
    routing_entry = routing_by_page.get(page_number)

    if routing_entry is None:
        raise SystemExit(f"Page routing entry missing for {document_id} page {page_number}")

    current_fields = _current_llm_fields(current_pass2_artifact)
    preview_fields = _preview_compat_fields(
        storage=storage,
        artifact_builder=artifact_builder,
        document_id=document_id,
        page_number=page_number,
        pass1_artifact=pass1_artifact,
        document_summary=document_summary,
        routing_entry=routing_entry,
    )

    shared_page_role = pass1_artifact["result"].get("page_role") if pass1_artifact else None
    shared_page_summary = pass1_artifact["result"].get("page_summary") if pass1_artifact else None
    candidate_anchor_count = len(pass1_artifact["result"].get("candidate_anchors", [])) if pass1_artifact else 0

    heuristic_row = {
        "page_number": page_number,
        "route_label": recovered_row.get("route_label"),
        "recommended_execution": recovered_row.get("recommended_execution"),
        "pass1_path": recovered_row.get("pass1_path"),
        "has_table": bool(recovered_row.get("has_table")),
        "hard_page_reasons": list(recovered_row.get("hard_page_reasons") or []),
        "page_summary": shared_page_summary,
        "text_first_likely": bool(recovered_row.get("text_first_likely")),
    }
    suspected_false_positive_reason = _suspected_false_positive_reason(
        heuristic_row,
        benchmark_llm_pages,
    )
    if suspected_false_positive_reason is None:
        suspected_false_positive_reason = routing_audit_reason_map.get((document_id, page_number))

    current_llm_artifact_missing = bool(current_fields["current_llm_artifact_missing"])
    path_consistency = str(recovered_row.get("current_effective_path_consistency") or "unknown")
    skipped_from_primary_review = (
        path_consistency != "consistent"
        or current_llm_artifact_missing
    )
    skipped_reasons: list[str] = []
    if path_consistency != "consistent":
        skipped_reasons.append(f"path_consistency={path_consistency}")
    if current_llm_artifact_missing:
        skipped_reasons.append("current_llm_artifact_missing")

    row = {
        "document_id": document_id,
        "source_pdf_relpath": source_pdf_relpath,
        "stored_pdf_relpath": _normalize_relpath(document_record.original_path if document_record else None),
        "page_number": page_number,
        "page_image_relpath": _normalize_relpath(page_record.image_path if page_record else None)
        or recovered_row.get("page_image_relpath"),
        "expected_type": expected_types.get(source_pdf_relpath or "")
        or recovered_row.get("expected_type"),
        "current_effective_path": recovered_row.get("current_effective_path"),
        "current_effective_path_source": recovered_row.get("current_effective_path_source"),
        "current_effective_path_consistency": path_consistency,
        "path_consistency_warning": path_consistency != "consistent",
        "current_pass2_generation_mode": recovered_row.get("current_pass2_generation_mode"),
        "route_label": recovered_row.get("route_label"),
        "route_reason": routing_entry.get("base_route_reason"),
        "hard_page_score": recovered_row.get("hard_page_score"),
        "hard_page_reasons": list(recovered_row.get("hard_page_reasons") or []),
        "recommended_execution": recovered_row.get("recommended_execution"),
        "pass1_path": recovered_row.get("pass1_path"),
        "candidate_anchor_count": candidate_anchor_count,
        "page_role": shared_page_role,
        "page_summary": shared_page_summary,
        "current_llm_page_role": current_fields["current_llm_page_role"],
        "current_llm_page_summary": current_fields["current_llm_page_summary"],
        "current_llm_anchor_labels": current_fields["current_llm_anchor_labels"],
        "current_llm_long_explanations": current_fields["current_llm_long_explanations"],
        "current_llm_related_pages": current_fields["current_llm_related_pages"],
        "current_llm_prerequisites": current_fields["current_llm_prerequisites"],
        "preview_compat_anchor_labels": preview_fields["preview_compat_anchor_labels"],
        "preview_compat_long_explanations": preview_fields["preview_compat_long_explanations"],
        "preview_compat_related_pages": preview_fields["preview_compat_related_pages"],
        "preview_compat_prerequisite": preview_fields["preview_compat_prerequisite"],
        "preview_compat_prerequisites_all": preview_fields["preview_compat_prerequisites_all"],
        "preview_compat_page_role": preview_fields["preview_compat_page_role"],
        "preview_compat_page_summary": preview_fields["preview_compat_page_summary"],
        "preview_compat_generation_mode": preview_fields["preview_compat_generation_mode"],
        "preview_compat_meta_generation_mode": preview_fields["preview_compat_meta_generation_mode"],
        "preview_error": preview_fields["preview_error"],
        "suspected_false_positive_reason": suspected_false_positive_reason,
        "visual_risk_signal_present": bool(recovered_row.get("has_visual_risk_signal")),
        "visual_risk_reasons": list(recovered_row.get("visual_risk_reasons") or []),
        "spine_hard_candidate": bool(recovered_row.get("is_spine_hard_candidate")),
        "skipped_from_primary_review": skipped_from_primary_review,
        "skip_reasons": skipped_reasons,
        "qa_status": "",
        "qa_notes": "",
    }
    return row


def _routing_audit_reason_map(routing_audit_payload: dict[str, Any]) -> dict[tuple[str, int], str | None]:
    result: dict[tuple[str, int], str | None] = {}
    for document in routing_audit_payload.get("documents", []):
        if not isinstance(document, dict):
            continue
        document_id = str(document.get("document_id") or "")
        for page in document.get("pages", []):
            if not isinstance(page, dict):
                continue
            page_number = int(page["page_number"])
            result[(document_id, page_number)] = page.get("suspected_false_positive_reason")
    return result


def _document_sort_key(document: dict[str, Any]) -> tuple[str, str]:
    return str(document["source_pdf_relpath"]), str(document["document_id"])


def _build_payload(
    *,
    storage: StorageService,
    artifact_builder: Pass2ArtifactBuilder,
    tiebreak_path: Path,
    routing_audit_path: Path,
    comparison_path: Path,
    manifest_path: Path,
    tiebreak_payload: dict[str, Any],
    comparison_payload: dict[str, Any],
    manifest_map: dict[str, dict[str, Any]],
    routing_audit_payload: dict[str, Any],
) -> dict[str, Any]:
    expected_types = _load_expected_types(manifest_map)
    run_map = _comparison_run_map(comparison_payload)
    active_run_key, _ = _active_and_baseline_run_keys(run_map)
    comparison_doc_map = _comparison_doc_map(comparison_payload)
    manifest_relpaths = {_normalize_relpath(relpath) for relpath in manifest_map.keys()}
    manifest_relpaths.discard(None)
    recovered_rows = list(tiebreak_payload["rules"][_RULE_A_KEY]["recovered_page_rows"])

    rows_by_document: dict[str, list[dict[str, Any]]] = {}
    routing_by_page_cache: dict[str, dict[int, dict[str, Any]]] = {}
    benchmark_llm_pages_cache: dict[str, set[int]] = {}
    audit_reason_map = _routing_audit_reason_map(routing_audit_payload)

    for recovered_row in recovered_rows:
        document_id = str(recovered_row["document_id"])
        if document_id not in routing_by_page_cache:
            routing_by_page_cache[document_id] = _load_routing_by_page(storage, document_id)
            processing_benchmark = _load_json(
                PROJECT_ROOT / "data" / "analysis" / document_id / "processing_benchmark.json"
            )
            benchmark_llm_pages_cache[document_id] = {
                int(page) for page in processing_benchmark.get("pass2_llm_pages", [])
            }

        row = _build_row(
            storage=storage,
            artifact_builder=artifact_builder,
            recovered_row=recovered_row,
            expected_types=expected_types,
            active_run_key=active_run_key,
            comparison_doc_map=comparison_doc_map,
            manifest_relpaths=manifest_relpaths,
            routing_by_page=routing_by_page_cache[document_id],
            benchmark_llm_pages=benchmark_llm_pages_cache[document_id],
            routing_audit_reason_map=audit_reason_map,
        )
        rows_by_document.setdefault(document_id, []).append(row)

    documents: list[dict[str, Any]] = []
    by_document_counts: dict[str, dict[str, Any]] = {}
    recovered_page_count = 0
    primary_review_page_count = 0
    skipped_page_count = 0
    visual_risk_page_count = 0
    spine_hard_candidate_count = 0
    path_mismatch_page_count = 0
    preview_error_page_count = 0
    missing_current_llm_artifact_page_count = 0

    for document_id, rows in rows_by_document.items():
        rows.sort(key=lambda row: int(row["page_number"]))
        document_record = _resolve_document_record(storage, document_id)
        source_pdf_relpath = rows[0]["source_pdf_relpath"]
        expected_type = rows[0]["expected_type"]
        recovered_count = len(rows)
        visual_count = sum(1 for row in rows if row["visual_risk_signal_present"])
        primary_count = sum(1 for row in rows if not row["skipped_from_primary_review"])
        skipped_count = sum(1 for row in rows if row["skipped_from_primary_review"])

        recovered_page_count += recovered_count
        primary_review_page_count += primary_count
        skipped_page_count += skipped_count
        visual_risk_page_count += visual_count
        spine_hard_candidate_count += sum(1 for row in rows if row["spine_hard_candidate"])
        path_mismatch_page_count += sum(
            1 for row in rows if row["current_effective_path_consistency"] != "consistent"
        )
        preview_error_page_count += sum(1 for row in rows if row["preview_error"])
        missing_current_llm_artifact_page_count += sum(
            1 for row in rows if "current_llm_artifact_missing" in row.get("skip_reasons", [])
        )

        documents.append(
            {
                "document_id": document_id,
                "source_pdf_relpath": source_pdf_relpath,
                "expected_type": expected_type,
                "stored_pdf_relpath": _normalize_relpath(
                    document_record.original_path if document_record else None
                ),
                "recovered_page_count": recovered_count,
                "primary_review_page_count": primary_count,
                "skipped_page_count": skipped_count,
                "visual_risk_page_count": visual_count,
                "pages": rows,
            }
        )
        by_document_counts[document_id] = {
            "source_pdf_relpath": source_pdf_relpath,
            "expected_type": expected_type,
            "recovered_page_count": recovered_count,
            "primary_review_page_count": primary_count,
            "skipped_page_count": skipped_count,
            "visual_risk_page_count": visual_count,
        }

    documents.sort(key=_document_sort_key)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_artifacts": {
            "routing_rule_tiebreak_json": str(tiebreak_path.resolve()),
            "routing_audit_json": str(routing_audit_path.resolve()),
            "comparison_json": str(comparison_path.resolve()),
            "corpus_manifest": str(manifest_path.resolve()),
        },
        "recommended_rule": _RULE_A_KEY,
        "expected_recovered_page_count": len(recovered_rows),
        "recovered_page_count": recovered_page_count,
        "primary_review_page_count": primary_review_page_count,
        "skipped_page_count": skipped_page_count,
        "visual_risk_page_count": visual_risk_page_count,
        "spine_hard_candidate_count": spine_hard_candidate_count,
        "by_document_counts": by_document_counts,
        "qa_status_counts_initial": {
            "safe": 0,
            "borderline": 0,
            "unsafe": 0,
            "unreviewed": primary_review_page_count,
        },
        "documents": documents,
        "limitations": {
            "path_mismatch_page_count": path_mismatch_page_count,
            "preview_error_page_count": preview_error_page_count,
            "missing_current_llm_artifact_page_count": missing_current_llm_artifact_page_count,
        },
    }


def _build_markdown(payload: dict[str, Any]) -> str:
    source_artifacts = payload["source_artifacts"]
    lines = [
        "# Rule A Recovered Pages Manual QA Pack",
        "",
        "## Scope and recommendation context",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- recommended_rule: `{payload['recommended_rule']}`",
        f"- expected_recovered_page_count: `{payload['expected_recovered_page_count']}`",
        f"- recovered_page_count: `{payload['recovered_page_count']}`",
        f"- primary_review_page_count: `{payload['primary_review_page_count']}`",
        f"- skipped_page_count: `{payload['skipped_page_count']}`",
        f"- visual_risk_page_count: `{payload['visual_risk_page_count']}`",
        f"- spine_hard_candidate_count: `{payload['spine_hard_candidate_count']}`",
        f"- routing_rule_tiebreak_json: `{source_artifacts['routing_rule_tiebreak_json']}`",
        f"- routing_audit_json: `{source_artifacts['routing_audit_json']}`",
        f"- comparison_json: `{source_artifacts['comparison_json']}`",
        f"- corpus_manifest: `{source_artifacts['corpus_manifest']}`",
        "",
        "## Recovered page list",
        "",
        "| document | page | expected_type | visual_risk | spine_hard | skipped |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for document in payload["documents"]:
        for row in document["pages"]:
            lines.append(
                f"| {row['source_pdf_relpath']} | {row['page_number']} | {row.get('expected_type') or 'missing'} | "
                f"{row['visual_risk_signal_present']} | {row['spine_hard_candidate']} | {row['skipped_from_primary_review']} |"
            )

    lines.extend(["", "## Per-document sections", ""])
    for document in payload["documents"]:
        lines.extend(
            [
                f"## `{document['document_id']}`",
                f"- source_pdf_relpath: `{document['source_pdf_relpath']}`",
                f"- expected_type: `{document.get('expected_type') or 'missing'}`",
                f"- recovered_page_count: `{document['recovered_page_count']}`",
                f"- primary_review_page_count: `{document['primary_review_page_count']}`",
                f"- skipped_page_count: `{document['skipped_page_count']}`",
                f"- visual_risk_page_count: `{document['visual_risk_page_count']}`",
                "",
            ]
        )
        for row in document["pages"]:
            if row["skipped_from_primary_review"]:
                continue
            lines.extend(
                [
                    f"### Page {row['page_number']}",
                    f"- page_image_relpath: `{row.get('page_image_relpath') or 'missing'}`",
                    f"- route_label: `{row.get('route_label') or 'missing'}`",
                    f"- route_reason: {row.get('route_reason') or 'missing'}",
                    f"- hard_page_score: `{row.get('hard_page_score')}`",
                    f"- hard_page_reasons: `{row.get('hard_page_reasons')}`",
                    f"- recommended_execution: `{row.get('recommended_execution') or 'missing'}`",
                    f"- pass1_path: `{row.get('pass1_path') or 'missing'}`",
                    f"- candidate_anchor_count: `{row.get('candidate_anchor_count')}`",
                    f"- page_role: `{row.get('page_role') or 'missing'}`",
                    f"- page_summary: {row.get('page_summary') or 'missing'}",
                    "- Current LLM",
                    f"  - current_llm_page_role: `{row.get('current_llm_page_role') or 'missing'}`",
                    f"  - current_llm_page_summary: {row.get('current_llm_page_summary') or 'missing'}",
                    f"  - current_llm_anchor_labels: `{row.get('current_llm_anchor_labels')}`",
                    f"  - current_llm_related_pages: `{row.get('current_llm_related_pages')}`",
                    f"  - current_llm_prerequisites: `{row.get('current_llm_prerequisites')}`",
                    "  - current_llm_long_explanations:",
                ]
            )
            for explanation in row.get("current_llm_long_explanations", []):
                lines.append(f"    - {explanation}")
            lines.extend(
                [
                    "- Preview Compat",
                    f"  - preview_compat_page_role: `{row.get('preview_compat_page_role') or 'missing'}`",
                    f"  - preview_compat_page_summary: {row.get('preview_compat_page_summary') or 'missing'}",
                    f"  - preview_compat_anchor_labels: `{row.get('preview_compat_anchor_labels')}`",
                    f"  - preview_compat_related_pages: `{row.get('preview_compat_related_pages')}`",
                    f"  - preview_compat_prerequisite: {row.get('preview_compat_prerequisite') or 'missing'}",
                    "  - preview_compat_long_explanations:",
                ]
            )
            for explanation in row.get("preview_compat_long_explanations", []):
                lines.append(f"    - {explanation}")
            lines.extend(
                [
                    "- Risk flags",
                    f"  - visual_risk_signal_present: `{row['visual_risk_signal_present']}`",
                    f"  - spine_hard_candidate: `{row['spine_hard_candidate']}`",
                    f"  - suspected_false_positive_reason: {row.get('suspected_false_positive_reason') or 'missing'}",
                    f"  - current_effective_path_source: `{row.get('current_effective_path_source')}`",
                    f"  - current_effective_path_consistency: `{row.get('current_effective_path_consistency')}`",
                    "- QA rubric",
                    f"  - qa_status: `{row.get('qa_status')}`",
                    f"  - qa_notes: {row.get('qa_notes') or ''}",
                    "",
                ]
            )

    skipped_rows = [
        row
        for document in payload["documents"]
        for row in document["pages"]
        if row["skipped_from_primary_review"]
    ]
    lines.extend(["## Skipped from primary review", ""])
    if not skipped_rows:
        lines.append("- none")
    else:
        for row in skipped_rows:
            lines.extend(
                [
                    f"### {row['source_pdf_relpath']} / Page {row['page_number']}",
                    f"- skip_reasons: `{row.get('skip_reasons')}`",
                    f"- current_effective_path_source: `{row.get('current_effective_path_source')}`",
                    f"- current_effective_path_consistency: `{row.get('current_effective_path_consistency')}`",
                    f"- page_image_relpath: `{row.get('page_image_relpath') or 'missing'}`",
                    f"- preview_error: {row.get('preview_error') or 'none'}",
                    "",
                ]
            )

    lines.extend(
        [
            "## Final tally template",
            "",
            "- safe:",
            "- borderline:",
            "- unsafe:",
            "- notes:",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    storage = get_storage_service()
    artifact_builder = Pass2ArtifactBuilder(storage=storage)

    tiebreak_path = _resolve_existing_file(args.routing_rule_tiebreak_json)
    comparison_path = _resolve_existing_file(args.comparison_json)
    manifest_path = _resolve_existing_file(args.corpus_manifest)
    output_prefix = _resolve_output_prefix(args.output_prefix)

    tiebreak_payload = _load_json(tiebreak_path)
    comparison_payload = _load_json(comparison_path)
    manifest_path, manifest_map = _load_manifest(comparison_payload, manifest_path)
    routing_audit_path = _validate_sources(
        tiebreak_payload=tiebreak_payload,
        comparison_path=comparison_path,
        manifest_path=manifest_path,
    )
    routing_audit_payload = _load_json(routing_audit_path)

    payload = _build_payload(
        storage=storage,
        artifact_builder=artifact_builder,
        tiebreak_path=tiebreak_path,
        routing_audit_path=routing_audit_path,
        comparison_path=comparison_path,
        manifest_path=manifest_path,
        tiebreak_payload=tiebreak_payload,
        comparison_payload=comparison_payload,
        manifest_map=manifest_map,
        routing_audit_payload=routing_audit_payload,
    )

    if payload["recovered_page_count"] != payload["expected_recovered_page_count"]:
        raise SystemExit(
            "QA pack row count does not match expected recovered page count: "
            f"{payload['recovered_page_count']} != {payload['expected_recovered_page_count']}"
        )

    json_path = output_prefix.with_suffix(".json")
    markdown_path = output_prefix.with_suffix(".md")
    _write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _write_text(markdown_path, _build_markdown(payload))

    print(f"JSON written to {json_path}")
    print(f"Markdown written to {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
