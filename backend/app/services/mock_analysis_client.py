from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import AppSettings, StageName, get_settings
from app.utils.validation import validate_payload


class MockAnalysisClient:
    """Deterministic local provider for tests and UI smoke runs."""

    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def run_pass1(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        optional_extracted_text: str | None = None,
    ) -> dict[str, Any]:
        return self._wrap(
            "pass1",
            {
                "document_id": document_id,
                "page_number": page_number,
                "page_role": "핵심 개념을 소개하는 페이지",
                "page_summary": "이 페이지는 문서의 핵심 개념과 읽을 때 막힐 수 있는 지점을 보여준다.",
                "candidate_anchors": self._candidate_anchors(count=8),
            },
        )

    def run_pass1_text_first(
        self,
        *,
        document_id: str,
        page_number: int,
        route_label: str,
        route_reason: str,
        parser_source: str,
        text_length: int,
        non_empty_text_block_count: int,
        page_text: str,
        parsed_blocks: list[dict[str, Any]],
        allowed_anchor_regions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        anchors = self._candidate_anchors(count=max(3, min(8, len(allowed_anchor_regions))))
        for anchor, region in zip(anchors, allowed_anchor_regions):
            anchor["bbox"] = region["bbox"]
        return self._wrap(
            "pass1",
            {
                "document_id": document_id,
                "page_number": page_number,
                "page_role": "텍스트 중심 개념 설명 페이지",
                "page_summary": "추출 텍스트를 바탕으로 핵심 용어와 문맥상 중요한 문장을 뽑은 페이지다.",
                "candidate_anchors": anchors,
            },
        )

    def run_document_synthesis(
        self,
        document_id: str,
        total_pages: int,
        page_analysis_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        pages = sorted({int(page["page_number"]) for page in page_analysis_summaries})
        if not pages:
            pages = [1]
        return self._wrap(
            "document_synthesis",
            {
                "document_id": document_id,
                "overall_topic": "Scholium mock document",
                "overall_summary": "테스트용 mock provider가 만든 문서 구조다. 실제 학습 품질 판단에는 쓰지 않는다.",
                "sections": [{"section_id": "mock-1", "title": "Mock section", "pages": pages}],
                "key_concepts": [
                    {
                        "term": "Mock concept",
                        "description": "테스트 중 viewer와 pipeline 연결을 확인하기 위한 개념이다.",
                        "pages": pages[: min(3, len(pages))],
                    }
                ],
                "difficult_pages": pages[:1],
                "prerequisite_links": [
                    {
                        "from_page": pages[index],
                        "to_page": pages[index - 1],
                        "reason": "앞 페이지의 mock context가 이어진다.",
                    }
                    for index in range(1, min(len(pages), 3))
                ],
            },
        )

    def run_pass2(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        pass1_result: dict[str, Any],
        document_summary: dict[str, Any],
        extra_guidance: str | None = None,
    ) -> dict[str, Any]:
        candidates = list(pass1_result.get("candidate_anchors", []))[:3]
        final_anchors = []
        for candidate in candidates:
            related_pages = [
                int(page)
                for page in document_summary.get("difficult_pages", [])
                if int(page) != page_number
            ][:1]
            final_anchors.append(
                {
                    **candidate,
                    "long_explanation": candidate["short_explanation"],
                    "prerequisite": "",
                    "related_pages": related_pages,
                    "study_importance": {
                        "level": "medium",
                        "score": 3,
                        "reason": "Mock provider가 내부 테스트용으로 지정한 중간 중요도다.",
                    },
                    "meaning_in_context": candidate["short_explanation"],
                    "why_it_matters_here": "이 anchor는 viewer와 schema 연결을 확인하기 위한 mock 설명이다.",
                    "related_concepts_and_pages": [
                        {
                            "concept": "Mock concept",
                            "page_number": related_pages[0] if related_pages else None,
                            "relation_reason": "같은 mock 문서 흐름에서 연결된다.",
                        }
                    ],
                    "source_cues": [
                        {
                            "source_type": "this_slide",
                            "label": "Mock page cue",
                            "page_number": page_number,
                            "snippet": candidate["label"],
                        }
                    ],
                }
            )
        return self._wrap(
            "pass2",
            {
                "document_id": document_id,
                "page_number": page_number,
                "page_role": pass1_result["page_role"],
                "page_summary": pass1_result["page_summary"],
                "final_anchors": final_anchors,
                "page_risk_note": "Mock provider로 생성된 artifact라 실제 품질 판단에는 쓰지 않는다.",
            },
        )

    def run_selection_explanation(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        selection_id: str,
        selected_bbox: list[float],
        selection_context: dict[str, Any],
    ) -> dict[str, Any]:
        matched_preprocessed_elements = list(selection_context.get("matched_page_elements", []))
        document_context = dict(selection_context.get("document_context_brief") or {})
        matched_label = (
            str(matched_preprocessed_elements[0].get("label"))
            if matched_preprocessed_elements
            else "Selected region"
        )
        related_page = None
        for concept in document_context.get("key_concepts", []):
            for page in concept.get("pages", []):
                if int(page) != page_number:
                    related_page = int(page)
                    break
            if related_page is not None:
                break

        return self._wrap(
            "selection_explanation",
            {
                "document_id": document_id,
                "page_number": page_number,
                "selection_id": selection_id,
                "anchor_id": selection_id,
                "concept_title": matched_label,
                "label": matched_label,
                "anchor_type": "text",
                "bbox": selected_bbox,
                "selected_bbox": selected_bbox,
                "question": "이 선택 영역은 문서 안에서 무슨 의미야?",
                "short_explanation": "Mock provider가 선택 영역을 문서 맥락에 맞춰 설명한 결과다.",
                "long_explanation": "전처리된 page summary와 document summary를 바탕으로 선택 영역의 역할을 설명한다.",
                "prerequisite": "",
                "related_pages": [related_page] if related_page else [],
                "confidence": 0.72,
                "study_importance": {
                    "level": "medium",
                    "score": 3,
                    "reason": "선택된 영역이 현재 페이지의 전처리 요소와 일부 겹친다.",
                },
                "meaning_in_context": "이 영역은 사용자가 직접 지정한 막힘 지점이며, 현재 페이지 요약과 연결해 해석된다.",
                "why_it_matters_here": "Scholium은 이 지점을 먼저 정하지 않고, 사용자가 선택한 순간에만 설명을 만든다.",
                "related_concepts_and_pages": [
                    {
                        "concept": "Mock concept",
                        "page_number": related_page,
                        "relation_reason": "문서 요약에서 같은 흐름으로 묶인 개념이다.",
                    }
                ],
                "source_cues": [
                    {
                        "source_type": "this_slide",
                        "label": "Selected bbox",
                        "page_number": page_number,
                        "snippet": matched_label,
                    }
                ],
                "explanation_mode": "selection",
            },
        )

    def run_selection_follow_up(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        selection_id: str,
        question: str,
        selection_explanation: dict[str, Any],
        pass1_result: dict[str, Any],
        document_summary: dict[str, Any],
    ) -> dict[str, Any]:
        return self._wrap(
            "selection_follow_up",
            {
                "answer": (
                    "### Mock follow-up\n"
                    "- 선택 설명과 현재 페이지 맥락을 바탕으로 짧게 이어서 설명한 응답이다.\n"
                    "- 실제 provider에서는 근거가 부족하면 그 한계를 함께 말한다."
                ),
                "source_cues": [
                    {
                        "source_type": "this_slide",
                        "label": "Selected explanation context",
                        "page_number": page_number,
                        "snippet": selection_explanation.get("concept_title") or selection_explanation.get("label"),
                    }
                ],
                "confidence": 0.68,
            },
        )

    def _candidate_anchors(self, *, count: int) -> list[dict[str, Any]]:
        anchors = []
        for index in range(count):
            row = index % 4
            col = index // 4
            anchors.append(
                {
                    "anchor_id": f"mock_anchor_{index + 1}",
                    "label": f"Mock concept {index + 1}",
                    "anchor_type": "text" if index % 2 == 0 else "diagram",
                    "bbox": [0.12 + col * 0.36, 0.14 + row * 0.16, 0.22, 0.08],
                    "question": "이 부분은 어떤 의미야?",
                    "short_explanation": "Mock provider가 만든 짧은 설명이다.",
                    "confidence": 0.7,
                }
            )
        return anchors

    def _wrap(self, stage: StageName, result: dict[str, Any]) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "schema_version": self.settings.schema_version,
            "prompt_version": self.settings.stage_config(stage).prompt_version,
            "model_name": "mock-analysis-provider",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if stage == "selection_explanation":
            meta["provider"] = "mock"
            meta["reasoning_effort"] = "none"
        return {
            "meta": meta,
            "result": validate_payload(stage, result),
        }
