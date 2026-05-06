from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
import re
from typing import Any

from app.services.storage import StorageService, get_storage_service
from app.utils.validation import validate_payload


class Pass2ArtifactBuilder:
    def __init__(self, storage: StorageService | None = None) -> None:
        self.storage = storage or get_storage_service()

    def normalize_llm_envelope(
        self,
        *,
        document_id: str,
        page_number: int,
        envelope: dict[str, Any],
        pass1_result: dict[str, Any],
        document_summary_result: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str], bool]:
        if not isinstance(envelope, dict):
            raise ValueError("Pass2 envelope must be a JSON object.")
        if not isinstance(envelope.get("meta"), dict):
            raise ValueError("Pass2 envelope must include a meta object.")
        if not isinstance(envelope.get("result"), dict):
            raise ValueError("Pass2 envelope must include a result object.")

        candidate_map = self.build_candidate_map(pass1_result["candidate_anchors"])

        validated_result = validate_payload(
            "pass2",
            {
                **dict(envelope["result"]),
                "document_id": document_id,
                "page_number": page_number,
            },
        )
        final_types = set()
        for anchor in validated_result["final_anchors"]:
            anchor_id = str(anchor["anchor_id"])
            if anchor_id not in candidate_map:
                raise ValueError(
                    f"final_anchors contains anchor_id not found in pass1 candidates: {anchor_id}"
                )
            final_types.add(str(candidate_map[anchor_id]["anchor_type"]))
        candidate_types = {candidate["anchor_type"] for candidate in candidate_map.values()}
        needs_diversity_retry = len(final_types) == 1 and len(candidate_types) > 1

        normalized_meta = {
            "schema_version": str(envelope["meta"]["schema_version"]),
            "prompt_version": str(envelope["meta"]["prompt_version"]),
            "model_name": str(envelope["meta"]["model_name"]),
            "generated_at": str(envelope["meta"]["generated_at"]),
            "pass2_generation_mode": "llm",
        }

        return {
            "meta": normalized_meta,
            "result": {
                **validated_result,
                "document_id": document_id,
                "page_number": page_number,
            },
        }, [], needs_diversity_retry

    def build_compat_envelope(
        self,
        *,
        document_id: str,
        page_number: int,
        pass1_result: dict[str, Any],
        pass1_meta: dict[str, Any] | None = None,
        document_summary_result: dict[str, Any],
        planner_reason: str | None = None,
        page_routing_entry: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_candidates = self._select_compat_candidates(pass1_result["candidate_anchors"])
        if len(selected_candidates) < 3:
            raise ValueError(
                "Pass1 candidate anchor pool has fewer than 3 candidates, so compat pass2 cannot select 3 final anchors."
            )

        compat_context = self._build_compat_context(
            document_id=document_id,
            page_number=page_number,
            pass1_result=pass1_result,
            pass1_meta=pass1_meta,
            document_summary_result=document_summary_result,
            page_routing_entry=page_routing_entry,
        )
        final_anchors = []
        for candidate in selected_candidates:
            short_explanation = self._normalize_text(candidate.get("short_explanation"))
            final_anchors.append(
                {
                    "anchor_id": candidate["anchor_id"],
                    "label": candidate["label"],
                    "anchor_type": candidate["anchor_type"],
                    "bbox": candidate["bbox"],
                    "question": candidate["question"],
                    "short_explanation": short_explanation,
                    "confidence": candidate["confidence"],
                    "long_explanation": self._compose_compat_long_explanation(
                        candidate=candidate,
                        compat_context=compat_context,
                    ),
                    "prerequisite": compat_context["prerequisite"],
                    "related_pages": compat_context["related_pages"],
                    "study_importance": None,
                    "meaning_in_context": None,
                    "why_it_matters_here": None,
                    "related_concepts_and_pages": None,
                    "source_cues": None,
                }
            )

        result = validate_payload(
            "pass2",
            {
                "document_id": document_id,
                "page_number": page_number,
                "page_role": pass1_result["page_role"],
                "page_summary": pass1_result["page_summary"],
                "final_anchors": final_anchors,
                "page_risk_note": self._build_page_risk_note(
                    page_number=page_number,
                    document_summary_result=document_summary_result,
                    planner_reason=planner_reason,
                    page_routing_entry=page_routing_entry,
                ),
            },
        )

        return {
            "meta": {
                "schema_version": self.storage.settings.schema_version,
                "prompt_version": "pass2_compat_v0_1",
                "model_name": "compat-builder",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "pass2_generation_mode": "compat",
            },
            "result": result,
        }

    def describe_compat_trace(
        self,
        *,
        document_id: str,
        page_number: int,
        pass1_result: dict[str, Any],
        pass1_meta: dict[str, Any] | None,
        document_summary_result: dict[str, Any],
        page_routing_entry: dict[str, Any] | None,
        final_anchors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        compat_context = self._build_compat_context(
            document_id=document_id,
            page_number=page_number,
            pass1_result=pass1_result,
            pass1_meta=pass1_meta,
            document_summary_result=document_summary_result,
            page_routing_entry=page_routing_entry,
        )
        sentence_count = max(
            (self._sentence_count(anchor.get("long_explanation")) for anchor in final_anchors),
            default=0,
        )
        return {
            "compat_prerequisite_source": compat_context["prerequisite_source"],
            "compat_related_pages_source": compat_context["related_pages_source"],
            "compat_long_explanation_shape": (
                "3_sentence" if sentence_count >= 3 else "2_sentence"
            )
            if final_anchors
            else None,
            "compat_used_section_title": bool(compat_context["use_section_title"]),
        }

    def build_candidate_map(self, candidate_anchors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(candidate["anchor_id"]): dict(candidate) for candidate in candidate_anchors}

    def _select_compat_candidates(
        self,
        candidate_anchors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ranked_candidates = sorted(
            (
                {
                    "original_index": index,
                    "candidate": dict(candidate),
                }
                for index, candidate in enumerate(candidate_anchors)
            ),
            key=lambda item: (
                -float(item["candidate"]["confidence"]),
                int(item["original_index"]),
                str(item["candidate"]["anchor_id"]),
            ),
        )

        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        used_labels: set[str] = set()
        used_anchor_types: set[str] = set()

        def append_candidate(item: dict[str, Any]) -> None:
            candidate = dict(item["candidate"])
            selected.append(candidate)
            selected_ids.add(str(candidate["anchor_id"]))
            used_labels.add(str(candidate["label"]))
            used_anchor_types.add(str(candidate["anchor_type"]))

        for item in ranked_candidates:
            if len(selected) >= 3:
                break
            candidate = item["candidate"]
            if str(candidate["anchor_id"]) in selected_ids:
                continue
            if (
                str(candidate["anchor_type"]) not in used_anchor_types
                and str(candidate["label"]) not in used_labels
            ):
                append_candidate(item)

        for item in ranked_candidates:
            if len(selected) >= 3:
                break
            candidate = item["candidate"]
            if str(candidate["anchor_id"]) in selected_ids:
                continue
            if str(candidate["label"]) not in used_labels:
                append_candidate(item)

        for item in ranked_candidates:
            if len(selected) >= 3:
                break
            candidate = item["candidate"]
            if str(candidate["anchor_id"]) in selected_ids:
                continue
            append_candidate(item)

        return selected

    def _build_compat_context(
        self,
        *,
        document_id: str,
        page_number: int,
        pass1_result: dict[str, Any],
        pass1_meta: dict[str, Any] | None,
        document_summary_result: dict[str, Any],
        page_routing_entry: dict[str, Any] | None,
    ) -> dict[str, Any]:
        section_context = self._find_section_context(
            page_number=page_number,
            document_summary_result=document_summary_result,
        )
        base_route_label = self._normalize_text(
            page_routing_entry.get("base_route_label") if page_routing_entry is not None else ""
        ).lower()
        recommended_execution = self._normalize_text(
            page_routing_entry.get("recommended_execution") if page_routing_entry is not None else ""
        ).lower()
        pass1_path = self._normalize_text(pass1_meta.get("pass1_path") if pass1_meta else "").lower()
        page_role = self._normalize_text(pass1_result.get("page_role"))
        page_summary = self._normalize_text(pass1_result.get("page_summary"))
        prerequisite, prerequisite_source = self._build_compat_prerequisite(
            document_id=document_id,
            page_number=page_number,
            base_route_label=base_route_label,
            pass1_path=pass1_path,
            document_summary_result=document_summary_result,
            section_context=section_context,
        )
        related_pages, related_pages_source = self._build_compat_related_pages(
            page_number=page_number,
            base_route_label=base_route_label,
            pass1_path=pass1_path,
            page_summary=page_summary,
            document_summary_result=document_summary_result,
            section_context=section_context,
        )
        return {
            "document_id": document_id,
            "base_route_label": base_route_label,
            "recommended_execution": recommended_execution,
            "pass1_path": pass1_path,
            "page_role": page_role,
            "page_summary": page_summary,
            "section_context": section_context,
            "use_section_title": bool(
                section_context["title"]
                and (base_route_label == "text-rich" or pass1_path == "text-first")
            ),
            "prerequisite": prerequisite,
            "prerequisite_source": prerequisite_source,
            "related_pages": related_pages,
            "related_pages_source": related_pages_source,
        }

    def _compose_compat_long_explanation(
        self,
        *,
        candidate: dict[str, Any],
        compat_context: dict[str, Any],
    ) -> str:
        first_sentence = self._build_anchor_focus_sentence(
            label=self._normalize_text(candidate.get("label")),
            question=self._normalize_text(candidate.get("question")),
            short_explanation=self._normalize_text(candidate.get("short_explanation")),
        )
        second_sentence = self._build_page_role_sentence(
            first_sentence=first_sentence,
            page_role=compat_context["page_role"],
            page_summary=compat_context["page_summary"],
            section_title=compat_context["section_context"]["title"],
            use_section_title=bool(compat_context["use_section_title"]),
        )
        third_sentence = self._build_context_sentence(compat_context=compat_context)
        return self._fit_sentences_with_limit(
            self._dedupe_sentences([first_sentence, second_sentence, third_sentence]),
            max_length=420,
        )

    def _build_compat_prerequisite(
        self,
        *,
        document_id: str,
        page_number: int,
        base_route_label: str,
        pass1_path: str,
        document_summary_result: dict[str, Any],
        section_context: dict[str, Any],
    ) -> tuple[str, str]:
        for link in document_summary_result.get("prerequisite_links", []):
            if int(link["from_page"]) != page_number:
                continue
            reason = self._normalize_text(link.get("reason"))
            if reason:
                return self._trim_with_ellipsis(reason, max_length=180), "direct"

        if base_route_label != "text-rich" or pass1_path != "text-first":
            return "", "none"

        previous_page = section_context.get("previous_page")
        if previous_page is None:
            return "", "none"

        previous_role, previous_summary = self._load_adjacent_page_context(
            document_id=document_id,
            page_number=int(previous_page),
        )
        if previous_role:
            return (
                f"앞선 p.{previous_page}의 {self._trim_context_phrase(previous_role)}를 먼저 보면 이 페이지 맥락이 더 분명해진다.",
                "previous_section_page",
            )
        if previous_summary:
            return (
                f"앞선 p.{previous_page}에서 다룬 {self._trim_context_phrase(previous_summary)}를 먼저 보면 이 페이지 맥락이 더 분명해진다.",
                "previous_section_page",
            )
        return "", "none"

    def _build_compat_related_pages(
        self,
        *,
        page_number: int,
        base_route_label: str,
        pass1_path: str,
        page_summary: str,
        document_summary_result: dict[str, Any],
        section_context: dict[str, Any],
    ) -> tuple[list[int], str]:
        related_pages: list[int] = []
        source = "none"

        for link in document_summary_result.get("prerequisite_links", []):
            if int(link["from_page"]) != page_number:
                continue
            target_page = int(link["to_page"])
            if target_page != page_number and target_page not in related_pages:
                related_pages.append(target_page)
                source = "direct"
            if len(related_pages) >= 2:
                return related_pages[:2], source

        previous_page = section_context.get("previous_page")
        if previous_page is not None and previous_page not in related_pages:
            related_pages.append(int(previous_page))
            if source == "none":
                source = "section_adjacent"
            if len(related_pages) >= 2:
                return related_pages[:2], source

        next_page = section_context.get("next_page")
        if (
            next_page is not None
            and next_page not in related_pages
            and self._summary_has_transition_signal(page_summary)
            and (base_route_label == "text-rich" or pass1_path == "text-first")
        ):
            related_pages.append(int(next_page))
            if source == "none":
                source = "section_adjacent"

        return related_pages[:2], source

    def _find_section_context(
        self,
        *,
        page_number: int,
        document_summary_result: dict[str, Any],
    ) -> dict[str, Any]:
        for section in document_summary_result.get("sections", []):
            pages = [int(page) for page in section.get("pages", [])]
            if page_number not in pages:
                continue
            index = pages.index(page_number)
            return {
                "title": self._normalize_text(section.get("title")),
                "pages": pages,
                "previous_page": pages[index - 1] if index > 0 else None,
                "next_page": pages[index + 1] if index + 1 < len(pages) else None,
            }
        return {
            "title": "",
            "pages": [],
            "previous_page": None,
            "next_page": None,
        }

    def _build_anchor_focus_sentence(
        self,
        *,
        label: str,
        question: str,
        short_explanation: str,
    ) -> str:
        short_sentences = self._split_sentences(short_explanation)
        sentence = short_sentences[0] if short_sentences else (short_explanation or label)
        if label and len(label) <= 24 and not self._text_overlap(label, sentence, threshold=0.9):
            sentence = f"{label}: {sentence}".strip()

        question_core = self._trim_context_phrase(question, max_length=80)
        if sentence and question_core and len(self._normalize_overlap_text(sentence)) < 10:
            sentence = f"{sentence.rstrip('. ')}로, '{question_core}'라는 질문과 직접 맞닿아 있다."
        return self._ensure_sentence(sentence)

    def _build_page_role_sentence(
        self,
        *,
        first_sentence: str,
        page_role: str,
        page_summary: str,
        section_title: str,
        use_section_title: bool,
    ) -> str:
        summary_sentence = self._first_summary_sentence(page_summary)
        if summary_sentence and (
            self._sentence_similarity(first_sentence, summary_sentence) >= 0.75
            or self._text_overlap(first_sentence, summary_sentence, threshold=0.75)
        ):
            summary_sentence = ""

        context_prefix = f"{section_title} 흐름에서 " if use_section_title and section_title else ""
        summary_clause = self._trim_context_phrase(summary_sentence, max_length=180)
        if page_role and summary_clause:
            return self._ensure_sentence(
                f"이 페이지는 {context_prefix}{page_role} 성격을 띠며, {summary_clause}"
            )
        if page_role:
            return self._ensure_sentence(
                f"이 페이지는 {context_prefix}{page_role} 성격의 페이지다."
            )
        if summary_clause:
            return self._ensure_sentence(summary_clause)
        return ""

    def _build_context_sentence(self, *, compat_context: dict[str, Any]) -> str:
        prerequisite = compat_context["prerequisite"]
        if compat_context["prerequisite_source"] == "direct" and prerequisite:
            return self._ensure_sentence(prerequisite)

        previous_page = compat_context["section_context"].get("previous_page")
        if previous_page is not None:
            previous_role, previous_summary = self._load_adjacent_page_context(
                document_id=compat_context["document_id"],
                page_number=int(previous_page),
            )
            previous_signal = previous_role or previous_summary
            if previous_signal:
                return self._ensure_sentence(
                    f"앞선 p.{previous_page}의 {self._trim_context_phrase(previous_signal)}와 이어서 읽으면 이 페이지의 위치가 더 분명해진다."
                )

        next_page = compat_context["section_context"].get("next_page")
        if next_page is not None and self._summary_has_transition_signal(compat_context["page_summary"]):
            next_role, next_summary = self._load_adjacent_page_context(
                document_id=compat_context["document_id"],
                page_number=int(next_page),
            )
            next_signal = next_role or next_summary
            if next_signal:
                return self._ensure_sentence(
                    f"이어지는 p.{next_page}의 {self._trim_context_phrase(next_signal)}와 함께 보면 이 페이지가 다음 논의로 어떻게 이어지는지 보인다."
                )

        return ""

    def _load_adjacent_page_context(self, *, document_id: str, page_number: int) -> tuple[str, str]:
        artifact = self.storage.load_pass1_result(document_id, page_number)
        if artifact is None:
            return "", ""
        result = artifact.get("result", {})
        return (
            self._normalize_text(result.get("page_role")),
            self._first_summary_sentence(result.get("page_summary")),
        )

    def _first_summary_sentence(self, value: object | None) -> str:
        text = self._normalize_text(value)
        if not text:
            return ""
        for part in self._split_sentences(text):
            normalized_part = self._normalize_text(part)
            if normalized_part:
                return self._ensure_sentence(normalized_part)
        return self._ensure_sentence(text)

    def _split_sentences(self, value: object | None) -> list[str]:
        text = self._normalize_text(value)
        if not text:
            return []
        return [part for part in re.split(r"(?<=[.!?])\s+", text) if self._normalize_text(part)]

    def _dedupe_sentences(self, sentences: list[str]) -> list[str]:
        deduped: list[str] = []
        normalized_seen: list[str] = []
        for sentence in sentences:
            normalized_sentence = self._normalize_text(sentence)
            if not normalized_sentence:
                continue
            overlap_text = self._normalize_overlap_text(normalized_sentence)
            if any(
                overlap_text in seen
                or seen in overlap_text
                or SequenceMatcher(None, overlap_text, seen).ratio() >= 0.88
                for seen in normalized_seen
            ):
                continue
            deduped.append(self._ensure_sentence(normalized_sentence))
            normalized_seen.append(overlap_text)
        return deduped

    def _fit_sentences_with_limit(self, sentences: list[str], *, max_length: int) -> str:
        if not sentences:
            return ""
        if len(sentences) == 1:
            return self._trim_with_ellipsis(sentences[0], max_length=max_length)

        candidate = " ".join(sentences[:3]).strip()
        if len(candidate) <= max_length:
            return candidate

        candidate = " ".join(sentences[:2]).strip()
        if len(candidate) <= max_length:
            return candidate

        trimmed_second = self._trim_with_ellipsis(
            sentences[1],
            max_length=max(max_length - len(sentences[0]) - 1, 20),
        )
        return " ".join([sentences[0], trimmed_second]).strip()

    def _summary_has_transition_signal(self, page_summary: str) -> bool:
        lowered = page_summary.lower()
        return any(
            token in lowered
            for token in (
                "이어",
                "다음",
                "이후",
                "전환",
                "확장",
                "비교",
                "연결",
                "next",
                "then",
                "transition",
                "compare",
            )
        )

    def _normalize_text(self, value: object | None) -> str:
        return " ".join(str(value or "").split()).strip()

    def _normalize_overlap_text(self, value: str) -> str:
        return re.sub(r"[\W_]+", "", value).lower()

    def _sentence_similarity(self, left: str, right: str) -> float:
        normalized_left = self._normalize_overlap_text(left)
        normalized_right = self._normalize_overlap_text(right)
        if not normalized_left or not normalized_right:
            return 0.0
        return SequenceMatcher(None, normalized_left, normalized_right).ratio()

    def _text_overlap(self, left: str, right: str, *, threshold: float) -> bool:
        normalized_left = self._normalize_overlap_text(left)
        normalized_right = self._normalize_overlap_text(right)
        if not normalized_left or not normalized_right:
            return False
        return (
            normalized_left in normalized_right
            or normalized_right in normalized_left
            or SequenceMatcher(None, normalized_left, normalized_right).ratio() >= threshold
        )

    def _trim_context_phrase(self, value: object | None, *, max_length: int = 90) -> str:
        text = self._normalize_text(value).rstrip(".!? ")
        if len(text) <= max_length:
            return text
        return self._trim_with_ellipsis(text, max_length=max_length).rstrip(".!? ")

    def _trim_with_ellipsis(self, value: str, *, max_length: int) -> str:
        text = self._normalize_text(value)
        if len(text) <= max_length:
            return text
        boundary = max(
            text.rfind(".", 0, max_length),
            text.rfind(",", 0, max_length),
            text.rfind(";", 0, max_length),
            text.rfind(" ", 0, max_length),
        )
        if boundary < max_length // 2:
            boundary = max_length - 3
        return text[:boundary].rstrip(" .,;") + "..."

    def _ensure_sentence(self, value: str) -> str:
        text = self._normalize_text(value)
        if not text:
            return ""
        if text.endswith(("...", ".", "!", "?")):
            return text
        return text + "."

    def _sentence_count(self, value: object | None) -> int:
        text = self._normalize_text(value)
        if not text:
            return 0
        return len([part for part in re.split(r"(?<=[.!?])\s+", text) if self._normalize_text(part)])

    def _build_page_risk_note(
        self,
        *,
        page_number: int,
        document_summary_result: dict[str, Any],
        planner_reason: str | None,
        page_routing_entry: dict[str, Any] | None,
    ) -> str:
        difficult_pages = {int(page) for page in document_summary_result.get("difficult_pages", [])}
        routing_label = ""
        if page_routing_entry is not None:
            routing_label = str(page_routing_entry.get("base_route_label", "")).strip().lower()
        planner_reason_text = (planner_reason or "").lower()

        if (
            page_number in difficult_pages
            or "visual" in planner_reason_text
            or "hard" in planner_reason_text
            or "selective" in planner_reason_text
            or routing_label in {"scan-like", "visual-rich"}
        ):
            return "이 페이지는 주변 맥락과 함께 읽어야 의미가 더 안정적이다."
        return "핵심 포인트는 비교적 명확하지만 관련 페이지를 함께 보면 이해가 더 좋아진다."
