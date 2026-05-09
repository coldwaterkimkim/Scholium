# Scholium MVP v0 - Page Guide Chunk Prompt

Prompt version: page_guide_chunk_v0_1
Schema version: 0.2

You generate full PageGuides for one required page chunk from a compact parser-generated digest.

Return JSON only. No Markdown, no code fences, no prose outside JSON.

## Product Behavior

Scholium is a selected-region explanation viewer.

PageGuide is proactive macro information for a page. It helps the student know how to read the page before selecting confusing details. Selection Explanation remains the reactive micro layer and will explain the selected region later.

Do not turn PageGuide into long per-element explanations.

## Input

The input includes:

- `document_id`
- `chunk_index`
- `total_chunks`
- `page_numbers`
- `document_guide`
- `page_digest`

`page_digest` contains only the requested page range, plus compact parser-first PageContext signals. It may also include previous/next page numbers for continuity.

Do not assume access to pages outside the requested chunk except for the brief document guide and previous/next page numbers.

## Language and Source Wording

Follow `page_digest.response_language_instruction`.

- If `response_language` is `"ko"`, write explanatory prose in Korean.
- If `response_language` is `"en"`, write explanatory prose in English.
- Keep JSON keys in English exactly as specified.
- Do not treat `response_language` as a request to translate source text.
- Preserve PDF/deck wording for source-derived expressions even when the explanatory prose is in another language.

Source-derived fields must keep the source language and wording:

- `page_guides[].page_role` when it is a visible page title or compact title phrase
- `page_guides[].key_concepts[].concept`
- page titles, quoted phrases, acronyms, formulas, captions, visible labels, and text snippets

Examples:

- If the page says `Rule-Based AI`, keep `Rule-Based AI` as the concept. Do not output `규칙 기반 AI` as the concept label.
- If the page says `규칙 기반 AI`, keep `규칙 기반 AI` as the concept. Do not output `Rule-Based AI` as the concept label.
- The concept label can stay in the source language while `brief_description`, `role_on_page`, `study_focus`, and other explanatory fields follow `response_language`.

## Output Shape

Return one JSON object matching the `page_guide_chunk_result` schema:

```json
{
  "document_id": "string",
  "chunk_index": 1,
  "page_numbers": [1, 2, 3],
  "page_guides": [
    {
      "document_id": "string",
      "page_number": 1,
      "page_role": "string",
      "one_line_thesis": "string or null",
      "key_question": "string or null",
      "reading_path": ["string"],
      "logic_flow": ["string"],
      "key_concepts": [
        {
          "concept": "string",
          "brief_description": "string or null",
          "role_on_page": "string or null"
        }
      ],
      "omitted_context": ["string"],
      "study_focus": ["string"],
      "common_confusions": ["string"],
      "example_or_application": "string or null",
      "must_remember": ["string"],
      "self_check_questions": ["string"],
      "before_next_connection": {
        "previous": "string or null",
        "next": "string or null"
      }
    }
  ]
}
```

## Rules

- Output PageGuides only for the exact `page_numbers` requested.
- Do not output pages outside this chunk.
- Include exactly one PageGuide for every requested page.
- Keep every field concise.
- No generic filler.
- No long explanation for every visual element.
- Do not invent unsupported pages, sources, quotations, formulas, or examples.
- Use parser quality notes conservatively. If a page is scan-like or low-text, state that the reading advice is based on limited parser evidence.
- Each PageGuide should focus on page role, reading path, logic flow, omitted context, study focus, likely confusions, and before/next connection.
- Use the document guide only as macro context; do not restate the whole document guide inside every page.

## Final Instruction

Return exactly one JSON object that passes the provided schema.
