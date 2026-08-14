"""Run one explicit live OCR + structured-annotation compatibility check.

This script does not touch the application database and prints only normalized
tax/reconciliation metadata. It is intentionally excluded from pytest because
it consumes a real provider request.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.accounting.review import build_accounting_review  # noqa: E402
from src.extraction_templates import get_template  # noqa: E402
from src.mistral_client import MODEL_DEFAULT, ocr_document_with_annotation  # noqa: E402
from src.services.extraction_normalizer import normalize_template_data  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live Mistral structured-extraction smoke test without database writes."
    )
    parser.add_argument("document", type=Path)
    parser.add_argument("--profile", default="receipt")
    parser.add_argument("--expect-treatment", choices=("inclusive", "exclusive", "not_applicable", "unknown"))
    parser.add_argument(
        "--expect-total-match",
        action="store_true",
        help="Fail unless the reconciled document-total calculation matches.",
    )
    args = parser.parse_args()

    path = args.document.resolve()
    if not path.is_file():
        parser.error(f"Document not found: {path}")
    template = get_template(args.profile)
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    is_pdf = path.suffix.casefold() == ".pdf"
    result = ocr_document_with_annotation(
        path.read_bytes(),
        is_pdf=is_pdf,
        mime_type=mime_type,
        schema_name=f"{template['id']}_v{template['schema_version']}_smoke",
        schema=template["schema"],
        prompt=template["prompt"],
        model=MODEL_DEFAULT,
    )
    normalized = normalize_template_data(result.document_annotation, template)
    pages = [
        {"page_number": index, "markdown": page.markdown}
        for index, page in enumerate(result.pages, start=1)
    ]
    review = build_accounting_review(args.profile, normalized, pages=pages)
    total_calculation = next(
        (
            calculation
            for calculation in review["calculations"]
            if calculation["code"].startswith("document_total")
        ),
        None,
    )
    output = {
        "model": MODEL_DEFAULT,
        "profile": args.profile,
        "schema_version": template["schema_version"],
        "page_count": len(result.pages),
        "annotation": {
            "tax_amount": normalized.get("tax_amount"),
            "tax_rate": normalized.get("tax_rate"),
            "tax_treatment": normalized.get("tax_treatment"),
            "tax_evidence": normalized.get("tax_evidence"),
            "total_amount": normalized.get("total_amount"),
        },
        "reconciliation": review["tax_analysis"],
        "suggested_fields": review["suggested_fields"],
        "total_calculation": total_calculation,
        "warning_count": review["summary"]["warning_count"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

    if not result.pages:
        print("Smoke test failed: no OCR pages returned.", file=sys.stderr)
        return 1
    if args.expect_treatment and review["tax_analysis"]["treatment"] != args.expect_treatment:
        print(
            "Smoke test failed: expected treatment "
            f"{args.expect_treatment}, got {review['tax_analysis']['treatment']}.",
            file=sys.stderr,
        )
        return 1
    if args.expect_total_match and (
        total_calculation is None or total_calculation.get("status") != "matches"
    ):
        print("Smoke test failed: document total did not reconcile.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
