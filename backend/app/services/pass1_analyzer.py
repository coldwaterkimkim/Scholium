from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.models.document import RenderStatus, StageStatus
from app.services.openai_client import OpenAIResponsesClient
from app.services.storage import StorageService, get_storage_service


_TEXT_FIRST_ROUTE_LABEL = "text-rich"
_TEXT_FIRST_PATH = "text-first"
_MULTIMODAL_PATH = "multimodal"
_ESCALATED_PATH = "escalated"
_MIN_TEXT_LENGTH_FOR_CHEAP_PATH = 200
_MIN_NON_EMPTY_TEXT_BLOCKS_FOR_CHEAP_PATH = 4
_MIN_PARSED_BLOCKS_FOR_CHEAP_PATH = 3
_MIN_CANDIDATE_ANCHORS_FOR_CHEAP_PATH = 6
_MAX_TEXT_FIRST_BLOCKS = 20
_MAX_TEXT_FIRST_BLOCK_CHARS = 300
_MAX_TEXT_FIRST_PAGE_TEXT_CHARS = 4000
_BBOX_ROUNDING_PRECISION = 4


class Pass1Analyzer:
    def __init__(
        self,
        storage: StorageService | None = None,
        openai_client: OpenAIResponsesClient | None = None,
        max_workers: int = 3,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.openai_client = openai_client or OpenAIResponsesClient(storage=self.storage)
        self.max_workers = max(1, max_workers)

    def analyze_page(
        self,
        document_id: str,
        page_number: int,
        optional_extracted_text: str | None = None,
        page_manifest_entry: dict[str, Any] | None = None,
        parsed_page: dict[str, Any] | None = None,
        parser_source: str | None = None,
    ) -> dict[str, Any]:
        page_record = self.storage.get_page(document_id, page_number)
        if page_record is None:
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message="Page metadata was not found.",
            )

        pass1_path: str | None = None
        qa_warnings: list[str] = []

        try:
            self.storage.update_page_pass1_status(
                document_id,
                page_number,
                StageStatus.PENDING,
                error_message=None,
            )
        except ValueError as exc:
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=self._summarize_error_message("Pass1 setup failed.", exc),
                pass1_path=pass1_path,
            )

        if page_record.render_status is not RenderStatus.RENDERED:
            error_message = (
                f"Page render_status must be 'rendered', got '{page_record.render_status.value}'."
            )
            self.storage.update_page_pass1_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                pass1_path=pass1_path,
            )

        extracted_text = self._build_page_text(parsed_page) or optional_extracted_text

        try:
            if self._should_use_text_first(page_manifest_entry, parsed_page):
                try:
                    envelope = self._run_text_first_pass1(
                        document_id=document_id,
                        page_number=page_number,
                        page_manifest_entry=page_manifest_entry,
                        parsed_page=parsed_page,
                        parser_source=parser_source,
                    )
                    candidate_anchor_count = len(envelope["result"]["candidate_anchors"])
                    if candidate_anchor_count < _MIN_CANDIDATE_ANCHORS_FOR_CHEAP_PATH:
                        raise ValueError(
                            "Text-first pass1 produced too few candidate anchors: "
                            f"{candidate_anchor_count} < {_MIN_CANDIDATE_ANCHORS_FOR_CHEAP_PATH}."
                        )
                    pass1_path = _TEXT_FIRST_PATH
                except Exception as exc:
                    pass1_path = _ESCALATED_PATH
                    qa_warnings.append(
                        self._summarize_error_message(
                            "text-first route escalated to multimodal fallback.",
                            exc,
                        )
                    )
                    envelope = self._run_multimodal_pass1(
                        document_id=document_id,
                        page_number=page_number,
                        page_record_image_path=page_record.image_path,
                        optional_extracted_text=extracted_text,
                        page_manifest_entry=page_manifest_entry,
                        parser_source=parser_source,
                        pass1_path=pass1_path,
                    )
            else:
                pass1_path = _MULTIMODAL_PATH
                envelope = self._run_multimodal_pass1(
                    document_id=document_id,
                    page_number=page_number,
                    page_record_image_path=page_record.image_path,
                    optional_extracted_text=extracted_text,
                    page_manifest_entry=page_manifest_entry,
                    parser_source=parser_source,
                    pass1_path=pass1_path,
                )

            saved_path = self.storage.save_pass1_result(document_id, page_number, envelope)
            self.storage.update_page_pass1_status(
                document_id,
                page_number,
                StageStatus.COMPLETED,
                error_message=None,
            )
        except Exception as exc:
            error_message = self._summarize_error_message("Pass1 failed.", exc)
            self.storage.update_page_pass1_status(
                document_id,
                page_number,
                StageStatus.FAILED,
                error_message=error_message,
            )
            return self._failed_page_result(
                document_id=document_id,
                page_number=page_number,
                error_message=error_message,
                pass1_path=pass1_path,
            )

        candidate_anchor_count = len(envelope["result"]["candidate_anchors"])
        if candidate_anchor_count < 8:
            qa_warnings.append(
                f"candidate_anchors count is {candidate_anchor_count}; recommended QA target is 8~15.",
            )

        return {
            "document_id": document_id,
            "page_number": page_number,
            "pass1_status": StageStatus.COMPLETED.value,
            "saved_path": saved_path,
            "candidate_anchor_count": candidate_anchor_count,
            "qa_warnings": qa_warnings,
            "error_message": None,
            "pass1_path": pass1_path,
        }

    def analyze_document(
        self,
        document_id: str,
        page_numbers: list[int] | None = None,
    ) -> dict[str, Any]:
        page_records = self.storage.get_pages(document_id)
        if not page_records:
            raise ValueError(f"No page metadata found for document_id={document_id}.")

        rendered_page_numbers = {
            page.page_number for page in page_records if page.render_status is RenderStatus.RENDERED
        }

        if page_numbers is None:
            selected_page_numbers = sorted(rendered_page_numbers)
        else:
            selected_page_numbers = sorted(set(page_numbers))

        if not selected_page_numbers:
            raise ValueError(f"No rendered pages are available for document_id={document_id}.")

        manifest_by_page, parsed_pages_by_number, parser_source = self._load_routing_context(document_id)

        worker_count = min(self.max_workers, len(selected_page_numbers))
        if worker_count <= 1:
            page_results = [
                self.analyze_page(
                    document_id=document_id,
                    page_number=page_number,
                    page_manifest_entry=manifest_by_page.get(page_number),
                    parsed_page=parsed_pages_by_number.get(page_number),
                    parser_source=parser_source,
                )
                for page_number in selected_page_numbers
            ]
        else:
            page_results = []
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {
                    executor.submit(
                        self.analyze_page,
                        document_id=document_id,
                        page_number=page_number,
                        page_manifest_entry=manifest_by_page.get(page_number),
                        parsed_page=parsed_pages_by_number.get(page_number),
                        parser_source=parser_source,
                    ): page_number
                    for page_number in selected_page_numbers
                }
                for future in as_completed(future_map):
                    page_number = future_map[future]
                    try:
                        page_results.append(future.result())
                    except Exception as exc:
                        error_message = self._summarize_error_message("Pass1 failed.", exc)
                        try:
                            self.storage.update_page_pass1_status(
                                document_id,
                                page_number,
                                StageStatus.FAILED,
                                error_message=error_message,
                            )
                        except Exception:
                            pass
                        page_results.append(
                            self._failed_page_result(
                                document_id=document_id,
                                page_number=page_number,
                                error_message=error_message,
                            )
                        )

        page_results.sort(key=lambda page_result: int(page_result["page_number"]))

        text_first_pages = [
            page_result["page_number"]
            for page_result in page_results
            if page_result["pass1_path"] == _TEXT_FIRST_PATH
        ]
        multimodal_pages = [
            page_result["page_number"]
            for page_result in page_results
            if page_result["pass1_path"] == _MULTIMODAL_PATH
        ]
        escalated_pages = [
            page_result["page_number"]
            for page_result in page_results
            if page_result["pass1_path"] == _ESCALATED_PATH
        ]

        return {
            "document_id": document_id,
            "requested_pages": selected_page_numbers,
            "completed_pages": [
                page_result["page_number"]
                for page_result in page_results
                if page_result["pass1_status"] == StageStatus.COMPLETED.value
            ],
            "failed_pages": [
                {
                    "page_number": page_result["page_number"],
                    "error_message": page_result["error_message"],
                }
                for page_result in page_results
                if page_result["pass1_status"] == StageStatus.FAILED.value
            ],
            "saved_paths": [
                page_result["saved_path"]
                for page_result in page_results
                if page_result["saved_path"] is not None
            ],
            "qa_warnings": [
                {
                    "page_number": page_result["page_number"],
                    "warnings": page_result["qa_warnings"],
                }
                for page_result in page_results
                if page_result["qa_warnings"]
            ],
            "text_first_pages": text_first_pages,
            "multimodal_pages": multimodal_pages,
            "escalated_pages": escalated_pages,
        }

    def _load_routing_context(
        self,
        document_id: str,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], str | None]:
        if self.storage.settings.pass1_routing_mode != "hybrid":
            return {}, {}, None

        manifest_payload: dict[str, object] | None = None
        parse_payload: dict[str, object] | None = None

        try:
            manifest_payload = self.storage.load_page_manifest(document_id)
        except ValueError:
            manifest_payload = None

        try:
            parse_payload = self.storage.load_parse_artifact(document_id)
        except ValueError:
            parse_payload = None

        if (
            not isinstance(manifest_payload, dict)
            or manifest_payload.get("schema_version") != self.storage.settings.parser_schema_version
        ):
            manifest_payload = None
        if (
            not isinstance(parse_payload, dict)
            or parse_payload.get("schema_version") != self.storage.settings.parser_schema_version
        ):
            parse_payload = None

        manifest_by_page: dict[int, dict[str, Any]] = {}
        if manifest_payload is not None:
            for page_payload in manifest_payload.get("pages", []):
                if not isinstance(page_payload, dict):
                    continue
                page_number = page_payload.get("page_number")
                if page_number is None:
                    continue
                manifest_by_page[int(page_number)] = dict(page_payload)

        parsed_pages_by_number: dict[int, dict[str, Any]] = {}
        if parse_payload is not None:
            for page_payload in parse_payload.get("pages", []):
                if not isinstance(page_payload, dict):
                    continue
                page_number = page_payload.get("page_number")
                if page_number is None:
                    continue
                parsed_pages_by_number[int(page_number)] = dict(page_payload)

        parser_source = None
        if manifest_payload is not None and manifest_payload.get("parser_source"):
            parser_source = str(manifest_payload["parser_source"])
        elif parse_payload is not None and parse_payload.get("parser_source"):
            parser_source = str(parse_payload["parser_source"])

        return manifest_by_page, parsed_pages_by_number, parser_source

    def _should_use_text_first(
        self,
        page_manifest_entry: dict[str, Any] | None,
        parsed_page: dict[str, Any] | None,
    ) -> bool:
        if self.storage.settings.pass1_routing_mode != "hybrid":
            return False
        if page_manifest_entry is None or parsed_page is None:
            return False

        if str(page_manifest_entry.get("route_label", "")).strip() != _TEXT_FIRST_ROUTE_LABEL:
            return False

        if int(page_manifest_entry.get("text_length", 0)) < _MIN_TEXT_LENGTH_FOR_CHEAP_PATH:
            return False

        if (
            int(page_manifest_entry.get("non_empty_text_block_count", 0))
            < _MIN_NON_EMPTY_TEXT_BLOCKS_FOR_CHEAP_PATH
        ):
            return False

        return len(self._build_text_first_blocks(parsed_page)) >= _MIN_PARSED_BLOCKS_FOR_CHEAP_PATH

    def _run_text_first_pass1(
        self,
        *,
        document_id: str,
        page_number: int,
        page_manifest_entry: dict[str, Any],
        parsed_page: dict[str, Any],
        parser_source: str | None,
    ) -> dict[str, Any]:
        page_text = self._build_page_text(parsed_page)
        parsed_blocks = self._build_text_first_blocks(parsed_page)
        allowed_anchor_regions = self._build_allowed_anchor_regions(parsed_blocks)
        if not page_text or len(parsed_blocks) < _MIN_PARSED_BLOCKS_FOR_CHEAP_PATH:
            raise ValueError("Text-first pass1 requires enough parsed text blocks.")
        if len(allowed_anchor_regions) < _MIN_PARSED_BLOCKS_FOR_CHEAP_PATH:
            raise ValueError("Text-first pass1 requires enough grounded anchor regions.")

        envelope = self.openai_client.run_pass1_text_first(
            document_id=document_id,
            page_number=page_number,
            route_label=str(page_manifest_entry["route_label"]),
            route_reason=str(page_manifest_entry["route_reason"]),
            parser_source=(parser_source or "").strip() or "unknown",
            text_length=int(page_manifest_entry["text_length"]),
            non_empty_text_block_count=int(page_manifest_entry["non_empty_text_block_count"]),
            page_text=page_text,
            parsed_blocks=parsed_blocks,
            allowed_anchor_regions=allowed_anchor_regions,
        )
        self._enforce_allowed_bbox_grounding(envelope, allowed_anchor_regions)
        return self._attach_pass1_meta(
            envelope=envelope,
            pass1_path=_TEXT_FIRST_PATH,
            page_manifest_entry=page_manifest_entry,
            parser_source=parser_source,
        )

    def _run_multimodal_pass1(
        self,
        *,
        document_id: str,
        page_number: int,
        page_record_image_path: str,
        optional_extracted_text: str | None,
        page_manifest_entry: dict[str, Any] | None,
        parser_source: str | None,
        pass1_path: str,
    ) -> dict[str, Any]:
        image_path = self.storage.resolve_relative_path(page_record_image_path)
        if not image_path.exists():
            raise ValueError(f"Rendered page image is missing: {page_record_image_path}")

        envelope = self.openai_client.run_pass1(
            page_image_path=image_path,
            document_id=document_id,
            page_number=page_number,
            optional_extracted_text=optional_extracted_text,
        )
        return self._attach_pass1_meta(
            envelope=envelope,
            pass1_path=pass1_path,
            page_manifest_entry=page_manifest_entry,
            parser_source=parser_source,
        )

    def _attach_pass1_meta(
        self,
        *,
        envelope: dict[str, Any],
        pass1_path: str,
        page_manifest_entry: dict[str, Any] | None,
        parser_source: str | None,
    ) -> dict[str, Any]:
        normalized_envelope = {
            "meta": dict(envelope["meta"]),
            "result": envelope["result"],
        }
        normalized_envelope["meta"]["pass1_path"] = pass1_path

        if page_manifest_entry is not None:
            route_label = str(page_manifest_entry.get("route_label", "")).strip()
            route_reason = str(page_manifest_entry.get("route_reason", "")).strip()
            if route_label:
                normalized_envelope["meta"]["route_label"] = route_label
            if route_reason:
                normalized_envelope["meta"]["route_reason"] = route_reason

        normalized_parser_source = (parser_source or "").strip()
        if normalized_parser_source:
            normalized_envelope["meta"]["parser_source"] = normalized_parser_source

        return normalized_envelope

    def _build_text_first_blocks(self, parsed_page: dict[str, Any] | None) -> list[dict[str, Any]]:
        if parsed_page is None:
            return []

        blocks = parsed_page.get("blocks", [])
        if not isinstance(blocks, list):
            return []

        normalized_blocks: list[dict[str, Any]] = []
        for block in sorted(
            (block for block in blocks if isinstance(block, dict)),
            key=lambda block: int(block.get("reading_order", 0)),
        ):
            text = str(block.get("text", "")).strip()
            bbox = block.get("bbox")
            if not text or not isinstance(bbox, list):
                continue
            normalized_blocks.append(
                {
                    "block_id": str(block.get("block_id", "")).strip() or "unknown",
                    "block_type": str(block.get("block_type", "other")),
                    "bbox": self._round_bbox(list(bbox)),
                    "text": text[:_MAX_TEXT_FIRST_BLOCK_CHARS],
                    "reading_order": int(block.get("reading_order", 0)),
                }
            )
            if len(normalized_blocks) >= _MAX_TEXT_FIRST_BLOCKS:
                break

        return normalized_blocks

    def _build_page_text(self, parsed_page: dict[str, Any] | None) -> str | None:
        text_first_blocks = self._build_text_first_blocks(parsed_page)
        if not text_first_blocks:
            return None

        page_text = "\n".join(block["text"] for block in text_first_blocks if block["text"]).strip()
        if not page_text:
            return None
        return page_text[:_MAX_TEXT_FIRST_PAGE_TEXT_CHARS]

    def _build_allowed_anchor_regions(
        self,
        parsed_blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        allowed_regions: list[dict[str, Any]] = []
        seen_bbox_keys: set[tuple[float, float, float, float]] = set()

        for block in parsed_blocks:
            bbox = self._round_bbox(block["bbox"])
            bbox_key = self._bbox_key(bbox)
            if bbox_key in seen_bbox_keys:
                continue
            seen_bbox_keys.add(bbox_key)
            allowed_regions.append(
                {
                    "region_id": f"single:{block['block_id']}",
                    "source_block_ids": [block["block_id"]],
                    "bbox": bbox,
                    "text_excerpt": str(block["text"])[:80],
                }
            )

        for current_block, next_block in zip(parsed_blocks, parsed_blocks[1:]):
            if next_block["reading_order"] - current_block["reading_order"] != 1:
                continue
            union_bbox = self._union_bbox(current_block["bbox"], next_block["bbox"])
            bbox_key = self._bbox_key(union_bbox)
            if bbox_key in seen_bbox_keys:
                continue
            seen_bbox_keys.add(bbox_key)
            allowed_regions.append(
                {
                    "region_id": f"pair:{current_block['block_id']}+{next_block['block_id']}",
                    "source_block_ids": [current_block["block_id"], next_block["block_id"]],
                    "bbox": union_bbox,
                    "text_excerpt": (
                        f"{str(current_block['text'])[:40]} | {str(next_block['text'])[:40]}"
                    ).strip(),
                }
            )

        return allowed_regions

    def _enforce_allowed_bbox_grounding(
        self,
        envelope: dict[str, Any],
        allowed_anchor_regions: list[dict[str, Any]],
    ) -> None:
        allowed_bbox_map = {
            self._bbox_key(region["bbox"]): list(region["bbox"])
            for region in allowed_anchor_regions
        }
        for anchor in envelope["result"]["candidate_anchors"]:
            bbox_key = self._bbox_key(anchor["bbox"])
            if bbox_key not in allowed_bbox_map:
                raise ValueError(
                    "Text-first pass1 bbox must match one parsed block bbox or one adjacent two-block union bbox."
                )
            anchor["bbox"] = list(allowed_bbox_map[bbox_key])

    def _round_bbox(self, bbox: list[float]) -> list[float]:
        return [round(float(value), _BBOX_ROUNDING_PRECISION) for value in bbox]

    def _bbox_key(self, bbox: list[float]) -> tuple[float, float, float, float]:
        rounded_bbox = self._round_bbox(bbox)
        return (
            rounded_bbox[0],
            rounded_bbox[1],
            rounded_bbox[2],
            rounded_bbox[3],
        )

    def _union_bbox(self, first_bbox: list[float], second_bbox: list[float]) -> list[float]:
        first = [float(value) for value in first_bbox]
        second = [float(value) for value in second_bbox]
        x0 = min(first[0], second[0])
        y0 = min(first[1], second[1])
        x1 = max(first[0] + first[2], second[0] + second[2])
        y1 = max(first[1] + first[3], second[1] + second[3])
        return self._round_bbox([x0, y0, x1 - x0, y1 - y0])

    def _failed_page_result(
        self,
        *,
        document_id: str,
        page_number: int,
        error_message: str,
        pass1_path: str | None = None,
    ) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "page_number": page_number,
            "pass1_status": StageStatus.FAILED.value,
            "saved_path": None,
            "candidate_anchor_count": None,
            "qa_warnings": [],
            "error_message": error_message,
            "pass1_path": pass1_path,
        }

    def _summarize_error_message(self, prefix: str, detail: object | None) -> str:
        normalized_prefix = " ".join(str(prefix).split())
        normalized_detail = " ".join(str(detail or "").split())
        if not normalized_detail:
            return normalized_prefix
        if normalized_detail.lower().startswith("traceback"):
            return normalized_prefix
        max_length = 220
        detail_budget = max_length - len(normalized_prefix) - 1
        if detail_budget <= 0:
            return normalized_prefix
        if len(normalized_detail) > detail_budget:
            normalized_detail = normalized_detail[: max(detail_budget - 3, 0)].rstrip()
            if normalized_detail:
                normalized_detail += "..."
        return f"{normalized_prefix} {normalized_detail}".strip()
