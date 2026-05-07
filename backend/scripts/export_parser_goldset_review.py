#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_GOLDSET_PATH = PROJECT_ROOT / "benchmarks" / "parser_selection_goldset.yaml"
DEFAULT_PDF_DIR = PROJECT_ROOT / "data" / "raw_pdfs"


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0.")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render parser goldset selection bboxes into a browser-reviewable HTML page.",
    )
    parser.add_argument("--goldset", default=str(DEFAULT_GOLDSET_PATH), help="Goldset YAML/JSON path.")
    parser.add_argument("--pdf-dir", default=str(DEFAULT_PDF_DIR), help="Directory containing source PDFs.")
    parser.add_argument("--output-dir", help="Output directory. Defaults to docs/perf_runs/goldset_bbox_review_<timestamp>.")
    parser.add_argument("--zoom", type=_positive_float, default=1.5, help="Render zoom factor.")
    return parser


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    suffix = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return f"{normalized[:80] or 'item'}_{suffix}"


def _load_goldset(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore

            payload = yaml.safe_load(raw)
        except Exception as exc:
            raise SystemExit(f"Could not parse goldset {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Goldset root must be an object.")
    if not isinstance(payload.get("selections"), list):
        raise SystemExit("Goldset must contain selections list.")
    return payload


def _bbox(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        x, y, width, height = [float(component) for component in value]
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    if x < 0 or y < 0 or x + width > 1.001 or y + height > 1.001:
        return None
    return [x, y, width, height]


def _render_page_image(pdf_path: Path, page_number: int, image_path: Path, zoom: float) -> tuple[int, int]:
    with fitz.open(pdf_path) as document:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(f"Page {page_number} out of range for {pdf_path.name}; pages={document.page_count}")
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(image_path)
        return int(pixmap.width), int(pixmap.height)


def _card_html(
    *,
    selection: dict[str, Any],
    image_relpath: str,
    image_width: int,
    image_height: int,
    error: str | None = None,
) -> str:
    selection_id = html.escape(str(selection.get("selection_id") or "selection"))
    pdf_filename = html.escape(str(selection.get("pdf_filename") or ""))
    page_number = html.escape(str(selection.get("page_number") or ""))
    selection_type = html.escape(str(selection.get("selection_type") or "other"))
    expected = html.escape(str(selection.get("expected_concept_label") or ""))
    notes = html.escape(str(selection.get("notes") or ""))
    bbox = _bbox(selection.get("selected_bbox"))

    if error:
        return f"""
        <article class="card error-card">
          <header>
            <strong>{selection_id}</strong>
            <span>{pdf_filename} · p{page_number}</span>
          </header>
          <p class="error">{html.escape(error)}</p>
        </article>
        """

    assert bbox is not None
    left = bbox[0] * 100
    top = bbox[1] * 100
    width = bbox[2] * 100
    height = bbox[3] * 100
    bbox_text = html.escape(json.dumps([round(value, 4) for value in bbox]))
    return f"""
    <article class="card">
      <header>
        <div>
          <strong>{selection_id}</strong>
          <span>{pdf_filename} · page {page_number}</span>
        </div>
        <span class="pill">{selection_type}</span>
      </header>
      <div class="meta">
        <span>expected: {expected or "-"}</span>
        <span>bbox: {bbox_text}</span>
      </div>
      <div class="stage" style="width: {image_width}px; max-width: 100%;">
        <img src="{html.escape(image_relpath)}" width="{image_width}" height="{image_height}" loading="lazy" />
        <div class="bbox" style="left: {left:.4f}%; top: {top:.4f}%; width: {width:.4f}%; height: {height:.4f}%;">
          <span>{selection_type}</span>
        </div>
      </div>
      <p class="notes">{notes}</p>
    </article>
    """


def _write_html(output_dir: Path, cards: list[str], goldset_path: Path) -> Path:
    html_path = output_dir / "index.html"
    html_path.write_text(
        f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Scholium Parser Goldset BBox Review</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eef2f5;
      --card: #ffffff;
      --border: #d6dde6;
      --text: #18212b;
      --muted: #596574;
      --accent: #c7352c;
      --accent-soft: rgba(199, 53, 44, 0.14);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
    }}
    main {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 56px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    .intro {{ margin: 0 0 24px; color: var(--muted); line-height: 1.5; }}
    .grid {{ display: grid; gap: 18px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
      padding: 18px;
      overflow: auto;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 10px;
    }}
    header div {{ display: grid; gap: 4px; }}
    header span, .meta, .notes {{ color: var(--muted); font-size: 13px; }}
    .pill {{
      flex: 0 0 auto;
      border-radius: 999px;
      padding: 5px 9px;
      background: #eef3e2;
      color: #45621e;
      font-weight: 700;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .stage {{
      position: relative;
      border: 1px solid var(--border);
      background: #f8fafc;
      line-height: 0;
    }}
    .stage img {{
      display: block;
      width: 100%;
      height: auto;
    }}
    .bbox {{
      position: absolute;
      border: 3px solid var(--accent);
      background: var(--accent-soft);
      outline: 1px solid rgba(255,255,255,0.9);
    }}
    .bbox span {{
      position: absolute;
      left: -3px;
      top: -28px;
      padding: 4px 7px;
      border-radius: 6px 6px 0 0;
      background: var(--accent);
      color: white;
      font-size: 12px;
      font-weight: 800;
      line-height: 1;
      white-space: nowrap;
    }}
    .notes {{ margin: 12px 0 0; line-height: 1.45; }}
    .error {{ color: var(--accent); }}
    @media (max-width: 720px) {{
      main {{ width: min(100% - 20px, 1180px); padding-top: 20px; }}
      .card {{ padding: 12px; }}
      header {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>Scholium Parser Goldset BBox Review</h1>
    <p class="intro">
      Source: <code>{html.escape(str(goldset_path))}</code><br />
      빨간 박스가 goldset의 normalized bbox야. 이 박스가 의도한 term / figure / caption / table을 잘 덮는지만 보면 돼.
    </p>
    <section class="grid">
      {''.join(cards)}
    </section>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return html_path


def main() -> int:
    args = _build_parser().parse_args()
    goldset_path = Path(args.goldset).expanduser().resolve()
    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else PROJECT_ROOT / "docs" / "perf_runs" / f"goldset_bbox_review_{_timestamp()}"
    )
    image_dir = output_dir / "images"
    output_dir.mkdir(parents=True, exist_ok=True)

    goldset = _load_goldset(goldset_path)
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for selection in goldset["selections"]:
        if not isinstance(selection, dict):
            continue
        filename = str(selection.get("pdf_filename") or "").strip()
        page_number = int(selection.get("page_number") or 0)
        grouped[(filename, page_number)].append(selection)

    rendered_pages: dict[tuple[str, int], tuple[str, int, int, str | None]] = {}
    for (filename, page_number), selections in grouped.items():
        pdf_path = pdf_dir / filename
        image_name = f"{_safe_name(filename)}__p{page_number}.png"
        image_path = image_dir / image_name
        if not pdf_path.exists():
            rendered_pages[(filename, page_number)] = (image_path.relative_to(output_dir).as_posix(), 0, 0, f"PDF not found: {pdf_path}")
            continue
        try:
            width, height = _render_page_image(pdf_path, page_number, image_path, args.zoom)
            rendered_pages[(filename, page_number)] = (image_path.relative_to(output_dir).as_posix(), width, height, None)
        except Exception as exc:
            rendered_pages[(filename, page_number)] = (image_path.relative_to(output_dir).as_posix(), 0, 0, str(exc))

    cards = []
    for selection in goldset["selections"]:
        if not isinstance(selection, dict):
            continue
        filename = str(selection.get("pdf_filename") or "").strip()
        page_number = int(selection.get("page_number") or 0)
        image_relpath, width, height, error = rendered_pages.get((filename, page_number), ("", 0, 0, "Selection did not render."))
        if _bbox(selection.get("selected_bbox")) is None:
            error = "Invalid selected_bbox. Expected normalized [x, y, w, h]."
        cards.append(
            _card_html(
                selection=selection,
                image_relpath=image_relpath,
                image_width=width,
                image_height=height,
                error=error,
            )
        )

    html_path = _write_html(output_dir, cards, goldset_path)
    manifest_path = output_dir / "review_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "goldset_path": str(goldset_path),
                "pdf_dir": str(pdf_dir),
                "selection_count": len(cards),
                "html_path": str(html_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
