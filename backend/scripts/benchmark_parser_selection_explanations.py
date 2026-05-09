#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.codex_cli_client import CodexCLIClient  # noqa: E402


DEFAULT_SELECTION_IDS = [
    "text_control_lineage_diagram_p14",
    "research_caption_p10",
    "finance_decision_flow_p4",
    "pharma_brand_table_p22",
    "scanned_code_block_p43",
]


@dataclass(frozen=True)
class SelectionSpec:
    selection_id: str
    pdf_filename: str
    page_number: int
    selected_bbox: list[float]
    selection_type: str
    expected_concept_label: str
    expected_source_type: str | None
    notes: str | None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Codex CLI selected-region explanation samples from normalized parser benchmark artifacts.",
    )
    parser.add_argument("--benchmark-results", nargs="+", required=True, help="Parser benchmark result JSON files.")
    parser.add_argument("--parsers", nargs="+", required=True, help="Parser names to sample.")
    parser.add_argument("--goldset", default=str(PROJECT_ROOT / "benchmarks" / "parser_selection_goldset.yaml"))
    parser.add_argument("--selection-ids", nargs="+", default=DEFAULT_SELECTION_IDS)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pdf-dir", default=str(PROJECT_ROOT / "data" / "raw_pdfs"))
    parser.add_argument("--render-zoom", type=float, default=1.5)
    parser.add_argument("--dry-run", action="store_true", help="Build contexts but do not call Codex CLI.")
    return parser


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_bbox(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        x, y, width, height = [float(component) for component in value]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(component) for component in [x, y, width, height]):
        return None
    if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > 1.001 or y + height > 1.001:
        return None
    return [round(x, 6), round(y, 6), round(width, 6), round(height, 6)]


def _area(bbox: list[float]) -> float:
    return max(0.0, bbox[2]) * max(0.0, bbox[3])


def _intersection_area(first: list[float], second: list[float]) -> float:
    first_right = first[0] + first[2]
    first_bottom = first[1] + first[3]
    second_right = second[0] + second[2]
    second_bottom = second[1] + second[3]
    width = min(first_right, second_right) - max(first[0], second[0])
    height = min(first_bottom, second_bottom) - max(first[1], second[1])
    return max(0.0, width) * max(0.0, height)


def _center_distance(first: list[float], second: list[float]) -> float:
    first_center_x = first[0] + first[2] / 2
    first_center_y = first[1] + first[3] / 2
    second_center_x = second[0] + second[2] / 2
    second_center_y = second[1] + second[3] / 2
    return ((first_center_x - second_center_x) ** 2 + (first_center_y - second_center_y) ** 2) ** 0.5


def _compact_text(value: object, max_chars: int = 360) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "..."


def _load_goldset(path: Path) -> dict[str, SelectionSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    specs: dict[str, SelectionSpec] = {}
    for selection in payload.get("selections", []):
        if not isinstance(selection, dict):
            continue
        bbox = _as_bbox(selection.get("selected_bbox"))
        if bbox is None:
            continue
        selection_id = str(selection.get("selection_id") or "")
        if not selection_id:
            continue
        specs[selection_id] = SelectionSpec(
            selection_id=selection_id,
            pdf_filename=str(selection.get("pdf_filename") or ""),
            page_number=int(selection.get("page_number") or 1),
            selected_bbox=bbox,
            selection_type=str(selection.get("selection_type") or "other"),
            expected_concept_label=str(selection.get("expected_concept_label") or ""),
            expected_source_type=(
                str(selection.get("expected_source_type"))
                if selection.get("expected_source_type") is not None
                else None
            ),
            notes=str(selection.get("notes")) if selection.get("notes") is not None else None,
        )
    return specs


def _load_artifact_index(result_paths: list[Path]) -> dict[tuple[str, str], Path]:
    artifact_index: dict[tuple[str, str], Path] = {}
    for result_path in result_paths:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        for row in payload.get("results", []):
            if not isinstance(row, dict) or row.get("status") != "completed":
                continue
            parser_name = str(row.get("parser_name") or "")
            filename = str(row.get("filename") or "")
            artifact_path = ((row.get("normalized_page_element_map") or {}).get("artifact_path") or row.get("artifact_path"))
            if parser_name and filename and artifact_path:
                artifact_index[(parser_name, filename)] = PROJECT_ROOT / str(artifact_path)
    return artifact_index


def _elements_for_page(page_map: dict[str, Any], page_number: int) -> list[dict[str, Any]]:
    for page in page_map.get("pages", []):
        if isinstance(page, dict) and int(page.get("page_number") or 0) == page_number:
            return [element for element in page.get("elements", []) if isinstance(element, dict)]
    return []


def _rank_elements(elements: list[dict[str, Any]], selected_bbox: list[float]) -> list[dict[str, Any]]:
    scored = []
    for element in elements:
        bbox = _as_bbox(element.get("bbox"))
        text = _compact_text(element.get("text"), max_chars=360)
        if bbox is None:
            continue
        overlap = _intersection_area(selected_bbox, bbox)
        overlap_ratio = overlap / max(0.0001, min(_area(selected_bbox), _area(bbox)))
        distance = _center_distance(selected_bbox, bbox)
        score = overlap_ratio - distance * 0.14
        scored.append((score, overlap_ratio, distance, element, bbox, text))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "element_id": str(element.get("element_id") or ""),
            "element_type": str(element.get("element_type") or "other"),
            "bbox": bbox,
            "text": text,
            "selection_overlap_ratio": round(max(0.0, overlap_ratio), 4),
            "selection_center_distance": round(max(0.0, distance), 4),
            "match_score": round(score, 4),
            "quality_notes": element.get("quality_notes") or [],
            "relations": element.get("relations") or {},
        }
        for score, overlap_ratio, distance, element, bbox, text in scored[:5]
    ]


def _nearby_text(elements: list[dict[str, Any]], selected_bbox: list[float]) -> list[dict[str, Any]]:
    ranked = _rank_elements(elements, selected_bbox)
    with_text = [element for element in ranked if element.get("text")]
    if len(with_text) >= 5:
        return with_text[:5]
    seen = {element["element_id"] for element in with_text}
    for element in elements:
        if len(with_text) >= 5:
            break
        text = _compact_text(element.get("text"), max_chars=360)
        if not text or str(element.get("element_id") or "") in seen:
            continue
        with_text.append(
            {
                "element_id": str(element.get("element_id") or ""),
                "element_type": str(element.get("element_type") or "other"),
                "bbox": _as_bbox(element.get("bbox")),
                "text": text,
            }
        )
    return with_text[:5]


def _build_context(parser_name: str, spec: SelectionSpec, page_map: dict[str, Any]) -> dict[str, Any]:
    elements = _elements_for_page(page_map, spec.page_number)
    matched = _rank_elements(elements, spec.selected_bbox)
    nearby = _nearby_text(elements, spec.selected_bbox)
    page_text = " ".join(_compact_text(element.get("text"), max_chars=240) for element in elements if element.get("text"))
    source_candidates = [
        {
            "source_type": element.get("element_type") or "other",
            "page_number": spec.page_number,
            "snippet": element.get("text") or element.get("element_type"),
            "bbox": element.get("bbox"),
            "element_id": element.get("element_id"),
        }
        for element in matched[:4]
        if element.get("text") or element.get("element_type") in {"figure", "table"}
    ]
    context = {
        "context_version": "parser_selection_sample_context_v1",
        "document_id": f"parser_bench_{hashlib.sha1(spec.pdf_filename.encode()).hexdigest()[:8]}",
        "page_number": spec.page_number,
        "selected_bbox": [round(value, 3) for value in spec.selected_bbox],
        "selection_type": spec.selection_type,
        "expected_concept_label": spec.expected_concept_label,
        "source_parser": parser_name,
        "matched_page_elements": matched,
        "nearby_text_blocks": nearby,
        "page_role": "parser benchmark selected-region sample",
        "page_summary": _compact_text(page_text, max_chars=700),
        "document_context_brief": {
            "available": True,
            "overall_topic": Path(spec.pdf_filename).stem,
            "overall_summary": "Parser benchmark context only; compare whether parser output grounds the selected region.",
            "key_concepts": [],
            "sections": [],
            "difficult_pages": [],
        },
        "related_page_candidates": [],
        "source_candidates": source_candidates,
        "parser_source": parser_name,
    }
    core = _stable_json(context)
    context["context_hash"] = hashlib.sha1(core.encode("utf-8")).hexdigest()
    context["metrics"] = {
        "selection_context_size_chars": len(core),
        "matched_element_count": len(matched),
        "nearby_text_block_count": len(nearby),
        "source_candidate_count": len(source_candidates),
    }
    return context


def _render_page(pdf_path: Path, page_number: int, output_path: Path, zoom: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as document:
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pixmap.save(output_path)


def _evaluate_result(result: dict[str, Any] | None, spec: SelectionSpec) -> dict[str, Any]:
    if not result:
        return {"source_cue_count": 0, "related_count": 0, "mentions_expected_label": False, "has_specific_title": False}
    text = " ".join(
        str(result.get(key) or "")
        for key in [
            "concept_title",
            "label",
            "what_this_is",
            "what_it_means_here",
            "meaning_in_context",
            "why_it_matters_here",
            "short_explanation",
            "long_explanation",
        ]
    ).lower()
    expected_terms = [term for term in spec.expected_concept_label.lower().replace("/", " ").split() if len(term) >= 4]
    return {
        "source_cue_count": len(result.get("source_cues") or []),
        "related_count": len(result.get("related_concepts_and_pages") or []),
        "mentions_expected_label": any(term in text for term in expected_terms[:4]),
        "has_specific_title": bool(str(result.get("concept_title") or result.get("label") or "").strip()),
        "confidence": result.get("confidence"),
    }


def main() -> int:
    args = _build_parser().parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    result_paths = [Path(path).expanduser().resolve() for path in args.benchmark_results]
    artifacts = _load_artifact_index(result_paths)
    specs_by_id = _load_goldset(Path(args.goldset).expanduser().resolve())
    specs = [specs_by_id[selection_id] for selection_id in args.selection_ids if selection_id in specs_by_id]

    client = None if args.dry_run else CodexCLIClient()
    rows = []
    for parser_name in args.parsers:
        for spec in specs:
            artifact_path = artifacts.get((parser_name, spec.pdf_filename))
            if artifact_path is None or not artifact_path.exists():
                rows.append(
                    {
                        "parser_name": parser_name,
                        "selection_id": spec.selection_id,
                        "status": "skipped",
                        "error": "Missing normalized PageElementMap artifact.",
                    }
                )
                continue
            page_map = json.loads(artifact_path.read_text(encoding="utf-8"))
            context = _build_context(parser_name, spec, page_map)
            image_path = output_dir / "images" / f"{parser_name}__{spec.selection_id}.png"
            _render_page(pdf_dir / spec.pdf_filename, spec.page_number, image_path, args.render_zoom)

            row = {
                "parser_name": parser_name,
                "selection_id": spec.selection_id,
                "pdf_filename": spec.pdf_filename,
                "page_number": spec.page_number,
                "selection_type": spec.selection_type,
                "expected_concept_label": spec.expected_concept_label,
                "selected_bbox": spec.selected_bbox,
                "context": context,
                "image_path": str(image_path),
                "status": "dry_run" if args.dry_run else "pending",
                "latency_seconds": None,
                "result": None,
                "meta": None,
                "quality_proxy": None,
                "error": None,
            }
            if client is not None:
                started_at = time.perf_counter()
                try:
                    envelope = client.run_selection_explanation(
                        page_image_path=image_path,
                        document_id=context["document_id"],
                        page_number=spec.page_number,
                        selection_id=f"{parser_name}__{spec.selection_id}",
                        selected_bbox=spec.selected_bbox,
                        selection_context=context,
                    )
                    row["latency_seconds"] = round(time.perf_counter() - started_at, 4)
                    row["status"] = "completed"
                    row["result"] = envelope.get("result")
                    row["meta"] = envelope.get("meta")
                    row["quality_proxy"] = _evaluate_result(row["result"], spec)
                except Exception as exc:
                    row["latency_seconds"] = round(time.perf_counter() - started_at, 4)
                    row["status"] = "failed"
                    row["error"] = str(exc)
            rows.append(row)
            (output_dir / "selection_explanation_samples.partial.json").write_text(
                json.dumps({"rows": rows}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parsers": args.parsers,
        "selection_ids": [spec.selection_id for spec in specs],
        "dry_run": args.dry_run,
        "result_count": len(rows),
        "rows": rows,
    }
    (output_dir / "selection_explanation_samples.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"result_count": len(rows), "status_counts": _status_counts(rows)}, ensure_ascii=False, indent=2))
    return 0


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":
    raise SystemExit(main())
