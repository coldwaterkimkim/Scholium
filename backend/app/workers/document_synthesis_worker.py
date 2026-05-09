from __future__ import annotations

import argparse
import json

from app.core.config import get_settings
from app.services.document_synthesizer import DocumentSynthesizer
from app.services.semantic_guide_generator import SemanticGuideGenerator


def main() -> None:
    parser = argparse.ArgumentParser(description="Run document synthesis from stored pass1 artifacts.")
    parser.add_argument("document_id", help="Document identifier returned by POST /api/documents")
    args = parser.parse_args()

    settings = get_settings()
    if settings.pass1_mode == "legacy_llm":
        summary = DocumentSynthesizer().synthesize_document(args.document_id)
    else:
        summary = SemanticGuideGenerator().generate_document(args.document_id)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if summary["synthesis_status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
