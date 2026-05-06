#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any
from uuid import uuid4


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import AppSettings, get_settings
from app.models.document import DocumentStatus
from app.services.orchestrator import DocumentOrchestrator
from app.services.storage import StorageService, get_storage_service


RUNNER_VERSION = "corpus_runner_v0_2"
COLLECTION_STATUS_COMPLETED = "completed"
COLLECTION_STATUS_FAILED = "failed"
COLLECTION_STATUS_BENCHMARK_MISSING = "benchmark_missing"
COLLECTION_STATUS_INTERRUPTED = "interrupted"
SUMMARY_METRIC_FIELDS = [
    "total_processing_time_seconds",
    "rendered_pages",
    "pass1_text_first_pages",
    "pass1_multimodal_pages",
    "pass1_escalated_pages",
    "pass2_completed_pages",
    "pass2_failed_pages",
    "pass2_llm_count",
    "pass2_compat_count",
    "openai_call_count_total",
    "openai_pass1_call_count",
    "openai_synthesis_call_count",
    "openai_pass2_call_count",
]
DOCUMENT_BENCHMARK_FIELDS = [
    "final_status",
    "total_processing_time_seconds",
    "rendered_pages",
    "pass1_text_first_pages",
    "pass1_multimodal_pages",
    "pass1_escalated_pages",
    "pass2_completed_pages",
    "pass2_failed_pages",
    "pass2_execution_mode",
    "pass2_llm_count",
    "pass2_compat_count",
    "openai_call_count_total",
    "openai_pass1_call_count",
    "openai_synthesis_call_count",
    "openai_pass2_call_count",
    "pass2_planner_status",
    "pass2_planner_reason",
    "compat_promoted_to_llm_count",
]
RUN_WARNING_FIELD_NAMES = {
    "total_processing_time_seconds",
    "pass2_execution_mode",
    "pass2_llm_count",
    "pass2_compat_count",
    "openai_call_count_total",
    "openai_pass2_call_count",
    "pass2_planner_status",
    "pass2_planner_reason",
    "compat_promoted_to_llm_count",
}


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0.")
    return parsed


def _run_pipeline_worker(document_id: str) -> None:
    storage = get_storage_service()
    orchestrator = DocumentOrchestrator(storage=storage)
    orchestrator.run_pipeline(document_id)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Scholium benchmark collection over a PDF corpus.",
    )
    parser.add_argument(
        "pdfs",
        nargs="*",
        help="PDF files to process sequentially.",
    )
    parser.add_argument(
        "--pdf-dir",
        help="Directory containing PDF files to process (non-recursive).",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        help="Maximum number of PDFs to process when using --pdf-dir.",
    )
    parser.add_argument(
        "--mode-name",
        help="Human-readable label for this run, for example 'baseline_hybrid' or 'v2_spine_active'.",
    )
    parser.add_argument(
        "--output",
        help="Optional output path for the corpus benchmark JSON.",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional directory where the timestamped corpus benchmark JSON should be saved.",
    )
    parser.add_argument(
        "--per-doc-timeout-seconds",
        type=_positive_int,
        help="Interrupt a single document after this many seconds and continue to the next.",
    )
    parser.add_argument(
        "--corpus-manifest",
        help="Optional corpus manifest JSON path. When provided, the resolved path and sha256 are recorded in run_config.",
    )
    return parser


def _resolve_input_pdfs(args: argparse.Namespace) -> tuple[str, list[Path], Path]:
    if args.pdf_dir and args.pdfs:
        raise SystemExit("Use either positional PDF paths or --pdf-dir, not both.")
    if not args.pdf_dir and not args.pdfs:
        raise SystemExit("Provide at least one PDF path or use --pdf-dir.")
    if args.limit is not None and not args.pdf_dir:
        raise SystemExit("--limit can only be used together with --pdf-dir.")

    if args.pdf_dir:
        pdf_dir = Path(args.pdf_dir).expanduser().resolve()
        if not pdf_dir.exists() or not pdf_dir.is_dir():
            raise SystemExit(f"PDF directory not found: {pdf_dir}")
        pdf_paths = sorted(
            path for path in pdf_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"
        )
        if args.limit is not None:
            pdf_paths = pdf_paths[: args.limit]
        input_mode = "pdf_dir"
        source_root = pdf_dir
    else:
        pdf_paths = []
        for raw_path in args.pdfs:
            path = Path(raw_path).expanduser().resolve()
            if not path.exists() or not path.is_file():
                raise SystemExit(f"PDF file not found: {path}")
            if path.suffix.lower() != ".pdf":
                raise SystemExit(f"Only PDF files are supported: {path}")
            pdf_paths.append(path)
        input_mode = "pdf_list"
        common_parent = os.path.commonpath([str(path.parent) for path in pdf_paths])
        source_root = Path(common_parent).resolve()

    if not pdf_paths:
        raise SystemExit("No PDF files were found for this corpus run.")

    return input_mode, pdf_paths, source_root


def _default_output_path(output_dir: Path | None = None) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base_dir = output_dir or (PROJECT_ROOT / "docs" / "perf_runs")
    return base_dir / f"{timestamp}.json"


def _resolve_output_path(raw_output: str | None, raw_output_dir: str | None) -> Path:
    if raw_output and raw_output_dir:
        raise SystemExit("Use either --output or --output-dir, not both.")
    if raw_output:
        return Path(raw_output).expanduser().resolve()
    output_dir = Path(raw_output_dir).expanduser().resolve() if raw_output_dir else None
    return _default_output_path(output_dir)


def _default_mode_name(settings: AppSettings) -> str:
    return (
        f"{settings.pipeline_mode}"
        f"__{settings.v2_spine_mode}"
        f"__{settings.pass2_execution_mode}"
    )


def _resolve_corpus_manifest_metadata(raw_manifest_path: str | None) -> tuple[str | None, str | None]:
    if not raw_manifest_path:
        return None, None

    manifest_path = Path(raw_manifest_path).expanduser().resolve()
    if not manifest_path.exists() or not manifest_path.is_file():
        raise SystemExit(f"Corpus manifest not found: {manifest_path}")

    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return manifest_path.as_posix(), manifest_sha256


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


def _benchmark_artifact_path(storage: StorageService, document_id: str) -> str:
    path = storage.get_processing_benchmark_path(document_id)
    return path.relative_to(PROJECT_ROOT).as_posix()


def _run_config(
    settings: AppSettings,
    *,
    per_doc_timeout_seconds: int | None,
    source_root: Path,
    corpus_manifest_path: str | None,
    corpus_manifest_sha256: str | None,
) -> dict[str, Any]:
    return {
        "source_root": str(source_root),
        "corpus_manifest_path": corpus_manifest_path,
        "corpus_manifest_sha256": corpus_manifest_sha256,
        "document_parser_backend": settings.document_parser_backend,
        "pass1_routing_mode": settings.pass1_routing_mode,
        "pipeline_mode": settings.pipeline_mode,
        "spine_mode": settings.v2_spine_mode,
        "pass2_execution_mode": settings.pass2_execution_mode,
        "openai_model_pass1": settings.stage_config("pass1").model_name,
        "openai_model_synthesis": settings.stage_config("document_synthesis").model_name,
        "openai_model_pass2": settings.stage_config("pass2").model_name,
        "reasoning_effort_pass1": settings.stage_config("pass1").reasoning_effort,
        "reasoning_effort_synthesis": settings.stage_config("document_synthesis").reasoning_effort,
        "reasoning_effort_pass2": settings.stage_config("pass2").reasoning_effort,
        "openai_timeout_seconds": settings.openai_timeout_seconds,
        "openai_max_retries": settings.openai_max_retries,
        "per_doc_timeout_seconds": per_doc_timeout_seconds,
    }


def _benchmark_defaults() -> dict[str, Any]:
    return {
        "final_status": None,
        "total_processing_time_seconds": None,
        "rendered_pages": None,
        "pass1_text_first_pages": None,
        "pass1_multimodal_pages": None,
        "pass1_escalated_pages": None,
        "pass2_completed_pages": None,
        "pass2_failed_pages": None,
        "pass2_execution_mode": None,
        "pass2_llm_count": None,
        "pass2_compat_count": None,
        "openai_call_count_total": None,
        "openai_pass1_call_count": None,
        "openai_synthesis_call_count": None,
        "openai_pass2_call_count": None,
        "pass2_planner_status": None,
        "pass2_planner_reason": None,
        "compat_promoted_to_llm_count": None,
    }


def _load_benchmark_payloads(
    storage: StorageService,
    document_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    target_path = storage.get_processing_benchmark_path(document_id)
    if not target_path.exists():
        return None, None, None

    raw_payload: dict[str, Any] | None = None
    try:
        loaded_payload = json.loads(target_path.read_text(encoding="utf-8"))
        if isinstance(loaded_payload, dict):
            raw_payload = loaded_payload
        else:
            return None, None, "Processing benchmark artifact is not a JSON object."
    except Exception as exc:
        return None, None, f"Processing benchmark artifact could not be parsed: {exc}"

    try:
        normalized_payload = storage.load_processing_benchmark(document_id)
    except Exception as exc:
        return raw_payload, None, f"Processing benchmark artifact could not be validated: {exc}"

    return raw_payload, normalized_payload, None


def _safe_benchmark_value(
    raw_payload: dict[str, Any] | None,
    field_name: str,
) -> Any:
    if raw_payload is None:
        return None
    return raw_payload.get(field_name) if field_name in raw_payload else None


def _normalize_source_pdf_relpath(source_root: Path, source_pdf: Path) -> str:
    try:
        relpath = source_pdf.relative_to(source_root)
    except ValueError:
        relpath = Path(source_pdf.name)
    return Path(relpath).as_posix()


def _collect_document_entry(
    *,
    storage: StorageService,
    source_pdf: Path,
    source_root: Path,
    document_id: str | None,
    interrupted: bool = False,
    collection_error: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "source_pdf": source_pdf.as_posix(),
        "source_pdf_relpath": _normalize_source_pdf_relpath(source_root, source_pdf),
        "filename": source_pdf.name,
        "document_id": document_id,
        "benchmark_available": False,
        "benchmark_artifact_path": None,
        "collection_status": COLLECTION_STATUS_BENCHMARK_MISSING,
        "collection_error": collection_error,
        "missing_benchmark_fields": [],
        "final_error_message": None,
        **_benchmark_defaults(),
    }

    if document_id is None:
        entry["collection_status"] = COLLECTION_STATUS_FAILED
        entry["final_status"] = DocumentStatus.FAILED.value
        entry["final_error_message"] = collection_error
        return entry

    document = storage.get_document(document_id)
    benchmark_payload_raw: dict[str, Any] | None = None
    benchmark_payload: dict[str, Any] | None = None
    benchmark_error: str | None = None
    benchmark_payload_raw, benchmark_payload, benchmark_error = _load_benchmark_payloads(
        storage,
        document_id,
    )

    if benchmark_payload is not None and benchmark_payload_raw is not None:
        entry["benchmark_available"] = True
        entry["benchmark_artifact_path"] = _benchmark_artifact_path(storage, document_id)
        missing_benchmark_fields: list[str] = []
        for field_name in DOCUMENT_BENCHMARK_FIELDS:
            if field_name in benchmark_payload_raw:
                entry[field_name] = benchmark_payload_raw.get(field_name)
            else:
                missing_benchmark_fields.append(field_name)
                entry[field_name] = None
        entry["missing_benchmark_fields"] = missing_benchmark_fields
        entry["final_error_message"] = _safe_benchmark_value(
            benchmark_payload_raw,
            "final_error_message",
        )
    else:
        if document is not None:
            entry["final_status"] = document.status.value
            entry["final_error_message"] = document.error_message
        if benchmark_error and not entry["collection_error"]:
            entry["collection_error"] = benchmark_error

    if entry["final_status"] is None and document is not None:
        entry["final_status"] = document.status.value
    if entry["final_error_message"] is None and document is not None:
        entry["final_error_message"] = document.error_message

    if interrupted:
        entry["collection_status"] = COLLECTION_STATUS_INTERRUPTED
        if not entry["collection_error"]:
            entry["collection_error"] = "Document processing exceeded per-document timeout."
        return entry

    if not entry["benchmark_available"]:
        entry["collection_status"] = COLLECTION_STATUS_BENCHMARK_MISSING
        if not entry["collection_error"]:
            entry["collection_error"] = "Processing benchmark artifact was not found."
        return entry

    if entry["final_status"] == DocumentStatus.COMPLETED.value:
        entry["collection_status"] = COLLECTION_STATUS_COMPLETED
    else:
        entry["collection_status"] = COLLECTION_STATUS_FAILED

    return entry


def _run_document_with_timeout(
    *,
    document_id: str,
    timeout_seconds: int | None,
) -> bool:
    if timeout_seconds is None:
        storage = get_storage_service()
        orchestrator = DocumentOrchestrator(storage=storage)
        orchestrator.run_pipeline(document_id)
        return False

    context = mp.get_context("spawn")
    process = context.Process(target=_run_pipeline_worker, args=(document_id,))
    process.start()
    process.join(timeout_seconds)
    if not process.is_alive():
        return False

    process.terminate()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)
    return True


def _round_numeric(value: float, *, prefer_int: bool) -> int | float:
    rounded = round(value, 4)
    if prefer_int and float(rounded).is_integer():
        return int(round(rounded))
    return rounded


def _summarize_metric(values: list[int | float]) -> dict[str, int | float | None]:
    if not values:
        return {"mean": None, "median": None, "min": None, "max": None}

    prefer_int = all(float(value).is_integer() for value in values)
    return {
        "mean": _round_numeric(mean(values), prefer_int=prefer_int),
        "median": _round_numeric(median(values), prefer_int=prefer_int),
        "min": _round_numeric(min(values), prefer_int=prefer_int),
        "max": _round_numeric(max(values), prefer_int=prefer_int),
    }


def _build_corpus_summary(documents: list[dict[str, Any]]) -> dict[str, Any]:
    completed_count = sum(1 for document in documents if document["collection_status"] == COLLECTION_STATUS_COMPLETED)
    failed_count = sum(1 for document in documents if document["collection_status"] == COLLECTION_STATUS_FAILED)
    benchmark_missing_count = sum(
        1 for document in documents if document["collection_status"] == COLLECTION_STATUS_BENCHMARK_MISSING
    )
    interrupted_count = sum(
        1 for document in documents if document["collection_status"] == COLLECTION_STATUS_INTERRUPTED
    )
    usable_documents = [document for document in documents if document["benchmark_available"]]

    metrics: dict[str, dict[str, int | float | None]] = {}
    for field_name in SUMMARY_METRIC_FIELDS:
        values = [
            document[field_name]
            for document in usable_documents
            if isinstance(document.get(field_name), (int, float))
        ]
        metrics[field_name] = _summarize_metric(values)

    document_count = len(documents)
    success_rate = round(completed_count / document_count, 4) if document_count else 0.0
    total_openai_pass2_call_count = sum(
        int(document["openai_pass2_call_count"])
        for document in usable_documents
        if isinstance(document.get("openai_pass2_call_count"), (int, float))
    )
    total_pass2_llm_count = sum(
        int(document["pass2_llm_count"])
        for document in usable_documents
        if isinstance(document.get("pass2_llm_count"), (int, float))
    )
    total_pass2_compat_count = sum(
        int(document["pass2_compat_count"])
        for document in usable_documents
        if isinstance(document.get("pass2_compat_count"), (int, float))
    )

    return {
        "document_count": document_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "usable_benchmark_count": len(usable_documents),
        "benchmark_missing_count": benchmark_missing_count,
        "interrupted_count": interrupted_count,
        "success_rate": success_rate,
        "avg_total_processing_time_seconds": metrics["total_processing_time_seconds"]["mean"],
        "median_total_processing_time_seconds": metrics["total_processing_time_seconds"]["median"],
        "total_openai_pass2_call_count": total_openai_pass2_call_count,
        "total_pass2_llm_count": total_pass2_llm_count,
        "total_pass2_compat_count": total_pass2_compat_count,
        "metrics": metrics,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _timeout_message(timeout_seconds: int) -> str:
    return f"Corpus runner interrupted after {timeout_seconds} second(s)."


def _build_run_warnings(documents: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for document in documents:
        missing_fields = document.get("missing_benchmark_fields") or []
        if not missing_fields:
            continue
        warning_fields = [field for field in missing_fields if field in RUN_WARNING_FIELD_NAMES]
        if not warning_fields:
            continue
        warnings.append(
            f"{document['filename']} ({document.get('document_id')}) benchmark is missing fields: "
            + ", ".join(sorted(warning_fields))
        )
    return warnings


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    input_mode, pdf_paths, source_root = _resolve_input_pdfs(args)
    output_path = _resolve_output_path(args.output, args.output_dir)
    corpus_manifest_path, corpus_manifest_sha256 = _resolve_corpus_manifest_metadata(
        args.corpus_manifest,
    )
    settings = get_settings()
    storage = get_storage_service()
    mode_name = args.mode_name or _default_mode_name(settings)

    run_timestamp = datetime.now(timezone.utc)
    run_id = f"run_{run_timestamp.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex[:8]}"
    documents: list[dict[str, Any]] = []

    for index, pdf_path in enumerate(pdf_paths, start=1):
        print(f"[{index}/{len(pdf_paths)}] Processing {pdf_path.name} ...", flush=True)
        document_id: str | None = None
        interrupted = False
        collection_error: str | None = None

        try:
            document_record = storage.save_uploaded_document(
                filename=pdf_path.name,
                file_bytes=pdf_path.read_bytes(),
            )
            document_id = document_record.document_id
        except Exception as exc:
            documents.append(
                _collect_document_entry(
                    storage=storage,
                    source_pdf=pdf_path,
                    source_root=source_root,
                    document_id=None,
                    collection_error=f"Failed to create document record: {exc}",
                )
            )
            continue

        try:
            interrupted = _run_document_with_timeout(
                document_id=document_id,
                timeout_seconds=args.per_doc_timeout_seconds,
            )
            if interrupted:
                try:
                    storage.update_document(
                        document_id,
                        status=DocumentStatus.FAILED,
                        error_message=_timeout_message(args.per_doc_timeout_seconds),
                    )
                except Exception:
                    pass
        except KeyboardInterrupt:
            interrupted = True
            collection_error = "Corpus runner interrupted by keyboard input."
            try:
                storage.update_document(
                    document_id,
                    status=DocumentStatus.FAILED,
                    error_message=collection_error,
                )
            except Exception:
                pass
        except Exception as exc:
            collection_error = f"Pipeline execution raised an unexpected error: {exc}"
            try:
                storage.update_document(
                    document_id,
                    status=DocumentStatus.FAILED,
                    error_message=collection_error,
                )
            except Exception:
                pass

        documents.append(
            _collect_document_entry(
                storage=storage,
                source_pdf=pdf_path,
                source_root=source_root,
                document_id=document_id,
                interrupted=interrupted,
                collection_error=collection_error,
            )
        )

        missing_fields = documents[-1].get("missing_benchmark_fields") or []
        if missing_fields:
            print(
                "[warning] "
                f"{documents[-1]['filename']} benchmark is missing fields: "
                + ", ".join(sorted(missing_fields)),
                file=sys.stderr,
                flush=True,
            )

    payload = {
        "run_id": run_id,
        "runner_version": RUNNER_VERSION,
        "git_head": _get_git_head(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode_name": mode_name,
        "execution_mode": "sequential",
        "input_mode": input_mode,
        "input_pdfs": [path.as_posix() for path in pdf_paths],
        "run_config": _run_config(
            settings,
            per_doc_timeout_seconds=args.per_doc_timeout_seconds,
            source_root=source_root,
            corpus_manifest_path=corpus_manifest_path,
            corpus_manifest_sha256=corpus_manifest_sha256,
        ),
        "documents": documents,
        "corpus_summary": _build_corpus_summary(documents),
        "warnings": _build_run_warnings(documents),
    }

    _write_json(output_path, payload)
    print(f"Saved corpus benchmark run to {output_path}", flush=True)
    print(json.dumps(payload["corpus_summary"], ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
