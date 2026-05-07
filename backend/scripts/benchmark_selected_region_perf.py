#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


RUNNER_VERSION = "selected_region_perf_v0_1"


@dataclass(frozen=True)
class SelectionSpec:
    document_id: str
    page_number: int
    selected_bbox: list[float]
    label: str | None = None
    source: str = "manual"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0.")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark Scholium selected-region readiness and optional real explanation latency. "
            "Dry-run is enabled by default and does not call the LLM provider."
        ),
    )
    parser.add_argument(
        "--selection",
        action="append",
        help=(
            "Selection JSON object. Expected keys: document_id, page_number, "
            "selected_bbox or bbox, optional label."
        ),
    )
    parser.add_argument(
        "--selection-file",
        help="JSON file containing a selection object, a list, or an object with a selections list.",
    )
    parser.add_argument("--document-id", help="Document id for a single CLI selection.")
    parser.add_argument("--page-number", type=_positive_int, help="Page number for a single CLI selection.")
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("X", "Y", "W", "H"),
        help="Normalized selected bbox for a single CLI selection.",
    )
    parser.add_argument(
        "--auto-first-ready",
        action="store_true",
        help="Build selections from the first processed pages that have rendered page images and pass1 page elements.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=5,
        help="Maximum auto-generated selections when using --auto-first-ready.",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When true, validate prerequisites and bbox matching without calling the LLM provider.",
    )
    parser.add_argument("--mode-name", help="Human-readable label for this run.")
    parser.add_argument("--output", help="Output JSON path. Use '-' for stdout.")
    parser.add_argument(
        "--output-dir",
        help="Directory for timestamped output JSON. Ignored when --output is provided.",
    )
    parser.add_argument(
        "--no-output",
        action="store_true",
        help="Do not write JSON; print summary only.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit non-zero if any selection fails or is not ready.",
    )
    return parser


def _default_output_path(output_dir: Path | None = None) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base_dir = output_dir or (PROJECT_ROOT / "docs" / "perf_runs")
    return base_dir / f"{timestamp}_selected_region_perf.json"


def _resolve_output_path(raw_output: str | None, raw_output_dir: str | None) -> Path | str:
    if raw_output == "-":
        return "-"
    if raw_output:
        return Path(raw_output).expanduser().resolve()
    output_dir = Path(raw_output_dir).expanduser().resolve() if raw_output_dir else None
    return _default_output_path(output_dir)


def _get_git_head() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    normalized = result.stdout.strip()
    return normalized or None


def _coerce_bbox(value: object) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("selected_bbox must be a list of four numbers.")
    bbox = [float(component) for component in value]
    if any(not math.isfinite(component) for component in bbox):
        raise ValueError("selected_bbox values must be finite numbers.")
    return bbox


def _selection_from_object(raw: object, *, source: str) -> SelectionSpec:
    if not isinstance(raw, dict):
        raise ValueError("Selection entry must be a JSON object.")
    document_id = str(raw.get("document_id") or "").strip()
    if not document_id:
        raise ValueError("Selection entry is missing document_id.")
    page_number = int(raw.get("page_number") or 0)
    if page_number <= 0:
        raise ValueError("Selection entry page_number must be > 0.")
    bbox_raw = raw.get("selected_bbox", raw.get("bbox"))
    bbox = _coerce_bbox(bbox_raw)
    label = raw.get("label")
    return SelectionSpec(
        document_id=document_id,
        page_number=page_number,
        selected_bbox=bbox,
        label=str(label).strip() if label else None,
        source=source,
    )


def _load_selection_file(path: Path) -> list[SelectionSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("selections"), list):
        entries = payload["selections"]
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = [payload]
    return [_selection_from_object(entry, source=f"selection_file:{path.as_posix()}") for entry in entries]


def _load_inline_selections(raw_items: list[str] | None) -> list[SelectionSpec]:
    specs: list[SelectionSpec] = []
    for raw_item in raw_items or []:
        specs.append(_selection_from_object(json.loads(raw_item), source="inline_json"))
    return specs


def _load_cli_selection(args: argparse.Namespace) -> list[SelectionSpec]:
    has_any = bool(args.document_id or args.page_number or args.bbox)
    if not has_any:
        return []
    if not args.document_id or not args.page_number or not args.bbox:
        raise SystemExit("--document-id, --page-number, and --bbox must be provided together.")
    return [
        SelectionSpec(
            document_id=args.document_id,
            page_number=args.page_number,
            selected_bbox=[float(value) for value in args.bbox],
            label="cli_selection",
            source="cli",
        )
    ]


def _candidate_bbox(candidate: object) -> list[float] | None:
    if not isinstance(candidate, dict):
        return None
    try:
        bbox = _coerce_bbox(candidate.get("bbox"))
    except Exception:
        return None
    x, y, width, height = bbox
    if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > 1 or y + height > 1:
        return None
    return bbox


def _is_rendered_status(value: object) -> bool:
    return getattr(value, "value", value) == "rendered"


def _load_auto_selections(storage: Any, limit: int) -> list[SelectionSpec]:
    specs: list[SelectionSpec] = []
    documents = storage.list_documents(limit=200)
    for document in documents:
        for page in storage.get_pages(document.document_id):
            if not _is_rendered_status(page.render_status):
                continue
            try:
                pass1_artifact = storage.load_pass1_result(document.document_id, page.page_number)
            except Exception:
                continue
            if not isinstance(pass1_artifact, dict):
                continue
            result = pass1_artifact.get("result")
            if not isinstance(result, dict):
                continue
            candidates = result.get("page_elements") or result.get("candidate_anchors")
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                bbox = _candidate_bbox(candidate)
                if bbox is None:
                    continue
                label = str(
                    candidate.get("label")
                    or candidate.get("element_id")
                    or candidate.get("anchor_id")
                    or "auto_selection"
                )
                specs.append(
                    SelectionSpec(
                        document_id=document.document_id,
                        page_number=page.page_number,
                        selected_bbox=bbox,
                        label=label,
                        source="auto_first_ready",
                    )
                )
                if len(specs) >= limit:
                    return specs
    return specs


def _load_selection_specs(args: argparse.Namespace, storage: Any) -> list[SelectionSpec]:
    specs: list[SelectionSpec] = []
    if args.selection_file:
        specs.extend(_load_selection_file(Path(args.selection_file).expanduser().resolve()))
    specs.extend(_load_inline_selections(args.selection))
    specs.extend(_load_cli_selection(args))
    if args.auto_first_ready:
        specs.extend(_load_auto_selections(storage, args.limit))
    if not specs:
        raise SystemExit(
            "No selections provided. Use --selection-file, --selection, "
            "--document-id/--page-number/--bbox, or --auto-first-ready."
        )
    return specs


def _round_number(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _safe_relative(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _bbox_metrics(bbox: list[float]) -> dict[str, float | None]:
    x, y, width, height = bbox
    area = max(0.0, width) * max(0.0, height)
    return {
        "selected_region_width": _round_number(width),
        "selected_region_height": _round_number(height),
        "selected_region_area": _round_number(area),
        "selected_region_center_x": _round_number(x + width / 2),
        "selected_region_center_y": _round_number(y + height / 2),
        "selected_region_aspect_ratio": _round_number(width / height) if height > 0 else None,
    }


def _summarize_matches(matches: list[dict[str, Any]]) -> dict[str, Any]:
    top = matches[0] if matches else {}
    element_ids = [
        item.get("element_id") or item.get("anchor_id")
        for item in matches
        if item.get("element_id") or item.get("anchor_id")
    ]
    element_types = sorted(
        {
            str(item.get("element_type") or item.get("anchor_type"))
            for item in matches
            if item.get("element_type") is not None or item.get("anchor_type") is not None
        }
    )
    return {
        "matched_preprocessed_element_count": len(matches),
        "matched_element_ids": element_ids,
        "matched_element_types": element_types,
        "matched_element_anchor_ids": element_ids,
        "matched_element_anchor_types": element_types,
        "top_matched_element_id": top.get("element_id") or top.get("anchor_id"),
        "top_matched_anchor_id": top.get("element_id") or top.get("anchor_id"),
        "top_matched_element_label": top.get("label"),
        "top_matched_anchor_label": top.get("label"),
        "top_match_score": top.get("match_score"),
        "top_selection_overlap_ratio": top.get("selection_overlap_ratio"),
        "top_selection_center_distance": top.get("selection_center_distance"),
    }


def _summarize_result(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {
            "result_element_type": None,
            "result_anchor_type": None,
            "result_confidence": None,
            "result_study_importance": None,
            "result_related_pages_count": None,
            "result_source_cues_count": None,
            "result_short_explanation_chars": None,
            "result_long_explanation_chars": None,
            "result_concept_title": None,
        }
    related_pages = result.get("related_pages")
    source_cues = result.get("source_cues")
    short_explanation = result.get("short_explanation")
    long_explanation = result.get("long_explanation")
    return {
        "result_element_type": result.get("element_type") or result.get("anchor_type"),
        "result_anchor_type": result.get("element_type") or result.get("anchor_type"),
        "result_confidence": result.get("confidence"),
        "result_study_importance": result.get("study_importance"),
        "result_related_pages_count": len(related_pages) if isinstance(related_pages, list) else None,
        "result_source_cues_count": len(source_cues) if isinstance(source_cues, list) else None,
        "result_short_explanation_chars": len(short_explanation) if isinstance(short_explanation, str) else None,
        "result_long_explanation_chars": len(long_explanation) if isinstance(long_explanation, str) else None,
        "result_concept_title": result.get("concept_title") or result.get("label"),
    }


def _inspect_prerequisites(
    *,
    storage: Any,
    service: Any,
    spec: SelectionSpec,
    selected_bbox: list[float],
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    row: dict[str, Any] = {}
    matches: list[dict[str, Any]] = []
    selection_context: dict[str, Any] | None = None

    page = storage.get_page(spec.document_id, spec.page_number)
    row["page_record_found"] = page is not None
    row["rendered_page_ready"] = bool(page and _is_rendered_status(page.render_status))
    row["page_image_relpath"] = page.image_path if page else None
    if page is not None:
        image_path = storage.resolve_relative_path(page.image_path)
        row["page_image_exists"] = image_path.exists()
    else:
        row["page_image_exists"] = False

    try:
        pass1_artifact = storage.load_pass1_result(spec.document_id, spec.page_number)
        row["pass1_available"] = pass1_artifact is not None
        row["pass1_load_error"] = None
    except Exception as exc:
        pass1_artifact = None
        row["pass1_available"] = False
        row["pass1_load_error"] = str(exc)

    document_summary = None
    try:
        document_summary = storage.load_document_summary(spec.document_id)
        row["document_summary_available"] = document_summary is not None
        row["document_summary_load_error"] = None
    except Exception as exc:
        row["document_summary_available"] = False
        row["document_summary_load_error"] = str(exc)

    if isinstance(pass1_artifact, dict) and isinstance(pass1_artifact.get("result"), dict):
        selection_context = service.selection_context_builder.build(
            document_id=spec.document_id,
            page_number=spec.page_number,
            selected_bbox=selected_bbox,
            pass1_artifact=dict(pass1_artifact),
            document_summary_artifact=dict(document_summary) if isinstance(document_summary, dict) else None,
        )
        matches = list(selection_context.get("matched_page_elements", []))
        metrics = dict(selection_context.get("metrics") or {})
        row["selection_context_size_chars"] = metrics.get("selection_context_size_chars")
        row["selection_context_hash"] = selection_context.get("context_hash")
        row["nearby_text_block_count"] = metrics.get("nearby_text_block_count")
        row["source_candidate_count"] = metrics.get("source_candidate_count")

    return row, selection_context, matches


def _benchmark_selection(
    *,
    storage: Any,
    service: Any,
    spec: SelectionSpec,
    dry_run: bool,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    row: dict[str, Any] = {
        "label": spec.label,
        "source": spec.source,
        "document_id": spec.document_id,
        "page_number": spec.page_number,
        "selected_bbox": spec.selected_bbox,
        "selection_id": None,
        "dry_run": dry_run,
        "status": "unknown",
        "error_message": None,
        **_bbox_metrics(spec.selected_bbox),
    }

    try:
        service._validate_selected_bbox(spec.selected_bbox)  # noqa: SLF001
        row["selected_bbox_valid"] = True
    except Exception as exc:
        row["selected_bbox_valid"] = False
        row["status"] = "failed"
        row["error_message"] = str(exc)
        row["total_wall_time_seconds"] = _round_number(time.perf_counter() - started_at)
        return row

    dry_run_started_at = time.perf_counter()
    prereq_row, selection_context, matches = _inspect_prerequisites(
        storage=storage,
        service=service,
        spec=spec,
        selected_bbox=spec.selected_bbox,
    )
    row.update(prereq_row)
    row.update(_summarize_matches(matches))
    row["dry_run_check_seconds"] = _round_number(time.perf_counter() - dry_run_started_at)
    row["service_call_seconds"] = None

    selection_id = None
    cached_before = None
    if selection_context is not None:
        selection_id = service._build_selection_id(  # noqa: SLF001 - benchmark records app-compatible ids.
            document_id=spec.document_id,
            page_number=spec.page_number,
            selected_bbox=spec.selected_bbox,
            selection_context=selection_context,
        )
        row["selection_id"] = selection_id
        artifact_path = storage.get_selection_explanation_path(
            spec.document_id,
            spec.page_number,
            selection_id,
        )
        row["selection_artifact_path"] = _safe_relative(artifact_path)
        cached_before = storage.load_selection_explanation(spec.document_id, spec.page_number, selection_id)
    else:
        row["selection_artifact_path"] = None
    row["cache_hit_before"] = cached_before is not None
    row["cache_matches_current_provider_before"] = (
        service._cache_matches_current_provider(cached_before, selection_context)
        if cached_before is not None and selection_context is not None
        else False
    )

    ready = all(
        bool(row.get(field_name))
        for field_name in (
            "page_record_found",
            "rendered_page_ready",
            "page_image_exists",
            "pass1_available",
        )
    )

    if dry_run:
        row["status"] = "ready" if ready else "not_ready"
        if not ready:
            row["error_message"] = "Selection prerequisites are incomplete; no provider call attempted."
        cached_after = (
            storage.load_selection_explanation(spec.document_id, spec.page_number, selection_id)
            if selection_id is not None
            else None
        )
        row["cache_hit_after"] = cached_after is not None
        row["selection_artifact_available_after"] = cached_after is not None
        row.update(_summarize_result(dict(cached_after["result"]) if cached_after else None))
        row["total_wall_time_seconds"] = _round_number(time.perf_counter() - started_at)
        return row

    service_started_at = time.perf_counter()
    result: dict[str, Any] | None = None
    try:
        result = service.explain_selection(
            document_id=spec.document_id,
            page_number=spec.page_number,
            selected_bbox=spec.selected_bbox,
        )
        row["status"] = "completed"
    except Exception as exc:
        row["status"] = "failed"
        row["error_message"] = str(exc)
    row["service_call_seconds"] = _round_number(time.perf_counter() - service_started_at)

    cached_after = (
        storage.load_selection_explanation(spec.document_id, spec.page_number, selection_id)
        if selection_id is not None
        else None
    )
    row["cache_hit_after"] = cached_after is not None
    row["selection_artifact_available_after"] = cached_after is not None
    if result is None and cached_after is not None:
        result = dict(cached_after["result"])
    row.update(_summarize_result(result))
    row["total_wall_time_seconds"] = _round_number(time.perf_counter() - started_at)
    return row


def _summarize_numeric(rows: list[dict[str, Any]], field_name: str) -> dict[str, float | None]:
    values = [float(row[field_name]) for row in rows if isinstance(row.get(field_name), (int, float))]
    if not values:
        return {"mean": None, "median": None, "min": None, "max": None}
    return {
        "mean": _round_number(mean(values)),
        "median": _round_number(median(values)),
        "min": _round_number(min(values)),
        "max": _round_number(max(values)),
    }


def _build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(row.get("status")) for row in rows)
    return {
        "selection_count": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "cache_hit_before_count": sum(1 for row in rows if row.get("cache_hit_before")),
        "cache_hit_after_count": sum(1 for row in rows if row.get("cache_hit_after")),
        "ready_count": sum(1 for row in rows if row.get("status") in {"ready", "completed"}),
        "failed_or_not_ready_count": sum(1 for row in rows if row.get("status") in {"failed", "not_ready"}),
        "metrics": {
            "selected_region_area": _summarize_numeric(rows, "selected_region_area"),
            "top_selection_overlap_ratio": _summarize_numeric(rows, "top_selection_overlap_ratio"),
            "dry_run_check_seconds": _summarize_numeric(rows, "dry_run_check_seconds"),
            "service_call_seconds": _summarize_numeric(rows, "service_call_seconds"),
            "total_wall_time_seconds": _summarize_numeric(rows, "total_wall_time_seconds"),
        },
    }


def _run_config(settings: Any, *, dry_run: bool) -> dict[str, Any]:
    stage_config = settings.stage_config("selection_explanation")
    return {
        "dry_run": dry_run,
        "llm_provider": settings.llm_provider,
        "codex_cli_model": settings.codex_cli_model,
        "codex_cli_reasoning_effort": settings.codex_cli_reasoning_effort,
        "pass1_max_workers": settings.pass1_max_workers,
        "selection_prompt_version": stage_config.prompt_version,
        "selection_schema_name": stage_config.schema_name,
        "schema_version": settings.schema_version,
        "analysis_dir": settings.analysis_dir,
        "rendered_pages_dir": settings.rendered_pages_dir,
    }


def _write_payload(path: Path | str, payload: dict[str, Any]) -> None:
    if path == "-":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    assert isinstance(path, Path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved selected-region benchmark to {path}", flush=True)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        from app.core.config import get_settings
        from app.services.selection_explainer import SelectionExplanationService
        from app.services.storage import get_storage_service
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Backend dependencies are unavailable. Run with backend/.venv/bin/python "
            "or install backend/requirements.txt."
        ) from exc

    settings = get_settings()
    storage = get_storage_service()
    service = SelectionExplanationService(storage=storage, settings=settings)
    specs = _load_selection_specs(args, storage)

    rows = [
        _benchmark_selection(
            storage=storage,
            service=service,
            spec=spec,
            dry_run=args.dry_run,
        )
        for spec in specs
    ]

    payload = {
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _get_git_head(),
        "mode_name": args.mode_name or ("selected_region_dry_run" if args.dry_run else "selected_region_real"),
        "honesty_note": (
            "dry_run rows do not include LLM generation latency; cache_hit_before rows measure cache return "
            "unless cache_matches_current_provider_before is false and the service regenerates."
        ),
        "run_config": _run_config(settings, dry_run=args.dry_run),
        "summary": _build_summary(rows),
        "selections": rows,
    }

    if not args.no_output:
        _write_payload(_resolve_output_path(args.output, args.output_dir), payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2), flush=True)

    if args.fail_on_error and payload["summary"]["failed_or_not_ready_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
