import io

from app import app
from src.accounting.review import build_accounting_review, find_duplicate_candidates
from src.mistral_client import MODEL_DEFAULT
from src.services.database import ADMIN_USER_ID


def _invoice(**overrides):
    data = {
        "document_type": "invoice",
        "supplier_name": "FurniPro Sdn. Bhd.",
        "supplier_registration_number": "202601000001",
        "supplier_address": "Kuala Lumpur",
        "customer_name": "Laju Office Solutions Sdn. Bhd.",
        "invoice_number": "INV-2026-338",
        "invoice_date": "2026-08-05",
        "due_date": "2026-09-04",
        "currency": "MYR",
        "subtotal": 100.0,
        "discount_amount": 5.0,
        "tax_amount": 7.6,
        "tax_rate": 7.6,
        "tax_treatment": "exclusive",
        "tax_evidence": "Tax excluded",
        "total_amount": 102.6,
        "payment_terms": "30 days",
        "line_items": [{
            "description": "Installation",
            "quantity": 2,
            "unit_price": 50,
            "tax_amount": 7.6,
            "amount": 100,
        }],
        "review_notes": [],
    }
    data.update(overrides)
    return data


def test_accounting_review_is_transparent_and_finds_source_page():
    source = _invoice()
    edited = _invoice(total_amount=103.0)

    review = build_accounting_review(
        "invoice",
        edited,
        source_data=source,
        pages=[{
            "page_number": 2,
            "markdown": "Invoice INV-2026-338 total MYR 103.00, Tax excluded, payment terms 30 days",
        }],
    )

    total = next(
        item
        for item in review["calculations"]
        if item["code"] == "document_total_exclusive_candidate"
    )
    assert total["formula"] == "subtotal - discount + tax"
    assert total["result"] == 102.6
    assert total["document_value"] == 103.0
    assert total["status"] == "candidate"
    assert {item["code"] for item in review["issues"]} >= {"subtotal_basis_unknown"}
    assert review["provenance"]["total_amount"]["kind"] == "user_edited"
    assert review["provenance"]["total_amount"]["source_page"] == 2


def test_duplicate_score_explains_its_signals():
    duplicates = find_duplicate_candidates(
        "invoice",
        _invoice(),
        [{
            "document_id": "other",
            "document_name": "copy.pdf",
            "session_id": "session-2",
            "session_title": "August close",
            "data": _invoice(),
        }],
    )

    assert duplicates[0]["score"] == 0.99
    assert "same document reference" in duplicates[0]["reasons"]
    assert "same total" in duplicates[0]["reasons"]


def test_tax_inclusive_receipt_uses_verified_wording_without_adding_tax_twice():
    receipt = {
        "document_type": "receipt",
        "merchant_name": "Berghotel Grosse Scheidegg",
        "receipt_number": "4572",
        "transaction_datetime": "2007-07-30T13:29:17",
        "currency": "CHF",
        "subtotal": 54.50,
        "discount_amount": 0,
        "tax_amount": 3.85,
        "tax_rate": 7.6,
        "tax_treatment": "inclusive",
        "tax_evidence": "Incl. 7.6% MwSt",
        "total_amount": 54.50,
        "payment_method": None,
        "purchased_items": [
            {"description": "Latte Macchiato", "quantity": 2, "unit_price": 4.5, "amount": 9},
            {"description": "Gloki", "quantity": 1, "unit_price": 5, "amount": 5},
            {"description": "Schweisschnitzel", "quantity": 1, "unit_price": 22, "amount": 22},
            {"description": "Chacquetzli", "quantity": 1, "unit_price": 18.5, "amount": 18.5},
        ],
        "review_notes": [],
    }
    review = build_accounting_review(
        "receipt",
        receipt,
        pages=[{
            "page_number": 1,
            "markdown": "Total: CHF 54.50\nIncl. 7.6% MwSt 54.50 CHF: 3.85",
        }],
    )

    total = next(item for item in review["calculations"] if item["code"] == "document_total")
    assert total["result"] == 54.5
    assert total["status"] == "matches"
    assert "already included" in total["formula"]
    assert review["tax_analysis"] == {
        "treatment": "inclusive",
        "confidence": "high",
        "source": "source_text",
        "evidence": "Incl. 7.6% MwSt",
        "source_page": 1,
        "signals": [
            "The extracted tax wording was verified against the saved OCR text.",
            "Saved OCR text explicitly indicates tax is inclusive.",
        ],
        "subtotal_basis": "gross",
    }
    assert "total_mismatch" not in {item["code"] for item in review["issues"]}


def test_legacy_receipt_gets_non_persistent_derived_tax_field_suggestions():
    legacy_receipt = {
        "document_type": "receipt",
        "merchant_name": "Berghotel Grosse Scheidegg",
        "receipt_number": "4572",
        "transaction_datetime": "2007-07-30T13:29:17",
        "currency": "CHF",
        "subtotal": 54.50,
        "discount_amount": None,
        "tax_amount": 3.85,
        "total_amount": 54.50,
        "payment_method": "Bar",
        "purchased_items": [],
        "review_notes": [],
    }
    review = build_accounting_review(
        "receipt",
        legacy_receipt,
        source_data=legacy_receipt,
        pages=[{
            "page_number": 1,
            "markdown": "Total: CHF 54.50\nIncl. 7.6% MwSt 54.50 CHF: 3.85",
        }],
    )

    assert review["suggested_fields"] == {
        "tax_treatment": "inclusive",
        "tax_evidence": "Incl. 7.6% MwSt",
        "tax_rate": 7.6,
    }
    for field in review["suggested_fields"]:
        assert field not in legacy_receipt
        assert review["provenance"][field]["kind"] == "derived"
        assert review["provenance"][field]["label"] == "Derived from OCR"
        assert review["provenance"][field]["source_page"] == 1


def test_tax_treatment_can_be_inferred_from_arithmetic_when_wording_is_absent():
    review = build_accounting_review(
        "invoice",
        _invoice(tax_treatment="unknown", tax_evidence=None),
        pages=[{"page_number": 1, "markdown": "Subtotal 100.00 Tax 7.60 Total 102.60"}],
    )

    assert review["tax_analysis"]["treatment"] == "exclusive"
    assert review["tax_analysis"]["source"] == "arithmetic"
    total = next(item for item in review["calculations"] if item["code"] == "document_total")
    assert total["formula"] == "subtotal - discount + tax (tax added to the tax-exclusive base)"
    assert total["status"] == "matches"


def test_unknown_tax_treatment_shows_both_candidates_instead_of_false_mismatch():
    review = build_accounting_review(
        "invoice",
        _invoice(tax_treatment="unknown", tax_evidence=None, total_amount=101.0),
        pages=[{"page_number": 1, "markdown": "Subtotal 100.00 Tax 7.60 Total 101.00"}],
    )

    assert review["tax_analysis"]["treatment"] == "unknown"
    assert {item["code"] for item in review["calculations"]} >= {
        "document_total_inclusive_candidate",
        "document_total_exclusive_candidate",
    }
    assert all(
        item["status"] == "candidate"
        for item in review["calculations"]
        if item["code"].startswith("document_total")
    )
    assert "subtotal_basis_unknown" in {item["code"] for item in review["issues"]}
    assert "total_mismatch" not in {item["code"] for item in review["issues"]}


def test_tax_inclusive_total_can_start_from_a_net_subtotal():
    review = build_accounting_review(
        "invoice",
        _invoice(
            discount_amount=0,
            total_amount=107.6,
            tax_treatment="inclusive",
            tax_evidence="Tax included",
        ),
        pages=[{"page_number": 1, "markdown": "Subtotal 100.00. Tax included: 7.60. Total 107.60"}],
    )

    assert review["tax_analysis"]["treatment"] == "inclusive"
    assert review["tax_analysis"]["confidence"] == "high"
    assert review["tax_analysis"]["subtotal_basis"] == "net"
    total = next(item for item in review["calculations"] if item["code"] == "document_total")
    assert total["status"] == "matches"
    assert "included tax added to the net base" in total["formula"]
    assert review["summary"]["warning_count"] == 0


def test_zero_tax_ignores_inclusive_and_exclusive_column_headings():
    review = build_accounting_review(
        "invoice",
        _invoice(
            discount_amount=0,
            tax_amount=0,
            total_amount=100,
            tax_treatment="unknown",
            tax_evidence=None,
        ),
        pages=[{
            "page_number": 1,
            "markdown": "Description | Total Excl. Tax | TaxAmt | Total Incl. Tax\nItem | 100.00 | 0.00 | 100.00",
        }],
    )

    assert review["tax_analysis"]["treatment"] == "not_applicable"
    assert review["tax_analysis"]["source"] == "arithmetic"
    assert review["summary"]["warning_count"] == 0


def test_arithmetic_breaks_tie_between_conflicting_tax_column_headings():
    review = build_accounting_review(
        "invoice",
        _invoice(
            discount_amount=0,
            total_amount=107.6,
            tax_treatment="unknown",
            tax_evidence=None,
        ),
        pages=[{
            "page_number": 1,
            "markdown": "Description | Total Excl. Tax | TaxAmt | Total Incl. Tax\nItem | 100.00 | 7.60 | 107.60",
        }],
    )

    assert review["tax_analysis"]["treatment"] == "exclusive"
    assert review["tax_analysis"]["source"] == "arithmetic"
    assert review["summary"]["warning_count"] == 0


def test_extraction_revisions_drive_provenance_and_review_queue(isolated_database):
    database = app.extensions["database"]
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        first = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"first-image"), "invoice-one.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]
        second = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"second-image"), "invoice-copy.png")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]

        for document_id in (first, second):
            database.save_page_markdown(
                document_id,
                1,
                "Invoice INV-2026-338 total MYR 102.60",
            )
            database.save_document_extraction(
                ADMIN_USER_ID,
                session_id,
                document_id,
                "invoice",
                _invoice(),
                MODEL_DEFAULT,
                origin="model",
            )

        edited = _invoice(total_amount=103.0)
        database.save_document_extraction(
            ADMIN_USER_ID,
            session_id,
            second,
            "invoice",
            edited,
            MODEL_DEFAULT,
            origin="user",
        )

        payload = client.get(
            f"/api/sessions/{session_id}/files/{second}/extractions/invoice"
        ).get_json()
        queue = client.get("/api/accounting/review-queue").get_json()

    assert payload["extraction"]["revision"] == 2
    assert payload["extraction"]["last_origin"] == "user"
    assert payload["accounting_review"]["provenance"]["total_amount"]["kind"] == "user_edited"
    assert payload["accounting_review"]["duplicates"][0]["document_id"] == first
    assert {item["document_id"] for item in queue["items"]} == {first, second}


def test_derived_tax_suggestions_are_saved_only_after_user_accepts_them(isolated_database):
    database = app.extensions["database"]
    legacy_receipt = {
        "document_type": "receipt",
        "merchant_name": "Berghotel Grosse Scheidegg",
        "merchant_registration_number": None,
        "merchant_address": "3818 Grindelwald",
        "receipt_number": "4572",
        "transaction_datetime": "2007-07-30T13:29:17",
        "currency": "CHF",
        "subtotal": 54.50,
        "discount_amount": None,
        "tax_amount": 3.85,
        "total_amount": 54.50,
        "payment_method": "Bar",
        "purchased_items": [],
        "review_notes": [],
    }
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_id = client.post(
            f"/api/sessions/{session_id}/files",
            data={"file": (io.BytesIO(b"legacy-receipt"), "receipt.jpg")},
            content_type="multipart/form-data",
        ).get_json()["active_document"]["id"]
        database.save_page_markdown(
            document_id,
            1,
            "Total: CHF 54.50\nIncl. 7.6% MwSt 54.50 CHF: 3.85",
        )
        database.save_document_extraction(
            ADMIN_USER_ID,
            session_id,
            document_id,
            "receipt",
            legacy_receipt,
            MODEL_DEFAULT,
            schema_version=1,
            origin="model",
        )
        endpoint = f"/api/sessions/{session_id}/files/{document_id}/extractions/receipt"

        before = client.get(endpoint).get_json()
        stored_before = database.get_document_extraction(
            ADMIN_USER_ID, session_id, document_id, "receipt"
        )
        accepted_data = {
            **before["extraction"]["data"],
            **before["accounting_review"]["suggested_fields"],
        }
        after = client.patch(
            endpoint,
            json={"data": accepted_data, "status": "needs_review"},
        ).get_json()

    assert "tax_treatment" not in stored_before["data"]
    assert before["accounting_review"]["suggested_fields"]["tax_treatment"] == "inclusive"
    assert after["extraction"]["data"]["tax_treatment"] == "inclusive"
    assert after["extraction"]["data"]["tax_rate"] == 7.6
    assert after["extraction"]["data"]["tax_evidence"] == "Incl. 7.6% MwSt"
    assert after["extraction"]["last_origin"] == "user"
    assert after["accounting_review"]["suggested_fields"] == {}
    assert after["accounting_review"]["provenance"]["tax_treatment"]["kind"] == "user_edited"
