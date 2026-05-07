#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import tracemalloc
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_GOLDSET_PATH = PROJECT_ROOT / "benchmarks" / "parser_selection_goldset.yaml"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


RUNNER_VERSION = "parser_benchmark_suite_v1"
NORMALIZED_SCHEMA_VERSION = "page_element_map_v1"
PARSER_STATUS_COMPLETED = "completed"
PARSER_STATUS_SKIPPED = "skipped"
PARSER_STATUS_FAILED = "failed"

ELEMENT_TYPES = {
    "heading",
    "paragraph",
    "figure",
    "table",
    "formula",
    "caption",
    "list",
    "other",
}

RUBRIC_WEIGHTS = {
    "selected_bbox_matching_quality": 20,
    "reading_order": 15,
    "layout_element_detection": 15,
    "source_cue_usefulness": 15,
    "related_context_usefulness": 10,
    "ocr_scanned_robustness": 10,
    "speed": 10,
    "integration_complexity": 5,
}

INSTALL_HINTS = {
    "docling": "python -m venv /tmp/scholium-parser-docling && /tmp/scholium-parser-docling/bin/pip install docling",
    "marker": "python -m venv /tmp/scholium-parser-marker && /tmp/scholium-parser-marker/bin/pip install marker-pdf",
    "mineru": "python -m venv /tmp/scholium-parser-mineru && /tmp/scholium-parser-mineru/bin/pip install mineru",
    "markitdown": "python -m venv /tmp/scholium-parser-markitdown && /tmp/scholium-parser-markitdown/bin/pip install markitdown[pdf]",
}


@dataclass(frozen=True)
class ParserCandidate:
    name: str
    display_name: str
    required_modules: tuple[str, ...]
    runner: Callable[[Path, str], dict[str, Any]]
    install_hint: str | None
    integration_complexity_score: float
    notes: str


@dataclass(frozen=True)
class SelectionRegion:
    selection_id: str
    pdf_filename: str
    page_number: int
    selected_bbox: list[float]
    selection_type: str
    expected_concept_label: str | None
    expected_source_type: str | None
    notes: str | None
    source: str


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0.")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark PDF parser backends for Scholium's selected-region explanation product. "
            "All outputs are normalized into PageElementMap-like JSON before comparison."
        ),
    )
    parser.add_argument("pdfs", nargs="*", help="PDF files to benchmark.")
    parser.add_argument("--pdf-dir", help="Directory containing PDF files to process non-recursively.")
    parser.add_argument("--limit", type=_positive_int, help="Maximum number of PDFs when using --pdf-dir.")
    parser.add_argument(
        "--parsers",
        nargs="+",
        default=[
            "pymupdf4llm_current",
            "pymupdf4llm_enhanced",
            "docling",
            "marker",
            "mineru",
            "markitdown",
        ],
        help="Parser names to run.",
    )
    parser.add_argument(
        "--goldset",
        default=str(DEFAULT_GOLDSET_PATH),
        help="Gold selection YAML/JSON file. JSON syntax is accepted inside .yaml files.",
    )
    parser.add_argument(
        "--proxy-selections-per-pdf",
        type=_positive_int,
        default=5,
        help="Auto-generated proxy selections per PDF when no matching gold selections exist.",
    )
    parser.add_argument("--mode-name", help="Human-readable label for this run.")
    parser.add_argument("--output-dir", help="Run directory. Defaults to docs/perf_runs/parser_benchmark_<timestamp>.")
    parser.add_argument("--output", help="Explicit result JSON path. If set, no timestamped run directory is required.")
    parser.add_argument(
        "--write-artifacts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write normalized per-parser PageElementMap artifacts.",
    )
    parser.add_argument(
        "--markdown-summary",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write parser_benchmark_summary.md.",
    )
    parser.add_argument("--fail-on-error", action="store_true", help="Exit non-zero if any parser run fails.")
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
        pdf_paths = sorted(path for path in pdf_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
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
        raise SystemExit("No PDF files were found.")
    return input_mode, pdf_paths, source_root


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _default_run_dir() -> Path:
    return PROJECT_ROOT / "docs" / "perf_runs" / f"parser_benchmark_{_timestamp()}"


def _resolve_run_paths(args: argparse.Namespace) -> tuple[Path | None, Path]:
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        return output_path.parent, output_path
    run_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else _default_run_dir()
    return run_dir, run_dir / "parser_benchmark_results.json"


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


def _get_git_branch() -> str | None:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    normalized = result.stdout.strip()
    return normalized or None


def _safe_relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _pdf_sha256(pdf_path: Path) -> str:
    digest = hashlib.sha256()
    with pdf_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _module_version(module_name: str) -> str | None:
    package_candidates = {
        "fitz": ["PyMuPDF", "pymupdf", "fitz"],
        "pymupdf4llm": ["pymupdf4llm"],
        "docling": ["docling"],
        "marker": ["marker-pdf", "marker"],
        "magic_pdf": ["mineru", "magic-pdf"],
        "mineru": ["mineru"],
        "markitdown": ["markitdown"],
    }.get(module_name, [module_name])
    for package_name in package_candidates:
        try:
            return importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _candidate_availability(candidate: ParserCandidate) -> dict[str, Any]:
    module_rows = []
    for module_name in candidate.required_modules:
        module_rows.append(
            {
                "module": module_name,
                "available": _package_available(module_name),
                "version": _module_version(module_name),
            }
        )
    available = all(row["available"] for row in module_rows)
    return {
        "available": available,
        "modules": module_rows,
        "install_hint": None if available else candidate.install_hint,
        "notes": candidate.notes,
    }


def _round(value: float | None, places: int = 4) -> float | None:
    if value is None:
        return None
    if not math.isfinite(float(value)):
        return None
    return round(float(value), places)


def _compact_text(value: object, *, max_chars: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "..."


def _as_bbox(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, width, height = [float(component) for component in value]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(component) for component in [x, y, width, height]):
        return None
    if width <= 0 or height <= 0:
        return None
    if x < 0 or y < 0 or x + width > 1.001 or y + height > 1.001:
        return None
    return [
        round(max(0.0, min(1.0, x)), 6),
        round(max(0.0, min(1.0, y)), 6),
        round(max(0.0, min(1.0, width)), 6),
        round(max(0.0, min(1.0, height)), 6),
    ]


def _bbox_area(bbox: list[float]) -> float:
    return max(0.0, float(bbox[2])) * max(0.0, float(bbox[3]))


def _intersection_area(first_bbox: list[float], second_bbox: list[float]) -> float:
    first_right = first_bbox[0] + first_bbox[2]
    first_bottom = first_bbox[1] + first_bbox[3]
    second_right = second_bbox[0] + second_bbox[2]
    second_bottom = second_bbox[1] + second_bbox[3]
    width = min(first_right, second_right) - max(first_bbox[0], second_bbox[0])
    height = min(first_bottom, second_bottom) - max(first_bbox[1], second_bbox[1])
    return max(0.0, width) * max(0.0, height)


def _overlap_ratio(selection_bbox: list[float], element_bbox: list[float]) -> float:
    overlap = _intersection_area(selection_bbox, element_bbox)
    denominator = max(0.0001, min(_bbox_area(selection_bbox), _bbox_area(element_bbox)))
    return overlap / denominator


def _center_distance(first_bbox: list[float], second_bbox: list[float]) -> float:
    first_center_x = first_bbox[0] + first_bbox[2] / 2
    first_center_y = first_bbox[1] + first_bbox[3] / 2
    second_center_x = second_bbox[0] + second_bbox[2] / 2
    second_center_y = second_bbox[1] + second_bbox[3] / 2
    return ((first_center_x - second_center_x) ** 2 + (first_center_y - second_center_y) ** 2) ** 0.5


def _normalize_fitz_rect(rect: Any, width: float, height: float) -> list[float] | None:
    if width <= 0 or height <= 0:
        return None
    x0 = max(0.0, min(float(rect.x0) / width, 1.0))
    y0 = max(0.0, min(float(rect.y0) / height, 1.0))
    x1 = max(0.0, min(float(rect.x1) / width, 1.0))
    y1 = max(0.0, min(float(rect.y1) / height, 1.0))
    return _as_bbox([x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0)])


def _element(
    *,
    parser_name: str,
    page_number: int,
    reading_order: int,
    element_type: str,
    text: str = "",
    bbox: list[float] | None = None,
    confidence: float | None = None,
    quality_notes: list[str] | None = None,
    relations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_type = element_type if element_type in ELEMENT_TYPES else "other"
    return {
        "element_id": f"{parser_name}:p{page_number}:e{reading_order}",
        "page_number": page_number,
        "element_type": normalized_type,
        "text": text,
        "bbox": bbox,
        "reading_order": reading_order,
        "source_parser": parser_name,
        "confidence": confidence,
        "quality_notes": quality_notes or [],
        "relations": relations or {},
    }


def _page_map(
    *,
    parser_name: str,
    page_number: int,
    width: float | None,
    height: float | None,
    elements: list[dict[str, Any]],
    ocr_used: bool = False,
    parser_notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "page_number": page_number,
        "width": width,
        "height": height,
        "ocr_used": ocr_used,
        "source_parser": parser_name,
        "parser_notes": parser_notes or [],
        "elements": elements,
    }


def _document_map(
    *,
    parser_name: str,
    pdf_path: Path,
    pages: list[dict[str, Any]],
    parser_notes: list[str] | None = None,
    raw_output_size_chars: int | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "parser_name": parser_name,
        "source_pdf": pdf_path.as_posix(),
        "filename": pdf_path.name,
        "pdf_sha256": _pdf_sha256(pdf_path),
        "page_count": len(pages),
        "parser_notes": parser_notes or [],
        "raw_output_size_chars": raw_output_size_chars,
        "pages": pages,
    }


def _infer_element_type_from_text(
    *,
    text: str,
    max_font_size: float | None = None,
    body_font_size: float | None = None,
    previous_type: str | None = None,
) -> str:
    stripped = text.strip()
    lower = stripped.lower()
    if not stripped:
        return "other"
    if re.match(r"^\s*[-*+•]\s+", stripped) or re.match(r"^\s*\d+[\.\)]\s+", stripped):
        return "list"
    if re.match(r"^\s*(figure|fig\.|table|chart|image|caption)\b", lower):
        return "caption"
    if previous_type in {"figure", "table"} and len(stripped) < 240:
        if re.search(r"\b(figure|fig\.|table|chart|source|caption)\b", lower):
            return "caption"
    formula_signals = len(re.findall(r"[=∑∫√≤≥±×÷]|\\frac|\\sum|\\int|\b(alpha|beta|gamma|sigma|mu)\b", stripped))
    if formula_signals >= 2 and len(stripped) <= 220:
        return "formula"
    if stripped.startswith("#"):
        return "heading"
    if body_font_size is not None and max_font_size is not None:
        if max_font_size >= body_font_size + 2.0 and len(stripped) <= 180 and len(stripped.splitlines()) <= 3:
            return "heading"
    if stripped.isupper() and 4 <= len(stripped) <= 90:
        return "heading"
    return "paragraph"


def _body_font_size(font_sizes: list[float]) -> float | None:
    rounded = [round(size, 1) for size in font_sizes if math.isfinite(size) and size > 0]
    if not rounded:
        return None
    return float(Counter(rounded).most_common(1)[0][0])


def _run_pymupdf4llm_current(pdf_path: Path, parser_name: str) -> dict[str, Any]:
    from app.services.pymupdf4llm_adapter import PyMuPDF4LLMDocumentParser

    artifact = PyMuPDF4LLMDocumentParser(parser_source="pymupdf4llm_enhanced+fitz/production").parse_document(
        document_id=f"bench_{pdf_path.stem}",
        pdf_path=pdf_path,
    )
    pages = []
    for page in artifact.pages:
        elements = []
        for block in page.blocks:
            elements.append(
                _element(
                    parser_name=parser_name,
                    page_number=page.page_number,
                    reading_order=int(block.reading_order),
                    element_type=str(block.block_type),
                    text=block.text,
                    bbox=_as_bbox(block.bbox),
                    confidence=0.72,
                    quality_notes=["production_current_parse_block"],
                )
            )
        pages.append(
            _page_map(
                parser_name=parser_name,
                page_number=page.page_number,
                width=page.width,
                height=page.height,
                ocr_used=page.ocr_used,
                elements=elements,
                parser_notes=[f"parser_source={artifact.parser_source}"],
            )
        )
    return _document_map(parser_name=parser_name, pdf_path=pdf_path, pages=pages)


def _extract_pymupdf4llm_page_chunks(pdf_path: Path) -> list[dict[str, Any]]:
    import pymupdf4llm

    raw = pymupdf4llm.to_markdown(
        str(pdf_path),
        page_chunks=True,
        hdr_info=False,
        write_images=False,
        embed_images=False,
        show_progress=False,
    )
    if not isinstance(raw, list):
        return []
    return [chunk for chunk in raw if isinstance(chunk, dict)]


def _extract_table_rects(page_chunk: dict[str, Any] | None) -> list[Any]:
    if not page_chunk:
        return []
    import fitz

    rects = []
    for table in page_chunk.get("tables", []):
        if not isinstance(table, dict):
            continue
        bbox = table.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        rect = fitz.Rect(bbox)
        if rect.width > 0 and rect.height > 0:
            rects.append(rect)
    return rects


def _fitz_rect_overlap_ratio(first: Any, second: Any) -> float:
    intersection = first & second
    if intersection.is_empty:
        return 0.0
    denominator = max(0.0001, min(abs(first), abs(second)))
    return abs(intersection) / denominator


def _run_pymupdf4llm_enhanced(pdf_path: Path, parser_name: str) -> dict[str, Any]:
    import fitz

    page_chunks = _extract_pymupdf4llm_page_chunks(pdf_path)
    pages = []
    with fitz.open(pdf_path) as document:
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            page_number = page_index + 1
            width = float(page.rect.width)
            height = float(page.rect.height)
            page_chunk = page_chunks[page_index] if page_index < len(page_chunks) else None
            text_dict = page.get_text("dict", sort=True)
            raw_text_blocks = []
            image_blocks = []
            all_font_sizes = []

            for raw_block in text_dict.get("blocks", []):
                if not isinstance(raw_block, dict):
                    continue
                block_type = int(raw_block.get("type", 0))
                rect = fitz.Rect(raw_block.get("bbox", page.rect))
                if block_type == 1:
                    image_blocks.append((rect, raw_block))
                    continue
                if block_type != 0:
                    continue

                lines = []
                font_sizes = []
                boldish_spans = 0
                span_count = 0
                for line in raw_block.get("lines", []):
                    if not isinstance(line, dict):
                        continue
                    spans = line.get("spans", [])
                    line_parts = []
                    for span in spans:
                        if not isinstance(span, dict):
                            continue
                        span_text = str(span.get("text") or "")
                        if span_text:
                            line_parts.append(span_text)
                        size = span.get("size")
                        if isinstance(size, (int, float)):
                            font_sizes.append(float(size))
                            all_font_sizes.append(float(size))
                        flags = int(span.get("flags", 0)) if isinstance(span.get("flags"), int) else 0
                        font_name = str(span.get("font") or "").lower()
                        if flags & 16 or "bold" in font_name:
                            boldish_spans += 1
                        span_count += 1
                    line_text = "".join(line_parts).strip()
                    if line_text:
                        lines.append(line_text)

                text = "\n".join(lines).strip()
                if not text:
                    continue
                raw_text_blocks.append(
                    {
                        "rect": rect,
                        "text": text,
                        "max_font_size": max(font_sizes) if font_sizes else None,
                        "bold_ratio": boldish_spans / span_count if span_count else 0.0,
                    }
                )

            body_font = _body_font_size(all_font_sizes)
            table_rects = _extract_table_rects(page_chunk)
            consumed_text_indexes = set()
            candidates = []

            for table_index, table_rect in enumerate(table_rects):
                overlapping = [
                    index
                    for index, block in enumerate(raw_text_blocks)
                    if _fitz_rect_overlap_ratio(block["rect"], table_rect) >= 0.28
                ]
                consumed_text_indexes.update(overlapping)
                table_text = "\n".join(raw_text_blocks[index]["text"] for index in overlapping).strip()
                candidates.append(
                    {
                        "rect": table_rect,
                        "text": table_text,
                        "element_type": "table",
                        "confidence": 0.82 if table_text else 0.58,
                        "quality_notes": [f"pymupdf4llm_table_hint:{table_index}"],
                    }
                )

            for image_index, (rect, _raw_block) in enumerate(image_blocks):
                candidates.append(
                    {
                        "rect": rect,
                        "text": "",
                        "element_type": "figure",
                        "confidence": 0.68,
                        "quality_notes": [f"fitz_image_block:{image_index}"],
                    }
                )

            previous_type: str | None = None
            for index, block in enumerate(raw_text_blocks):
                if index in consumed_text_indexes:
                    continue
                element_type = _infer_element_type_from_text(
                    text=block["text"],
                    max_font_size=block["max_font_size"],
                    body_font_size=body_font,
                    previous_type=previous_type,
                )
                confidence = 0.76
                notes = ["fitz_text_block"]
                if element_type == "heading" and block.get("bold_ratio", 0.0) >= 0.5:
                    confidence += 0.08
                    notes.append("bold_heading_signal")
                if element_type == "caption":
                    confidence += 0.06
                    notes.append("caption_text_signal")
                candidates.append(
                    {
                        "rect": block["rect"],
                        "text": block["text"],
                        "element_type": element_type,
                        "confidence": min(confidence, 0.92),
                        "quality_notes": notes,
                    }
                )
                previous_type = element_type

            candidates.sort(key=lambda candidate: (round(candidate["rect"].y0, 4), round(candidate["rect"].x0, 4)))
            elements = []
            for reading_order, candidate in enumerate(candidates):
                bbox = _normalize_fitz_rect(candidate["rect"], width, height)
                elements.append(
                    _element(
                        parser_name=parser_name,
                        page_number=page_number,
                        reading_order=reading_order,
                        element_type=str(candidate["element_type"]),
                        text=str(candidate["text"] or ""),
                        bbox=bbox,
                        confidence=float(candidate["confidence"]),
                        quality_notes=list(candidate["quality_notes"]),
                    )
                )

            for index, current in enumerate(elements[:-1]):
                next_element = elements[index + 1]
                if current["element_type"] in {"figure", "table"} and next_element["element_type"] == "caption":
                    current["relations"]["caption_element_id"] = next_element["element_id"]
                    next_element["relations"]["parent_element_id"] = current["element_id"]
                if current["element_type"] == "caption" and next_element["element_type"] in {"figure", "table"}:
                    current["relations"]["parent_element_id"] = next_element["element_id"]
                    next_element["relations"]["caption_element_id"] = current["element_id"]

            notes = []
            if not elements:
                notes.append("no_elements_detected")
            if page.get_images(full=True) and sum(1 for element in elements if element["text"].strip()) == 0:
                notes.append("scan_like_or_image_only_page_without_ocr")

            pages.append(
                _page_map(
                    parser_name=parser_name,
                    page_number=page_number,
                    width=width,
                    height=height,
                    ocr_used=False,
                    elements=elements,
                    parser_notes=notes,
                )
            )

    return _document_map(
        parser_name=parser_name,
        pdf_path=pdf_path,
        pages=pages,
        parser_notes=["best_effort_fitz_layout_plus_pymupdf4llm_table_hints"],
    )


def _docling_element_type(label: object, bucket_name: str) -> str:
    label_value = str(getattr(label, "value", label) or "").lower()
    if bucket_name == "tables" or "table" in label_value:
        return "table"
    if bucket_name == "pictures" or "picture" in label_value or "image" in label_value:
        return "figure"
    if "caption" in label_value:
        return "caption"
    if "formula" in label_value or "equation" in label_value:
        return "formula"
    if "list" in label_value:
        return "list"
    if "title" in label_value or "header" in label_value:
        return "heading"
    return "paragraph"


def _docling_bbox_to_normalized(prov: dict[str, Any], page_sizes: dict[int, tuple[float, float]]) -> tuple[int, list[float] | None]:
    page_number = int(prov.get("page_no") or 1)
    bbox = prov.get("bbox")
    page_width, page_height = page_sizes.get(page_number, (0.0, 0.0))
    if not isinstance(bbox, dict) or page_width <= 0 or page_height <= 0:
        return page_number, None

    try:
        left = float(bbox["l"])
        right = float(bbox["r"])
        top = float(bbox["t"])
        bottom = float(bbox["b"])
    except (KeyError, TypeError, ValueError):
        return page_number, None

    origin = str(bbox.get("coord_origin") or "").lower()
    x0 = min(left, right) / page_width
    x1 = max(left, right) / page_width
    if "bottomleft" in origin:
        y0 = (page_height - max(top, bottom)) / page_height
        y1 = (page_height - min(top, bottom)) / page_height
    else:
        y0 = min(top, bottom) / page_height
        y1 = max(top, bottom) / page_height
    return page_number, _as_bbox([x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0)])


def _docling_item_text(item: Any, bucket_name: str) -> str:
    direct_text = str(getattr(item, "text", "") or "").strip()
    if direct_text:
        return direct_text
    if bucket_name == "tables":
        for method_name in ("export_to_markdown", "export_to_html"):
            method = getattr(item, method_name, None)
            if callable(method):
                try:
                    text = str(method() or "").strip()
                except Exception:
                    continue
                if text:
                    return _compact_text(text, max_chars=1200)
    return ""


def _run_docling(pdf_path: Path, parser_name: str) -> dict[str, Any]:
    import fitz
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

    pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        generate_parsed_pages=True,
    )
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=StandardPdfPipeline,
                pipeline_options=pipeline_options,
            )
        }
    )
    converted = converter.convert(str(pdf_path))

    page_sizes: dict[int, tuple[float, float]] = {}
    with fitz.open(pdf_path) as document:
        for page_index in range(document.page_count):
            page_sizes[page_index + 1] = (float(document[page_index].rect.width), float(document[page_index].rect.height))

    elements_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    raw_size = 0
    doc = converted.document
    for bucket_name in ("texts", "tables", "pictures"):
        for item in getattr(doc, bucket_name, []) or []:
            try:
                payload = item.model_dump(mode="json", exclude_none=True)
            except Exception:
                payload = {}
            raw_size += len(json.dumps(payload, ensure_ascii=False, default=str))
            provs = payload.get("prov") if isinstance(payload, dict) else None
            prov = provs[0] if isinstance(provs, list) and provs and isinstance(provs[0], dict) else {}
            page_number, bbox = _docling_bbox_to_normalized(prov, page_sizes)
            element_type = _docling_element_type(getattr(item, "label", None), bucket_name)
            quality_notes = ["docling_native_item"]
            if bbox is None:
                quality_notes.append("docling_item_without_bbox")
            if getattr(converted, "status", None) is not None:
                quality_notes.append(f"conversion_status={getattr(converted.status, 'value', converted.status)}")
            relations: dict[str, Any] = {}
            captions = payload.get("captions") if isinstance(payload, dict) else None
            if isinstance(captions, list) and captions:
                relations["caption_refs"] = captions
            elements_by_page[page_number].append(
                _element(
                    parser_name=parser_name,
                    page_number=page_number,
                    reading_order=len(elements_by_page[page_number]),
                    element_type=element_type,
                    text=_docling_item_text(item, bucket_name),
                    bbox=bbox,
                    confidence=0.72 if bbox is not None else 0.45,
                    quality_notes=quality_notes,
                    relations=relations,
                )
            )

    pages = []
    for page_number in range(1, len(page_sizes) + 1):
        width, height = page_sizes[page_number]
        elements = elements_by_page.get(page_number, [])
        elements.sort(key=lambda element: (element["bbox"][1], element["bbox"][0]) if element.get("bbox") else (1.0, 1.0))
        for reading_order, element in enumerate(elements):
            element["reading_order"] = reading_order
            element["element_id"] = f"{parser_name}:p{page_number}:e{reading_order}"
        pages.append(
            _page_map(
                parser_name=parser_name,
                page_number=page_number,
                width=width,
                height=height,
                elements=elements,
                parser_notes=["docling_native_bbox_adapter", "docling_ocr_disabled_for_isolated_benchmark"],
            )
        )
    return _document_map(
        parser_name=parser_name,
        pdf_path=pdf_path,
        pages=pages,
        parser_notes=["docling_native_text_table_picture_items", "ocr_disabled_to_measure_layout_parser_without_scan_fallback"],
        raw_output_size_chars=raw_size,
    )


def _run_marker(pdf_path: Path, parser_name: str) -> dict[str, Any]:
    import fitz
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    converter = PdfConverter(artifact_dict=create_model_dict())
    rendered = converter(str(pdf_path))
    text, _, _images = text_from_rendered(rendered)
    markdown = str(text or "")
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    with fitz.open(pdf_path) as document:
        page_count = document.page_count
    pages = []
    for page_number in range(1, page_count + 1):
        elements = []
        for reading_order, line in enumerate(lines if page_count == 1 else []):
            elements.append(
                _element(
                    parser_name=parser_name,
                    page_number=page_number,
                    reading_order=reading_order,
                    element_type=_infer_element_type_from_text(text=line),
                    text=line,
                    bbox=None,
                    confidence=0.45,
                    quality_notes=["marker_text_output_no_bbox_in_this_adapter"],
                )
            )
        pages.append(_page_map(parser_name=parser_name, page_number=page_number, width=None, height=None, elements=elements))
    return _document_map(
        parser_name=parser_name,
        pdf_path=pdf_path,
        pages=pages,
        parser_notes=["marker_adapter_uses_text_from_rendered_only"],
        raw_output_size_chars=len(markdown),
    )


def _run_mineru(pdf_path: Path, parser_name: str) -> dict[str, Any]:
    cli = shutil.which("mineru")
    if not cli:
        raise RuntimeError("MinerU package/CLI was detected inconsistently, but no mineru CLI is available.")
    raise RuntimeError(
        "MinerU adapter is intentionally not wired in this repo yet. "
        "Use isolated installation and add a JSON-output adapter before scoring it."
    )


def _run_markitdown(pdf_path: Path, parser_name: str) -> dict[str, Any]:
    import fitz
    from markitdown import MarkItDown

    result = MarkItDown().convert(str(pdf_path))
    text = str(getattr(result, "text_content", "") or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    with fitz.open(pdf_path) as document:
        page_count = document.page_count
    pages = []
    for page_number in range(1, page_count + 1):
        elements = []
        if page_count == 1:
            page_lines = lines
        else:
            page_lines = [
                line
                for index, line in enumerate(lines)
                if min(page_count, int(index / max(1, len(lines) / page_count)) + 1) == page_number
            ]
        for reading_order, line in enumerate(page_lines):
            elements.append(
                _element(
                    parser_name=parser_name,
                    page_number=page_number,
                    reading_order=reading_order,
                    element_type=_infer_element_type_from_text(text=line),
                    text=line,
                    bbox=None,
                    confidence=0.38,
                    quality_notes=["markitdown_text_only_no_bbox"],
                )
            )
        pages.append(_page_map(parser_name=parser_name, page_number=page_number, width=None, height=None, elements=elements))
    return _document_map(
        parser_name=parser_name,
        pdf_path=pdf_path,
        pages=pages,
        parser_notes=["markitdown_adapter_is_text_extraction_baseline"],
        raw_output_size_chars=len(text),
    )


def _available_candidates() -> dict[str, ParserCandidate]:
    return {
        "pymupdf4llm_current": ParserCandidate(
            name="pymupdf4llm_current",
            display_name="Current PyMuPDF4LLM + fitz adapter",
            required_modules=("fitz", "pymupdf4llm"),
            runner=_run_pymupdf4llm_current,
            install_hint="Already part of the backend environment. If missing: pip install PyMuPDF pymupdf4llm",
            integration_complexity_score=5.0,
            notes="Production parser adapter. Since 2026-05-07 this is the enhanced PyMuPDF4LLM + fitz path.",
        ),
        "pymupdf4llm_enhanced": ParserCandidate(
            name="pymupdf4llm_enhanced",
            display_name="Enhanced PyMuPDF4LLM / fitz benchmark adapter",
            required_modules=("fitz", "pymupdf4llm"),
            runner=_run_pymupdf4llm_enhanced,
            install_hint="Already part of the backend environment. If missing: pip install PyMuPDF pymupdf4llm",
            integration_complexity_score=4.6,
            notes="Best-effort benchmark-only adapter with richer element classification and relations.",
        ),
        "docling": ParserCandidate(
            name="docling",
            display_name="Docling",
            required_modules=("docling",),
            runner=_run_docling,
            install_hint=INSTALL_HINTS["docling"],
            integration_complexity_score=3.0,
            notes="Optional heavy parser; this adapter uses Docling native text/table/picture items with bbox where available.",
        ),
        "marker": ParserCandidate(
            name="marker",
            display_name="Marker",
            required_modules=("marker",),
            runner=_run_marker,
            install_hint=INSTALL_HINTS["marker"],
            integration_complexity_score=2.8,
            notes="Optional heavy parser; may require model downloads and GPU/CPU time.",
        ),
        "mineru": ParserCandidate(
            name="mineru",
            display_name="MinerU",
            required_modules=("mineru",),
            runner=_run_mineru,
            install_hint=INSTALL_HINTS["mineru"],
            integration_complexity_score=2.5,
            notes="Optional heavy parser; adapter requires a stable JSON output contract before production use.",
        ),
        "markitdown": ParserCandidate(
            name="markitdown",
            display_name="MarkItDown",
            required_modules=("markitdown",),
            runner=_run_markitdown,
            install_hint=INSTALL_HINTS["markitdown"],
            integration_complexity_score=3.8,
            notes="Lightweight text conversion baseline; likely weak for bbox-grounded selected-region use.",
        ),
    }


def _load_goldset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore

            payload = yaml.safe_load(raw)
        except Exception as exc:
            raise SystemExit(f"Could not parse goldset {path}: {exc}") from exc
    if isinstance(payload, dict):
        selections = payload.get("selections", [])
    else:
        selections = payload
    if not isinstance(selections, list):
        raise SystemExit(f"Goldset must contain a list of selections: {path}")
    return [selection for selection in selections if isinstance(selection, dict)]


def _coerce_selection(selection: dict[str, Any], *, source: str) -> SelectionRegion | None:
    filename = str(
        selection.get("pdf_filename")
        or selection.get("filename")
        or selection.get("pdf")
        or selection.get("pdf_id")
        or ""
    ).strip()
    page_number = selection.get("page_number")
    bbox = _as_bbox(selection.get("selected_bbox") or selection.get("bbox"))
    if not filename or not isinstance(page_number, int) or page_number < 1 or bbox is None:
        return None
    selection_type = str(selection.get("selection_type") or "other").strip() or "other"
    return SelectionRegion(
        selection_id=str(selection.get("selection_id") or f"{filename}:p{page_number}:{bbox}"),
        pdf_filename=filename,
        page_number=page_number,
        selected_bbox=bbox,
        selection_type=selection_type,
        expected_concept_label=(
            str(selection.get("expected_concept_label")).strip()
            if selection.get("expected_concept_label") is not None
            else None
        ),
        expected_source_type=(
            str(selection.get("expected_source_type")).strip()
            if selection.get("expected_source_type") is not None
            else None
        ),
        notes=str(selection.get("notes")).strip() if selection.get("notes") is not None else None,
        source=source,
    )


def _matching_gold_selections(pdf_path: Path, raw_goldset: list[dict[str, Any]]) -> list[SelectionRegion]:
    names = {pdf_path.name, pdf_path.stem, pdf_path.as_posix()}
    selections = []
    for raw_selection in raw_goldset:
        filename = str(
            raw_selection.get("pdf_filename")
            or raw_selection.get("filename")
            or raw_selection.get("pdf")
            or raw_selection.get("pdf_id")
            or ""
        ).strip()
        if filename not in names:
            continue
        selection = _coerce_selection(raw_selection, source="goldset")
        if selection is not None:
            selections.append(selection)
    return selections


def _all_elements(page_element_map: dict[str, Any]) -> list[dict[str, Any]]:
    elements = []
    for page in page_element_map.get("pages", []):
        if not isinstance(page, dict):
            continue
        for element in page.get("elements", []):
            if isinstance(element, dict):
                elements.append(element)
    return elements


def _auto_proxy_selections(
    *,
    pdf_path: Path,
    page_element_map: dict[str, Any],
    limit: int,
) -> list[SelectionRegion]:
    candidates = []
    preferred_types = ["table", "figure", "caption", "formula", "heading", "paragraph", "list"]
    elements = [
        element
        for element in _all_elements(page_element_map)
        if _as_bbox(element.get("bbox")) is not None
    ]
    for element_type in preferred_types:
        for element in elements:
            text = str(element.get("text") or "").strip()
            bbox = _as_bbox(element.get("bbox"))
            if bbox is None:
                continue
            if element.get("element_type") != element_type:
                continue
            if element_type not in {"figure", "table"} and not text:
                continue
            candidates.append(element)
            break
    if not candidates:
        candidates = elements[:limit]

    selections = []
    seen = set()
    for index, element in enumerate(candidates):
        bbox = _as_bbox(element.get("bbox"))
        if bbox is None:
            continue
        key = (int(element.get("page_number", 0)), tuple(round(value, 3) for value in bbox))
        if key in seen:
            continue
        seen.add(key)
        selections.append(
            SelectionRegion(
                selection_id=f"proxy:{pdf_path.name}:p{element.get('page_number')}:e{index}",
                pdf_filename=pdf_path.name,
                page_number=int(element.get("page_number", 1)),
                selected_bbox=bbox,
                selection_type=str(element.get("element_type") or "other"),
                expected_concept_label=_compact_text(element.get("text"), max_chars=80) or None,
                expected_source_type=None,
                notes="Auto-generated from parser output; not a human-labeled accuracy target.",
                source="proxy",
            )
        )
        if len(selections) >= limit:
            break

    if not selections:
        selections.append(
            SelectionRegion(
                selection_id=f"proxy:{pdf_path.name}:center",
                pdf_filename=pdf_path.name,
                page_number=1,
                selected_bbox=[0.2, 0.2, 0.45, 0.18],
                selection_type="other",
                expected_concept_label=None,
                expected_source_type=None,
                notes="Generic center-page proxy because no parser element bbox was available.",
                source="proxy",
            )
        )
    return selections


def _element_type_counts(elements: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(element.get("element_type") or "other") for element in elements).items()))


def _reading_order_continuity(page_element_map: dict[str, Any]) -> float:
    page_scores = []
    for page in page_element_map.get("pages", []):
        if not isinstance(page, dict):
            continue
        orders = [
            int(element.get("reading_order", -1))
            for element in page.get("elements", [])
            if isinstance(element, dict) and isinstance(element.get("reading_order"), int)
        ]
        if len(orders) <= 1:
            page_scores.append(1.0 if orders else 0.0)
            continue
        sorted_orders = sorted(orders)
        unique_ratio = len(set(sorted_orders)) / len(sorted_orders)
        continuity = sum(
            1 for first, second in zip(sorted_orders, sorted_orders[1:]) if second - first == 1
        ) / max(1, len(sorted_orders) - 1)
        page_scores.append((unique_ratio + continuity) / 2)
    return mean(page_scores) if page_scores else 0.0


def _bbox_coverage_ratio(page_element_map: dict[str, Any]) -> float:
    page_coverages = []
    for page in page_element_map.get("pages", []):
        if not isinstance(page, dict):
            continue
        area = 0.0
        for element in page.get("elements", []):
            if not isinstance(element, dict):
                continue
            bbox = _as_bbox(element.get("bbox"))
            if bbox is not None:
                area += _bbox_area(bbox)
        page_coverages.append(min(1.0, area))
    return mean(page_coverages) if page_coverages else 0.0


def _scan_like_page_count(page_element_map: dict[str, Any]) -> int:
    count = 0
    for page in page_element_map.get("pages", []):
        if not isinstance(page, dict):
            continue
        elements = [element for element in page.get("elements", []) if isinstance(element, dict)]
        text_chars = sum(len(str(element.get("text") or "").strip()) for element in elements)
        figure_count = sum(1 for element in elements if element.get("element_type") == "figure")
        if text_chars < 80 and (figure_count > 0 or not elements):
            count += 1
    return count


def _structural_metrics(page_element_map: dict[str, Any], *, duration_seconds: float | None, peak_mb: float | None) -> dict[str, Any]:
    elements = _all_elements(page_element_map)
    bbox_count = sum(1 for element in elements if _as_bbox(element.get("bbox")) is not None)
    non_empty_text_count = sum(1 for element in elements if str(element.get("text") or "").strip())
    text_chars = sum(len(str(element.get("text") or "")) for element in elements)
    page_count = int(page_element_map.get("page_count") or len(page_element_map.get("pages", [])))
    type_counts = _element_type_counts(elements)
    seconds_per_page = duration_seconds / page_count if duration_seconds is not None and page_count else None
    return {
        "page_count": page_count,
        "total_parse_time_seconds": _round(duration_seconds),
        "seconds_per_page": _round(seconds_per_page),
        "peak_tracemalloc_mb": _round(peak_mb),
        "output_size_chars": len(json.dumps(page_element_map, ensure_ascii=False, separators=(",", ":"))),
        "block_count": len(elements),
        "non_empty_text_block_count": non_empty_text_count,
        "text_char_count": text_chars,
        "element_type_counts": type_counts,
        "bbox_coverage_ratio": _round(_bbox_coverage_ratio(page_element_map)),
        "percentage_of_elements_with_bbox": _round(bbox_count / len(elements) if elements else 0.0),
        "bbox_available_count": bbox_count,
        "reading_order_continuity_proxy": _round(_reading_order_continuity(page_element_map)),
        "table_count": type_counts.get("table", 0),
        "figure_count": type_counts.get("figure", 0),
        "caption_count": type_counts.get("caption", 0),
        "formula_count": type_counts.get("formula", 0),
        "ocr_used_count": sum(1 for page in page_element_map.get("pages", []) if isinstance(page, dict) and page.get("ocr_used")),
        "ocr_available": False,
        "scan_like_page_count": _scan_like_page_count(page_element_map),
        "parse_error_count": 0,
    }


def _selection_usefulness_metrics(
    page_element_map: dict[str, Any],
    selections: list[SelectionRegion],
) -> list[dict[str, Any]]:
    rows = []
    pages_by_number = {
        int(page.get("page_number")): page
        for page in page_element_map.get("pages", [])
        if isinstance(page, dict) and isinstance(page.get("page_number"), int)
    }
    for selection in selections:
        page = pages_by_number.get(selection.page_number)
        elements = page.get("elements", []) if isinstance(page, dict) else []
        scored = []
        nearby_text = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            bbox = _as_bbox(element.get("bbox"))
            text = str(element.get("text") or "").strip()
            if bbox is None:
                if text:
                    nearby_text.append(element)
                continue
            overlap = _overlap_ratio(selection.selected_bbox, bbox)
            distance = _center_distance(selection.selected_bbox, bbox)
            score = overlap - distance * 0.15
            scored.append((score, overlap, distance, element))
            if text and distance <= 0.42:
                nearby_text.append(element)

        scored.sort(key=lambda item: item[0], reverse=True)
        matched = [item for item in scored if item[1] > 0 or item[2] <= 0.24]
        best = scored[0] if scored else None
        source_candidates = []
        for _score, overlap, distance, element in matched[:5]:
            snippet = _compact_text(element.get("text"), max_chars=180)
            if snippet or element.get("element_type") in {"figure", "table"}:
                source_candidates.append(
                    {
                        "source_type": element.get("element_type") or "other",
                        "element_id": element.get("element_id"),
                        "snippet": snippet,
                        "bbox": element.get("bbox"),
                        "overlap": _round(overlap),
                        "distance": _round(distance),
                    }
                )
        nearby_text_available = any(str(element.get("text") or "").strip() for element in nearby_text[:5])
        best_overlap = float(best[1]) if best else 0.0
        best_type = str(best[3].get("element_type") or "other") if best else None
        bbox_matching_possible = any(_as_bbox(element.get("bbox")) is not None for element in elements if isinstance(element, dict))
        enough_context = bool(
            bbox_matching_possible
            and (best_overlap >= 0.05 or (best is not None and float(best[2]) <= 0.20))
            and (nearby_text_available or best_type in {"figure", "table"})
        )
        rows.append(
            {
                "selection_id": selection.selection_id,
                "selection_source": selection.source,
                "page_number": selection.page_number,
                "selected_bbox": selection.selected_bbox,
                "selection_type": selection.selection_type,
                "expected_concept_label": selection.expected_concept_label,
                "expected_source_type": selection.expected_source_type,
                "matched_element_count": len(matched),
                "best_matched_element_type": best_type,
                "best_overlap_score": _round(best_overlap),
                "best_center_distance": _round(float(best[2]) if best else None),
                "nearby_text_count": len(nearby_text[:5]),
                "nearby_text_available": nearby_text_available,
                "source_candidate_count": len(source_candidates),
                "source_candidates": source_candidates[:5],
                "related_context_candidate_count": sum(
                    1
                    for element in elements
                    if isinstance(element, dict) and element.get("element_type") in {"heading", "caption"}
                ),
                "bbox_matching_possible": bbox_matching_possible,
                "enough_context_for_selected_region_explanation": enough_context,
            }
        )
    return rows


def _summarize_selection_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "selection_count": 0,
            "bbox_matching_possible_ratio": None,
            "enough_context_ratio": None,
            "mean_best_overlap_score": None,
            "mean_matched_element_count": None,
            "mean_source_candidate_count": None,
        }
    return {
        "selection_count": len(rows),
        "bbox_matching_possible_ratio": _round(
            sum(1 for row in rows if row["bbox_matching_possible"]) / len(rows)
        ),
        "enough_context_ratio": _round(
            sum(1 for row in rows if row["enough_context_for_selected_region_explanation"]) / len(rows)
        ),
        "mean_best_overlap_score": _round(mean(float(row.get("best_overlap_score") or 0.0) for row in rows)),
        "mean_matched_element_count": _round(mean(float(row.get("matched_element_count") or 0.0) for row in rows)),
        "mean_source_candidate_count": _round(mean(float(row.get("source_candidate_count") or 0.0) for row in rows)),
    }


def _benchmark_parser_on_pdf(
    *,
    candidate: ParserCandidate,
    availability: dict[str, Any],
    pdf_path: Path,
    source_root: Path,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source_pdf": pdf_path.as_posix(),
        "source_pdf_relpath": _safe_relpath(pdf_path, source_root),
        "filename": pdf_path.name,
        "parser_name": candidate.name,
        "parser_display_name": candidate.display_name,
        "status": PARSER_STATUS_SKIPPED if not availability["available"] else PARSER_STATUS_COMPLETED,
        "skip_reason": None if availability["available"] else "Required parser module is not installed.",
        "install_hint": None if availability["available"] else candidate.install_hint,
        "error_message": None,
        "normalized_page_element_map": None,
        "structural_metrics": None,
        "selection_metrics": [],
        "selection_summary": None,
    }
    if not availability["available"]:
        return row

    started_at = time.perf_counter()
    tracemalloc.start()
    try:
        page_element_map = candidate.runner(pdf_path, candidate.name)
        _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        duration = time.perf_counter() - started_at
        row["normalized_page_element_map"] = page_element_map
        row["structural_metrics"] = _structural_metrics(
            page_element_map,
            duration_seconds=duration,
            peak_mb=peak_bytes / 1024 / 1024,
        )
    except Exception as exc:
        row["status"] = PARSER_STATUS_FAILED
        row["error_message"] = str(exc)
        duration = time.perf_counter() - started_at
        row["structural_metrics"] = {
            "total_parse_time_seconds": _round(duration),
            "parse_error_count": 1,
        }
    finally:
        tracemalloc.stop()
    return row


def _choose_selection_source_map(rows_for_pdf: list[dict[str, Any]]) -> dict[str, Any] | None:
    preferred = ["pymupdf4llm_enhanced", "pymupdf4llm_current"]
    for parser_name in preferred:
        for row in rows_for_pdf:
            if row["parser_name"] == parser_name and row["status"] == PARSER_STATUS_COMPLETED:
                return row.get("normalized_page_element_map")
    for row in rows_for_pdf:
        if row["status"] == PARSER_STATUS_COMPLETED:
            return row.get("normalized_page_element_map")
    return None


def _attach_selection_metrics(
    *,
    rows: list[dict[str, Any]],
    pdf_paths: list[Path],
    raw_goldset: list[dict[str, Any]],
    proxy_limit: int,
) -> dict[str, list[dict[str, Any]]]:
    selections_by_pdf: dict[str, list[SelectionRegion]] = {}
    for pdf_path in pdf_paths:
        rows_for_pdf = [row for row in rows if row["filename"] == pdf_path.name]
        gold = _matching_gold_selections(pdf_path, raw_goldset)
        if gold:
            selections = gold
        else:
            source_map = _choose_selection_source_map(rows_for_pdf)
            selections = (
                _auto_proxy_selections(pdf_path=pdf_path, page_element_map=source_map, limit=proxy_limit)
                if source_map
                else []
            )
        selections_by_pdf[pdf_path.name] = [
            {
                "selection_id": selection.selection_id,
                "pdf_filename": selection.pdf_filename,
                "page_number": selection.page_number,
                "selected_bbox": selection.selected_bbox,
                "selection_type": selection.selection_type,
                "expected_concept_label": selection.expected_concept_label,
                "expected_source_type": selection.expected_source_type,
                "notes": selection.notes,
                "source": selection.source,
            }
            for selection in selections
        ]
        for row in rows_for_pdf:
            page_element_map = row.get("normalized_page_element_map")
            if row["status"] != PARSER_STATUS_COMPLETED or not isinstance(page_element_map, dict):
                row["selection_metrics"] = []
                row["selection_summary"] = _summarize_selection_rows([])
                continue
            selection_regions = [
                selection
                for selection in (
                    _coerce_selection(selection_payload, source=str(selection_payload.get("source") or "proxy"))
                    for selection_payload in selections_by_pdf[pdf_path.name]
                )
                if selection is not None
            ]
            selection_rows = _selection_usefulness_metrics(page_element_map, selection_regions)
            row["selection_metrics"] = selection_rows
            row["selection_summary"] = _summarize_selection_rows(selection_rows)
    return selections_by_pdf


def _summarize_numeric(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "min": None, "max": None}
    return {
        "mean": _round(mean(values)),
        "median": _round(median(values)),
        "min": _round(min(values)),
        "max": _round(max(values)),
    }


def _metric_values(rows: list[dict[str, Any]], field_name: str) -> list[float]:
    values = []
    for row in rows:
        metrics = row.get("structural_metrics")
        if isinstance(metrics, dict) and isinstance(metrics.get(field_name), (int, float)):
            values.append(float(metrics[field_name]))
    return values


def _selection_values(rows: list[dict[str, Any]], field_name: str) -> list[float]:
    values = []
    for row in rows:
        summary = row.get("selection_summary")
        if isinstance(summary, dict) and isinstance(summary.get(field_name), (int, float)):
            values.append(float(summary[field_name]))
    return values


def _score_parser(
    *,
    parser_rows: list[dict[str, Any]],
    fastest_seconds_per_page: float | None,
    candidate: ParserCandidate,
) -> dict[str, Any]:
    completed_rows = [row for row in parser_rows if row["status"] == PARSER_STATUS_COMPLETED]
    if not completed_rows:
        status_counts = Counter(str(row["status"]) for row in parser_rows)
        return {
            "score": None,
            "status": "not_scored",
            "status_counts": dict(sorted(status_counts.items())),
            "reason": "Parser did not complete on any benchmark PDF.",
            "component_scores": {},
        }

    bbox_possible = _selection_values(completed_rows, "bbox_matching_possible_ratio")
    enough_context = _selection_values(completed_rows, "enough_context_ratio")
    best_overlap = _selection_values(completed_rows, "mean_best_overlap_score")
    reading_order = _metric_values(completed_rows, "reading_order_continuity_proxy")
    bbox_ratio = _metric_values(completed_rows, "percentage_of_elements_with_bbox")
    source_candidates = _selection_values(completed_rows, "mean_source_candidate_count")
    related_candidates = [
        float(row.get("related_context_candidate_count") or 0.0)
        for parser_row in completed_rows
        for row in parser_row.get("selection_metrics", [])
        if isinstance(row, dict)
    ]
    seconds_per_page = _metric_values(completed_rows, "seconds_per_page")
    ocr_used = _metric_values(completed_rows, "ocr_used_count")
    scan_like = _metric_values(completed_rows, "scan_like_page_count")

    type_counts = Counter()
    for row in completed_rows:
        metrics = row.get("structural_metrics") or {}
        for key, value in (metrics.get("element_type_counts") or {}).items():
            type_counts[str(key)] += int(value)
    layout_variety = len([key for key in type_counts if key not in {"paragraph", "other"}])
    layout_detection_ratio = min(1.0, (layout_variety / 5) + (sum(type_counts.get(t, 0) for t in ["table", "figure", "caption", "formula"]) / 40))

    bbox_quality_ratio = min(
        1.0,
        0.45 * (mean(enough_context) if enough_context else 0.0)
        + 0.25 * (mean(bbox_possible) if bbox_possible else 0.0)
        + 0.20 * min(1.0, (mean(best_overlap) if best_overlap else 0.0))
        + 0.10 * (mean(bbox_ratio) if bbox_ratio else 0.0),
    )
    source_ratio = min(1.0, (mean(source_candidates) / 3) if source_candidates else 0.0)
    related_ratio = min(1.0, (mean(related_candidates) / 5) if related_candidates else 0.0)
    reading_ratio = mean(reading_order) if reading_order else 0.0
    if ocr_used and mean(ocr_used) > 0:
        ocr_ratio = 0.8
    elif scan_like and mean(scan_like) > 0:
        ocr_ratio = 0.2
    else:
        ocr_ratio = 0.5
    if fastest_seconds_per_page and seconds_per_page:
        speed_ratio = min(1.0, fastest_seconds_per_page / max(0.0001, mean(seconds_per_page)))
    else:
        speed_ratio = 0.0
    complexity_ratio = max(0.0, min(1.0, candidate.integration_complexity_score / 5))

    component_ratios = {
        "selected_bbox_matching_quality": bbox_quality_ratio,
        "reading_order": reading_ratio,
        "layout_element_detection": layout_detection_ratio,
        "source_cue_usefulness": source_ratio,
        "related_context_usefulness": related_ratio,
        "ocr_scanned_robustness": ocr_ratio,
        "speed": speed_ratio,
        "integration_complexity": complexity_ratio,
    }
    component_scores = {
        key: _round(component_ratios[key] * weight)
        for key, weight in RUBRIC_WEIGHTS.items()
    }
    total_score = _round(sum(float(value or 0.0) for value in component_scores.values()))
    return {
        "score": total_score,
        "status": "scored",
        "component_scores": component_scores,
        "component_ratios": {key: _round(value) for key, value in component_ratios.items()},
        "rubric_weights": RUBRIC_WEIGHTS,
    }


def _build_summary(
    *,
    rows: list[dict[str, Any]],
    candidates: dict[str, ParserCandidate],
) -> dict[str, Any]:
    by_parser = {}
    all_seconds_per_page = _metric_values(
        [row for row in rows if row["status"] == PARSER_STATUS_COMPLETED],
        "seconds_per_page",
    )
    fastest_seconds_per_page = min(all_seconds_per_page) if all_seconds_per_page else None
    for parser_name in sorted({str(row["parser_name"]) for row in rows}):
        parser_rows = [row for row in rows if row["parser_name"] == parser_name]
        completed_rows = [row for row in parser_rows if row["status"] == PARSER_STATUS_COMPLETED]
        status_counts = Counter(str(row["status"]) for row in parser_rows)
        by_parser[parser_name] = {
            "status_counts": dict(sorted(status_counts.items())),
            "completed_count": len(completed_rows),
            "metrics": {
                "total_parse_time_seconds": _summarize_numeric(_metric_values(completed_rows, "total_parse_time_seconds")),
                "seconds_per_page": _summarize_numeric(_metric_values(completed_rows, "seconds_per_page")),
                "page_count": _summarize_numeric(_metric_values(completed_rows, "page_count")),
                "block_count": _summarize_numeric(_metric_values(completed_rows, "block_count")),
                "non_empty_text_block_count": _summarize_numeric(
                    _metric_values(completed_rows, "non_empty_text_block_count")
                ),
                "percentage_of_elements_with_bbox": _summarize_numeric(
                    _metric_values(completed_rows, "percentage_of_elements_with_bbox")
                ),
                "reading_order_continuity_proxy": _summarize_numeric(
                    _metric_values(completed_rows, "reading_order_continuity_proxy")
                ),
                "bbox_coverage_ratio": _summarize_numeric(_metric_values(completed_rows, "bbox_coverage_ratio")),
                "selection_enough_context_ratio": _summarize_numeric(
                    _selection_values(completed_rows, "enough_context_ratio")
                ),
                "selection_mean_best_overlap_score": _summarize_numeric(
                    _selection_values(completed_rows, "mean_best_overlap_score")
                ),
            },
            "score": _score_parser(
                parser_rows=parser_rows,
                fastest_seconds_per_page=fastest_seconds_per_page,
                candidate=candidates[parser_name],
            ),
        }
    ranking = sorted(
        [
            {
                "parser_name": parser_name,
                "score": details["score"]["score"],
                "status": details["score"]["status"],
            }
            for parser_name, details in by_parser.items()
        ],
        key=lambda item: -1 if item["score"] is None else float(item["score"]),
        reverse=True,
    )
    return {
        "result_count": len(rows),
        "status_counts": dict(sorted(Counter(str(row["status"]) for row in rows).items())),
        "fastest_seconds_per_page": _round(fastest_seconds_per_page),
        "by_parser": by_parser,
        "ranking": ranking,
    }


def _artifact_safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")[:120] or "artifact"


def _write_artifacts(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    artifact_dir = run_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        page_element_map = row.get("normalized_page_element_map")
        if not isinstance(page_element_map, dict):
            continue
        source_name = str(row["filename"])
        source_stem = _artifact_safe_name(Path(source_name).stem)[:72]
        source_hash = hashlib.sha1(source_name.encode("utf-8")).hexdigest()[:8]
        parser_name = _artifact_safe_name(str(row["parser_name"]))
        filename = f"{source_stem}_{source_hash}__{parser_name}.json"
        path = artifact_dir / filename
        path.write_text(json.dumps(page_element_map, ensure_ascii=False, indent=2), encoding="utf-8")
        row["artifact_path"] = _safe_relpath(path, PROJECT_ROOT)
        row["normalized_page_element_map"] = {
            "omitted_from_results": True,
            "artifact_path": row["artifact_path"],
            "schema_version": NORMALIZED_SCHEMA_VERSION,
        }


def _format_markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _write_markdown_summary(
    *,
    run_dir: Path,
    payload: dict[str, Any],
) -> None:
    summary = payload["summary"]
    rows = []
    for item in summary["ranking"]:
        parser_name = item["parser_name"]
        parser_summary = summary["by_parser"][parser_name]
        metrics = parser_summary["metrics"]
        rows.append(
            [
                parser_name,
                item["status"],
                item["score"] if item["score"] is not None else "-",
                parser_summary["status_counts"],
                metrics["seconds_per_page"]["mean"],
                metrics["block_count"]["mean"],
                metrics["percentage_of_elements_with_bbox"]["mean"],
                metrics["selection_enough_context_ratio"]["mean"],
            ]
        )

    content = [
        "# Scholium Parser Benchmark Summary",
        "",
        f"- Runner: `{payload['runner_version']}`",
        f"- Generated: `{payload['generated_at']}`",
        f"- Branch: `{payload.get('git_branch')}`",
        f"- Goldset path: `{payload['run_config']['goldset_path']}`",
        f"- Selection mode: `{payload['run_config']['selection_mode']}`",
        "",
        "## Ranking",
        "",
        _format_markdown_table(
            [
                "parser",
                "status",
                "score",
                "runs",
                "sec/page mean",
                "blocks mean",
                "bbox ratio mean",
                "selection context ratio",
            ],
            rows,
        ),
        "",
        "## Rubric",
        "",
        _format_markdown_table(
            ["dimension", "weight"],
            [[key, value] for key, value in RUBRIC_WEIGHTS.items()],
        ),
        "",
        "## Important Notes",
        "",
        "- Proxy selections are not human accuracy labels. They only test whether parser output can support bbox matching and local context retrieval.",
        "- Text-only adapters can score on speed/text extraction, but cannot be considered production-ready for arbitrary selected-region explanation without bbox recovery.",
        "- This run does not switch `DOCUMENT_PARSER_BACKEND`.",
        "",
        "## Install Hints For Skipped Parsers",
        "",
    ]
    for parser_name, availability in payload["run_config"]["parser_availability"].items():
        if availability["available"]:
            continue
        content.append(f"- `{parser_name}`: `{availability.get('install_hint')}`")
    content.append("")
    (run_dir / "parser_benchmark_summary.md").write_text("\n".join(content), encoding="utf-8")


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved parser benchmark to {path}", flush=True)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    input_mode, pdf_paths, source_root = _resolve_input_pdfs(args)
    run_dir, output_path = _resolve_run_paths(args)

    candidates = _available_candidates()
    unknown_parsers = sorted(set(args.parsers) - set(candidates))
    if unknown_parsers:
        raise SystemExit(f"Unknown parser name(s): {', '.join(unknown_parsers)}")

    selected_candidates = {name: candidates[name] for name in args.parsers}
    availability = {
        name: _candidate_availability(candidate)
        for name, candidate in selected_candidates.items()
    }
    raw_goldset = _load_goldset(Path(args.goldset).expanduser().resolve())

    rows = []
    for pdf_path in pdf_paths:
        for candidate in selected_candidates.values():
            rows.append(
                _benchmark_parser_on_pdf(
                    candidate=candidate,
                    availability=availability[candidate.name],
                    pdf_path=pdf_path,
                    source_root=source_root,
                )
            )

    selections_by_pdf = _attach_selection_metrics(
        rows=rows,
        pdf_paths=pdf_paths,
        raw_goldset=raw_goldset,
        proxy_limit=args.proxy_selections_per_pdf,
    )
    selection_mode = (
        "goldset"
        if any(selection.get("source") == "goldset" for selections in selections_by_pdf.values() for selection in selections)
        else "proxy_only"
    )

    payload = {
        "runner_version": RUNNER_VERSION,
        "normalized_schema_version": NORMALIZED_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _get_git_head(),
        "git_branch": _get_git_branch(),
        "mode_name": args.mode_name or "full_product_parser_benchmark",
        "input_mode": input_mode,
        "input_pdfs": [path.as_posix() for path in pdf_paths],
        "run_config": {
            "source_root": source_root.as_posix(),
            "parsers": args.parsers,
            "parser_availability": availability,
            "goldset_path": str(Path(args.goldset).expanduser().resolve()),
            "selection_mode": selection_mode,
            "proxy_selections_per_pdf": args.proxy_selections_per_pdf,
            "rubric_weights": RUBRIC_WEIGHTS,
        },
        "normalized_format": {
            "name": "PageElementMap",
            "element_fields": [
                "element_id",
                "page_number",
                "element_type",
                "text",
                "bbox",
                "reading_order",
                "source_parser",
                "confidence",
                "quality_notes",
                "relations",
            ],
        },
        "benchmark_selections": selections_by_pdf,
        "results": rows,
    }
    payload["summary"] = _build_summary(rows=rows, candidates=selected_candidates)

    if run_dir is not None and args.write_artifacts:
        _write_artifacts(run_dir, rows)
    _write_payload(output_path, payload)
    if run_dir is not None and args.markdown_summary:
        _write_markdown_summary(run_dir=run_dir, payload=payload)

    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2), flush=True)
    if args.fail_on_error and any(row["status"] == PARSER_STATUS_FAILED for row in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
