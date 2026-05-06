#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent

COMPARISON_FIELDS = [
    "final_status",
    "total_processing_time_seconds",
    "rendered_pages",
    "pass1_text_first_pages",
    "pass1_multimodal_pages",
    "pass1_escalated_pages",
    "pass2_llm_count",
    "pass2_compat_count",
    "openai_call_count_total",
    "openai_pass2_call_count",
]
DELTA_FIELDS = [
    "total_processing_time_seconds",
    "openai_call_count_total",
    "openai_pass2_call_count",
    "pass2_llm_count",
]
NULL_SENSITIVE_FIELDS = {
    "total_processing_time_seconds",
    "openai_call_count_total",
    "openai_pass2_call_count",
    "pass2_llm_count",
    "pass2_compat_count",
}
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "perf_runs"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two or more Scholium corpus benchmark run summaries.",
    )
    parser.add_argument(
        "run_summaries",
        nargs="+",
        help="Corpus run summary JSON files to compare.",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional output directory for comparison JSON/Markdown artifacts.",
    )
    parser.add_argument(
        "--output-prefix",
        help="Optional output prefix path without extension, for example docs/perf_runs/20260329T000000Z_comparison.",
    )
    return parser


def _default_output_prefix(output_dir: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return output_dir / f"{timestamp}_comparison"


def _resolve_output_prefix(raw_output_dir: str | None, raw_output_prefix: str | None) -> Path:
    if raw_output_dir and raw_output_prefix:
        raise SystemExit("Use either --output-dir or --output-prefix, not both.")
    if raw_output_prefix:
        return Path(raw_output_prefix).expanduser().resolve()
    output_dir = Path(raw_output_dir).expanduser().resolve() if raw_output_dir else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    return _default_output_prefix(output_dir)


def _normalize_relpath(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return Path(normalized).as_posix()


def _normalize_nullable_number(value: object | None) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _display_metric(value: object | None) -> str:
    if value is None:
        return "missing in run"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _display_delta(value: object | None) -> str:
    if value is None:
        return "missing in run"
    if isinstance(value, float):
        normalized = f"{value:+.4f}".rstrip("0").rstrip(".")
        return normalized
    if isinstance(value, int):
        return f"{value:+d}"
    return str(value)


def _run_key(base_key: str, seen: set[str]) -> str:
    candidate = base_key
    suffix = 2
    while candidate in seen:
        candidate = f"{base_key}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _normalize_document_record(document: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "source_pdf_relpath": _normalize_relpath(document.get("source_pdf_relpath")),
        "filename": document.get("filename"),
        "document_id": document.get("document_id"),
        "collection_status": document.get("collection_status"),
        "benchmark_available": bool(document.get("benchmark_available", False)),
    }
    for field_name in COMPARISON_FIELDS:
        raw_value = document.get(field_name)
        if field_name == "final_status":
            normalized[field_name] = raw_value if raw_value is not None else None
            continue
        normalized[field_name] = _normalize_nullable_number(raw_value)
    return normalized


def _load_run(path: Path, seen_run_keys: set[str]) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Run summary is not a JSON object: {path}")

    base_mode_name = str(
        payload.get("mode_name")
        or payload.get("run_id")
        or path.stem
    ).strip()
    run_key = _run_key(base_mode_name or path.stem, seen_run_keys)

    run_config = payload.get("run_config") if isinstance(payload.get("run_config"), dict) else {}
    source_root = run_config.get("source_root")
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise ValueError(f"Run summary documents must be a list: {path}")

    normalized_documents = [_normalize_document_record(document) for document in documents if isinstance(document, dict)]

    return {
        "run_key": run_key,
        "mode_name": base_mode_name or run_key,
        "path": path.resolve().as_posix(),
        "run_id": payload.get("run_id"),
        "git_head": payload.get("git_head"),
        "pipeline_mode": run_config.get("pipeline_mode"),
        "spine_mode": run_config.get("spine_mode"),
        "pass2_execution_mode": run_config.get("pass2_execution_mode"),
        "source_root": str(source_root).strip() if source_root else None,
        "corpus_manifest_path": (
            str(run_config.get("corpus_manifest_path")).strip()
            if run_config.get("corpus_manifest_path")
            else None
        ),
        "corpus_manifest_sha256": (
            str(run_config.get("corpus_manifest_sha256")).strip()
            if run_config.get("corpus_manifest_sha256")
            else None
        ),
        "documents": normalized_documents,
    }


def _index_run_documents(run: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    indexed: dict[str, dict[str, Any]] = {}
    excluded_missing_key: list[dict[str, Any]] = []
    warnings: list[str] = []
    for document in run["documents"]:
        key = document["source_pdf_relpath"]
        if key is None:
            excluded_missing_key.append(
                {
                    "filename": document.get("filename"),
                    "document_id": document.get("document_id"),
                }
            )
            continue
        if key in indexed:
            warnings.append(
                f"{run['run_key']} has duplicate source_pdf_relpath '{key}'. The first document record is kept."
            )
            continue
        indexed[key] = document
    return indexed, excluded_missing_key, warnings


def _sum_metric(documents: list[dict[str, Any]], field_name: str) -> int | float | None:
    usable_documents = [document for document in documents if document.get("benchmark_available")]
    if not usable_documents:
        return None

    values: list[int | float] = []
    for document in usable_documents:
        value = document.get(field_name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        values.append(value)

    if all(float(value).is_integer() for value in values):
        return int(sum(int(value) for value in values))
    return round(sum(float(value) for value in values), 4)


def _mean_metric(documents: list[dict[str, Any]], field_name: str) -> float | None:
    usable_documents = [document for document in documents if document.get("benchmark_available")]
    if not usable_documents:
        return None
    values: list[float] = []
    for document in usable_documents:
        value = document.get(field_name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        values.append(float(value))
    return round(sum(values) / len(values), 4)


def _median_metric(documents: list[dict[str, Any]], field_name: str) -> float | None:
    usable_documents = [document for document in documents if document.get("benchmark_available")]
    if not usable_documents:
        return None
    values: list[float] = []
    for document in usable_documents:
        value = document.get(field_name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        values.append(float(value))
    return round(float(median(values)), 4)


def _aggregate_run(run: dict[str, Any]) -> dict[str, Any]:
    documents = run["documents"]
    return {
        "completed_docs": sum(1 for document in documents if document.get("collection_status") == "completed"),
        "failed_docs": sum(1 for document in documents if document.get("collection_status") == "failed"),
        "avg_total_processing_time_seconds": _mean_metric(documents, "total_processing_time_seconds"),
        "median_total_processing_time_seconds": _median_metric(documents, "total_processing_time_seconds"),
        "total_openai_call_count": _sum_metric(documents, "openai_call_count_total"),
        "total_openai_pass2_call_count": _sum_metric(documents, "openai_pass2_call_count"),
        "total_pass2_llm_count": _sum_metric(documents, "pass2_llm_count"),
        "total_pass2_compat_count": _sum_metric(documents, "pass2_compat_count"),
    }


def _delta(lhs: object | None, rhs: object | None) -> int | float | None:
    if not isinstance(lhs, (int, float)) or isinstance(lhs, bool):
        return None
    if not isinstance(rhs, (int, float)) or isinstance(rhs, bool):
        return None
    value = float(rhs) - float(lhs)
    if float(lhs).is_integer() and float(rhs).is_integer():
        return int(value)
    return round(value, 4)


def _build_comparison(runs: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    indexed_runs: dict[str, dict[str, dict[str, Any]]] = {}
    excluded_documents_missing_key: dict[str, list[dict[str, Any]]] = {}
    available_keys_by_run: dict[str, set[str]] = {}

    source_roots = {run["source_root"] for run in runs if run.get("source_root")}
    if len(source_roots) > 1:
        warnings.append(
            "Run summaries use different source_root values. Serious comparison should use the same corpus layout and the same --pdf-dir basis."
        )

    manifest_hashes = {run["corpus_manifest_sha256"] for run in runs if run.get("corpus_manifest_sha256")}
    if len(manifest_hashes) > 1:
        warnings.append(
            "Run summaries use different corpus_manifest_sha256 values. Treat this comparison as different-manifest evidence, not same-manifest evidence."
        )
    elif len(manifest_hashes) == 1 and sum(1 for run in runs if run.get("corpus_manifest_sha256")) != len(runs):
        warnings.append(
            "Some run summaries are missing corpus_manifest_sha256. Same-manifest verification is incomplete."
        )

    for run in runs:
        indexed, excluded, run_warnings = _index_run_documents(run)
        indexed_runs[run["run_key"]] = indexed
        excluded_documents_missing_key[run["run_key"]] = excluded
        available_keys_by_run[run["run_key"]] = set(indexed)
        warnings.extend(run_warnings)

    if not available_keys_by_run:
        matched_keys: set[str] = set()
    else:
        matched_keys = set.intersection(*available_keys_by_run.values())

    unmatched_documents_by_run: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        run_key = run["run_key"]
        unmatched_documents_by_run[run_key] = []
        for key in sorted(available_keys_by_run[run_key] - matched_keys):
            document = indexed_runs[run_key][key]
            unmatched_documents_by_run[run_key].append(
                {
                    "source_pdf_relpath": key,
                    "filename": document.get("filename"),
                    "document_id": document.get("document_id"),
                }
            )

    reference_run = runs[0]
    documents: list[dict[str, Any]] = []
    for key in sorted(matched_keys):
        filename = next(
            (
                indexed_runs[run["run_key"]][key].get("filename")
                for run in runs
                if indexed_runs[run["run_key"]][key].get("filename")
            ),
            None,
        )
        document_row: dict[str, Any] = {
            "source_pdf_relpath": key,
            "filename": filename,
            "runs": {},
            "deltas_vs_reference": {},
        }
        for run in runs:
            run_key = run["run_key"]
            document = indexed_runs[run_key][key]
            document_row["runs"][run_key] = {
                field_name: document.get(field_name)
                for field_name in ("document_id", *COMPARISON_FIELDS)
            }

        reference_document = document_row["runs"][reference_run["run_key"]]
        for run in runs[1:]:
            run_key = run["run_key"]
            compared_document = document_row["runs"][run_key]
            document_row["deltas_vs_reference"][run_key] = {
                f"delta_{field_name}": _delta(
                    reference_document.get(field_name),
                    compared_document.get(field_name),
                )
                for field_name in DELTA_FIELDS
            }
        documents.append(document_row)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference_run_key": reference_run["run_key"],
        "runs": [
            {
                "run_key": run["run_key"],
                "mode_name": run["mode_name"],
                "path": run["path"],
                "run_id": run["run_id"],
                "git_head": run["git_head"],
                "pipeline_mode": run["pipeline_mode"],
                "spine_mode": run["spine_mode"],
                "pass2_execution_mode": run["pass2_execution_mode"],
                "source_root": run["source_root"],
                "corpus_manifest_path": run["corpus_manifest_path"],
                "corpus_manifest_sha256": run["corpus_manifest_sha256"],
            }
            for run in runs
        ],
        "matched_document_count": len(matched_keys),
        "unmatched_documents_by_run": unmatched_documents_by_run,
        "excluded_documents_missing_key": excluded_documents_missing_key,
        "warnings": warnings,
        "corpus_aggregate_comparison": {
            run["run_key"]: _aggregate_run(run)
            for run in runs
        },
        "documents": documents,
    }


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _build_markdown(comparison: dict[str, Any]) -> str:
    runs = comparison["runs"]
    reference_run_key = comparison["reference_run_key"]
    run_metadata_rows = [
        [
            run["run_key"],
            run.get("git_head") or "missing in run",
            str(run.get("pipeline_mode") or "missing in run"),
            str(run.get("spine_mode") or "missing in run"),
            str(run.get("pass2_execution_mode") or "missing in run"),
            str(run.get("source_root") or "missing in run"),
            str(run.get("corpus_manifest_sha256") or "missing in run"),
        ]
        for run in runs
    ]

    aggregate_rows = []
    for run in runs:
        aggregate = comparison["corpus_aggregate_comparison"][run["run_key"]]
        aggregate_rows.append(
            [
                run["run_key"],
                str(aggregate["completed_docs"]),
                str(aggregate["failed_docs"]),
                _display_metric(aggregate["avg_total_processing_time_seconds"]),
                _display_metric(aggregate["median_total_processing_time_seconds"]),
                _display_metric(aggregate["total_openai_call_count"]),
                _display_metric(aggregate["total_openai_pass2_call_count"]),
                _display_metric(aggregate["total_pass2_llm_count"]),
                _display_metric(aggregate["total_pass2_compat_count"]),
            ]
        )

    unmatched_sections: list[str] = []
    for run in runs:
        run_key = run["run_key"]
        unmatched = comparison["unmatched_documents_by_run"].get(run_key, [])
        excluded = comparison["excluded_documents_missing_key"].get(run_key, [])
        if not unmatched and not excluded:
            continue
        lines = [f"### `{run_key}`"]
        if unmatched:
            lines.append("Unmatched documents:")
            for document in unmatched:
                lines.append(
                    f"- `{document['source_pdf_relpath']}`"
                    f" (`{document.get('document_id')}`)"
                )
        if excluded:
            lines.append("Excluded because source_pdf_relpath is missing:")
            for document in excluded:
                lines.append(
                    f"- `{document.get('filename')}`"
                    f" (`{document.get('document_id')}`)"
                )
        unmatched_sections.append("\n".join(lines))

    document_sections: list[str] = []
    reference_label = reference_run_key
    reference_runs = [run for run in runs if run["run_key"] != reference_run_key]
    for run in reference_runs:
        rows: list[list[str]] = []
        for document in comparison["documents"]:
            reference_payload = document["runs"][reference_run_key]
            target_payload = document["runs"][run["run_key"]]
            deltas = document["deltas_vs_reference"][run["run_key"]]
            rows.append(
                [
                    document["source_pdf_relpath"],
                    str(reference_payload.get("final_status") or "missing in run"),
                    str(target_payload.get("final_status") or "missing in run"),
                    _display_metric(reference_payload.get("total_processing_time_seconds")),
                    _display_metric(target_payload.get("total_processing_time_seconds")),
                    _display_delta(deltas.get("delta_total_processing_time_seconds")),
                    _display_metric(reference_payload.get("openai_pass2_call_count")),
                    _display_metric(target_payload.get("openai_pass2_call_count")),
                    _display_delta(deltas.get("delta_openai_pass2_call_count")),
                    _display_metric(reference_payload.get("pass2_llm_count")),
                    _display_metric(target_payload.get("pass2_llm_count")),
                    _display_delta(deltas.get("delta_pass2_llm_count")),
                    _display_metric(reference_payload.get("pass2_compat_count")),
                    _display_metric(target_payload.get("pass2_compat_count")),
                ]
            )
        document_sections.append(
            "\n".join(
                [
                    f"## Document Comparison: `{reference_label}` vs `{run['run_key']}`",
                    _markdown_table(
                        [
                            "source_pdf_relpath",
                            f"{reference_label} final_status",
                            f"{run['run_key']} final_status",
                            f"{reference_label} total_time",
                            f"{run['run_key']} total_time",
                            "delta total_time",
                            f"{reference_label} openai_pass2",
                            f"{run['run_key']} openai_pass2",
                            "delta openai_pass2",
                            f"{reference_label} pass2_llm",
                            f"{run['run_key']} pass2_llm",
                            "delta pass2_llm",
                            f"{reference_label} pass2_compat",
                            f"{run['run_key']} pass2_compat",
                        ],
                        rows,
                    ),
                ]
            )
        )

    warning_lines = comparison.get("warnings", [])
    warning_section = ""
    if warning_lines:
        warning_section = "\n".join(["## Warnings", *[f"- {warning}" for warning in warning_lines]])

    unmatched_section = "\n\n".join(unmatched_sections) if unmatched_sections else "없음"
    document_section = "\n\n".join(document_sections) if document_sections else "비교 가능한 문서가 없음"

    return "\n\n".join(
        [
            "# Pipeline Mode Comparison",
            "같은 corpus layout과 같은 `--pdf-dir` 기준 run 비교를 권장한다. `source_root`나 `corpus_manifest_sha256`가 다르면 serious comparison으로 보기 어렵다.",
            warning_section if warning_section else "",
            "## Run Metadata",
            _markdown_table(
                [
                    "run_key",
                    "git_head",
                    "pipeline_mode",
                    "spine_mode",
                    "pass2_execution_mode",
                    "source_root",
                    "corpus_manifest_sha256",
                ],
                run_metadata_rows,
            ),
            "## Corpus Aggregate Comparison",
            _markdown_table(
                [
                    "run_key",
                    "completed_docs",
                    "failed_docs",
                    "avg_total_processing_time_seconds",
                    "median_total_processing_time_seconds",
                    "total_openai_call_count",
                    "total_openai_pass2_call_count",
                    "total_pass2_llm_count",
                    "total_pass2_compat_count",
                ],
                aggregate_rows,
            ),
            "## Unmatched Or Excluded Documents",
            unmatched_section,
            document_section,
        ]
    ).strip() + "\n"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if len(args.run_summaries) < 2:
        raise SystemExit("Provide at least two corpus run summaries to compare.")

    seen_run_keys: set[str] = set()
    runs = [
        _load_run(Path(raw_path).expanduser().resolve(), seen_run_keys)
        for raw_path in args.run_summaries
    ]
    comparison = _build_comparison(runs)

    output_prefix = _resolve_output_prefix(args.output_dir, args.output_prefix)
    json_path = output_prefix.with_suffix(".json")
    markdown_path = output_prefix.with_suffix(".md")

    _write_text(json_path, json.dumps(comparison, ensure_ascii=False, indent=2))
    _write_text(markdown_path, _build_markdown(comparison))

    print(f"Saved comparison JSON to {json_path}")
    print(f"Saved comparison Markdown to {markdown_path}")
    if comparison.get("warnings"):
        for warning in comparison["warnings"]:
            print(f"[warning] {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
