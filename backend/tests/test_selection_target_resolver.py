from __future__ import annotations

import unittest

from app.services.selection_target_resolver import SelectionTargetResolver
from app.utils.validation import validate_payload


class SelectionTargetResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = SelectionTargetResolver()

    def test_small_phrase_inside_large_text_block(self) -> None:
        page_parse = {
            "blocks": [
                {
                    "block_id": "p1_b1",
                    "block_type": "paragraph",
                    "text": "ML · Deep Learning · Neural Networks · LLM · AIBT",
                    "bbox": [0.1, 0.2, 0.8, 0.1],
                    "reading_order": 1,
                    "words": [
                        {"text": "ML", "bbox": [0.12, 0.22, 0.04, 0.04], "reading_order": 1},
                        {"text": "Deep", "bbox": [0.22, 0.22, 0.07, 0.04], "reading_order": 2},
                        {"text": "Learning", "bbox": [0.3, 0.22, 0.1, 0.04], "reading_order": 3},
                        {"text": "Neural", "bbox": [0.45, 0.22, 0.08, 0.04], "reading_order": 4},
                        {"text": "Networks", "bbox": [0.54, 0.22, 0.11, 0.04], "reading_order": 5},
                        {"text": "LLM", "bbox": [0.7, 0.22, 0.06, 0.04], "reading_order": 6},
                    ],
                }
            ]
        }
        page_elements = [
            {
                "element_id": "p1_b1",
                "element_type": "text",
                "label": "ML · Deep Learning · Neural Networks · LLM · AIBT",
                "bbox": [0.1, 0.2, 0.8, 0.1],
            }
        ]

        result = self.resolver.resolve(
            document_id="doc_test",
            page_number=1,
            selected_bbox=[0.44, 0.21, 0.23, 0.07],
            page_parse=page_parse,
            page_elements=page_elements,
        )

        self.assertEqual(result["selected_text_exact"], "Neural Networks")
        self.assertEqual(result["enclosing_block_text"], "ML · Deep Learning · Neural Networks · LLM · AIBT")
        self.assertEqual(result["target_kind"], "exact_text")
        self.assertEqual(result["bbox_match_mode"], "exact_text_inside_large_element")
        self.assertEqual(result["matched_word_count"], 2)

    def test_near_exact_visual_element_match(self) -> None:
        result = self.resolver.resolve(
            document_id="doc_test",
            page_number=2,
            selected_bbox=[0.205, 0.205, 0.39, 0.29],
            page_parse={"blocks": []},
            page_elements=[
                {
                    "element_id": "table_1",
                    "element_type": "table",
                    "label": "Result table",
                    "bbox": [0.2, 0.2, 0.4, 0.3],
                }
            ],
        )

        self.assertEqual(result["target_kind"], "page_element")
        self.assertEqual(result["target_type"], "table")
        self.assertEqual(result["bbox_match_mode"], "near_exact_element_match")
        self.assertTrue(result["crop_needed"])

    def test_multi_element_selection(self) -> None:
        result = self.resolver.resolve(
            document_id="doc_test",
            page_number=3,
            selected_bbox=[0.1, 0.1, 0.55, 0.12],
            page_parse={
                "blocks": [
                    {
                        "block_id": "p3_b1",
                        "block_type": "paragraph",
                        "text": "CNN GNN",
                        "bbox": [0.1, 0.1, 0.55, 0.12],
                        "reading_order": 1,
                        "words": [
                            {"text": "CNN", "bbox": [0.12, 0.13, 0.1, 0.04], "reading_order": 1},
                            {"text": "GNN", "bbox": [0.47, 0.13, 0.1, 0.04], "reading_order": 2},
                        ],
                    }
                ]
            },
            page_elements=[
                {"element_id": "cnn", "element_type": "text", "label": "CNN", "bbox": [0.1, 0.1, 0.2, 0.12]},
                {"element_id": "gnn", "element_type": "text", "label": "GNN", "bbox": [0.45, 0.1, 0.2, 0.12]},
            ],
        )

        self.assertIn(result["target_kind"], {"multi_element", "mixed"})
        self.assertEqual(result["bbox_match_mode"], "multi_element_selection")
        self.assertEqual(result["matched_element_ids"], ["cnn", "gnn"])

    def test_visual_selection_without_text(self) -> None:
        result = self.resolver.resolve(
            document_id="doc_test",
            page_number=4,
            selected_bbox=[0.25, 0.25, 0.2, 0.2],
            page_parse={"blocks": []},
            page_elements=[
                {"element_id": "fig_1", "element_type": "figure", "label": "Architecture figure", "bbox": [0.2, 0.2, 0.5, 0.5]}
            ],
        )

        self.assertEqual(result["target_kind"], "visual_crop")
        self.assertEqual(result["bbox_match_mode"], "visual_crop")
        self.assertTrue(result["crop_needed"])
        self.assertEqual(result["crop_bbox"], [0.25, 0.25, 0.2, 0.2])

    def test_selection_schema_normalizes_old_fields_and_removes_key_detail(self) -> None:
        payload = {
            "document_id": "doc_test",
            "page_number": 1,
            "selection_id": "sel_test",
            "anchor_id": "sel_test",
            "concept_title": "Neural Networks",
            "label": "Neural Networks",
            "anchor_type": "text",
            "bbox": [0.1, 0.1, 0.2, 0.1],
            "selected_bbox": [0.1, 0.1, 0.2, 0.1],
            "question": "What are Neural Networks?",
            "short_explanation": "Neural Networks are model structures.",
            "long_explanation": "Neural Networks are model structures used in Deep Learning.",
            "prerequisite": "",
            "related_pages": [],
            "confidence": 0.8,
            "study_importance": "This supports the slide's learning path.",
            "meaning_in_context": "Here it bridges Deep Learning and LLMs.",
            "why_it_matters_here": "It helps explain the Week 4 concept chain.",
            "key_concept_detail": "Do not render this as a separate section.",
            "related_concepts_and_pages": [],
            "source_cues": [],
            "explanation_mode": "selection",
        }

        result = validate_payload("selection_explanation", payload)

        self.assertEqual(result["what_this_is"], "Do not render this as a separate section.")
        self.assertEqual(result["what_it_means_here"], "Here it bridges Deep Learning and LLMs.")
        self.assertEqual(result["study_importance"]["importance_level"], "medium")
        self.assertEqual(result["study_importance"]["focus_type"], "background_context")
        self.assertIn("This supports", result["study_importance"]["reason"])
        self.assertNotIn("key_concept_detail", result)
        self.assertNotIn("why_it_matters_here", result)
        self.assertNotIn("meaning_in_context", result)


if __name__ == "__main__":
    unittest.main()
