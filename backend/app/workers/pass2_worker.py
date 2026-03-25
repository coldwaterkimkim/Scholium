from __future__ import annotations

import argparse
import json

from app.services.pass2_refiner import Pass2Refiner


def main() -> None:
    parser = argparse.ArgumentParser(description="Run pass2 refinement for rendered pages.")
    parser.add_argument("document_id", help="Document identifier returned by POST /api/documents")
    parser.add_argument(
        "--page-number",
        dest="page_numbers",
        action="append",
        type=int,
        help="Specific page number to refine. Repeat for multiple pages.",
    )
    args = parser.parse_args()

    summary = Pass2Refiner().refine_document(
        document_id=args.document_id,
        page_numbers=args.page_numbers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if summary["failed_pages"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
