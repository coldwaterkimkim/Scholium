#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "perf_runs"

_TEXT_FIRST_PATH = "text-first"
_TEXT_RICH_LABEL = "text-rich"
_VISUAL_REASON_WEIGHTS = {
    "base_route=visual-rich": 30,
    "has_table": 25,
    "has_figure": 15,
    "image_count>=3": 20,
    "image_count>=1": 10,
}
_OTHER_REASON_WEIGHTS = {
    "base_route=scan-like": 40,
    "text_length<80": 20,
    "text_length<200": 10,
    "non_empty_text_block_count<=1": 20,
    "non_empty_text_block_count<=3": 10,
    "ocr_used": 25,
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a routing audit from existing comparison and analysis artifacts.",
    )
    parser.add_argument("--comparison-json", required=True, help="Comparison JSON path.")
    parser.add_argument("--corpus-manifest", required=True, help="Corpus manifest JSON path.")
    parser.add_argument(
        "--output-prefix",
        required=True,
        help="Output prefix path without extension, for example docs/perf_runs/20260329T000000Z_routing_audit.",
    )
    return parser


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _normalize_relpath(value: str | None) -> str | None:
    if not value:
        return None
    return Path(value).as_posix()


def _resolve_existing_file(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise SystemExit(f"Required file not found: {path}")
    return path


def _resolve_output_prefix(raw_output_prefix: str) -> Path:
    output_prefix = Path(raw_output_prefix).expanduser().resolve()
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    return output_prefix


def _comparison_run_map(comparison_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    runs = comparison_payload.get("runs")
    if not isinstance(runs, list):
        raise SystemExit("Comparison payload runs must be a list.")
    return {
        str(run["run_key"]): dict(run)
        for run in runs
        if isinstance(run, dict) and run.get("run_key")
    }


def _active_and_baseline_run_keys(run_map: dict[str, dict[str, Any]]) -> tuple[str, str]:
    active_key = None
    baseline_key = None
    for run_key, run in run_map.items():
        if (
            str(run.get("pipeline_mode")) == "v2_spine"
            and str(run.get("pass2_execution_mode")) == "hard_pages_only"
        ):
            active_key = run_key
        if (
            str(run.get("pipeline_mode")) == "hybrid"
            and str(run.get("pass2_execution_mode")) == "all_pages"
        ):
            baseline_key = run_key
    if active_key is None or baseline_key is None:
        raise SystemExit("Could not identify active/baseline runs from comparison JSON.")
    return active_key, baseline_key


def _load_manifest(
    comparison_payload: dict[str, Any],
    explicit_manifest_path: Path,
) -> tuple[Path, dict[str, dict[str, Any]]]:
    run_map = _comparison_run_map(comparison_payload)
    manifest_paths = {
        _normalize_relpath(str(run.get("corpus_manifest_path") or "")) for run in run_map.values()
    }
    manifest_paths.discard(None)
    if manifest_paths:
        if len(manifest_paths) != 1:
            raise SystemExit("Comparison runs must point to exactly one corpus manifest path.")
        embedded_manifest_path = _resolve_existing_file(next(iter(manifest_paths)))
        if embedded_manifest_path != explicit_manifest_path:
            raise SystemExit(
                "Comparison JSON corpus_manifest_path does not match --corpus-manifest."
            )
    manifest_path = explicit_manifest_path
    manifest_payload = _load_json(manifest_path)
    documents = manifest_payload.get("documents")
    if not isinstance(documents, list):
        raise SystemExit("Corpus manifest documents must be a list.")
    manifest_map: dict[str, dict[str, Any]] = {}
    for document in documents:
        if not isinstance(document, dict):
            continue
        relpath = _normalize_relpath(document.get("source_pdf_relpath"))
        if relpath:
            manifest_map[relpath] = dict(document)
    return manifest_path, manifest_map


def _active_doc_paths(document_id: str) -> dict[str, Path]:
    analysis_dir = PROJECT_ROOT / "data" / "analysis" / document_id
    parsed_dir = PROJECT_ROOT / "data" / "parsed" / document_id
    return {
        "analysis_dir": analysis_dir,
        "parsed_dir": parsed_dir,
        "processing_benchmark": analysis_dir / "processing_benchmark.json",
        "document_spine": analysis_dir / "document_spine.json",
        "page_routing": analysis_dir / "page_routing.json",
        "page_manifest": parsed_dir / "page_manifest.json",
    }


def _require_document_artifacts(document_id: str) -> dict[str, Path]:
    paths = _active_doc_paths(document_id)
    missing = [name for name, path in paths.items() if name.endswith("_dir") is False and not path.exists()]
    if missing:
        raise SystemExit(
            f"Document {document_id} is missing required audit artifacts: {', '.join(missing)}"
        )
    return paths


def _summarize_routing_distribution(page_routing_payload: dict[str, Any]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for entry in page_routing_payload["result"].get("pages", []):
        key = (
            f"{entry.get('base_route_label', 'missing')} -> "
            f"{entry.get('recommended_execution', 'missing')}"
        )
        distribution[key] = distribution.get(key, 0) + 1
    return dict(sorted(distribution.items()))


def _hard_candidate_pages(document_spine_payload: dict[str, Any]) -> set[int]:
    candidates = document_spine_payload["result"].get("hard_page_candidates", [])
    return {
        int(candidate["page_number"])
        for candidate in candidates
        if isinstance(candidate, dict) and "page_number" in candidate
    }


def _text_first_likely(
    *,
    pass1_path: str | None,
    route_label: str | None,
    text_length: int | None,
    non_empty_text_block_count: int | None,
    block_count: int | None,
) -> bool:
    if pass1_path == _TEXT_FIRST_PATH:
        return True
    if pass1_path:
        return False
    return (
        route_label == _TEXT_RICH_LABEL
        and (text_length or 0) >= 200
        and (non_empty_text_block_count or 0) >= 4
        and (block_count or 0) >= 3
    )


def _visual_weight(hard_page_reasons: list[str]) -> tuple[int, int]:
    visual = 0
    other = 0
    for reason in hard_page_reasons:
        if reason in _VISUAL_REASON_WEIGHTS:
            visual += _VISUAL_REASON_WEIGHTS[reason]
        elif reason in _OTHER_REASON_WEIGHTS:
            other += _OTHER_REASON_WEIGHTS[reason]
    return visual, other


def _summary_is_text_centric(page_summary: str | None) -> bool:
    summary = (page_summary or "").strip()
    if not summary:
        return False
    lowered = summary.lower()
    return not any(marker in lowered for marker in _VISUAL_CENTRIC_MARKERS)


def _suspected_false_positive_reason(row: dict[str, Any], active_llm_pages: set[int]) -> str | None:
    route_label = str(row.get("route_label") or "").strip().lower()
    recommended_execution = str(row.get("recommended_execution") or "").strip().lower()
    pass1_path = str(row.get("pass1_path") or "").strip()
    page_number = int(row["page_number"])
    has_table = bool(row.get("has_table"))
    hard_page_reasons = [str(reason) for reason in row.get("hard_page_reasons") or []]
    summary = str(row.get("page_summary") or "").strip()

    if route_label != _TEXT_RICH_LABEL:
        return None
    if not (recommended_execution == "selective_visual_enrichment" or page_number in active_llm_pages):
        return None
    if not row.get("text_first_likely"):
        return None
    if has_table:
        return None
    if not summary:
        return None

    visual_weight, other_weight = _visual_weight(hard_page_reasons)
    if visual_weight <= 0 or visual_weight < other_weight:
        return None
    if not _summary_is_text_centric(summary) and pass1_path != _TEXT_FIRST_PATH:
        return None

    if pass1_path == _TEXT_FIRST_PATH and page_number in active_llm_pages:
        return "text-first compatible page remains on llm path because visual flags dominate routing"
    if page_number == 1 and any(reason.startswith("image_count") for reason in hard_page_reasons):
        return "first page image density likely overstates true visual reasoning need"
    if any(reason == "has_figure" for reason in hard_page_reasons):
        return "text-rich body but elevated by decorative/illustrative figure count"
    return "table/figure flag dominates score although explanation remains text-centric"


def _selection_sort_key(row: dict[str, Any]) -> tuple[int, int]:
    return (-int(row.get("hard_page_score") or 0), int(row["page_number"]))


def _load_page_artifact(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    return _load_json(path)


def _page_row(
    *,
    document_id: str,
    source_pdf_relpath: str,
    expected_type: str | None,
    page_number: int,
    page_manifest_entry: dict[str, Any],
    page_routing_entry: dict[str, Any],
    pass1_artifact: dict[str, Any] | None,
    pass2_artifact: dict[str, Any] | None,
    hard_candidate_pages: set[int],
    active_llm_pages: set[int],
) -> dict[str, Any]:
    pass1_meta = (pass1_artifact or {}).get("meta") or {}
    pass1_result = (pass1_artifact or {}).get("result") or {}
    pass2_meta = (pass2_artifact or {}).get("meta") or {}
    row = {
        "document_id": document_id,
        "source_pdf_relpath": source_pdf_relpath,
        "expected_type": expected_type,
        "page_number": page_number,
        "route_label": page_manifest_entry.get("route_label"),
        "route_reason": page_manifest_entry.get("route_reason"),
        "pass1_path": pass1_meta.get("pass1_path"),
        "hard_page_score": page_routing_entry.get("hard_page_score"),
        "hard_page_reasons": list(page_routing_entry.get("hard_page_reasons") or []),
        "recommended_execution": page_routing_entry.get("recommended_execution"),
        "image_count": page_manifest_entry.get("image_count"),
        "has_table": page_manifest_entry.get("has_table"),
        "has_figure": page_manifest_entry.get("has_figure"),
        "text_length": page_manifest_entry.get("text_length"),
        "block_count": page_manifest_entry.get("block_count"),
        "non_empty_text_block_count": page_manifest_entry.get("non_empty_text_block_count"),
        "page_role": pass1_result.get("page_role"),
        "page_summary": pass1_result.get("page_summary"),
        "pass2_generation_mode": pass2_meta.get("pass2_generation_mode"),
        "is_spine_hard_candidate": page_number in hard_candidate_pages,
    }
    row["text_first_likely"] = _text_first_likely(
        pass1_path=row["pass1_path"],
        route_label=row["route_label"],
        text_length=row["text_length"],
        non_empty_text_block_count=row["non_empty_text_block_count"],
        block_count=row["block_count"],
    )
    row["suspected_false_positive_reason"] = _suspected_false_positive_reason(row, active_llm_pages)
    return row


def _collect_document_rows(
    *,
    source_pdf_relpath: str,
    expected_type: str | None,
    document_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    paths = _require_document_artifacts(document_id)
    processing_benchmark = _load_json(paths["processing_benchmark"])
    document_spine = _load_json(paths["document_spine"])
    page_routing = _load_json(paths["page_routing"])
    page_manifest = _load_json(paths["page_manifest"])

    manifest_by_page = {
        int(page["page_number"]): dict(page)
        for page in page_manifest.get("pages", [])
    }
    routing_by_page = {
        int(page["page_number"]): dict(page)
        for page in page_routing["result"].get("pages", [])
    }
    hard_candidate_pages = _hard_candidate_pages(document_spine)
    active_llm_pages = {int(page) for page in processing_benchmark.get("pass2_llm_pages") or []}
    active_compat_pages = {int(page) for page in processing_benchmark.get("pass2_compat_pages") or []}

    all_pages = sorted(set(manifest_by_page) & set(routing_by_page))
    rows: list[dict[str, Any]] = []
    for page_number in all_pages:
        pass1_path = paths["analysis_dir"] / "pages" / str(page_number) / "page_analysis_pass1.json"
        pass2_path = paths["analysis_dir"] / "pages" / str(page_number) / "page_analysis_pass2.json"
        row = _page_row(
            document_id=document_id,
            source_pdf_relpath=source_pdf_relpath,
            expected_type=expected_type,
            page_number=page_number,
            page_manifest_entry=manifest_by_page[page_number],
            page_routing_entry=routing_by_page[page_number],
            pass1_artifact=_load_page_artifact(pass1_path),
            pass2_artifact=_load_page_artifact(pass2_path),
            hard_candidate_pages=hard_candidate_pages,
            active_llm_pages=active_llm_pages,
        )
        row["_active_llm"] = page_number in active_llm_pages
        row["_active_compat"] = page_number in active_compat_pages
        rows.append(row)

    document_context = {
        "paths": paths,
        "processing_benchmark": processing_benchmark,
        "document_spine": document_spine,
        "page_routing": page_routing,
        "routing_distribution": _summarize_routing_distribution(page_routing),
        "hard_candidate_pages": hard_candidate_pages,
    }
    return document_context, rows


def _append_bucket(
    *,
    selected: list[dict[str, Any]],
    seen_pages: set[int],
    candidates: list[dict[str, Any]],
    selection_bucket: str,
    limit: int,
) -> None:
    count = 0
    for row in candidates:
        page_number = int(row["page_number"])
        if page_number in seen_pages:
            continue
        enriched = dict(row)
        enriched["selection_bucket"] = selection_bucket
        selected.append(enriched)
        seen_pages.add(page_number)
        count += 1
        if count >= limit:
            break


def _select_document_pages(
    *,
    document_role: str,
    document_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_pages: set[int] = set()

    llm_text_rows = [
        row
        for row in document_rows
        if row["_active_llm"] and row.get("route_label") == _TEXT_RICH_LABEL
    ]
    llm_reference_rows = [
        row
        for row in document_rows
        if row["_active_llm"]
    ]
    compat_text_rows = [
        row
        for row in document_rows
        if row["_active_compat"] and row.get("route_label") == _TEXT_RICH_LABEL
    ]
    conflict_rows = [
        row
        for row in llm_text_rows
        if not row.get("is_spine_hard_candidate")
    ]
    hard_aligned_rows = [
        row
        for row in document_rows
        if row.get("is_spine_hard_candidate") and row["_active_llm"]
    ]

    llm_text_rows.sort(key=_selection_sort_key)
    llm_reference_rows.sort(key=_selection_sort_key)
    compat_text_rows.sort(key=lambda row: int(row["page_number"]))
    conflict_rows.sort(key=_selection_sort_key)
    hard_aligned_rows.sort(key=_selection_sort_key)

    if document_role == "outlier":
        _append_bucket(
            selected=selected,
            seen_pages=seen_pages,
            candidates=conflict_rows,
            selection_bucket="outlier_spine_conflict",
            limit=2,
        )
        _append_bucket(
            selected=selected,
            seen_pages=seen_pages,
            candidates=llm_text_rows,
            selection_bucket="outlier_llm_text_rich",
            limit=5,
        )
        _append_bucket(
            selected=selected,
            seen_pages=seen_pages,
            candidates=compat_text_rows,
            selection_bucket="outlier_compat_text_rich",
            limit=3,
        )
    else:
        _append_bucket(
            selected=selected,
            seen_pages=seen_pages,
            candidates=hard_aligned_rows,
            selection_bucket="success_spine_alignment",
            limit=1,
        )
        _append_bucket(
            selected=selected,
            seen_pages=seen_pages,
            candidates=llm_reference_rows,
            selection_bucket="success_llm_reference",
            limit=2,
        )
        _append_bucket(
            selected=selected,
            seen_pages=seen_pages,
            candidates=compat_text_rows,
            selection_bucket="success_compat_text_rich",
            limit=3,
        )

    return sorted(selected, key=lambda row: int(row["page_number"]))


def _select_documents(
    *,
    comparison_payload: dict[str, Any],
    manifest_map: dict[str, dict[str, Any]],
    active_key: str,
    baseline_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outlier_candidates: list[dict[str, Any]] = []
    success_candidates: list[dict[str, Any]] = []

    for document in comparison_payload.get("documents", []):
        source_pdf_relpath = _normalize_relpath(document.get("source_pdf_relpath"))
        if not source_pdf_relpath:
            continue
        expected_type = (manifest_map.get(source_pdf_relpath) or {}).get("expected_type")
        active_run = dict(document["runs"][active_key])
        baseline_run = dict(document["runs"][baseline_key])
        document_id = str(active_run["document_id"])
        spine_payload = _load_json(_active_doc_paths(document_id)["document_spine"])
        hard_page_count = int(spine_payload["result"]["routing_summary"]["hard_page_count"])
        baseline_llm = int(baseline_run.get("pass2_llm_count") or 0)
        active_llm = int(active_run.get("pass2_llm_count") or 0)
        rendered_pages = int(active_run.get("rendered_pages") or 0)
        reduction_ratio = 0.0 if baseline_llm == 0 else 1 - (active_llm / baseline_llm)
        record = {
            "source_pdf_relpath": source_pdf_relpath,
            "expected_type": expected_type,
            "active_document_id": document_id,
            "baseline_document_id": str(baseline_run["document_id"]),
            "active_llm_count": active_llm,
            "baseline_llm_count": baseline_llm,
            "rendered_pages": rendered_pages,
            "active_llm_share": 0.0 if rendered_pages == 0 else active_llm / rendered_pages,
            "reduction_ratio": reduction_ratio,
            "llm_vs_hard_gap": active_llm - hard_page_count,
        }

        if expected_type in {"text_rich", "mixed"}:
            outlier_candidates.append(record)

        if (
            str(active_run.get("final_status")) == "completed"
            and str(baseline_run.get("final_status")) == "completed"
            and int(active_run.get("pass2_compat_count") or 0) > 0
            and baseline_llm >= 2
            and rendered_pages >= 5
        ):
            success_candidates.append(record)

    outlier_candidates.sort(
        key=lambda record: (
            float(record["reduction_ratio"]),
            -float(record["active_llm_share"]),
            -int(record["llm_vs_hard_gap"]),
            -int(record["active_llm_count"]),
        )
    )
    success_candidates.sort(
        key=lambda record: (
            -float(record["reduction_ratio"]),
            record["source_pdf_relpath"],
            record["source_pdf_relpath"],
        )
    )
    return outlier_candidates[:3], success_candidates[:2]


def _count_reasons(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = row.get("suspected_false_positive_reason")
        if not reason:
            continue
        counts[str(reason)] = counts.get(str(reason), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _top_priority_rule(candidate_counts: dict[str, int]) -> dict[str, Any]:
    ordered = sorted(candidate_counts.items(), key=lambda item: (-item[1], item[0]))
    if not ordered or ordered[0][1] <= 0:
        return {
            "rule": "inconclusive",
            "why": "No tuning candidate has positive support from the current audit rows.",
            "supporting_page_count": 0,
        }
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return {
            "rule": "inconclusive",
            "why": "Top candidate counts are tied, so the audit does not separate one dominant rule strongly enough yet.",
            "supporting_page_count": ordered[0][1],
        }
    reasons = {
        "figure/image signal attenuation under text-rich + no-table pages": (
            "The strongest repeated pattern is text-rich, no-table pages whose hard-page score is mostly driven by figure/image signals."
        ),
        "first-page cover/title heuristic": (
            "A smaller but repeated cluster comes from first-page/title layouts where image density likely overstates visual reasoning need."
        ),
        "text-first override when page is not a spine hard candidate": (
            "Several text-first-compatible pages remain on the llm path even though they are not spine hard candidates."
        ),
    }
    return {
        "rule": ordered[0][0],
        "why": reasons[ordered[0][0]],
        "supporting_page_count": ordered[0][1],
    }


def _build_summary(
    *,
    documents: list[dict[str, Any]],
    all_rows_by_document_id: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    outlier_documents = [document for document in documents if document["document_role"] == "outlier"]
    success_documents = [document for document in documents if document["document_role"] == "success_reference"]
    outlier_rows = [
        row
        for document in outlier_documents
        for row in all_rows_by_document_id[document["document_id"]]
    ]
    success_rows = [
        row
        for document in success_documents
        for row in all_rows_by_document_id[document["document_id"]]
    ]
    suspected_rows = [row for row in outlier_rows if row.get("suspected_false_positive_reason")]

    false_positive_counts = _count_reasons(outlier_rows)
    visual_dominant_no_table_count = sum(
        1
        for row in suspected_rows
        if not row.get("has_table")
        and any(
            reason in {"has_figure", "image_count>=1", "image_count>=3"}
            for reason in row.get("hard_page_reasons") or []
        )
    )
    first_page_count = sum(1 for row in suspected_rows if int(row["page_number"]) == 1)
    text_first_non_hard_count = sum(
        1
        for row in suspected_rows
        if row.get("pass1_path") == _TEXT_FIRST_PATH and not row.get("is_spine_hard_candidate")
    )

    repeated_patterns = []
    if visual_dominant_no_table_count > 0:
        repeated_patterns.append(
            f"{visual_dominant_no_table_count}개의 outlier 페이지가 no-table text-rich body인데 has_figure/image_count 신호가 hard-page score를 끌어올렸다."
        )
    if text_first_non_hard_count > 0:
        repeated_patterns.append(
            f"{text_first_non_hard_count}개의 outlier 페이지가 이미 pass1 text-first compatible인데도 spine hard candidate가 아닌 상태로 llm path에 남아 있다."
        )
    if first_page_count > 0:
        repeated_patterns.append(
            f"{first_page_count}개의 suspected false-positive 페이지는 first-page/title 성격이라 image density가 실제 visual reasoning need를 과대평가했을 가능성이 있다."
        )

    success_text_first_count = sum(
        1
        for row in success_rows
        if row.get("route_label") == _TEXT_RICH_LABEL
        and row.get("recommended_execution") == "text_first"
        and row["_active_compat"]
    )
    success_hard_alignment_count = sum(
        1
        for row in success_rows
        if row.get("is_spine_hard_candidate") and row["_active_llm"]
    )
    success_patterns = []
    if success_text_first_count > 0:
        success_patterns.append(
            f"성공 사례에서는 text-rich 페이지 {success_text_first_count}개가 text_first -> compat로 내려가면서 bulk reduction을 만들었다."
        )
    if success_hard_alignment_count > 0:
        success_patterns.append(
            f"남은 llm 페이지 {success_hard_alignment_count}개는 spine hard candidate와 정렬돼 있어, llm 유지 이유가 비교적 명확하다."
        )

    candidate_counts = {
        "figure/image signal attenuation under text-rich + no-table pages": visual_dominant_no_table_count,
        "first-page cover/title heuristic": first_page_count,
        "text-first override when page is not a spine hard candidate": text_first_non_hard_count,
    }

    return {
        "repeated_false_positive_patterns": repeated_patterns,
        "false_positive_reason_counts": false_positive_counts,
        "success_reference_patterns": success_patterns,
        "tuning_candidates": [
            {
                "candidate": candidate,
                "supporting_page_count": count,
            }
            for candidate, count in sorted(candidate_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "top_priority_rule": _top_priority_rule(candidate_counts),
        "evidence_limitations": [
            "This audit uses existing artifacts only and does not rerun the pipeline.",
            "suspected_false_positive_reason is a heuristic audit label, not a ground-truth routing verdict.",
            "Page-level conclusions are derived primarily from comparison, benchmark, spine, routing, page_manifest, and pass1/pass2 artifacts.",
        ],
    }


def _build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Routing Audit for Text-Rich False Positive Escalation",
        "",
        "## 1. Audit scope and source artifacts",
        f"- generated_at: `{payload['generated_at']}`",
        f"- comparison_json: `{payload['source_artifacts']['comparison_json']}`",
        f"- corpus_manifest_path: `{payload['source_artifacts']['corpus_manifest_path']}`",
        "",
        "## 2. Document selection rules",
        "- outlier rule:",
        f"  - expected_type filter: `{payload['document_selection']['outlier_rule']['expected_types']}`",
        f"  - sort: `{payload['document_selection']['outlier_rule']['sort']}`",
        f"  - selected: `{payload['document_selection']['outlier_ids']}`",
        "- success reference rule:",
        f"  - completed_both_runs: `{payload['document_selection']['success_rule']['completed_both_runs']}`",
        f"  - requires_active_compat: `{payload['document_selection']['success_rule']['requires_active_compat']}`",
        f"  - sort: `{payload['document_selection']['success_rule']['sort']}`",
        f"  - selected: `{payload['document_selection']['success_ids']}`",
        "",
        "## 3. Repeated false positive patterns",
    ]
    for pattern in payload["summary"]["repeated_false_positive_patterns"]:
        lines.append(f"- {pattern}")
    if payload["summary"]["false_positive_reason_counts"]:
        lines.append("- false_positive_reason_counts:")
        for reason, count in payload["summary"]["false_positive_reason_counts"].items():
            lines.append(f"  - `{reason}`: `{count}`")
    lines.extend(
        [
            "",
            "## 4. Outlier document deep dives",
        ]
    )
    for document in payload["documents"]:
        if document["document_role"] != "outlier":
            continue
        lines.extend(
            [
                f"### `{document['document_id']}` / `{document['source_pdf_relpath']}`",
                f"- expected_type: `{document.get('expected_type')}`",
                f"- rendered_pages: `{document['active_metrics'].get('rendered_pages')}`",
                f"- baseline pass2_llm_count: `{document['baseline_metrics'].get('pass2_llm_count')}`",
                f"- active pass2_llm_count: `{document['active_metrics'].get('pass2_llm_count')}`",
                f"- active pass2_compat_count: `{document['active_metrics'].get('pass2_compat_count')}`",
                f"- hard_page_count: `{document['spine_summary'].get('hard_page_count')}`",
                f"- routing_distribution: `{document['routing_distribution']}`",
                "",
                "| page | bucket | route_label | pass1_path | hard_score | recommended_execution | has_figure | image_count | has_table | text_first_likely | pass2_mode | suspected_false_positive_reason |",
                "| ---: | --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |",
            ]
        )
        for row in document["pages"]:
            lines.append(
                "| {page_number} | {selection_bucket} | {route_label} | {pass1_path} | {hard_page_score} | {recommended_execution} | {has_figure} | {image_count} | {has_table} | {text_first_likely} | {pass2_generation_mode} | {suspected_false_positive_reason} |".format(
                    page_number=row["page_number"],
                    selection_bucket=row["selection_bucket"],
                    route_label=row.get("route_label"),
                    pass1_path=row.get("pass1_path"),
                    hard_page_score=row.get("hard_page_score"),
                    recommended_execution=row.get("recommended_execution"),
                    has_figure=row.get("has_figure"),
                    image_count=row.get("image_count"),
                    has_table=row.get("has_table"),
                    text_first_likely=row.get("text_first_likely"),
                    pass2_generation_mode=row.get("pass2_generation_mode"),
                    suspected_false_positive_reason=row.get("suspected_false_positive_reason"),
                )
            )
        lines.append("")
    lines.append("## 5. Success reference patterns")
    for pattern in payload["summary"]["success_reference_patterns"]:
        lines.append(f"- {pattern}")
    for document in payload["documents"]:
        if document["document_role"] != "success_reference":
            continue
        lines.extend(
            [
                f"### `{document['document_id']}` / `{document['source_pdf_relpath']}`",
                f"- expected_type: `{document.get('expected_type')}`",
                f"- baseline pass2_llm_count: `{document['baseline_metrics'].get('pass2_llm_count')}`",
                f"- active pass2_llm_count: `{document['active_metrics'].get('pass2_llm_count')}`",
                f"- active pass2_compat_count: `{document['active_metrics'].get('pass2_compat_count')}`",
                f"- hard_page_count: `{document['spine_summary'].get('hard_page_count')}`",
                f"- routing_distribution: `{document['routing_distribution']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## 6. Next tuning candidates",
        ]
    )
    for candidate in payload["summary"]["tuning_candidates"]:
        lines.append(
            f"- `{candidate['candidate']}`: supporting_page_count=`{candidate['supporting_page_count']}`"
        )
    lines.extend(
        [
            "",
            "## 7. First rule to touch",
            f"- rule: `{payload['summary']['top_priority_rule']['rule']}`",
            f"- why: {payload['summary']['top_priority_rule']['why']}",
            f"- supporting_page_count: `{payload['summary']['top_priority_rule']['supporting_page_count']}`",
            "",
            "## 8. Evidence limitations",
        ]
    )
    for limitation in payload["summary"]["evidence_limitations"]:
        lines.append(f"- {limitation}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    comparison_path = _resolve_existing_file(args.comparison_json)
    corpus_manifest_path = _resolve_existing_file(args.corpus_manifest)
    output_prefix = _resolve_output_prefix(args.output_prefix)

    comparison_payload = _load_json(comparison_path)
    manifest_path, manifest_map = _load_manifest(comparison_payload, corpus_manifest_path)
    run_map = _comparison_run_map(comparison_payload)
    active_key, baseline_key = _active_and_baseline_run_keys(run_map)

    outliers, successes = _select_documents(
        comparison_payload=comparison_payload,
        manifest_map=manifest_map,
        active_key=active_key,
        baseline_key=baseline_key,
    )

    documents: list[dict[str, Any]] = []
    all_rows_by_document_id: dict[str, list[dict[str, Any]]] = {}
    selected_document_ids = {
        *[record["active_document_id"] for record in outliers],
        *[record["active_document_id"] for record in successes],
    }
    for role, records in (("outlier", outliers), ("success_reference", successes)):
        for record in records:
            source_pdf_relpath = record["source_pdf_relpath"]
            comparison_document = next(
                doc
                for doc in comparison_payload["documents"]
                if _normalize_relpath(doc.get("source_pdf_relpath")) == source_pdf_relpath
            )
            document_id = record["active_document_id"]
            document_context, document_rows = _collect_document_rows(
                source_pdf_relpath=source_pdf_relpath,
                expected_type=record["expected_type"],
                document_id=document_id,
            )
            all_rows_by_document_id[document_id] = document_rows
            selected_rows = _select_document_pages(
                document_role=role,
                document_rows=document_rows,
            )
            documents.append(
                {
                    "document_role": role,
                    "document_id": document_id,
                    "source_pdf_relpath": source_pdf_relpath,
                    "expected_type": record["expected_type"],
                    "baseline_metrics": comparison_document["runs"][baseline_key],
                    "active_metrics": comparison_document["runs"][active_key],
                    "active_llm_share": record["active_llm_share"],
                    "spine_summary": document_context["document_spine"]["result"]["routing_summary"],
                    "routing_distribution": document_context["routing_distribution"],
                    "page_counts": {
                        "selected_pages": len(selected_rows),
                        "active_llm_pages": len(
                            document_context["processing_benchmark"].get("pass2_llm_pages") or []
                        ),
                        "active_compat_pages": len(
                            document_context["processing_benchmark"].get("pass2_compat_pages") or []
                        ),
                    },
                    "pages": selected_rows,
                }
            )

    documents.sort(
        key=lambda document: (
            document["document_role"] != "outlier",
            document["source_pdf_relpath"],
        )
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_artifacts": {
            "comparison_json": comparison_path.as_posix(),
            "corpus_manifest_path": manifest_path.as_posix(),
        },
        "document_selection": {
            "outlier_rule": {
                "expected_types": ["text_rich", "mixed"],
                "sort": [
                    "reduction_ratio asc",
                    "active_llm_share desc",
                    "llm_vs_hard_gap desc",
                    "active pass2_llm_count desc",
                ],
            },
            "success_rule": {
                "completed_both_runs": True,
                "requires_active_compat": True,
                "baseline_pass2_llm_count_gte": 2,
                "rendered_pages_gte": 5,
                "sort": [
                    "reduction_ratio desc",
                    "source_pdf_relpath asc",
                ],
            },
            "outlier_ids": [record["active_document_id"] for record in outliers],
            "success_ids": [record["active_document_id"] for record in successes],
            "selected_document_ids": sorted(selected_document_ids),
        },
        "documents": documents,
        "summary": _build_summary(
            documents=documents,
            all_rows_by_document_id=all_rows_by_document_id,
        ),
    }

    json_path = output_prefix.with_suffix(".json")
    markdown_path = output_prefix.with_suffix(".md")
    _write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    _write_text(markdown_path, _build_markdown(payload))
    print(f"Saved routing audit JSON to {json_path}")
    print(f"Saved routing audit Markdown to {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
