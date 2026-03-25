from __future__ import annotations

import argparse
import json

from app.services.document_synthesizer import DocumentSynthesizer


def main() -> None:
    parser = argparse.ArgumentParser(description="Run document synthesis from stored pass1 artifacts.")
    parser.add_argument("document_id", help="Document identifier returned by POST /api/documents")
    args = parser.parse_args()

    summary = DocumentSynthesizer().synthesize_document(args.document_id)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if summary["synthesis_status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
