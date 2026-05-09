#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
RUNNER_VERSION = "pass1_mode_comparison_v0_1"
DEFAULT_PDF = PROJECT_ROOT / "data" / "raw_pdfs" / "W1.Lecture01-Financial Management and Firm Value.pdf"
PASS1_MODES = ("parser_first", "legacy_llm", "hybrid")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare Scholium PASS1_MODE processing benchmarks on one PDF.",
    )
    parser.add_argument("--pdf", default=str(DEFAULT_PDF), help="PDF path to process.")
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=PASS1_MODES,
        default=["parser_first", "legacy_llm", "hybrid"],
        help="PASS1_MODE values to compare.",
    )
    parser.add_argument("--mode-name", default="pass1_mode_comparison")
    parser.add_argument("--output", help="Output JSON path. Use '-' for stdout.")
    parser.add_argument("--output-dir", help="Directory for timestamped output JSON.")
    parser.add_argument("--child-mode", choices=PASS1_MODES, help=argparse.SUPPRESS)
    parser.add_argument("--real-selection", action="store_true", help="Run one real selected-region explanation.")
    return parser


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _default_output_path(output_dir: str | None) -> Path:
    base_dir = Path(output_dir).expanduser().resolve() if output_dir else PROJECT_ROOT / "docs" / "perf_runs"
    return base_dir / f"{_timestamp()}_pass1_mode_comparison.json"


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
    return result.stdout.strip() or None


def _run_child(*, pdf_path: Path, mode: str, real_selection: bool) -> dict[str, Any]:
    env = os.environ.copy()
    env["PASS1_MODE"] = mode
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--pdf",
        str(pdf_path),
        "--child-mode",
        mode,
    ]
    if real_selection:
        command.append("--real-selection")
    started_at = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    wall_time = round(time.perf_counter() - started_at, 4)
    if completed.returncode != 0:
        return {
            "mode": mode,
            "status": "failed",
            "wall_time_seconds": wall_time,
            "error_message": completed.stderr.strip() or completed.stdout.strip(),
        }
    json_lines = [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip().startswith("{") and line.strip().endswith("}")
    ]
    raw_json = json_lines[-1] if json_lines else completed.stdout
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return {
            "mode": mode,
            "status": "failed",
            "wall_time_seconds": wall_time,
            "error_message": f"Child output was not JSON: {exc}; stdout={completed.stdout[:700]}",
        }
    payload["wall_time_seconds"] = wall_time
    return payload


def _child_main(*, pdf_path: Path, mode: str, real_selection: bool) -> int:
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    from app.services.orchestrator import DocumentOrchestrator
    from app.services.selection_explainer import SelectionExplanationService
    from app.services.storage import get_storage_service

    storage = get_storage_service()
    storage.init_storage()
    filename = f"{pdf_path.stem}.benchmark-{mode}-{storage.settings.llm_provider}{pdf_path.suffix}"
    document = storage.save_uploaded_document(filename=filename, file_bytes=pdf_path.read_bytes())

    pipeline_started_at = time.perf_counter()
    orchestrator = DocumentOrchestrator(storage=storage)
    orchestrator.run_pipeline(document.document_id)
    pipeline_wall_time_seconds = round(time.perf_counter() - pipeline_started_at, 4)

    benchmark = storage.load_processing_benchmark(document.document_id) or {}
    selection_row = _selection_smoke(
        storage=storage,
        document_id=document.document_id,
        real_selection=real_selection,
    )
    result = {
        "mode": mode,
        "status": benchmark.get("final_status"),
        "document_id": document.document_id,
        "filename": filename,
        "pipeline_wall_time_seconds": pipeline_wall_time_seconds,
        "upload_to_render_seconds": benchmark.get("upload_to_render_seconds"),
        "upload_to_parser_map_ready_seconds": benchmark.get("upload_to_parser_map_ready_seconds"),
        "upload_to_semantic_guide_ready_seconds": benchmark.get("upload_to_semantic_guide_ready_seconds"),
        "upload_to_viewer_ready_seconds": benchmark.get("upload_to_viewer_ready_seconds"),
        "pass1_time_seconds": benchmark.get("pass1_time_seconds"),
        "semantic_guide_time_seconds": benchmark.get("semantic_guide_time_seconds"),
        "codex_cli_pass1_call_count": benchmark.get("codex_cli_pass1_call_count"),
        "codex_cli_semantic_guide_call_count": benchmark.get("codex_cli_semantic_guide_call_count"),
        "page_element_count": benchmark.get("page_element_count"),
        "page_guide_count": benchmark.get("page_guide_count"),
        "selection_explanation_success": selection_row.get("status") in {"ready", "completed", "cached"},
        "first_selection_latency_seconds": selection_row.get("first_selection_latency_seconds"),
        "cached_selection_latency_seconds": selection_row.get("cached_selection_latency_seconds"),
        "selection_smoke": selection_row,
        "benchmark": benchmark,
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


def _selection_smoke(
    *,
    storage: Any,
    document_id: str,
    real_selection: bool,
) -> dict[str, Any]:
    from app.services.selection_explainer import SelectionExplanationService

    service = SelectionExplanationService(storage=storage)
    for page in storage.get_pages(document_id):
        pass1_artifact = storage.load_pass1_result(document_id, page.page_number)
        if not isinstance(pass1_artifact, dict):
            continue
        result = pass1_artifact.get("result")
        if not isinstance(result, dict):
            continue
        page_elements = result.get("page_elements") or result.get("candidate_anchors")
        if not isinstance(page_elements, list):
            continue
        for element in page_elements:
            if not isinstance(element, dict) or not isinstance(element.get("bbox"), list):
                continue
            selected_bbox = [float(value) for value in element["bbox"]]
            started_at = time.perf_counter()
            if not real_selection:
                context = service.selection_context_builder.build(
                    document_id=document_id,
                    page_number=page.page_number,
                    selected_bbox=selected_bbox,
                    pass1_artifact=pass1_artifact,
                    document_summary_artifact=storage.load_document_summary(document_id),
                )
                return {
                    "status": "ready",
                    "page_number": page.page_number,
                    "selected_bbox": selected_bbox,
                    "first_selection_latency_seconds": round(time.perf_counter() - started_at, 4),
                    "cached_selection_latency_seconds": None,
                    "context_hash": context.get("context_hash"),
                    "matched_element_count": context.get("metrics", {}).get("matched_element_count"),
                }

            first_result = service.explain_selection(
                document_id=document_id,
                page_number=page.page_number,
                selected_bbox=selected_bbox,
            )
            first_latency = round(time.perf_counter() - started_at, 4)
            cached_started_at = time.perf_counter()
            service.explain_selection(
                document_id=document_id,
                page_number=page.page_number,
                selected_bbox=selected_bbox,
            )
            cached_latency = round(time.perf_counter() - cached_started_at, 4)
            return {
                "status": "completed" if first_result else "failed",
                "page_number": page.page_number,
                "selected_bbox": selected_bbox,
                "first_selection_latency_seconds": first_latency,
                "cached_selection_latency_seconds": cached_latency,
                "selection_id": first_result.get("selection_id") if isinstance(first_result, dict) else None,
            }

    return {
        "status": "not_ready",
        "error_message": "No page element bbox was available for selection smoke.",
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mode_count": len(rows),
        "completed_modes": [row["mode"] for row in rows if row.get("status") == "completed"],
        "failed_modes": [row["mode"] for row in rows if row.get("status") != "completed"],
        "codex_cli_pass1_call_count_by_mode": {
            row["mode"]: row.get("codex_cli_pass1_call_count") for row in rows
        },
        "codex_cli_semantic_guide_call_count_by_mode": {
            row["mode"]: row.get("codex_cli_semantic_guide_call_count") for row in rows
        },
        "pass1_time_seconds_by_mode": {
            row["mode"]: row.get("pass1_time_seconds") for row in rows
        },
        "parser_map_ready_seconds_by_mode": {
            row["mode"]: row.get("upload_to_parser_map_ready_seconds") for row in rows
        },
    }


def _write_payload(path: Path | str, payload: dict[str, Any]) -> None:
    if path == "-":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    assert isinstance(path, Path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved pass1 mode comparison to {path}", flush=True)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    if args.child_mode:
        return _child_main(
            pdf_path=pdf_path,
            mode=args.child_mode,
            real_selection=bool(args.real_selection),
        )

    rows = [
        _run_child(pdf_path=pdf_path, mode=mode, real_selection=bool(args.real_selection))
        for mode in args.modes
    ]
    payload = {
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _get_git_head(),
        "mode_name": args.mode_name,
        "pdf": str(pdf_path),
        "modes": args.modes,
        "real_selection": bool(args.real_selection),
        "summary": _summarize(rows),
        "results": rows,
    }
    output_path: Path | str = args.output if args.output == "-" else (
        Path(args.output).expanduser().resolve() if args.output else _default_output_path(args.output_dir)
    )
    _write_payload(output_path, payload)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2), flush=True)
    return 1 if payload["summary"]["failed_modes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
