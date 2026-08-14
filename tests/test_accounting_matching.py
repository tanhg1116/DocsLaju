import io

from app import app
from src.accounting.matching import build_purchase_match_groups
from src.mistral_client import MODEL_DEFAULT
from src.services.database import ADMIN_USER_ID


def _row(document_id, profile, data):
    return {
        "document_id": document_id,
        "document_name": f"{document_id}.pdf",
        "session_id": "session-1",
        "session_title": "August close",
        "profile": profile,
        "status": "needs_review",
        "data": data,
    }


def _purchase_chain(*, delivered_quantity=10, invoice_supplier="Office Supply Sdn Bhd"):
    quotation = _row("quote", "quotation", {
        "quotation_number": "QT-100",
        "supplier_name": "Office Supply Sdn Bhd",
        "currency": "MYR",
        "total_amount": 530.00,
        "quoted_items": [{"item_code": "CHR-1", "description": "Office chairs", "quantity": 10}],
    })
    purchase_order = _row("po", "purchase_order", {
        "purchase_order_number": "PO-200",
        "quotation_number": "QT-100",
        "supplier_name": "Office Supply Sdn Bhd",
        "currency": "MYR",
        "total_amount": 530.00,
        "line_items": [{"item_code": "CHR-1", "description": "Office chairs", "quantity": 10}],
    })
    delivery = _row("delivery", "delivery_order", {
        "delivery_order_number": "DO-300",
        "purchase_order_number": "PO-200",
        "supplier_name": "Office Supply Sdn Bhd",
        "delivered_items": [{
            "item_code": "CHR-1", "description": "Office chair", "quantity": delivered_quantity,
        }],
    })
    invoice = _row("invoice", "invoice", {
        "invoice_number": "INV-400",
        "purchase_order_number": "PO-200",
        "supplier_name": invoice_supplier,
        "currency": "MYR",
        "total_amount": 530.00,
        "line_items": [{"item_code": "CHR-1", "description": "Office chairs", "quantity": 10}],
    })
    return [quotation, purchase_order, delivery, invoice]


def _settled_purchase_chain(*, payment_amount=500.0, payment_status="Successful"):
    rows = _purchase_chain()
    rows.append(_row("credit", "credit_note", {
        "supplier_name": "Office Supply Sdn Bhd",
        "credit_note_number": "CN-500",
        "invoice_number": "INV-400",
        "currency": "MYR",
        "total_amount": 30.0,
        "line_items": [{"item_code": "CHR-1", "description": "Office chair credit", "quantity": 1}],
    }))
    rows.append(_row("payment", "payment_confirmation", {
        "payment_reference": "PAY-600",
        "payee_name": "Office Supply Sdn Bhd",
        "invoice_number": "INV-400",
        "credit_note_number": "CN-500",
        "currency": "MYR",
        "amount": payment_amount,
        "payment_status": payment_status,
    }))
    return rows


def test_three_way_match_explains_quantity_mismatch():
    group = build_purchase_match_groups(_purchase_chain(delivered_quantity=9))[0]

    assert group["anchor"]["reference"] == "PO-200"
    assert {link["relationship"] for link in group["links"]} == {
        "quotation_to_purchase_order",
        "purchase_order_to_delivery",
        "purchase_order_to_invoice",
    }
    assert group["status"] == "needs_review"
    assert group["issues"][0]["code"] == "delivery_quantity_mismatch"
    assert group["checks"][0]["ordered_quantity"] == 10.0
    assert group["checks"][0]["delivered_quantity"] == 9.0


def test_confirmed_links_form_a_matched_bundle_and_rejected_links_are_excluded():
    rows = _purchase_chain()
    decisions = [
        {
            "anchor_document_id": "po",
            "related_document_id": document_id,
            "relationship_type": relationship,
            "decision": "confirmed",
            "note": None,
        }
        for document_id, relationship in (
            ("quote", "quotation_to_purchase_order"),
            ("delivery", "purchase_order_to_delivery"),
            ("invoice", "purchase_order_to_invoice"),
        )
    ]
    group = build_purchase_match_groups(rows, decisions)[0]
    assert group["status"] == "awaiting_payment"
    assert all(link["decision"] == "confirmed" for link in group["links"])

    decisions[-1]["decision"] = "rejected"
    rejected = build_purchase_match_groups(rows, decisions)[0]
    assert "invoice" in rejected["missing_profiles"]
    assert all(link["document"]["document_id"] != "invoice" for link in rejected["links"])
    assert rejected["rejected_links"][0]["document"]["document_id"] == "invoice"


def test_partial_deliveries_accumulate_before_quantity_comparison():
    rows = _purchase_chain(delivered_quantity=6)
    rows.append(_row("delivery-2", "delivery_order", {
        "delivery_order_number": "DO-301",
        "purchase_order_number": "PO-200",
        "supplier_name": "Office Supply Sdn Bhd",
        "delivered_items": [{
            "item_code": "CHR-1", "description": "Office chairs", "quantity": 4,
        }],
    }))

    group = build_purchase_match_groups(rows)[0]
    delivery_check = next(
        check for check in group["checks"]
        if check["code"].startswith("purchase_order_to_delivery_item")
    )

    assert delivery_check["status"] == "matches"
    assert delivery_check["delivered_quantity"] == 10.0
    assert delivery_check["source_document_count"] == 2
    assert group["bundle"]["delivery_count"] == 2
    assert group["bundle"]["fulfilled_line_count"] == 1
    assert group["status"] == "suggested"


def test_invoice_value_and_unit_price_variances_are_explained():
    rows = _purchase_chain()
    invoice = next(row for row in rows if row["profile"] == "invoice")
    invoice["data"]["total_amount"] = 550.0
    invoice["data"]["line_items"][0]["unit_price"] = 55.0
    po = next(row for row in rows if row["profile"] == "purchase_order")
    po["data"]["line_items"][0]["unit_price"] = 53.0

    group = build_purchase_match_groups(rows)[0]
    issue_codes = {issue["code"] for issue in group["issues"]}
    total_check = next(
        check for check in group["checks"]
        if check["code"] == "purchase_order_to_invoice_total"
    )

    assert {"invoice_total_mismatch", "invoice_unit_price_mismatch"} <= issue_codes
    assert total_check["status"] == "mismatch"
    assert total_check["difference"] == 20.0
    assert group["bundle"]["invoice_total_difference"] == 20.0
    assert group["status"] == "needs_review"


def test_credit_note_and_payment_complete_the_settlement_bundle():
    rows = _settled_purchase_chain()
    group = build_purchase_match_groups(rows)[0]
    relationships = {link["relationship"] for link in group["links"]}
    settlement_checks = {check["code"]: check for check in group["checks"]}

    assert {
        "purchase_order_to_credit_note", "purchase_order_to_payment"
    } <= relationships
    assert group["bundle"]["credit_note_count"] == 1
    assert group["bundle"]["payment_count"] == 1
    assert group["bundle"]["credit_total"] == 30.0
    assert group["bundle"]["net_payable"] == 500.0
    assert group["bundle"]["payment_total"] == 500.0
    assert group["bundle"]["outstanding_balance"] == 0.0
    assert group["bundle"]["settlement_status"] == "paid"
    assert settlement_checks["net_payable"]["result"] == 500.0
    assert settlement_checks["outstanding_balance"]["status"] == "matches"
    assert group["status"] == "suggested"


def test_partial_and_unconfirmed_payments_do_not_hide_balance_risk():
    partial = build_purchase_match_groups(
        _settled_purchase_chain(payment_amount=450.0)
    )[0]
    assert partial["bundle"]["outstanding_balance"] == 50.0
    assert partial["bundle"]["settlement_status"] == "partially_paid"
    assert "outstanding_balance" in {issue["code"] for issue in partial["issues"]}

    unconfirmed = build_purchase_match_groups(
        _settled_purchase_chain(payment_status="Pending")
    )[0]
    assert unconfirmed["bundle"]["payment_total"] is None
    assert unconfirmed["bundle"]["outstanding_balance"] is None
    assert "payment_status_unresolved" in {
        issue["code"] for issue in unconfirmed["issues"]
    }


def test_furnipro_acceptance_chain_keeps_delivery_issue_but_reconciles_payment():
    rows = _settled_purchase_chain(payment_amount=3218.40)
    for row in rows:
        if row["profile"] in {"quotation", "purchase_order", "invoice"}:
            row["data"]["total_amount"] = 3564.0
        if row["profile"] == "delivery_order":
            row["data"]["delivered_items"][0]["quantity"] = 9
        if row["profile"] == "credit_note":
            row["data"]["total_amount"] = 345.60

    group = build_purchase_match_groups(rows)[0]

    assert group["bundle"]["net_payable"] == 3218.40
    assert group["bundle"]["payment_total"] == 3218.40
    assert group["bundle"]["outstanding_balance"] == 0.0
    assert group["bundle"]["settlement_status"] == "paid"
    assert "delivery_quantity_mismatch" in {
        issue["code"] for issue in group["issues"]
    }
    assert "outstanding_balance" not in {
        issue["code"] for issue in group["issues"]
    }


def test_settlement_refuses_unsafe_credit_sign_and_cross_currency_arithmetic():
    negative_rows = _settled_purchase_chain()
    next(row for row in negative_rows if row["profile"] == "credit_note")["data"]["total_amount"] = -30
    negative = build_purchase_match_groups(negative_rows)[0]
    assert negative["bundle"]["net_payable"] is None
    assert negative["bundle"]["settlement_status"] == "unresolved"
    assert "credit_note_sign_unresolved" in {
        issue["code"] for issue in negative["issues"]
    }

    currency_rows = _settled_purchase_chain()
    next(row for row in currency_rows if row["profile"] == "payment_confirmation")["data"]["currency"] = "USD"
    currency = build_purchase_match_groups(currency_rows)[0]
    assert currency["bundle"]["net_payable"] is None
    assert currency["bundle"]["outstanding_balance"] is None
    assert "settlement_currency_mismatch" in {
        issue["code"] for issue in currency["issues"]
    }


def test_match_decision_api_persists_and_cascades_when_document_is_deleted(isolated_database):
    database = app.extensions["database"]
    with app.test_client() as client:
        session_id = client.get("/api/state").get_json()["active_session_id"]
        document_ids = {}
        for profile in (
            "purchase_order", "delivery_order", "invoice", "credit_note", "payment_confirmation"
        ):
            document_ids[profile] = client.post(
                f"/api/sessions/{session_id}/files",
                data={"file": (io.BytesIO(profile.encode()), f"{profile}.png")},
                content_type="multipart/form-data",
            ).get_json()["active_document"]["id"]

        database.save_document_extraction(
            ADMIN_USER_ID, session_id, document_ids["purchase_order"], "purchase_order",
            _purchase_chain()[1]["data"], MODEL_DEFAULT,
        )
        database.save_document_extraction(
            ADMIN_USER_ID, session_id, document_ids["delivery_order"], "delivery_order",
            _purchase_chain()[2]["data"], MODEL_DEFAULT,
        )
        database.save_document_extraction(
            ADMIN_USER_ID, session_id, document_ids["invoice"], "invoice",
            _purchase_chain()[3]["data"], MODEL_DEFAULT,
        )
        settlement_rows = _settled_purchase_chain()
        database.save_document_extraction(
            ADMIN_USER_ID, session_id, document_ids["credit_note"], "credit_note",
            settlement_rows[4]["data"], MODEL_DEFAULT,
        )
        database.save_document_extraction(
            ADMIN_USER_ID, session_id, document_ids["payment_confirmation"], "payment_confirmation",
            settlement_rows[5]["data"], MODEL_DEFAULT,
        )
        suggested = client.get("/api/accounting/matches").get_json()["groups"][0]
        assert suggested["bundle"]["delivery_count"] == 1
        assert suggested["bundle"]["invoice_count"] == 1
        assert suggested["bundle"]["credit_note_count"] == 1
        assert suggested["bundle"]["payment_count"] == 1
        assert suggested["bundle"]["outstanding_balance"] == 0.0
        assert all(link["decision"] == "suggested" for link in suggested["links"])

        response = client.patch("/api/accounting/matches/decision", json={
            "anchor_document_id": document_ids["purchase_order"],
            "related_document_id": document_ids["invoice"],
            "relationship_type": "purchase_order_to_invoice",
            "decision": "confirmed",
        })
        assert response.status_code == 200
        links = response.get_json()["groups"][0]["links"]
        invoice_link = next(link for link in links if link["relationship"] == "purchase_order_to_invoice")
        assert invoice_link["decision"] == "confirmed"
        assert len(database.list_accounting_match_decisions(ADMIN_USER_ID)) == 1

        credit_response = client.patch("/api/accounting/matches/decision", json={
            "anchor_document_id": document_ids["purchase_order"],
            "related_document_id": document_ids["credit_note"],
            "relationship_type": "purchase_order_to_credit_note",
            "decision": "confirmed",
        })
        assert credit_response.status_code == 200
        assert len(database.list_accounting_match_decisions(ADMIN_USER_ID)) == 2

        client.delete(
            f"/api/sessions/{session_id}/files/{document_ids['credit_note']}"
        )
        assert len(database.list_accounting_match_decisions(ADMIN_USER_ID)) == 1

        client.delete(
            f"/api/sessions/{session_id}/files/{document_ids['invoice']}"
        )

    assert database.list_accounting_match_decisions(ADMIN_USER_ID) == []
