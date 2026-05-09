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
                "page_guide": self._page_guide(
                    page_role="핵심 개념 소개",
                    one_line_thesis="이 페이지는 뒤에서 반복될 핵심 개념을 먼저 잡아주는 역할을 한다.",
                ),
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
                "page_guide": self._page_guide(
                    page_role="텍스트 중심 개념 설명",
                    one_line_thesis="추출된 텍스트 블록을 순서대로 읽으면 이 페이지의 핵심 논리를 따라갈 수 있다.",
                ),
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

    def run_document_guide(
        self,
        document_id: str,
        document_digest: dict[str, Any],
    ) -> dict[str, Any]:
        response_language = "en" if document_digest.get("response_language") == "en" else "ko"
        pages = self._digest_page_numbers(document_digest)
        return self._wrap(
            "document_guide",
            {
                "document_id": document_id,
                "document_guide": self._mock_document_guide(document_id, pages, response_language),
            },
        )

    def run_page_guide_chunk(
        self,
        document_id: str,
        chunk_index: int,
        total_chunks: int,
        page_numbers: list[int],
        document_guide: dict[str, Any],
        page_digest: dict[str, Any],
    ) -> dict[str, Any]:
        response_language = "en" if page_digest.get("response_language") == "en" else "ko"
        return self._wrap(
            "page_guide_chunk",
            {
                "document_id": document_id,
                "chunk_index": chunk_index,
                "page_numbers": page_numbers,
                "page_guides": [
                    {
                        "document_id": document_id,
                        "page_number": page_number,
                        **self._page_guide(
                            page_role=(
                                "Mock semantic page role"
                                if response_language == "en"
                                else "Mock semantic page role"
                            ),
                            one_line_thesis=(
                                "This chunked page guide is generated by the mock provider."
                                if response_language == "en"
                                else "이 페이지는 mock provider가 만든 chunked page guide다."
                            ),
                        ),
                    }
                    for page_number in page_numbers
                ],
            },
        )

    def run_semantic_guide(
        self,
        document_id: str,
        document_digest: dict[str, Any],
    ) -> dict[str, Any]:
        response_language = "en" if document_digest.get("response_language") == "en" else "ko"
        pages = self._digest_page_numbers(document_digest)
        return self._wrap(
            "semantic_guide",
            {
                "document_id": document_id,
                "document_guide": self._mock_document_guide(document_id, pages, response_language),
                "page_guides": [
                    {
                        "document_id": document_id,
                        "page_number": page_number,
                        **self._page_guide(
                            page_role=(
                                "Mock semantic page role"
                                if response_language == "en"
                                else "Mock semantic page role"
                            ),
                            one_line_thesis=(
                                "This page guide is enriched by the mock semantic guide."
                                if response_language == "en"
                                else "이 페이지는 mock semantic guide로 보강된 page guide다."
                            ),
                        ),
                    }
                    for page_number in pages
                ],
            },
        )

    def _digest_page_numbers(self, document_digest: dict[str, Any]) -> list[int]:
        pages = [
            int(page.get("page_number"))
            for page in document_digest.get("pages", [])
            if isinstance(page, dict) and page.get("page_number")
        ]
        return pages or [1]

    def _mock_document_guide(
        self,
        document_id: str,
        pages: list[int],
        response_language: str,
    ) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "overall_topic": (
                "Scholium mock semantic guide"
                if response_language == "en"
                else "Scholium mock semantic guide"
            ),
            "overall_summary": (
                "A mock semantic guide generated from the parser digest."
                if response_language == "en"
                else "Parser digest를 바탕으로 만든 mock semantic guide다."
            ),
            "section_structure": [
                {
                    "section_id": "mock-section-1",
                    "title": "Mock semantic section",
                    "pages": pages,
                }
            ],
            "key_concepts": [
                {
                    "concept": "Mock semantic concept",
                    "description": "Semantic Guide wiring을 확인하기 위한 개념이다.",
                    "pages": pages[:3],
                }
            ],
            "page_sequence_overview": [
                "Read in parser digest page order."
                if response_language == "en"
                else "Parser digest page order를 따라 읽는다."
            ],
            "prerequisite_links": [
                {
                    "from_page": pages[index],
                    "to_page": pages[index - 1],
                    "reason": "앞 페이지의 mock semantic context가 이어진다.",
                }
                for index in range(1, min(len(pages), 3))
            ],
            "difficult_pages": pages[:1],
            "study_strategy_notes": [
                "Use the Page Guide for the macro flow and drag only confusing regions."
                if response_language == "en"
                else "Page Guide로 큰 흐름을 잡고, 막히는 부분만 드래그한다."
            ],
        }

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
        response_language = "en" if selection_context.get("response_language") == "en" else "ko"
        related_candidates = [
            candidate
            for candidate in selection_context.get("related_page_candidates", [])
            if isinstance(candidate, dict)
        ]
        related_page = None
        related_concept = "Mock concept"
        if related_candidates:
            first_candidate = related_candidates[0]
            related_page = int(first_candidate.get("page_number") or 0) or None
            related_concept = str(first_candidate.get("concept") or first_candidate.get("source_label") or related_concept)
        else:
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
                "question": (
                    "What does this selected region mean in the document?"
                    if response_language == "en"
                    else "이 선택 영역은 문서 안에서 무슨 의미야?"
                ),
                "short_explanation": (
                    "The mock provider explains the selected region in document context."
                    if response_language == "en"
                    else "Mock provider가 선택 영역을 문서 맥락에 맞춰 설명한 결과다."
                ),
                "long_explanation": (
                    "It uses the preprocessed page summary and document summary to explain the selected region's role."
                    if response_language == "en"
                    else "전처리된 page summary와 document summary를 바탕으로 선택 영역의 역할을 설명한다."
                ),
                "prerequisite": "",
                "related_pages": [related_page] if related_page else [],
                "confidence": 0.72,
                "study_importance": {
                    "level": "medium",
                    "score": 3,
                    "reason": (
                        "The selected region overlaps with a preprocessed element on the current page."
                        if response_language == "en"
                        else "선택된 영역이 현재 페이지의 전처리 요소와 일부 겹친다."
                    ),
                },
                "meaning_in_context": (
                    "This region is the user's chosen point of confusion, interpreted through the current page summary."
                    if response_language == "en"
                    else "이 영역은 사용자가 직접 지정한 막힘 지점이며, 현재 페이지 요약과 연결해 해석된다."
                ),
                "why_it_matters_here": (
                    "Scholium does not preselect this point; it explains it only after the user selects it."
                    if response_language == "en"
                    else "Scholium은 이 지점을 먼저 정하지 않고, 사용자가 선택한 순간에만 설명을 만든다."
                ),
                "related_concepts_and_pages": [
                    {
                        "concept": related_concept,
                        "page_number": related_page,
                        "relation_reason": (
                            "The document summary groups it into the same learning path."
                            if response_language == "en"
                            else "문서 요약에서 같은 흐름으로 묶인 개념이다."
                        ),
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
        response_language: str,
        selection_explanation: dict[str, Any],
        pass1_result: dict[str, Any],
        document_summary: dict[str, Any],
    ) -> dict[str, Any]:
        answer = (
            "### Mock follow-up\n"
            "- This short answer continues from the selected explanation and current page context.\n"
            "- A real provider should state limitations when grounding is weak."
            if response_language == "en"
            else "### Mock follow-up\n"
            "- 선택 설명과 현재 페이지 맥락을 바탕으로 짧게 이어서 설명한 응답이다.\n"
            "- 실제 provider에서는 근거가 부족하면 그 한계를 함께 말한다."
        )
        return self._wrap(
            "selection_follow_up",
            {
                "answer": answer,
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

    def _page_guide(self, *, page_role: str, one_line_thesis: str) -> dict[str, Any]:
        return {
            "page_role": page_role,
            "one_line_thesis": one_line_thesis,
            "key_question": "이 페이지를 읽을 때 먼저 잡아야 할 중심 질문은 무엇인가?",
            "reading_path": [
                "제목과 핵심 문장을 먼저 읽는다.",
                "강조된 개념과 시각 요소를 연결한다.",
                "마지막으로 후보 요소들이 어떤 질문을 만들 수 있는지 확인한다.",
            ],
            "logic_flow": [
                "핵심 개념 제시",
                "근거 또는 예시 확인",
                "선택 설명으로 세부 의미 확인",
            ],
            "key_concepts": [
                {
                    "concept": "Mock concept",
                    "brief_description": "테스트용 page guide가 노출되는지 확인하기 위한 개념이다.",
                    "role_on_page": "Page Guide 렌더링과 schema validation을 확인한다.",
                }
            ],
            "omitted_context": [
                "Mock provider 결과이므로 실제 강의 배경은 포함하지 않는다.",
            ],
            "study_focus": [
                "페이지 역할과 선택 가능한 핵심 요소의 관계를 확인한다.",
            ],
            "common_confusions": [
                "Page Guide는 전체 페이지 방향이고, 선택 설명은 사용자가 고른 영역의 세부 설명이다.",
            ],
            "example_or_application": "테스트 문서에서 Page Guide 패널이 PDF 상단에 보이면 연결이 정상이다.",
            "must_remember": [
                "Page Guide는 페이지 읽기 방향을 제공한다.",
                "선택 설명은 드래그한 영역에만 반응한다.",
            ],
            "self_check_questions": [
                "이 페이지가 문서에서 맡는 역할을 한 문장으로 말할 수 있는가?",
            ],
            "before_next_connection": {
                "previous": None,
                "next": None,
            },
        }

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
