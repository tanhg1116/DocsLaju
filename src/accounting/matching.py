"""Deterministic document-chain matching for accounts-payable review.

Suggestions are derived from explicit references and transparent secondary
signals. This module never confirms a relationship or changes accounting data.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


MATCH_THRESHOLD = Decimal("0.60")
MONEY_TOLERANCE = Decimal("0.02")
MATCH_RELATIONSHIPS = frozenset({
    "quotation_to_purchase_order",
    "purchase_order_to_delivery",
    "purchase_order_to_invoice",
    "purchase_order_to_credit_note",
    "purchase_order_to_payment",
})


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _tokens(value: Any) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(value or "").casefold())
    ignored = {"the", "and", "of", "item", "items", "unit", "units", "delivered"}
    return {
        word[:-1] if len(word) > 4 and word.endswith("s") else word
        for word in words
        if word not in ignored
    }


def _token_similarity(left: Any, right: Any) -> Decimal:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return Decimal("0")
    return Decimal(len(left_tokens & right_tokens)) / Decimal(len(left_tokens | right_tokens))


def _same(left: Any, right: Any) -> bool:
    return bool(_normalized(left)) and _normalized(left) == _normalized(right)


def _same_money(left: Any, right: Any) -> bool:
    left_value = _decimal(left)
    right_value = _decimal(right)
    return (
        left_value is not None
        and right_value is not None
        and abs(left_value - right_value) <= MONEY_TOLERANCE
    )


def _document_summary(row: dict) -> dict:
    data = row.get("data") or {}
    profile = str(row.get("profile") or "")
    reference_fields = {
        "quotation": "quotation_number",
        "purchase_order": "purchase_order_number",
        "delivery_order": "delivery_order_number",
        "invoice": "invoice_number",
        "credit_note": "credit_note_number",
        "payment_confirmation": "payment_reference",
    }
    return {
        "document_id": row["document_id"],
        "document_name": row["document_name"],
        "session_id": row["session_id"],
        "session_title": row["session_title"],
        "profile": profile,
        "reference": data.get(reference_fields.get(profile, "")),
        "status": row.get("status", "needs_review"),
    }


def _item_rows(profile: str, data: dict) -> list[dict]:
    key = {
        "quotation": "quoted_items",
        "purchase_order": "line_items",
        "delivery_order": "delivered_items",
        "invoice": "line_items",
        "credit_note": "line_items",
    }.get(profile)
    return list(data.get(key) or []) if key else []


def _line_overlap(anchor: dict, candidate: dict) -> Decimal:
    anchor_rows = _item_rows(str(anchor["profile"]), anchor.get("data") or {})
    candidate_rows = _item_rows(str(candidate["profile"]), candidate.get("data") or {})
    if not anchor_rows or not candidate_rows:
        return Decimal("0")
    matched = 0
    for source in anchor_rows:
        source_code = source.get("item_code")
        source_description = source.get("description")
        if any(
            (_same(source_code, target.get("item_code")) if source_code else False)
            or _token_similarity(source_description, target.get("description")) >= Decimal("0.60")
            for target in candidate_rows
        ):
            matched += 1
    return Decimal(matched) / Decimal(len(anchor_rows))


def _score_link(
    relationship: str,
    purchase_order: dict,
    candidate: dict,
    context: dict[str, Any] | None = None,
) -> dict:
    po_data = purchase_order.get("data") or {}
    other = candidate.get("data") or {}
    context = context or {}
    score = Decimal("0")
    reasons: list[str] = []

    direct_reference_fields = {
        "quotation_to_purchase_order": ("quotation_number", "quotation_number"),
        "purchase_order_to_delivery": ("purchase_order_number", "purchase_order_number"),
        "purchase_order_to_invoice": ("purchase_order_number", "purchase_order_number"),
    }
    if relationship in direct_reference_fields:
        reference_field = direct_reference_fields[relationship]
        po_reference = po_data.get(reference_field[0])
        other_reference = other.get(reference_field[1])
        reference_weight = Decimal("0.60") if relationship == "quotation_to_purchase_order" else Decimal("0.65")
        if _same(po_reference, other_reference):
            score += reference_weight
            reasons.append(f"explicit reference {po_reference}")
    elif relationship == "purchase_order_to_credit_note":
        invoice_references = context.get("invoice_references", set())
        if any(_same(other.get("invoice_number"), reference) for reference in invoice_references):
            score += Decimal("0.65")
            reasons.append(f"invoice reference {other.get('invoice_number')}")
    elif relationship == "purchase_order_to_payment":
        invoice_references = context.get("invoice_references", set())
        credit_references = context.get("credit_references", set())
        if any(_same(other.get("invoice_number"), reference) for reference in invoice_references):
            score += Decimal("0.55")
            reasons.append(f"invoice reference {other.get('invoice_number')}")
        if any(_same(other.get("credit_note_number"), reference) for reference in credit_references):
            score += Decimal("0.15")
            reasons.append(f"credit-note reference {other.get('credit_note_number')}")

    candidate_party = (
        other.get("payee_name")
        if relationship == "purchase_order_to_payment"
        else other.get("supplier_name")
    )
    if _same(po_data.get("supplier_name"), candidate_party):
        score += Decimal("0.15")
        reasons.append("same supplier")
    if relationship != "purchase_order_to_delivery" and _same(
        po_data.get("currency"), other.get("currency")
    ):
        score += Decimal("0.10")
        reasons.append("same currency")
    if relationship in {"quotation_to_purchase_order", "purchase_order_to_invoice"} and _same_money(
        po_data.get("total_amount"), other.get("total_amount")
    ):
        score += Decimal("0.15")
        reasons.append("same total")

    if relationship == "purchase_order_to_payment" and _same_money(
        context.get("net_payable"), other.get("amount")
    ):
        score += Decimal("0.10")
        reasons.append("amount equals calculated net payable")

    overlap = _line_overlap(purchase_order, candidate)
    line_weight = Decimal("0.20") if relationship == "purchase_order_to_delivery" else Decimal("0.10")
    if overlap:
        score += line_weight * overlap
        reasons.append(f"{int(overlap * 100)}% item overlap")

    return {
        "relationship": relationship,
        "score": float(min(score, Decimal("0.99"))),
        "reasons": reasons,
        "document": _document_summary(candidate),
    }


def _candidate_links(
    relationship: str,
    purchase_order: dict,
    candidates: Iterable[dict],
    decisions: dict[tuple[str, str, str], dict],
    *,
    multiple: bool,
    context: dict[str, Any] | None = None,
) -> list[dict]:
    scored: list[dict] = []
    anchor_id = str(purchase_order["document_id"])
    for candidate in candidates:
        item = _score_link(relationship, purchase_order, candidate, context)
        decision = decisions.get((anchor_id, str(candidate["document_id"]), relationship))
        item["decision"] = decision["decision"] if decision else "suggested"
        item["decision_note"] = decision.get("note") if decision else None
        if item["decision"] == "rejected":
            continue
        if item["decision"] == "confirmed" or Decimal(str(item["score"])) >= MATCH_THRESHOLD:
            scored.append(item)
    scored.sort(
        key=lambda item: (item["decision"] == "confirmed", item["score"]),
        reverse=True,
    )
    return scored if multiple else scored[:1]


def _matching_item(source: dict, candidates: list[dict]) -> dict | None:
    source_code = source.get("item_code")
    ranked = [
        (
            Decimal("1") if source_code and _same(source_code, target.get("item_code"))
            else _token_similarity(source.get("description"), target.get("description")),
            target,
        )
        for target in candidates
    ]
    score, row = max(ranked, key=lambda item: item[0], default=(Decimal("0"), None))
    return row if row is not None and score >= Decimal("0.60") else None


def _quantity_checks(
    purchase_order: dict,
    related_documents: list[dict],
    relationship: str,
) -> tuple[list[dict], list[dict]]:
    po_rows = _item_rows("purchase_order", purchase_order.get("data") or {})
    checks: list[dict] = []
    issues: list[dict] = []
    related_profile = (
        str(related_documents[0]["profile"]) if related_documents
        else "delivery_order" if relationship == "purchase_order_to_delivery"
        else "invoice"
    )
    quantity_label = "delivered" if related_profile == "delivery_order" else "invoiced"
    for index, ordered_item in enumerate(po_rows, start=1):
        matches = [
            (related, _matching_item(
                ordered_item,
                _item_rows(str(related["profile"]), related.get("data") or {}),
            ))
            for related in related_documents
        ]
        matches = [(related, item) for related, item in matches if item is not None]
        ordered = _decimal(ordered_item.get("quantity"))
        quantities = [_decimal(item.get("quantity")) for _, item in matches]
        actual = sum((value for value in quantities if value is not None), Decimal("0"))
        has_actual = any(value is not None for value in quantities)
        description = ordered_item.get("description") or ordered_item.get("item_code") or f"item {index}"
        if not matches or ordered is None or not has_actual:
            checks.append({
                "code": f"{relationship}_item_{index}",
                "label": str(description),
                "status": "unresolved",
                "detail": f"Could not compare ordered and {quantity_label} quantities.",
            })
            issues.append({
                "code": f"{quantity_label}_quantity_unresolved",
                "severity": "warning",
                "title": f"{quantity_label.title()} quantity unresolved",
                "detail": f"{description}: a reliable quantity comparison could not be made.",
                "documents": [purchase_order["document_id"]] + [
                    related["document_id"] for related in related_documents
                ],
            })
            continue
        quantity_matches = ordered == actual
        checks.append({
            "code": f"{relationship}_item_{index}",
            "label": str(description),
            "status": "matches" if quantity_matches else "mismatch",
            "ordered_quantity": float(ordered),
            f"{quantity_label}_quantity": float(actual),
            "difference": float(actual - ordered),
            "source_document_count": len(matches),
        })
        if not quantity_matches:
            issues.append({
                "code": "delivery_quantity_mismatch" if related_profile == "delivery_order" else "invoice_quantity_mismatch",
                "severity": "warning",
                "title": f"{quantity_label.title()} quantity differs",
                "detail": f"{description}: ordered {ordered}, {quantity_label} {actual}.",
                "documents": [purchase_order["document_id"]] + [
                    related["document_id"] for related, _ in matches
                ],
            })
        if related_profile == "invoice":
            ordered_price = _decimal(ordered_item.get("unit_price"))
            for related, item in matches:
                invoice_price = _decimal(item.get("unit_price"))
                if (
                    ordered_price is not None
                    and invoice_price is not None
                    and abs(invoice_price - ordered_price) > MONEY_TOLERANCE
                ):
                    issues.append({
                        "code": "invoice_unit_price_mismatch",
                        "severity": "warning",
                        "title": "Invoiced unit price differs",
                        "detail": (
                            f"{description}: PO unit price {ordered_price}, "
                            f"invoice unit price {invoice_price}."
                        ),
                        "documents": [purchase_order["document_id"], related["document_id"]],
                    })
    return checks, issues


def _invoice_total_check(
    purchase_order: dict,
    invoices: list[dict],
) -> tuple[dict, list[dict]]:
    po_data = purchase_order.get("data") or {}
    ordered_total = _decimal(po_data.get("total_amount"))
    invoice_totals = [
        _decimal((invoice.get("data") or {}).get("total_amount"))
        for invoice in invoices
    ]
    known_totals = [value for value in invoice_totals if value is not None]
    check = {
        "code": "purchase_order_to_invoice_total",
        "label": "Invoice total versus purchase order",
        "status": "unresolved",
        "formula": "sum(invoice totals) - purchase order total",
        "purchase_order_total": float(ordered_total) if ordered_total is not None else None,
        "invoice_total": float(sum(known_totals, Decimal("0"))) if known_totals else None,
        "difference": None,
        "tolerance": float(MONEY_TOLERANCE),
        "source_document_count": len(invoices),
    }
    po_currency = _normalized(po_data.get("currency"))
    conflicting_currency = any(
        po_currency
        and _normalized((invoice.get("data") or {}).get("currency"))
        and _normalized((invoice.get("data") or {}).get("currency")) != po_currency
        for invoice in invoices
    )
    if conflicting_currency:
        return check, [{
            "code": "invoice_currency_mismatch",
            "severity": "warning",
            "title": "Invoice currency differs from purchase order",
            "detail": "Combined invoice values were not compared across different currencies.",
            "documents": [purchase_order["document_id"]] + [
                invoice["document_id"] for invoice in invoices
            ],
        }]
    if ordered_total is None or not invoices or len(known_totals) != len(invoices):
        return check, ([{
            "code": "invoice_total_unresolved",
            "severity": "warning",
            "title": "Invoice value comparison unresolved",
            "detail": "A PO or linked invoice is missing a reliable total amount.",
            "documents": [purchase_order["document_id"]] + [
                invoice["document_id"] for invoice in invoices
            ],
        }] if invoices else [])
    invoice_total = sum(known_totals, Decimal("0"))
    difference = invoice_total - ordered_total
    check["difference"] = float(difference)
    check["status"] = "matches" if abs(difference) <= MONEY_TOLERANCE else "mismatch"
    if check["status"] == "matches":
        return check, []
    return check, [{
        "code": "invoice_total_mismatch",
        "severity": "warning",
        "title": "Invoice value differs from purchase order",
        "detail": (
            f"PO total {ordered_total}; combined invoice total {invoice_total}; "
            f"difference {difference}."
        ),
        "documents": [purchase_order["document_id"]] + [
            invoice["document_id"] for invoice in invoices
        ],
    }]


def _known_money_total(documents: Iterable[dict], field: str) -> tuple[Decimal, bool]:
    values = [_decimal((document.get("data") or {}).get(field)) for document in documents]
    return sum((value for value in values if value is not None), Decimal("0")), all(
        value is not None for value in values
    )


def _completed_payment(payment: dict) -> bool:
    status = _normalized((payment.get("data") or {}).get("payment_status"))
    return status in {
        "success", "successful", "completed", "paid", "processed", "confirmed",
        "settled", "executed",
    }


def _settlement_checks(
    purchase_order: dict,
    invoices: list[dict],
    credit_notes: list[dict],
    payments: list[dict],
) -> tuple[list[dict], list[dict], dict]:
    checks: list[dict] = []
    issues: list[dict] = []
    po_currency = _normalized((purchase_order.get("data") or {}).get("currency"))
    linked_documents = [*invoices, *credit_notes, *payments]
    missing_currency = bool(linked_documents) and (
        not po_currency
        or any(
            not _normalized((document.get("data") or {}).get("currency"))
            for document in linked_documents
        )
    )
    currency_conflict = any(
        po_currency
        and _normalized((document.get("data") or {}).get("currency"))
        and _normalized((document.get("data") or {}).get("currency")) != po_currency
        for document in linked_documents
    )
    invoice_total, invoices_complete = _known_money_total(invoices, "total_amount")
    credit_total, credits_complete = _known_money_total(credit_notes, "total_amount")
    completed_payments = [payment for payment in payments if _completed_payment(payment)]
    payment_total, payments_complete = _known_money_total(completed_payments, "amount")
    negative_credit = any(
        (_decimal((credit.get("data") or {}).get("total_amount")) or Decimal("0")) < 0
        for credit in credit_notes
    )
    nonpositive_payment = any(
        (amount := _decimal((payment.get("data") or {}).get("amount"))) is not None
        and amount <= 0
        for payment in completed_payments
    )
    if negative_credit:
        credits_complete = False
        issues.append({
            "code": "credit_note_sign_unresolved",
            "severity": "warning",
            "title": "Credit-note amount sign needs review",
            "detail": "A linked credit total is negative. Confirm its magnitude before applying it.",
            "documents": [purchase_order["document_id"]] + [
                credit["document_id"] for credit in credit_notes
            ],
        })
    if nonpositive_payment:
        payments_complete = False
        issues.append({
            "code": "payment_amount_nonpositive",
            "severity": "warning",
            "title": "Completed payment amount needs review",
            "detail": "A completed linked payment has a zero or negative amount and was not used to settle the balance.",
            "documents": [purchase_order["document_id"]] + [
                payment["document_id"] for payment in completed_payments
            ],
        })

    for payment in payments:
        if not _completed_payment(payment):
            issues.append({
                "code": "payment_status_unresolved",
                "severity": "warning",
                "title": "Payment status is not confirmed",
                "detail": (
                    f"{_document_summary(payment)['reference'] or payment['document_name']} is linked, "
                    "but its status is not an explicitly completed status, so its amount is excluded."
                ),
                "documents": [purchase_order["document_id"], payment["document_id"]],
            })

    net_payable: Decimal | None = None
    outstanding: Decimal | None = None
    if missing_currency:
        issues.append({
            "code": "settlement_currency_unresolved",
            "severity": "warning",
            "title": "Settlement currency is missing",
            "detail": "Every invoice, credit, and payment needs an explicit currency before values can be combined.",
            "documents": [purchase_order["document_id"]] + [
                document["document_id"] for document in linked_documents
            ],
        })
    elif currency_conflict:
        issues.append({
            "code": "settlement_currency_mismatch",
            "severity": "warning",
            "title": "Settlement currencies differ",
            "detail": "Invoices, credits, and payments were not combined across different currencies.",
            "documents": [purchase_order["document_id"]] + [
                document["document_id"] for document in linked_documents
            ],
        })
    elif invoices and invoices_complete and credits_complete:
        net_payable = invoice_total - credit_total
        checks.append({
            "code": "net_payable",
            "label": "Net payable after credits",
            "status": "informational",
            "formula": "sum(invoice totals) - sum(credit-note totals)",
            "invoice_total": float(invoice_total),
            "credit_total": float(credit_total),
            "result": float(net_payable),
            "difference": None,
            "source_document_count": len(invoices) + len(credit_notes),
        })
        if payments:
            if len(completed_payments) == len(payments) and payments_complete:
                outstanding = net_payable - payment_total
                payment_status = (
                    "matches" if abs(outstanding) <= MONEY_TOLERANCE
                    else "mismatch"
                )
                checks.append({
                    "code": "outstanding_balance",
                    "label": "Outstanding balance",
                    "status": payment_status,
                    "formula": "net payable - sum(completed payments)",
                    "net_payable": float(net_payable),
                    "payment_total": float(payment_total),
                    "result": float(outstanding),
                    "difference": float(outstanding),
                    "source_document_count": len(completed_payments),
                })
                if outstanding > MONEY_TOLERANCE:
                    issues.append({
                        "code": "outstanding_balance",
                        "severity": "warning",
                        "title": "Payment does not fully settle the bundle",
                        "detail": f"A balance of {outstanding} remains after linked completed payments.",
                        "documents": [purchase_order["document_id"]] + [
                            payment["document_id"] for payment in completed_payments
                        ],
                    })
                elif outstanding < -MONEY_TOLERANCE:
                    issues.append({
                        "code": "payment_overage",
                        "severity": "warning",
                        "title": "Linked payments exceed net payable",
                        "detail": f"Completed payments exceed the calculated net payable by {abs(outstanding)}.",
                        "documents": [purchase_order["document_id"]] + [
                            payment["document_id"] for payment in completed_payments
                        ],
                    })
            else:
                issues.append({
                    "code": "payment_amount_unresolved",
                    "severity": "warning",
                    "title": "Payment amount comparison unresolved",
                    "detail": "A completed linked payment is missing a reliable amount.",
                    "documents": [purchase_order["document_id"]] + [
                        payment["document_id"] for payment in payments
                    ],
                })
    elif invoices:
        issues.append({
            "code": "net_payable_unresolved",
            "severity": "warning",
            "title": "Net payable could not be calculated",
            "detail": "A linked invoice or credit note is missing a reliable total.",
            "documents": [purchase_order["document_id"]] + [
                document["document_id"] for document in [*invoices, *credit_notes]
            ],
        })

    settlement_status = (
        "unresolved" if missing_currency or currency_conflict or negative_credit
        or nonpositive_payment or (invoices and net_payable is None)
        else "awaiting_payment" if net_payable is not None and not payments
        else "paid" if outstanding is not None and abs(outstanding) <= MONEY_TOLERANCE
        else "partially_paid" if outstanding is not None and outstanding > MONEY_TOLERANCE
        else "overpaid" if outstanding is not None and outstanding < -MONEY_TOLERANCE
        else "not_available"
    )
    return checks, issues, {
        "credit_total": float(credit_total) if credit_notes and credits_complete else None,
        "net_payable": float(net_payable) if net_payable is not None else None,
        "payment_total": float(payment_total) if completed_payments and payments_complete else None,
        "outstanding_balance": float(outstanding) if outstanding is not None else None,
        "settlement_status": settlement_status,
    }


def build_purchase_match_groups(
    extractions: Iterable[dict],
    decisions: Iterable[dict] = (),
) -> list[dict]:
    """Build explainable PO-centred quotation, delivery, and invoice suggestions."""
    rows = list(extractions)
    decision_map = {
        (
            str(item["anchor_document_id"]),
            str(item["related_document_id"]),
            str(item["relationship_type"]),
        ): item
        for item in decisions
    }
    purchase_orders = [row for row in rows if row.get("profile") == "purchase_order"]
    by_profile = {
        profile: [row for row in rows if row.get("profile") == profile]
        for profile in (
            "quotation", "delivery_order", "invoice", "credit_note", "payment_confirmation"
        )
    }
    groups: list[dict] = []
    for purchase_order in purchase_orders:
        anchor_id = str(purchase_order["document_id"])
        core_links = [
            *_candidate_links(
                "quotation_to_purchase_order", purchase_order,
                by_profile["quotation"], decision_map, multiple=False,
            ),
            *_candidate_links(
                "purchase_order_to_delivery", purchase_order,
                by_profile["delivery_order"], decision_map, multiple=True,
            ),
            *_candidate_links(
                "purchase_order_to_invoice", purchase_order,
                by_profile["invoice"], decision_map, multiple=True,
            ),
        ]
        rows_by_id = {str(row["document_id"]): row for row in rows}
        invoice_documents = [
            rows_by_id[str(link["document"]["document_id"])]
            for link in core_links
            if link["relationship"] == "purchase_order_to_invoice"
        ]
        invoice_references = {
            (invoice.get("data") or {}).get("invoice_number")
            for invoice in invoice_documents
            if (invoice.get("data") or {}).get("invoice_number")
        }
        credit_context = {"invoice_references": invoice_references}
        credit_links = _candidate_links(
            "purchase_order_to_credit_note", purchase_order,
            by_profile["credit_note"], decision_map, multiple=True,
            context=credit_context,
        )
        credit_documents = [
            rows_by_id[str(link["document"]["document_id"])] for link in credit_links
        ]
        credit_references = {
            (credit.get("data") or {}).get("credit_note_number")
            for credit in credit_documents
            if (credit.get("data") or {}).get("credit_note_number")
        }
        invoice_total, invoice_totals_known = _known_money_total(
            invoice_documents, "total_amount"
        )
        credit_total, credit_totals_known = _known_money_total(
            credit_documents, "total_amount"
        )
        net_payable = (
            invoice_total - credit_total
            if invoice_documents and invoice_totals_known and credit_totals_known
            else None
        )
        payment_context = {
            "invoice_references": invoice_references,
            "credit_references": credit_references,
            "net_payable": net_payable,
        }
        payment_links = _candidate_links(
            "purchase_order_to_payment", purchase_order,
            by_profile["payment_confirmation"], decision_map, multiple=True,
            context=payment_context,
        )
        links = [*core_links, *credit_links, *payment_links]
        rejected_links: list[dict] = []
        for (decision_anchor, related_id, relationship), decision in decision_map.items():
            if decision_anchor != anchor_id or decision.get("decision") != "rejected":
                continue
            related = rows_by_id.get(related_id)
            if related is None or relationship not in MATCH_RELATIONSHIPS:
                continue
            rejected_context = (
                credit_context if relationship == "purchase_order_to_credit_note"
                else payment_context if relationship == "purchase_order_to_payment"
                else None
            )
            rejected = _score_link(relationship, purchase_order, related, rejected_context)
            rejected["decision"] = "rejected"
            rejected["decision_note"] = decision.get("note")
            rejected_links.append(rejected)
        related_by_profile: dict[str, list[dict]] = {}
        for link in links:
            related_by_profile.setdefault(link["document"]["profile"], []).append(next(
                row for row in rows if row["document_id"] == link["document"]["document_id"]
            ))
        checks: list[dict] = []
        issues: list[dict] = []
        for profile, relationship in (
            ("delivery_order", "purchase_order_to_delivery"),
            ("invoice", "purchase_order_to_invoice"),
        ):
            related = related_by_profile.get(profile, [])
            if related:
                relationship_checks, relationship_issues = _quantity_checks(
                    purchase_order, related, relationship
                )
                checks.extend(relationship_checks)
                issues.extend(relationship_issues)

        invoice_total_check, invoice_total_issues = _invoice_total_check(
            purchase_order,
            related_by_profile.get("invoice", []),
        )
        if related_by_profile.get("invoice"):
            checks.append(invoice_total_check)
            issues.extend(invoice_total_issues)

        settlement_checks, settlement_issues, settlement = _settlement_checks(
            purchase_order,
            related_by_profile.get("invoice", []),
            related_by_profile.get("credit_note", []),
            related_by_profile.get("payment_confirmation", []),
        )
        checks.extend(settlement_checks)
        issues.extend(settlement_issues)

        matched_profiles = set(related_by_profile)
        missing = [
            profile
            for profile in ("delivery_order", "invoice")
            if profile not in matched_profiles
        ]
        has_suggestions = any(link["decision"] == "suggested" for link in links)
        status = (
            "needs_review" if issues
            else "incomplete" if missing
            else "suggested" if has_suggestions
            else "awaiting_payment" if settlement["settlement_status"] == "awaiting_payment"
            else "matched"
        )
        delivery_checks = [
            check for check in checks
            if check["code"].startswith("purchase_order_to_delivery_item")
        ]
        invoice_quantity_checks = [
            check for check in checks
            if check["code"].startswith("purchase_order_to_invoice_item")
        ]
        fulfilled_lines = sum(check["status"] == "matches" for check in delivery_checks)
        invoiced_lines = sum(check["status"] == "matches" for check in invoice_quantity_checks)
        invoice_difference = invoice_total_check.get("difference")
        groups.append({
            "anchor": _document_summary(purchase_order),
            "links": links,
            "rejected_links": rejected_links,
            "checks": checks,
            "issues": issues,
            "missing_profiles": missing,
            "status": status,
            "bundle": {
                "document_count": 1 + len(links),
                "quotation_count": len(related_by_profile.get("quotation", [])),
                "delivery_count": len(related_by_profile.get("delivery_order", [])),
                "invoice_count": len(related_by_profile.get("invoice", [])),
                "credit_note_count": len(related_by_profile.get("credit_note", [])),
                "payment_count": len(related_by_profile.get("payment_confirmation", [])),
                "confirmed_count": sum(link["decision"] == "confirmed" for link in links),
                "suggested_count": sum(link["decision"] == "suggested" for link in links),
                "ordered_line_count": len(_item_rows("purchase_order", purchase_order.get("data") or {})),
                "fulfilled_line_count": fulfilled_lines,
                "invoiced_line_count": invoiced_lines,
                "invoice_total_status": invoice_total_check["status"],
                "invoice_total_difference": invoice_difference,
                "currency": (purchase_order.get("data") or {}).get("currency"),
                **settlement,
            },
        })
    return groups
