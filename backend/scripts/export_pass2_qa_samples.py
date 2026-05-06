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

from app.services.storage import StorageService, get_storage_service
from app.services.pass2_artifact_builder import Pass2ArtifactBuilder


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "perf_runs"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0.")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export pass2 QA samples from existing analysis artifacts.",
    )
    parser.add_argument(
        "document_ids",
        nargs="*",
        help="Document IDs to export. If omitted, analysis_dir is scanned.",
    )
    parser.add_argument(
        "--analysis-dir",
        help="Optional analysis directory to scan when document_ids are omitted.",
    )
    parser.add_argument(
        "--limit-docs",
        type=_positive_int,
        help="Maximum number of documents to include.",
    )
    parser.add_argument(
        "--limit-pages",
        type=_positive_int,
        help="Maximum number of sampled pages per document.",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional output directory for QA sample JSON/Markdown artifacts.",
    )
    parser.add_argument(
        "--output-prefix",
        help="Optional output prefix path without extension, for example docs/perf_runs/20260329T000000Z_qa_samples.",
    )
    parser.add_argument(
        "--corpus-manifest",
        help="Optional corpus manifest JSON path used to enrich rows with expected_type.",
    )
    return parser


def _resolve_output_prefix(raw_output_dir: str | None, raw_output_prefix: str | None) -> Path:
    if raw_output_dir and raw_output_prefix:
        raise SystemExit("Use either --output-dir or --output-prefix, not both.")
    if raw_output_prefix:
        return Path(raw_output_prefix).expanduser().resolve()
    output_dir = Path(raw_output_dir).expanduser().resolve() if raw_output_dir else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return output_dir / f"{timestamp}_qa_samples"


def _normalize_relpath(value: str | None) -> str | None:
    if not value:
        return None
    return Path(value).as_posix()


def _load_manifest_expected_types(raw_manifest_path: str | None) -> tuple[str | None, dict[str, str]]:
    if not raw_manifest_path:
        return None, {}

    manifest_path = Path(raw_manifest_path).expanduser().resolve()
    if not manifest_path.exists() or not manifest_path.is_file():
        raise SystemExit(f"Corpus manifest not found: {manifest_path}")

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise SystemExit(f"Corpus manifest documents must be a list: {manifest_path}")

    expected_types: dict[str, str] = {}
    for document in documents:
        if not isinstance(document, dict):
            continue
        relpath = _normalize_relpath(document.get("source_pdf_relpath"))
        expected_type = document.get("expected_type")
        if relpath and isinstance(expected_type, str) and expected_type.strip():
            expected_types[relpath] = expected_type.strip()
            expected_types[Path(relpath).name] = expected_type.strip()
    return manifest_path.as_posix(), expected_types


def _resolve_document_ids(
    *,
    storage: StorageService,
    raw_document_ids: list[str],
    raw_analysis_dir: str | None,
    limit_docs: int | None,
) -> list[str]:
    if raw_document_ids:
        return raw_document_ids[:limit_docs] if limit_docs is not None else raw_document_ids

    analysis_dir = (
        Path(raw_analysis_dir).expanduser().resolve()
        if raw_analysis_dir
        else storage.analysis_dir.resolve()
    )
    if not analysis_dir.exists() or not analysis_dir.is_dir():
        raise SystemExit(f"Analysis directory not found: {analysis_dir}")

    document_ids = sorted(
        path.name
        for path in analysis_dir.iterdir()
        if path.is_dir()
    )
    if limit_docs is not None:
        document_ids = document_ids[:limit_docs]
    return document_ids


def _page_routing_by_page(storage: StorageService, document_id: str) -> dict[int, dict[str, Any]]:
    artifact = storage.load_page_routing(document_id)
    if artifact is None:
        return {}
    return {
        int(page["page_number"]): dict(page)
        for page in artifact["result"].get("pages", [])
    }


def _document_has_generation_mode(rows: list[dict[str, Any]], generation_mode: str) -> bool:
    return any(row.get("pass2_generation_mode") == generation_mode for row in rows)


def _score_or_default(value: object | None, fallback: int) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return fallback


def _spread_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) <= 2:
        return list(rows)

    ascending = sorted(rows, key=lambda row: (_score_or_default(row.get("hard_page_score"), -1), row["page_number"]))
    spread: list[dict[str, Any]] = []
    left = 0
    right = len(ascending) - 1
    take_high = True
    while left <= right:
        if take_high:
            spread.append(ascending[right])
            right -= 1
        else:
            spread.append(ascending[left])
            left += 1
        take_high = not take_high
    return spread


def _sample_document_rows(rows: list[dict[str, Any]], limit_pages: int | None) -> list[dict[str, Any]]:
    compat_rows = [row for row in rows if row.get("pass2_generation_mode") == "compat"]
    llm_rows = [row for row in rows if row.get("pass2_generation_mode") == "llm"]
    compat_rows = _spread_rows(compat_rows)
    llm_rows = _spread_rows(llm_rows)

    selected: list[dict[str, Any]] = []
    if compat_rows:
        selected.append(compat_rows.pop(0))
    if llm_rows:
        selected.append(llm_rows.pop(0))

    remaining = compat_rows + llm_rows
    remaining.sort(
        key=lambda row: (
            row.get("pass2_generation_mode") != "compat",
            -_score_or_default(row.get("hard_page_score"), -1),
            row["page_number"],
        )
    )

    selected.extend(remaining)
    if limit_pages is not None:
        selected = selected[:limit_pages]
    return selected


def _build_sample_row(
    *,
    storage: StorageService,
    artifact_builder: Pass2ArtifactBuilder,
    document_id: str,
    page_number: int,
    routing_entry: dict[str, Any] | None,
    expected_types: dict[str, str],
    document_summary_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    pass1_artifact = storage.load_pass1_result(document_id, page_number)
    pass2_artifact = storage.load_pass2_result(document_id, page_number)
    if pass1_artifact is None or pass2_artifact is None:
        return None

    page_record = storage.get_page(document_id, page_number)
    document_record = storage.get_document(document_id)

    final_anchors = pass2_artifact["result"].get("final_anchors", [])
    prerequisites = []
    anchor_questions = []
    for anchor in final_anchors:
        prerequisite = str(anchor.get("prerequisite") or "").strip()
        if prerequisite and prerequisite not in prerequisites:
            prerequisites.append(prerequisite)
        question = str(anchor.get("question") or "").strip()
        if question:
            anchor_questions.append(question)
    source_pdf_relpath = _normalize_relpath(document_record.filename if document_record else None)
    related_pages: list[int] = []
    for anchor in final_anchors:
        for related_page in anchor.get("related_pages", []):
            normalized_page = int(related_page)
            if normalized_page not in related_pages:
                related_pages.append(normalized_page)

    planner_reason = None
    if routing_entry is not None:
        base_route_reason = routing_entry.get("base_route_reason")
        hard_page_reasons = routing_entry.get("hard_page_reasons")
        if isinstance(base_route_reason, str) and base_route_reason.strip():
            planner_reason = base_route_reason.strip()
        elif isinstance(hard_page_reasons, list):
            normalized_reasons = [str(reason).strip() for reason in hard_page_reasons if str(reason).strip()]
            planner_reason = "; ".join(normalized_reasons) or None

    compat_trace = {
        "compat_prerequisite_source": None,
        "compat_related_pages_source": None,
        "compat_long_explanation_shape": None,
        "compat_used_section_title": None,
    }
    if (
        pass2_artifact["meta"].get("pass2_generation_mode") == "compat"
        and document_summary_result is not None
    ):
        compat_trace = artifact_builder.describe_compat_trace(
            document_id=document_id,
            page_number=page_number,
            pass1_result=pass1_artifact["result"],
            pass1_meta=pass1_artifact.get("meta"),
            document_summary_result=document_summary_result,
            page_routing_entry=routing_entry,
            final_anchors=final_anchors,
        )

    return {
        "document_id": document_id,
        "source_pdf_relpath": source_pdf_relpath,
        "stored_pdf_relpath": _normalize_relpath(document_record.original_path if document_record else None),
        "page_number": page_number,
        "page_image_relpath": _normalize_relpath(page_record.image_path if page_record else None),
        "expected_type": expected_types.get(source_pdf_relpath or ""),
        "pass2_generation_mode": pass2_artifact["meta"].get("pass2_generation_mode"),
        "pass1_path": pass1_artifact["meta"].get("pass1_path"),
        "route_label": (
            routing_entry.get("base_route_label")
            if routing_entry is not None
            else pass1_artifact["meta"].get("route_label")
        ),
        "hard_page_score": (
            routing_entry.get("hard_page_score")
            if routing_entry is not None
            else None
        ),
        "recommended_execution": (
            routing_entry.get("recommended_execution")
            if routing_entry is not None
            else None
        ),
        "planner_reason": planner_reason,
        "candidate_anchor_count": len(pass1_artifact["result"].get("candidate_anchors", [])),
        "final_anchor_count": len(final_anchors),
        "anchor_labels": [anchor.get("label") for anchor in final_anchors],
        "anchor_types": [anchor.get("anchor_type") for anchor in final_anchors],
        "short_explanations": [anchor.get("short_explanation") for anchor in final_anchors],
        "long_explanations": [anchor.get("long_explanation") for anchor in final_anchors],
        "related_pages": related_pages,
        "page_risk_note": pass2_artifact["result"].get("page_risk_note"),
        "page_role": pass2_artifact["result"].get("page_role") or pass1_artifact["result"].get("page_role"),
        "page_summary": pass2_artifact["result"].get("page_summary") or pass1_artifact["result"].get("page_summary"),
        "prerequisites": prerequisites,
        "anchor_questions": anchor_questions,
        **compat_trace,
    }


def _collect_document_samples(
    *,
    storage: StorageService,
    artifact_builder: Pass2ArtifactBuilder,
    document_id: str,
    limit_pages: int | None,
    expected_types: dict[str, str],
) -> dict[str, Any] | None:
    routing_by_page = _page_routing_by_page(storage, document_id)
    document_summary = storage.load_document_summary(document_id)
    document_summary_result = document_summary["result"] if document_summary is not None else None
    page_rows: list[dict[str, Any]] = []
    for page_record in storage.get_pages(document_id):
        row = _build_sample_row(
            storage=storage,
            artifact_builder=artifact_builder,
            document_id=document_id,
            page_number=page_record.page_number,
            routing_entry=routing_by_page.get(page_record.page_number),
            expected_types=expected_types,
            document_summary_result=document_summary_result,
        )
        if row is not None:
            page_rows.append(row)

    if not page_rows:
        return None

    page_rows.sort(
        key=lambda row: (
            row.get("pass2_generation_mode") != "compat",
            -_score_or_default(row.get("hard_page_score"), -1),
            row["page_number"],
        )
    )
    sampled_rows = _sample_document_rows(page_rows, limit_pages)
    return {
        "document_id": document_id,
        "source_pdf_relpath": sampled_rows[0].get("source_pdf_relpath"),
        "expected_type": sampled_rows[0].get("expected_type"),
        "has_compat_pages": _document_has_generation_mode(page_rows, "compat"),
        "has_llm_pages": _document_has_generation_mode(page_rows, "llm"),
        "samples": sampled_rows,
    }


def _sort_document_samples(document_samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        document_samples,
        key=lambda document: (
            not document["has_compat_pages"],
            not document["has_llm_pages"],
            document["document_id"],
        ),
    )


def _build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Pass2 QA Samples",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- sampled_document_count: `{payload['sampled_document_count']}`",
        f"- total_sample_count: `{payload['total_sample_count']}`",
        "",
    ]
    for document in payload["documents"]:
        lines.extend(
            [
                f"## `{document['document_id']}`",
                f"- source_pdf_relpath: `{document.get('source_pdf_relpath') or 'missing'}`",
                f"- expected_type: `{document.get('expected_type') or 'missing'}`",
                f"- has_compat_pages: `{document['has_compat_pages']}`",
                f"- has_llm_pages: `{document['has_llm_pages']}`",
                "",
            ]
        )
        for sample in document["samples"]:
            lines.extend(
                [
                    f"### Page {sample['page_number']} ({sample.get('pass2_generation_mode') or 'unknown'})",
                    f"- source_pdf_relpath: `{sample.get('source_pdf_relpath') or 'missing'}`",
                    f"- stored_pdf_relpath: `{sample.get('stored_pdf_relpath') or 'missing'}`",
                    f"- page_image_relpath: `{sample.get('page_image_relpath') or 'missing'}`",
                    f"- expected_type: `{sample.get('expected_type') or 'missing'}`",
                    f"- pass1_path: `{sample.get('pass1_path') or 'missing'}`",
                    f"- route_label: `{sample.get('route_label') or 'missing'}`",
                    f"- hard_page_score: `{sample.get('hard_page_score')}`",
                    f"- recommended_execution: `{sample.get('recommended_execution') or 'missing'}`",
                    f"- planner_reason: {sample.get('planner_reason') or 'missing'}",
                    f"- compat_prerequisite_source: `{sample.get('compat_prerequisite_source')}`",
                    f"- compat_related_pages_source: `{sample.get('compat_related_pages_source')}`",
                    f"- compat_long_explanation_shape: `{sample.get('compat_long_explanation_shape')}`",
                    f"- compat_used_section_title: `{sample.get('compat_used_section_title')}`",
                    f"- page_role: `{sample.get('page_role') or 'missing'}`",
                    f"- candidate_anchor_count: `{sample.get('candidate_anchor_count')}`",
                    f"- final_anchor_count: `{sample.get('final_anchor_count')}`",
                    f"- anchor_labels: `{sample.get('anchor_labels')}`",
                    f"- anchor_types: `{sample.get('anchor_types')}`",
                    f"- prerequisites: `{sample.get('prerequisites')}`",
                    f"- anchor_questions: `{sample.get('anchor_questions')}`",
                    f"- related_pages: `{sample.get('related_pages')}`",
                    f"- page_risk_note: {sample.get('page_risk_note') or 'missing'}",
                    f"- page_summary: {sample.get('page_summary') or 'missing'}",
                    "- short_explanations:",
                ]
            )
            for explanation in sample.get("short_explanations", []):
                lines.append(f"  - {explanation}")
            lines.append("- long_explanations:")
            for explanation in sample.get("long_explanations", []):
                lines.append(f"  - {explanation}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    storage = get_storage_service()
    artifact_builder = Pass2ArtifactBuilder(storage=storage)
    corpus_manifest_path, expected_types = _load_manifest_expected_types(args.corpus_manifest)
    document_ids = _resolve_document_ids(
        storage=storage,
        raw_document_ids=args.document_ids,
        raw_analysis_dir=args.analysis_dir,
        limit_docs=args.limit_docs,
    )

    document_samples = []
    for document_id in document_ids:
        sample = _collect_document_samples(
            storage=storage,
            artifact_builder=artifact_builder,
            document_id=document_id,
            limit_pages=args.limit_pages,
            expected_types=expected_types,
        )
        if sample is not None:
            document_samples.append(sample)

    document_samples = _sort_document_samples(document_samples)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_dir": (
            str(Path(args.analysis_dir).expanduser().resolve())
            if args.analysis_dir
            else storage.analysis_dir.resolve().as_posix()
        ),
        "corpus_manifest_path": corpus_manifest_path,
        "sampled_document_count": len(document_samples),
        "total_sample_count": sum(len(document["samples"]) for document in document_samples),
        "documents": document_samples,
    }

    output_prefix = _resolve_output_prefix(args.output_dir, args.output_prefix)
    json_path = output_prefix.with_suffix(".json")
    markdown_path = output_prefix.with_suffix(".md")

    _write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    _write_text(markdown_path, _build_markdown(payload))

    print(f"Saved QA samples JSON to {json_path}")
    print(f"Saved QA samples Markdown to {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
