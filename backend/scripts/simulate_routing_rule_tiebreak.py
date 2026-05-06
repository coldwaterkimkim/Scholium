#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from export_routing_audit import (
    DEFAULT_OUTPUT_DIR,
    PROJECT_ROOT,
    _active_and_baseline_run_keys,
    _active_doc_paths,
    _comparison_run_map,
    _hard_candidate_pages,
    _load_json,
    _load_manifest,
    _normalize_relpath,
    _require_document_artifacts,
    _resolve_existing_file,
    _resolve_output_prefix,
    _text_first_likely,
)


_TEXT_RICH_LABEL = "text-rich"
_TEXT_FIRST_PATH = "text-first"
_TEXT_FIRST_EXECUTION = "text_first"
_LLM_PATH = "llm"
_COMPAT_PATH = "compat"
_UNKNOWN_PATH = "unknown"
_PASS2_META_TO_PATH = {
    "llm": _LLM_PATH,
    "compat": _COMPAT_PATH,
}
_VISUAL_REASON_WEIGHTS = {
    "has_figure": 15,
    "image_count>=3": 20,
    "image_count>=1": 10,
}
_VISUAL_CENTRIC_MARKERS = (
    "diagram",
    "chart",
    "graph",
    "heatmap",
    "figure",
    "fig.",
    "image",
    "screenshot",
    "screen capture",
    "panel",
    "microscope",
    "fluorescent",
    "visual",
    "도식",
    "그래프",
    "이미지",
    "캡처",
    "현미경",
    "형광",
    "시각",
    "패널",
)
_RULE_A_KEY = "rule_a"
_RULE_B_KEY = "rule_b"
_RULES = (_RULE_A_KEY, _RULE_B_KEY)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulate routing rule tie-break candidates using existing artifacts only.",
    )
    parser.add_argument("--routing-audit-json", required=True, help="Routing audit JSON path.")
    parser.add_argument("--comparison-json", required=True, help="Comparison JSON path.")
    parser.add_argument("--corpus-manifest", required=True, help="Corpus manifest JSON path.")
    parser.add_argument(
        "--output-prefix",
        required=True,
        help=(
            "Output prefix path without extension, for example "
            "docs/perf_runs/20260329T000000Z_routing_rule_tiebreak."
        ),
    )
    return parser


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_page_image_relpath(document_id: str, page_number: int) -> str | None:
    path = PROJECT_ROOT / "data" / "rendered_pages" / document_id / f"{page_number}.png"
    if not path.exists():
        return None
    return _normalize_relpath(path.relative_to(PROJECT_ROOT).as_posix())


def _page_summary_has_visual_marker(page_summary: str | None) -> bool:
    summary = str(page_summary or "").strip().lower()
    if not summary:
        return False
    return any(marker in summary for marker in _VISUAL_CENTRIC_MARKERS)


def _visual_risk_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if row.get("has_table"):
        reasons.append("has_table")
    if int(row.get("image_count") or 0) >= 3:
        reasons.append("image_count>=3")
    if row.get("has_figure"):
        reasons.append("has_figure")
    if _page_summary_has_visual_marker(row.get("page_summary")):
        reasons.append("summary_visual_marker")
    return reasons


def _pass2_meta_path(pass2_artifact: dict[str, Any] | None) -> str | None:
    if not pass2_artifact:
        return None
    mode = str(pass2_artifact.get("meta", {}).get("pass2_generation_mode") or "").strip().lower()
    return _PASS2_META_TO_PATH.get(mode)


def _current_path_from_benchmark(
    page_number: int,
    benchmark_llm_pages: set[int],
    benchmark_compat_pages: set[int],
) -> str | None:
    if page_number in benchmark_llm_pages:
        return _LLM_PATH
    if page_number in benchmark_compat_pages:
        return _COMPAT_PATH
    return None


def _effective_path_metadata(
    *,
    page_number: int,
    benchmark_llm_pages: set[int],
    benchmark_compat_pages: set[int],
    pass2_artifact: dict[str, Any] | None,
) -> tuple[str, str, str]:
    benchmark_path = _current_path_from_benchmark(
        page_number,
        benchmark_llm_pages=benchmark_llm_pages,
        benchmark_compat_pages=benchmark_compat_pages,
    )
    pass2_meta_path = _pass2_meta_path(pass2_artifact)

    if benchmark_path is not None:
        source = "benchmark"
        effective_path = benchmark_path
    elif pass2_meta_path is not None:
        source = "pass2_meta"
        effective_path = pass2_meta_path
    else:
        source = "unknown"
        effective_path = _UNKNOWN_PATH

    if benchmark_path is not None and pass2_meta_path is not None:
        consistency = "consistent" if benchmark_path == pass2_meta_path else "mismatched"
    else:
        consistency = "unknown"

    return effective_path, source, consistency


def _comparison_documents_by_relpath(
    comparison_payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
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


def _resolve_selected_documents(
    *,
    routing_audit_payload: dict[str, Any],
    comparison_payload: dict[str, Any],
    manifest_map: dict[str, dict[str, Any]],
    active_run_key: str,
    baseline_run_key: str,
) -> list[dict[str, Any]]:
    selection = routing_audit_payload.get("document_selection") or {}
    selected_ids = list(selection.get("selected_document_ids") or [])
    outlier_ids = set(selection.get("outlier_ids") or [])
    success_ids = set(selection.get("success_ids") or [])
    if not selected_ids:
        raise SystemExit("Routing audit selected_document_ids must not be empty.")

    audit_documents = routing_audit_payload.get("documents")
    if not isinstance(audit_documents, list):
        raise SystemExit("Routing audit documents must be a list.")
    audit_by_id = {
        str(document["document_id"]): dict(document)
        for document in audit_documents
        if isinstance(document, dict) and document.get("document_id")
    }
    comparison_by_relpath = _comparison_documents_by_relpath(comparison_payload)

    resolved: list[dict[str, Any]] = []
    for document_id in selected_ids:
        audit_document = audit_by_id.get(str(document_id))
        if audit_document is None:
            raise SystemExit(f"Routing audit selected document missing in documents[]: {document_id}")
        relpath = _normalize_relpath(audit_document.get("source_pdf_relpath"))
        if relpath is None:
            raise SystemExit(f"Routing audit document missing source_pdf_relpath: {document_id}")
        manifest_entry = manifest_map.get(relpath)
        if manifest_entry is None:
            raise SystemExit(f"Selected document not found in manifest: {relpath}")
        comparison_document = comparison_by_relpath.get(relpath)
        if comparison_document is None:
            raise SystemExit(f"Selected document not found in comparison JSON: {relpath}")
        runs = comparison_document.get("runs") or {}
        active_run = runs.get(active_run_key)
        baseline_run = runs.get(baseline_run_key)
        if not isinstance(active_run, dict) or not isinstance(baseline_run, dict):
            raise SystemExit(f"Comparison JSON missing baseline/active runs for {relpath}")
        active_document_id = str(active_run.get("document_id") or "")
        if active_document_id != str(document_id):
            raise SystemExit(
                "Routing audit selected document id does not match comparison active document id "
                f"for {relpath}: {document_id} != {active_document_id}"
            )
        document_role = "outlier" if str(document_id) in outlier_ids else "success_reference"
        if document_role == "success_reference" and str(document_id) not in success_ids:
            raise SystemExit(f"Selected document id is neither outlier nor success reference: {document_id}")

        resolved.append(
            {
                "document_id": str(document_id),
                "document_role": document_role,
                "source_pdf_relpath": relpath,
                "expected_type": manifest_entry.get("expected_type"),
                "notes": manifest_entry.get("notes"),
                "baseline_metrics": dict(baseline_run),
                "active_metrics": dict(active_run),
            }
        )

    if len(resolved) != len(selected_ids):
        raise SystemExit("Selected document count mismatch after resolution.")
    return resolved


def _build_page_row(
    *,
    document_id: str,
    document_role: str,
    source_pdf_relpath: str,
    expected_type: str | None,
    page_number: int,
    page_manifest_entry: dict[str, Any],
    page_routing_entry: dict[str, Any] | None,
    hard_candidate_pages: set[int],
    benchmark_llm_pages: set[int],
    benchmark_compat_pages: set[int],
    pass1_artifact: dict[str, Any] | None,
    pass2_artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    pass1_meta = pass1_artifact.get("meta", {}) if pass1_artifact else {}
    pass1_result = pass1_artifact.get("result", {}) if pass1_artifact else {}
    page_summary = pass1_result.get("page_summary")
    pass1_path = pass1_meta.get("pass1_path")
    candidate_anchor_count = len(pass1_result.get("candidate_anchors", []))
    effective_path, effective_source, consistency = _effective_path_metadata(
        page_number=page_number,
        benchmark_llm_pages=benchmark_llm_pages,
        benchmark_compat_pages=benchmark_compat_pages,
        pass2_artifact=pass2_artifact,
    )
    page_image_relpath = _resolve_page_image_relpath(document_id, page_number)
    row = {
        "document_id": document_id,
        "document_role": document_role,
        "source_pdf_relpath": source_pdf_relpath,
        "expected_type": expected_type,
        "page_number": page_number,
        "route_label": page_manifest_entry.get("route_label"),
        "route_reason": page_manifest_entry.get("route_reason"),
        "recommended_execution": (
            page_routing_entry.get("recommended_execution") if page_routing_entry else None
        ),
        "hard_page_score": page_routing_entry.get("hard_page_score") if page_routing_entry else None,
        "hard_page_reasons": list(page_routing_entry.get("hard_page_reasons") or [])
        if page_routing_entry
        else [],
        "is_spine_hard_candidate": page_number in hard_candidate_pages,
        "pass1_path": pass1_path,
        "pass1_candidate_anchor_count": candidate_anchor_count,
        "text_first_likely": _text_first_likely(
            pass1_path=pass1_path,
            route_label=page_manifest_entry.get("route_label"),
            text_length=page_manifest_entry.get("text_length"),
            non_empty_text_block_count=page_manifest_entry.get("non_empty_text_block_count"),
            block_count=page_manifest_entry.get("block_count"),
        ),
        "image_count": page_manifest_entry.get("image_count"),
        "has_table": bool(page_manifest_entry.get("has_table")),
        "has_figure": bool(page_manifest_entry.get("has_figure")),
        "text_length": page_manifest_entry.get("text_length"),
        "block_count": page_manifest_entry.get("block_count"),
        "non_empty_text_block_count": page_manifest_entry.get("non_empty_text_block_count"),
        "page_role": pass1_result.get("page_role"),
        "page_summary": page_summary,
        "page_image_relpath": page_image_relpath,
        "current_pass2_generation_mode": (
            pass2_artifact.get("meta", {}).get("pass2_generation_mode") if pass2_artifact else None
        ),
        "current_effective_path": effective_path,
        "current_effective_path_source": effective_source,
        "current_effective_path_consistency": consistency,
    }
    row["visual_risk_reasons"] = _visual_risk_reasons(row)
    row["has_visual_risk_signal"] = bool(row["visual_risk_reasons"])
    return row


def _load_document_rows(document_meta: dict[str, Any]) -> dict[str, Any]:
    document_id = str(document_meta["document_id"])
    paths = _require_document_artifacts(document_id)
    processing_benchmark = _load_json(paths["processing_benchmark"])
    document_spine = _load_json(paths["document_spine"])
    page_routing = _load_json(paths["page_routing"])
    page_manifest = _load_json(paths["page_manifest"])

    benchmark_llm_pages = {int(page) for page in processing_benchmark.get("pass2_llm_pages", [])}
    benchmark_compat_pages = {
        int(page) for page in processing_benchmark.get("pass2_compat_pages", [])
    }
    hard_candidate_pages = _hard_candidate_pages(document_spine)
    page_routing_by_page = {
        int(entry["page_number"]): dict(entry)
        for entry in page_routing["result"].get("pages", [])
        if isinstance(entry, dict) and entry.get("page_number") is not None
    }

    rows: list[dict[str, Any]] = []
    for page_entry in page_manifest.get("pages", []):
        if not isinstance(page_entry, dict):
            continue
        page_number = int(page_entry["page_number"])
        pass1_artifact = _load_optional_json(
            paths["analysis_dir"] / "pages" / str(page_number) / "page_analysis_pass1.json"
        )
        pass2_artifact = _load_optional_json(
            paths["analysis_dir"] / "pages" / str(page_number) / "page_analysis_pass2.json"
        )
        row = _build_page_row(
            document_id=document_id,
            document_role=str(document_meta["document_role"]),
            source_pdf_relpath=str(document_meta["source_pdf_relpath"]),
            expected_type=document_meta.get("expected_type"),
            page_number=page_number,
            page_manifest_entry=dict(page_entry),
            page_routing_entry=page_routing_by_page.get(page_number),
            hard_candidate_pages=hard_candidate_pages,
            benchmark_llm_pages=benchmark_llm_pages,
            benchmark_compat_pages=benchmark_compat_pages,
            pass1_artifact=pass1_artifact,
            pass2_artifact=pass2_artifact,
        )
        rows.append(row)

    rows.sort(key=lambda row: int(row["page_number"]))
    return {
        **document_meta,
        "paths": paths,
        "processing_benchmark": processing_benchmark,
        "document_spine": document_spine,
        "page_routing": page_routing,
        "page_manifest": page_manifest,
        "benchmark_llm_pages": sorted(benchmark_llm_pages),
        "benchmark_compat_pages": sorted(benchmark_compat_pages),
        "hard_candidate_pages": sorted(hard_candidate_pages),
        "rows": rows,
        "path_mismatch_page_count": sum(
            1 for row in rows if row["current_effective_path_consistency"] == "mismatched"
        ),
        "unknown_effective_path_page_count": sum(
            1 for row in rows if row["current_effective_path"] == _UNKNOWN_PATH
        ),
    }


def _simulate_rule_a(row: dict[str, Any]) -> dict[str, Any]:
    eligible = (
        row.get("route_label") == _TEXT_RICH_LABEL
        and not bool(row.get("has_table"))
        and bool(row.get("text_first_likely"))
    )
    simulated_hard_page_score = int(row.get("hard_page_score") or 0)
    simulated_hard_page_reasons = list(row.get("hard_page_reasons") or [])
    removed_reasons: list[str] = []
    if eligible:
        removed_reasons = [
            reason for reason in simulated_hard_page_reasons if reason in _VISUAL_REASON_WEIGHTS
        ]
        simulated_hard_page_reasons = [
            reason for reason in simulated_hard_page_reasons if reason not in _VISUAL_REASON_WEIGHTS
        ]
        simulated_hard_page_score = max(
            0,
            simulated_hard_page_score
            - sum(_VISUAL_REASON_WEIGHTS[reason] for reason in removed_reasons),
        )

    if eligible:
        if row.get("route_label") == "scan-like":
            simulated_recommended_execution = "selective_visual_enrichment"
        elif bool(row.get("has_table")):
            simulated_recommended_execution = "selective_visual_enrichment"
        elif row.get("route_label") == _TEXT_RICH_LABEL and simulated_hard_page_score < 40:
            simulated_recommended_execution = _TEXT_FIRST_EXECUTION
        else:
            simulated_recommended_execution = "multimodal"
    else:
        simulated_recommended_execution = row.get("recommended_execution")

    can_land_on_compat = (
        simulated_recommended_execution == _TEXT_FIRST_EXECUTION
        and int(row.get("pass1_candidate_anchor_count") or 0) >= 3
    )
    simulated_final_path = _COMPAT_PATH if can_land_on_compat else _LLM_PATH
    candidate_pool_blocked = (
        eligible
        and simulated_recommended_execution == _TEXT_FIRST_EXECUTION
        and int(row.get("pass1_candidate_anchor_count") or 0) < 3
    )
    return {
        "eligible": eligible,
        "simulated_hard_page_score": simulated_hard_page_score,
        "simulated_hard_page_reasons": simulated_hard_page_reasons,
        "removed_reasons": removed_reasons,
        "simulated_recommended_execution": simulated_recommended_execution,
        "simulated_final_path": simulated_final_path,
        "candidate_pool_blocked": candidate_pool_blocked,
    }


def _simulate_rule_b(row: dict[str, Any]) -> dict[str, Any]:
    eligible = (
        row.get("route_label") == _TEXT_RICH_LABEL
        and bool(row.get("text_first_likely"))
        and not bool(row.get("is_spine_hard_candidate"))
    )
    simulated_recommended_execution = (
        _TEXT_FIRST_EXECUTION if eligible else row.get("recommended_execution")
    )
    can_land_on_compat = (
        simulated_recommended_execution == _TEXT_FIRST_EXECUTION
        and int(row.get("pass1_candidate_anchor_count") or 0) >= 3
    )
    simulated_final_path = _COMPAT_PATH if can_land_on_compat else _LLM_PATH
    candidate_pool_blocked = (
        eligible
        and simulated_recommended_execution == _TEXT_FIRST_EXECUTION
        and int(row.get("pass1_candidate_anchor_count") or 0) < 3
    )
    return {
        "eligible": eligible,
        "simulated_hard_page_score": row.get("hard_page_score"),
        "simulated_hard_page_reasons": list(row.get("hard_page_reasons") or []),
        "removed_reasons": [],
        "simulated_recommended_execution": simulated_recommended_execution,
        "simulated_final_path": simulated_final_path,
        "candidate_pool_blocked": candidate_pool_blocked,
    }


def _row_pointer(row: dict[str, Any], simulation: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": row["document_id"],
        "source_pdf_relpath": row["source_pdf_relpath"],
        "expected_type": row["expected_type"],
        "page_number": row["page_number"],
        "page_image_relpath": row["page_image_relpath"],
        "route_label": row["route_label"],
        "recommended_execution": row["recommended_execution"],
        "simulated_recommended_execution": simulation["simulated_recommended_execution"],
        "simulated_final_path": simulation["simulated_final_path"],
        "current_pass2_generation_mode": row["current_pass2_generation_mode"],
        "current_effective_path": row["current_effective_path"],
        "current_effective_path_source": row["current_effective_path_source"],
        "current_effective_path_consistency": row["current_effective_path_consistency"],
        "is_spine_hard_candidate": row["is_spine_hard_candidate"],
        "pass1_path": row["pass1_path"],
        "pass1_candidate_anchor_count": row["pass1_candidate_anchor_count"],
        "text_first_likely": row["text_first_likely"],
        "has_table": row["has_table"],
        "has_figure": row["has_figure"],
        "image_count": row["image_count"],
        "hard_page_score": row["hard_page_score"],
        "hard_page_reasons": row["hard_page_reasons"],
        "simulated_hard_page_score": simulation["simulated_hard_page_score"],
        "simulated_hard_page_reasons": simulation["simulated_hard_page_reasons"],
        "removed_reasons": simulation["removed_reasons"],
        "has_visual_risk_signal": row["has_visual_risk_signal"],
        "visual_risk_reasons": row["visual_risk_reasons"],
        "page_role": row["page_role"],
        "page_summary": row["page_summary"],
        "candidate_pool_blocked": simulation["candidate_pool_blocked"],
    }


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _aggregate_scope(rows: list[dict[str, Any]], simulations: dict[int, dict[str, Any]]) -> dict[str, Any]:
    eligible_rows = [row for row in rows if simulations[int(row["page_number"])]["eligible"]]
    eligible_current_llm_rows = [
        row for row in eligible_rows if row["current_effective_path"] == _LLM_PATH
    ]
    eligible_current_compat_rows = [
        row for row in eligible_rows if row["current_effective_path"] == _COMPAT_PATH
    ]
    recovered_rows = [
        row
        for row in eligible_current_llm_rows
        if simulations[int(row["page_number"])]["simulated_final_path"] == _COMPAT_PATH
    ]
    still_llm_rows = [
        row
        for row in eligible_current_llm_rows
        if simulations[int(row["page_number"])]["simulated_final_path"] != _COMPAT_PATH
    ]
    candidate_pool_blocked_rows = [
        row
        for row in eligible_current_llm_rows
        if simulations[int(row["page_number"])]["candidate_pool_blocked"]
    ]
    metrics = {
        "candidate_pages_considered": len(eligible_rows),
        "eligible_pages_total": len(eligible_rows),
        "eligible_current_llm_pages": len(eligible_current_llm_rows),
        "eligible_current_compat_pages": len(eligible_current_compat_rows),
        "llm_pages_recovered": len(recovered_rows),
        "recovery_rate_over_current_llm": _safe_rate(
            len(recovered_rows), len(eligible_current_llm_rows)
        ),
        "pages_still_left_on_llm": len(still_llm_rows),
        "pages_touching_table": sum(1 for row in eligible_rows if row["has_table"]),
        "pages_touching_spine_hard_candidate": sum(
            1 for row in eligible_rows if row["is_spine_hard_candidate"]
        ),
        "pages_with_visual_risk_signal": sum(
            1 for row in eligible_rows if row["has_visual_risk_signal"]
        ),
        "recovered_pages_with_visual_risk_signal": sum(
            1 for row in recovered_rows if row["has_visual_risk_signal"]
        ),
        "recovered_pages_touching_spine_hard_candidate": sum(
            1 for row in recovered_rows if row["is_spine_hard_candidate"]
        ),
        "recovered_pages_missing_candidate_pool": len(candidate_pool_blocked_rows),
        "path_mismatch_page_count": sum(
            1
            for row in eligible_rows
            if row["current_effective_path_consistency"] == "mismatched"
        ),
    }
    metrics["eligible_page_numbers"] = [int(row["page_number"]) for row in eligible_rows]
    metrics["eligible_current_llm_page_numbers"] = [
        int(row["page_number"]) for row in eligible_current_llm_rows
    ]
    metrics["eligible_current_compat_page_numbers"] = [
        int(row["page_number"]) for row in eligible_current_compat_rows
    ]
    metrics["recovered_page_numbers"] = [int(row["page_number"]) for row in recovered_rows]
    metrics["still_llm_page_numbers"] = [int(row["page_number"]) for row in still_llm_rows]
    metrics["candidate_pool_blocked_page_numbers"] = [
        int(row["page_number"]) for row in candidate_pool_blocked_rows
    ]
    return metrics


def _build_document_breakdown(
    *,
    document_data: dict[str, Any],
    rule_key: str,
    simulations: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    rows = list(document_data["rows"])
    metrics = _aggregate_scope(rows, simulations)
    recovered_rows = [
        _row_pointer(row, simulations[int(row["page_number"])])
        for row in rows
        if row["current_effective_path"] == _LLM_PATH
        and simulations[int(row["page_number"])]["simulated_final_path"] == _COMPAT_PATH
    ]
    eligible_rows = [
        _row_pointer(row, simulations[int(row["page_number"])])
        for row in rows
        if simulations[int(row["page_number"])]["eligible"]
    ]
    return {
        "document_id": document_data["document_id"],
        "document_role": document_data["document_role"],
        "source_pdf_relpath": document_data["source_pdf_relpath"],
        "expected_type": document_data.get("expected_type"),
        "baseline_metrics": document_data["baseline_metrics"],
        "active_metrics": document_data["active_metrics"],
        "rendered_pages": int(document_data["active_metrics"].get("rendered_pages") or len(rows)),
        "current_llm_page_numbers": list(document_data["benchmark_llm_pages"]),
        "current_compat_page_numbers": list(document_data["benchmark_compat_pages"]),
        "hard_candidate_page_numbers": list(document_data["hard_candidate_pages"]),
        "path_mismatch_page_count": int(document_data["path_mismatch_page_count"]),
        "unknown_effective_path_page_count": int(document_data["unknown_effective_path_page_count"]),
        "rule_key": rule_key,
        **metrics,
        "eligible_page_rows": eligible_rows,
        "recovered_page_rows": recovered_rows,
    }

def _simulate_rule_for_documents(
    *,
    rule_key: str,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    simulate = _simulate_rule_a if rule_key == _RULE_A_KEY else _simulate_rule_b

    def scope_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
        keyed_rows = []
        keyed_simulations: dict[int, dict[str, Any]] = {}
        for idx, row in enumerate(rows, start=1):
            keyed_row = dict(row)
            keyed_row["page_number"] = idx
            keyed_rows.append(keyed_row)
            keyed_simulations[idx] = simulate(row)
        metrics = _aggregate_scope(keyed_rows, keyed_simulations)
        metrics["eligible_page_numbers"] = [
            int(rows[int(index) - 1]["page_number"]) for index in metrics["eligible_page_numbers"]
        ]
        metrics["eligible_current_llm_page_numbers"] = [
            int(rows[int(index) - 1]["page_number"])
            for index in metrics["eligible_current_llm_page_numbers"]
        ]
        metrics["eligible_current_compat_page_numbers"] = [
            int(rows[int(index) - 1]["page_number"])
            for index in metrics["eligible_current_compat_page_numbers"]
        ]
        metrics["recovered_page_numbers"] = [
            int(rows[int(index) - 1]["page_number"]) for index in metrics["recovered_page_numbers"]
        ]
        metrics["still_llm_page_numbers"] = [
            int(rows[int(index) - 1]["page_number"]) for index in metrics["still_llm_page_numbers"]
        ]
        metrics["candidate_pool_blocked_page_numbers"] = [
            int(rows[int(index) - 1]["page_number"])
            for index in metrics["candidate_pool_blocked_page_numbers"]
        ]
        return metrics

    document_breakdown: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    recovered_page_rows: list[dict[str, Any]] = []

    for document in documents:
        simulations = {
            int(row["page_number"]): simulate(row)
            for row in document["rows"]
        }
        breakdown = _build_document_breakdown(
            document_data=document,
            rule_key=rule_key,
            simulations=simulations,
        )
        document_breakdown.append(breakdown)
        all_rows.extend(document["rows"])
        recovered_page_rows.extend(breakdown["recovered_page_rows"])

    all_rows_sorted = sorted(
        all_rows,
        key=lambda row: (str(row["document_id"]), int(row["page_number"])),
    )
    outlier_rows = [row for row in all_rows_sorted if row["document_role"] == "outlier"]
    success_rows = [row for row in all_rows_sorted if row["document_role"] == "success_reference"]
    overall_metrics = scope_metrics(all_rows_sorted)
    outlier_metrics = scope_metrics(outlier_rows)
    success_reference_metrics = scope_metrics(success_rows)

    return {
        "definition": {
            "rule_key": rule_key,
            "label": (
                "figure/image signal attenuation under text-rich + no-table pages"
                if rule_key == _RULE_A_KEY
                else "text-first override when page is not a spine hard candidate"
            ),
            "eligibility_conditions": (
                [
                    "route_label == text-rich",
                    "has_table == false",
                    "text_first_likely == true",
                ]
                if rule_key == _RULE_A_KEY
                else [
                    "route_label == text-rich",
                    "text_first_likely == true",
                    "is_spine_hard_candidate == false",
                ]
            ),
        },
        "overall_metrics": overall_metrics,
        "outlier_metrics": outlier_metrics,
        "success_reference_metrics": success_reference_metrics,
        "document_breakdown": document_breakdown,
        "recovered_page_rows": recovered_page_rows,
        "risk_notes": {
            "primary_risk": (
                "visual-heavy page에서 has_figure/image_count 신호를 약하게 봐서 실제 visual reasoning 필요 페이지를 compat로 내릴 수 있다."
                if rule_key == _RULE_A_KEY
                else "spine hard candidate가 아니어도 실제 visual reasoning이 필요한 text-rich page를 text_first로 override할 수 있다."
            ),
            "recovered_pages_with_visual_risk_signal": overall_metrics[
                "recovered_pages_with_visual_risk_signal"
            ],
            "success_reference_recovered_pages_with_visual_risk_signal": success_reference_metrics[
                "recovered_pages_with_visual_risk_signal"
            ],
            "path_mismatch_page_count": overall_metrics["path_mismatch_page_count"],
        },
    }


def _outlier_takeaway(
    rule_a: dict[str, Any],
    rule_b: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    for breakdown_a in rule_a["document_breakdown"]:
        if breakdown_a["document_role"] != "outlier":
            continue
        doc_id = breakdown_a["document_id"]
        breakdown_b = next(
            item for item in rule_b["document_breakdown"] if item["document_id"] == doc_id
        )
        relpath = breakdown_a["source_pdf_relpath"]
        recovered_a = int(breakdown_a["llm_pages_recovered"])
        recovered_b = int(breakdown_b["llm_pages_recovered"])
        if recovered_a > recovered_b:
            lines.append(f"{relpath}: Rule A가 {recovered_a} vs {recovered_b}로 더 많이 회수한다.")
        elif recovered_b > recovered_a:
            lines.append(f"{relpath}: Rule B가 {recovered_b} vs {recovered_a}로 더 많이 회수한다.")
        else:
            lines.append(f"{relpath}: 두 규칙 모두 {recovered_a}페이지를 회수해 차이가 없다.")
    return lines


def _success_takeaway(
    rule_a: dict[str, Any],
    rule_b: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    for breakdown_a in rule_a["document_breakdown"]:
        if breakdown_a["document_role"] != "success_reference":
            continue
        doc_id = breakdown_a["document_id"]
        breakdown_b = next(
            item for item in rule_b["document_breakdown"] if item["document_id"] == doc_id
        )
        relpath = breakdown_a["source_pdf_relpath"]
        risk_a = int(breakdown_a["recovered_pages_with_visual_risk_signal"])
        risk_b = int(breakdown_b["recovered_pages_with_visual_risk_signal"])
        recovered_a = int(breakdown_a["llm_pages_recovered"])
        recovered_b = int(breakdown_b["llm_pages_recovered"])
        if risk_a < risk_b:
            lines.append(
                f"{relpath}: success reference 기준 visual-risk recovered page는 Rule A가 더 적다 ({risk_a} vs {risk_b})."
            )
        elif risk_b < risk_a:
            lines.append(
                f"{relpath}: success reference 기준 visual-risk recovered page는 Rule B가 더 적다 ({risk_b} vs {risk_a})."
            )
        else:
            lines.append(
                f"{relpath}: success reference 기준 visual-risk recovered page는 동률이고 recovered count는 {recovered_a} vs {recovered_b}다."
            )
    return lines


def _recommended_rule(rule_a: dict[str, Any], rule_b: dict[str, Any]) -> tuple[str, list[str], str]:
    candidates = {
        _RULE_A_KEY: rule_a,
        _RULE_B_KEY: rule_b,
    }

    def sort_key(rule_key: str) -> tuple[int, int, int, int, int]:
        payload = candidates[rule_key]
        other_key = _RULE_B_KEY if rule_key == _RULE_A_KEY else _RULE_A_KEY
        del other_key
        return (
            -int(payload["outlier_metrics"]["llm_pages_recovered"]),
            int(payload["success_reference_metrics"]["recovered_pages_with_visual_risk_signal"]),
            int(payload["overall_metrics"]["recovered_pages_with_visual_risk_signal"]),
            int(payload["overall_metrics"]["candidate_pages_considered"]),
            0 if rule_key == _RULE_A_KEY else 1,
        )

    recommended = min(_RULES, key=sort_key)
    other = _RULE_B_KEY if recommended == _RULE_A_KEY else _RULE_A_KEY
    winner = candidates[recommended]
    loser = candidates[other]

    winner_outlier_recovered = int(winner["outlier_metrics"]["llm_pages_recovered"])
    loser_outlier_recovered = int(loser["outlier_metrics"]["llm_pages_recovered"])
    winner_success_visual_risk = int(
        winner["success_reference_metrics"]["recovered_pages_with_visual_risk_signal"]
    )
    loser_success_visual_risk = int(
        loser["success_reference_metrics"]["recovered_pages_with_visual_risk_signal"]
    )
    winner_overall_visual_risk = int(
        winner["overall_metrics"]["recovered_pages_with_visual_risk_signal"]
    )
    loser_overall_visual_risk = int(
        loser["overall_metrics"]["recovered_pages_with_visual_risk_signal"]
    )
    winner_scope = int(winner["overall_metrics"]["candidate_pages_considered"])
    loser_scope = int(loser["overall_metrics"]["candidate_pages_considered"])

    reasons = [
        f"Outlier recover potential: {recommended}={winner_outlier_recovered}, {other}={loser_outlier_recovered}.",
        (
            "Success reference visual-risk recovered pages: "
            f"{recommended}={winner_success_visual_risk}, {other}={loser_success_visual_risk}."
        ),
        (
            "Overall visual-risk recovered pages: "
            f"{recommended}={winner_overall_visual_risk}, {other}={loser_overall_visual_risk}."
        ),
        f"Candidate scope: {recommended}={winner_scope}, {other}={loser_scope}.",
    ]

    recover_gap = abs(winner_outlier_recovered - loser_outlier_recovered)
    success_visual_gap = abs(winner_success_visual_risk - loser_success_visual_risk)
    overall_visual_gap = abs(winner_overall_visual_risk - loser_overall_visual_risk)
    if recover_gap >= 3 and winner_success_visual_risk <= loser_success_visual_risk:
        confidence = "high"
    elif recover_gap >= 1 or success_visual_gap >= 2 or overall_visual_gap >= 2:
        confidence = "medium"
    else:
        confidence = "low"
    reasons.append(f"Recommendation confidence: {confidence}.")
    return recommended, reasons[:5], confidence


def _summary_payload(
    *,
    rule_a: dict[str, Any],
    rule_b: dict[str, Any],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    recommended, reasons, confidence = _recommended_rule(rule_a, rule_b)
    total_path_mismatches = sum(int(document["path_mismatch_page_count"]) for document in documents)
    total_unknown_paths = sum(
        int(document["unknown_effective_path_page_count"]) for document in documents
    )
    return {
        "recover_potential_comparison": {
            _RULE_A_KEY: {
                "eligible_pages_total": rule_a["overall_metrics"]["eligible_pages_total"],
                "eligible_current_llm_pages": rule_a["overall_metrics"][
                    "eligible_current_llm_pages"
                ],
                "eligible_current_compat_pages": rule_a["overall_metrics"][
                    "eligible_current_compat_pages"
                ],
                "llm_pages_recovered": rule_a["overall_metrics"]["llm_pages_recovered"],
                "recovery_rate_over_current_llm": rule_a["overall_metrics"][
                    "recovery_rate_over_current_llm"
                ],
            },
            _RULE_B_KEY: {
                "eligible_pages_total": rule_b["overall_metrics"]["eligible_pages_total"],
                "eligible_current_llm_pages": rule_b["overall_metrics"][
                    "eligible_current_llm_pages"
                ],
                "eligible_current_compat_pages": rule_b["overall_metrics"][
                    "eligible_current_compat_pages"
                ],
                "llm_pages_recovered": rule_b["overall_metrics"]["llm_pages_recovered"],
                "recovery_rate_over_current_llm": rule_b["overall_metrics"][
                    "recovery_rate_over_current_llm"
                ],
            },
        },
        "risk_comparison": {
            _RULE_A_KEY: {
                "recovered_pages_with_visual_risk_signal": rule_a["overall_metrics"][
                    "recovered_pages_with_visual_risk_signal"
                ],
                "success_reference_recovered_pages_with_visual_risk_signal": rule_a[
                    "success_reference_metrics"
                ]["recovered_pages_with_visual_risk_signal"],
                "recovered_pages_touching_spine_hard_candidate": rule_a["overall_metrics"][
                    "recovered_pages_touching_spine_hard_candidate"
                ],
            },
            _RULE_B_KEY: {
                "recovered_pages_with_visual_risk_signal": rule_b["overall_metrics"][
                    "recovered_pages_with_visual_risk_signal"
                ],
                "success_reference_recovered_pages_with_visual_risk_signal": rule_b[
                    "success_reference_metrics"
                ]["recovered_pages_with_visual_risk_signal"],
                "recovered_pages_touching_spine_hard_candidate": rule_b["overall_metrics"][
                    "recovered_pages_touching_spine_hard_candidate"
                ],
            },
        },
        "outlier_takeaway": _outlier_takeaway(rule_a, rule_b),
        "success_reference_takeaway": _success_takeaway(rule_a, rule_b),
        "recommended_first_rule": recommended,
        "recommendation_confidence": confidence,
        "recommendation_reason": reasons,
        "evidence_limitations": [
            "This simulation uses existing artifacts only and does not rerun the pipeline.",
            (
                f"current_effective_path mismatch pages: {total_path_mismatches}, "
                f"unknown pages: {total_unknown_paths}."
            ),
            "processing_benchmark.json remains the primary ground truth for current llm/compat path.",
            "pass2 meta mismatch or missing pages are counted as limitations and kept visible in per-document summaries.",
        ],
    }


def _markdown_metrics_table(title: str, metrics: dict[str, Any]) -> list[str]:
    return [
        f"### {title}",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| eligible_pages_total | {metrics['eligible_pages_total']} |",
        f"| eligible_current_llm_pages | {metrics['eligible_current_llm_pages']} |",
        f"| eligible_current_compat_pages | {metrics['eligible_current_compat_pages']} |",
        f"| llm_pages_recovered | {metrics['llm_pages_recovered']} |",
        f"| recovery_rate_over_current_llm | {metrics['recovery_rate_over_current_llm']} |",
        f"| pages_still_left_on_llm | {metrics['pages_still_left_on_llm']} |",
        f"| pages_with_visual_risk_signal | {metrics['pages_with_visual_risk_signal']} |",
        f"| recovered_pages_with_visual_risk_signal | {metrics['recovered_pages_with_visual_risk_signal']} |",
        f"| path_mismatch_page_count | {metrics['path_mismatch_page_count']} |",
        "",
    ]


def _render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Routing Rule Tie-Break Simulation",
        "",
        "## Audit scope and source artifacts",
        "",
        f"- routing_audit_json: `{payload['source_artifacts']['routing_audit_json']}`",
        f"- comparison_json: `{payload['source_artifacts']['comparison_json']}`",
        f"- corpus_manifest: `{payload['source_artifacts']['corpus_manifest']}`",
        "- raw artifacts: processing_benchmark.json, document_spine.json, page_routing.json, page_manifest.json, page_analysis_pass1.json, page_analysis_pass2.json",
        "",
        "## Selected document set",
        "",
        "| role | document_id | source_pdf_relpath | expected_type | rendered_pages | active_llm | active_compat |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for document in payload["selected_documents"]["documents"]:
        lines.append(
            f"| {document['document_role']} | {document['document_id']} | {document['source_pdf_relpath']} | "
            f"{document.get('expected_type') or 'missing'} | {document['active_metrics'].get('rendered_pages')} | "
            f"{document['active_metrics'].get('pass2_llm_count')} | {document['active_metrics'].get('pass2_compat_count')} |"
        )

    lines.extend(["", "## Rule A simulation", ""])
    lines.extend(_markdown_metrics_table("Overall", payload["rules"][_RULE_A_KEY]["overall_metrics"]))
    lines.extend(_markdown_metrics_table("Outlier only", payload["rules"][_RULE_A_KEY]["outlier_metrics"]))
    lines.extend(
        _markdown_metrics_table(
            "Success reference only",
            payload["rules"][_RULE_A_KEY]["success_reference_metrics"],
        )
    )

    lines.extend(["## Rule B simulation", ""])
    lines.extend(_markdown_metrics_table("Overall", payload["rules"][_RULE_B_KEY]["overall_metrics"]))
    lines.extend(_markdown_metrics_table("Outlier only", payload["rules"][_RULE_B_KEY]["outlier_metrics"]))
    lines.extend(
        _markdown_metrics_table(
            "Success reference only",
            payload["rules"][_RULE_B_KEY]["success_reference_metrics"],
        )
    )

    lines.extend(["## Outlier impact comparison", ""])
    for line in payload["summary"]["outlier_takeaway"]:
        lines.append(f"- {line}")

    lines.extend(["", "## Success reference impact comparison", ""])
    for line in payload["summary"]["success_reference_takeaway"]:
        lines.append(f"- {line}")

    lines.extend(["", "## Risk comparison", ""])
    lines.append("| rule | overall recovered visual-risk pages | success recovered visual-risk pages | recovered spine-hard pages |")
    lines.append("| --- | ---: | ---: | ---: |")
    for rule_key in _RULES:
        risk = payload["summary"]["risk_comparison"][rule_key]
        lines.append(
            f"| {rule_key} | {risk['recovered_pages_with_visual_risk_signal']} | "
            f"{risk['success_reference_recovered_pages_with_visual_risk_signal']} | "
            f"{risk['recovered_pages_touching_spine_hard_candidate']} |"
        )

    lines.extend(["", "## Recommended first rule", ""])
    lines.append(
        f"- recommended_first_rule: `{payload['summary']['recommended_first_rule']}`"
    )
    lines.append(
        f"- recommendation_confidence: `{payload['summary']['recommendation_confidence']}`"
    )
    for reason in payload["summary"]["recommendation_reason"]:
        lines.append(f"- {reason}")

    lines.extend(["", "## Evidence limitations", ""])
    for item in payload["summary"]["evidence_limitations"]:
        lines.append(f"- {item}")

    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    routing_audit_path = _resolve_existing_file(args.routing_audit_json)
    comparison_path = _resolve_existing_file(args.comparison_json)
    manifest_path = _resolve_existing_file(args.corpus_manifest)
    output_prefix = _resolve_output_prefix(args.output_prefix)

    routing_audit_payload = _load_json(routing_audit_path)
    comparison_payload = _load_json(comparison_path)
    manifest_path, manifest_map = _load_manifest(comparison_payload, manifest_path)

    run_map = _comparison_run_map(comparison_payload)
    active_run_key, baseline_run_key = _active_and_baseline_run_keys(run_map)
    selected_documents = _resolve_selected_documents(
        routing_audit_payload=routing_audit_payload,
        comparison_payload=comparison_payload,
        manifest_map=manifest_map,
        active_run_key=active_run_key,
        baseline_run_key=baseline_run_key,
    )
    documents_with_rows = [_load_document_rows(document) for document in selected_documents]

    rule_a = _simulate_rule_for_documents(rule_key=_RULE_A_KEY, documents=documents_with_rows)
    rule_b = _simulate_rule_for_documents(rule_key=_RULE_B_KEY, documents=documents_with_rows)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_artifacts": {
            "routing_audit_json": str(routing_audit_path),
            "comparison_json": str(comparison_path),
            "corpus_manifest": str(manifest_path),
        },
        "selected_documents": {
            "outlier_ids": [doc["document_id"] for doc in documents_with_rows if doc["document_role"] == "outlier"],
            "success_ids": [
                doc["document_id"] for doc in documents_with_rows if doc["document_role"] == "success_reference"
            ],
            "documents": [
                {
                    "document_id": doc["document_id"],
                    "document_role": doc["document_role"],
                    "source_pdf_relpath": doc["source_pdf_relpath"],
                    "expected_type": doc.get("expected_type"),
                    "baseline_metrics": doc["baseline_metrics"],
                    "active_metrics": doc["active_metrics"],
                    "path_mismatch_page_count": doc["path_mismatch_page_count"],
                    "unknown_effective_path_page_count": doc["unknown_effective_path_page_count"],
                }
                for doc in documents_with_rows
            ],
        },
        "rules": {
            _RULE_A_KEY: rule_a,
            _RULE_B_KEY: rule_b,
        },
    }
    payload["summary"] = _summary_payload(
        rule_a=rule_a,
        rule_b=rule_b,
        documents=documents_with_rows,
    )

    json_path = output_prefix.with_suffix(".json")
    markdown_path = output_prefix.with_suffix(".md")
    _write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _write_text(markdown_path, _render_markdown(payload))

    print(f"JSON written to {json_path}")
    print(f"Markdown written to {markdown_path}")


if __name__ == "__main__":
    main()
