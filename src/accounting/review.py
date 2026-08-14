"""Explainable checks over user-reviewable structured document data.

Nothing in this module posts accounting entries, decides statutory tax
eligibility, or changes extracted values. It reconciles the document's displayed
tax convention and returns evidence, calculations, and warnings that an
accountant can verify against the original document.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable


FINANCIAL_PROFILES = frozenset({
    "invoice",
    "receipt",
    "quotation",
    "purchase_order",
    "delivery_order",
    "credit_note",
    "payment_confirmation",
})
MONEY_TOLERANCE = Decimal("0.02")
TAX_TREATMENTS = frozenset({"inclusive", "exclusive", "not_applicable", "unknown"})

_TAX_TERM = r"(?:tax(?:es)?|vat|gst|sst|mwst|service\s+tax)"
_TAX_SIGNAL_PATTERNS = {
    "inclusive": (
        re.compile(
            rf"\b(?:incl(?:uded|usive)?|inkl(?:usive)?|including)\.?\b.{{0,55}}?\b{_TAX_TERM}\b",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            rf"\b{_TAX_TERM}\b.{{0,55}}?\b(?:included|inclusive)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    "exclusive": (
        re.compile(
            rf"\b(?:excl(?:uded|usive)?|excluding)\.?\b.{{0,55}}?\b{_TAX_TERM}\b",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            rf"\b{_TAX_TERM}\b.{{0,55}}?\b(?:excluded|exclusive|not\s+included|extra|additional)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            rf"\b(?:plus|before)\b.{{0,25}}?\b{_TAX_TERM}\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    "not_applicable": (
        re.compile(
            rf"\b(?:no|zero)\s+{_TAX_TERM}\b",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:tax\s+exempt|exempt\s+from\s+{_TAX_TERM}|not\s+subject\s+to\s+{_TAX_TERM})\b",
            re.IGNORECASE,
        ),
    ),
}

PROFILE_FIELDS = {
    "invoice": {
        "party": "supplier_name",
        "reference": "invoice_number",
        "date": "invoice_date",
        "required": (
            ("supplier_name", "Supplier"),
            ("invoice_number", "Invoice number"),
            ("invoice_date", "Invoice date"),
            ("currency", "Currency"),
            ("total_amount", "Total"),
        ),
        "table": "line_items",
    },
    "receipt": {
        "party": "merchant_name",
        "reference": "receipt_number",
        "date": "transaction_datetime",
        "required": (
            ("merchant_name", "Merchant"),
            ("transaction_datetime", "Transaction date"),
            ("currency", "Currency"),
            ("total_amount", "Total"),
        ),
        "table": "purchased_items",
    },
    "quotation": {
        "party": "supplier_name",
        "reference": "quotation_number",
        "date": "quotation_date",
        "required": (
            ("supplier_name", "Supplier"),
            ("quotation_number", "Quotation number"),
            ("quotation_date", "Quotation date"),
            ("currency", "Currency"),
            ("total_amount", "Total"),
        ),
        "table": "quoted_items",
    },
    "purchase_order": {
        "party": "supplier_name",
        "reference": "purchase_order_number",
        "date": "purchase_order_date",
        "required": (
            ("supplier_name", "Supplier"),
            ("purchase_order_number", "Purchase order number"),
            ("purchase_order_date", "Purchase order date"),
            ("currency", "Currency"),
            ("total_amount", "Total"),
        ),
        "table": "line_items",
    },
    "delivery_order": {
        "party": "supplier_name",
        "reference": "delivery_order_number",
        "date": "delivery_date",
        "required": (
            ("supplier_name", "Supplier"),
            ("delivery_order_number", "Delivery order number"),
            ("delivery_date", "Delivery date"),
            ("purchase_order_number", "Purchase order number"),
        ),
        "table": "delivered_items",
    },
    "credit_note": {
        "party": "supplier_name",
        "reference": "credit_note_number",
        "date": "credit_note_date",
        "required": (
            ("supplier_name", "Supplier"),
            ("credit_note_number", "Credit note number"),
            ("credit_note_date", "Credit note date"),
            ("invoice_number", "Original invoice number"),
            ("currency", "Currency"),
            ("total_amount", "Credit total"),
        ),
        "table": "line_items",
    },
    "payment_confirmation": {
        "party": "payee_name",
        "reference": "payment_reference",
        "date": "payment_date",
        "required": (
            ("payment_reference", "Payment reference"),
            ("payment_date", "Payment date"),
            ("payee_name", "Payee"),
            ("invoice_number", "Invoice number"),
            ("currency", "Currency"),
            ("amount", "Amount paid"),
        ),
        "table": "line_items",
    },
}


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _json_number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            flattened.update(_flatten(item, f"{prefix}[{index}]"))
    else:
        flattened[prefix] = value
    return flattened


def _evidence_needles(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = _decimal(value)
        if number is None:
            return []
        fixed = f"{number:.2f}"
        return list(dict.fromkeys((str(value), fixed, f"{number:,.2f}")))
    text = " ".join(str(value).split())
    return [text] if len(text) >= 2 else []


def _find_evidence(value: Any, pages: Iterable[dict]) -> tuple[int | None, str | None]:
    needles = _evidence_needles(value)
    matches: list[tuple[int, str]] = []
    for page in pages:
        markdown = str(page.get("markdown") or "")
        folded = markdown.casefold()
        for needle in needles:
            position = folded.find(needle.casefold())
            if position < 0:
                continue
            start = max(0, position - 55)
            end = min(len(markdown), position + len(needle) + 55)
            excerpt = " ".join(markdown[start:end].split())
            matches.append((int(page["page_number"]), excerpt))
            break
    if len(matches) == 1:
        return matches[0]
    if len(list(pages)) == 1:
        only = next(iter(pages), None)
        if only:
            return int(only["page_number"]), None
    return None, None


def _tax_treatment(value: Any) -> str:
    normalized = str(value or "unknown").strip().casefold().replace("-", "_").replace(" ", "_")
    return normalized if normalized in TAX_TREATMENTS else "unknown"


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _find_tax_phrase(phrase: Any, pages: Iterable[dict]) -> tuple[int | None, str | None]:
    needle = _compact_text(phrase)
    if not needle:
        return None, None
    for page in pages:
        if needle in _compact_text(page.get("markdown")):
            return int(page["page_number"]), " ".join(str(phrase).split())
    return None, None


def _classify_tax_phrase(text: Any) -> set[str]:
    value = str(text or "")
    return {
        treatment
        for treatment, patterns in _TAX_SIGNAL_PATTERNS.items()
        if any(pattern.search(value) for pattern in patterns)
    }


def _source_tax_signals(pages: Iterable[dict]) -> list[dict]:
    signals: list[dict] = []
    seen: set[tuple[str, int, str]] = set()
    for page in pages:
        markdown = str(page.get("markdown") or "")
        page_number = int(page["page_number"])
        for treatment, patterns in _TAX_SIGNAL_PATTERNS.items():
            for pattern in patterns:
                for match in pattern.finditer(markdown):
                    evidence = " ".join(match.group(0).split())
                    identity = (treatment, page_number, evidence.casefold())
                    if identity in seen:
                        continue
                    seen.add(identity)
                    signals.append({
                        "treatment": treatment,
                        "evidence": evidence,
                        "source_page": page_number,
                    })
    return signals


def _matches_money(left: Decimal, right: Decimal | None) -> bool:
    return right is not None and abs(_money(left) - _money(right)) <= MONEY_TOLERANCE


def _complete_item_amount_sum(data: dict, table_key: str) -> Decimal | None:
    rows = data.get(table_key) or []
    if not rows:
        return None
    amounts = [_decimal(row.get("amount")) for row in rows]
    if any(amount is None for amount in amounts):
        return None
    return sum((amount for amount in amounts if amount is not None), Decimal("0"))


def _tax_rate_from_evidence(evidence: Any) -> Decimal | None:
    match = re.search(r"(?<!\d)(\d{1,2}(?:[.,]\d{1,4})?)\s*%", str(evidence or ""))
    if not match:
        return None
    rate = _decimal(match.group(1).replace(",", "."))
    return rate if rate is not None and Decimal("0") <= rate <= Decimal("100") else None


def _resolve_tax_treatment(
    data: dict,
    *,
    source_data: dict | None,
    pages: list[dict],
    base_amount: Decimal,
    discount: Decimal,
    tax: Decimal | None,
    total: Decimal | None,
) -> dict:
    """Resolve tax semantics from user choice, verified wording, then arithmetic."""
    selected = _tax_treatment(data.get("tax_treatment"))
    source_selected = _tax_treatment((source_data or {}).get("tax_treatment"))
    user_selected = (
        source_data is not None
        and selected != "unknown"
        and selected != source_selected
    )
    evidence_page, verified_evidence = _find_tax_phrase(data.get("tax_evidence"), pages)
    evidence_classes = _classify_tax_phrase(verified_evidence)
    source_signals = _source_tax_signals(pages)
    source_classes = {signal["treatment"] for signal in source_signals}
    signals: list[str] = []

    if data.get("tax_evidence") and not verified_evidence:
        signals.append("Extracted tax wording was not found verbatim in the saved OCR text.")

    explicit_classes = set(source_classes)
    if verified_evidence and selected in evidence_classes:
        explicit_classes.add(selected)
        signals.append("The extracted tax wording was verified against the saved OCR text.")
    elif verified_evidence and selected != "unknown":
        signals.append("The extracted treatment is not supported by the quoted tax wording.")

    tax_value = tax or Decimal("0")
    without_tax_result = base_amount - discount
    with_tax_result = without_tax_result + tax_value
    without_tax_matches = _matches_money(without_tax_result, total)
    with_tax_matches = _matches_money(with_tax_result, total)
    subtotal_basis = (
        "not_applicable"
        if tax is not None and abs(tax) <= MONEY_TOLERANCE
        else "gross"
        if without_tax_matches and not with_tax_matches
        else "net"
        if with_tax_matches and not without_tax_matches
        else "unknown"
    )

    treatment = "unknown"
    confidence = "low"
    source = "unresolved"
    evidence = verified_evidence
    source_page = evidence_page

    if user_selected:
        treatment = selected
        confidence = "user_confirmed"
        source = "user"
        signals.append("A user explicitly changed the tax treatment.")
    elif tax is not None and abs(tax) <= MONEY_TOLERANCE and without_tax_matches:
        treatment = "not_applicable"
        confidence = "medium"
        source = "arithmetic"
        signals.append("The extracted tax is zero and no additional tax changes the total.")
    elif len(explicit_classes) == 1:
        treatment = next(iter(explicit_classes))
        confidence = "high"
        source = "source_text"
        matching_signal = next(
            (item for item in source_signals if item["treatment"] == treatment),
            None,
        )
        if matching_signal and not evidence:
            evidence = matching_signal["evidence"]
            source_page = matching_signal["source_page"]
        signals.append(f"Saved OCR text explicitly indicates tax is {treatment.replace('_', ' ')}.")
    elif len(explicit_classes) > 1:
        signals.append("Saved OCR text contains conflicting inclusive and exclusive tax wording.")
        if tax is not None and without_tax_matches != with_tax_matches:
            treatment = "inclusive" if without_tax_matches else "exclusive"
            confidence = "medium"
            source = "arithmetic"
            signals.append(
                "Arithmetic resolved the conflicting labels because only the "
                f"tax-{treatment} formula matches the printed total."
            )
    elif tax is not None and without_tax_matches != with_tax_matches:
        treatment = "inclusive" if without_tax_matches else "exclusive"
        confidence = "medium"
        source = "arithmetic"
        signals.append(
            "Only the tax-inclusive formula matches the printed total."
            if without_tax_matches
            else "Only the tax-exclusive formula matches the printed total."
        )

    return {
        "treatment": treatment,
        "confidence": confidence,
        "source": source,
        "evidence": evidence,
        "source_page": source_page,
        "signals": signals,
        "subtotal_basis": subtotal_basis,
        "without_tax_result": without_tax_result,
        "with_tax_result": with_tax_result,
        "without_tax_matches": without_tax_matches,
        "with_tax_matches": with_tax_matches,
    }


def _issue(
    code: str,
    title: str,
    detail: str,
    *,
    severity: str = "warning",
    field_path: str | None = None,
) -> dict:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "detail": detail,
        "field_path": field_path,
    }


def _calculation(
    code: str,
    label: str,
    formula: str,
    inputs: dict[str, Decimal],
    result: Decimal,
    document_value: Decimal | None,
    field_path: str,
) -> dict:
    rounded_result = _money(result)
    rounded_document = _money(document_value) if document_value is not None else None
    difference = (
        _money(rounded_document - rounded_result)
        if rounded_document is not None
        else None
    )
    status = (
        "informational"
        if difference is None
        else "matches"
        if abs(difference) <= MONEY_TOLERANCE
        else "mismatch"
    )
    return {
        "code": code,
        "label": label,
        "formula": formula,
        "inputs": {key: _json_number(value) for key, value in inputs.items()},
        "result": _json_number(rounded_result),
        "document_value": _json_number(rounded_document),
        "difference": _json_number(difference),
        "status": status,
        "field_path": field_path,
        "rounding": "2 decimal places, half up",
    }


def find_duplicate_candidates(
    profile: str,
    data: dict,
    candidate_extractions: Iterable[dict],
) -> list[dict]:
    """Score explainable duplicate signals without calling a model."""
    rules = PROFILE_FIELDS.get(profile)
    if not rules:
        return []
    reference = _normalized(data.get(rules["reference"]))
    party = _normalized(data.get(rules["party"]))
    document_date = _normalized(data.get(rules["date"]))
    total = _decimal(data.get("total_amount"))
    results: list[dict] = []
    for candidate in candidate_extractions:
        other = candidate.get("data") or {}
        reasons: list[str] = []
        score = Decimal("0")
        other_reference = _normalized(other.get(rules["reference"]))
        other_party = _normalized(other.get(rules["party"]))
        other_date = _normalized(other.get(rules["date"]))
        other_total = _decimal(other.get("total_amount"))
        if reference and reference == other_reference:
            score = max(score, Decimal("0.99"))
            reasons.append("same document reference")
        if party and party == other_party:
            score += Decimal("0.25")
            reasons.append("same supplier or merchant")
        if document_date and document_date == other_date:
            score += Decimal("0.25")
            reasons.append("same document date")
        if total is not None and other_total is not None and abs(total - other_total) <= MONEY_TOLERANCE:
            score += Decimal("0.35")
            reasons.append("same total")
        score = min(score, Decimal("0.99"))
        if score < Decimal("0.75"):
            continue
        results.append({
            "document_id": candidate["document_id"],
            "document_name": candidate["document_name"],
            "session_id": candidate["session_id"],
            "session_title": candidate["session_title"],
            "page_number": 1,
            "score": float(score),
            "reasons": reasons,
        })
    return sorted(results, key=lambda item: (-item["score"], item["document_name"]))


def build_accounting_review(
    profile: str,
    data: dict,
    *,
    source_data: dict | None = None,
    extraction_status: str = "needs_review",
    pages: Iterable[dict] = (),
    duplicate_candidates: Iterable[dict] = (),
) -> dict:
    """Return non-destructive checks and transparent calculations."""
    if profile not in FINANCIAL_PROFILES:
        return {
            "applicable": False,
            "profile": profile,
            "summary": {"issue_count": 0, "warning_count": 0, "calculation_count": 0},
            "issues": [],
            "calculations": [],
            "provenance": {},
            "duplicates": [],
        }

    page_list = list(pages)
    rules = PROFILE_FIELDS[profile]
    issues: list[dict] = []
    calculations: list[dict] = []

    for key, label in rules["required"]:
        if data.get(key) in (None, ""):
            issues.append(_issue(
                "missing_field",
                f"Missing {label.lower()}",
                "The selected document template normally expects this value. Check the original document; it may genuinely be absent.",
                field_path=key,
            ))

    table_key = str(rules["table"])
    subtotal = _decimal(data.get("subtotal"))
    item_sum = _complete_item_amount_sum(data, table_key)
    base_amount = subtotal if subtotal is not None else item_sum
    base_key = "subtotal" if subtotal is not None else "item_amount_sum"
    discount = _decimal(data.get("discount_amount")) or Decimal("0")
    tax = _decimal(data.get("tax_amount"))
    total = _decimal(data.get("total_amount"))
    tax_analysis = {
        "treatment": "unknown",
        "confidence": "low",
        "source": "unresolved",
        "evidence": None,
        "source_page": None,
        "signals": ["There are not enough extracted values to determine tax treatment."],
        "subtotal_basis": "unknown",
    }
    suggested_fields: dict[str, Any] = {}

    if base_amount is not None:
        resolved_tax = _resolve_tax_treatment(
            data,
            source_data=source_data,
            pages=page_list,
            base_amount=base_amount,
            discount=discount,
            tax=tax,
            total=total,
        )
        tax_analysis = {
            key: resolved_tax[key]
            for key in (
                "treatment",
                "confidence",
                "source",
                "evidence",
                "source_page",
                "signals",
                "subtotal_basis",
            )
        }
        if (
            _tax_treatment(data.get("tax_treatment")) == "unknown"
            and resolved_tax["treatment"] != "unknown"
        ):
            suggested_fields["tax_treatment"] = resolved_tax["treatment"]
        if not str(data.get("tax_evidence") or "").strip() and resolved_tax["evidence"]:
            suggested_fields["tax_evidence"] = resolved_tax["evidence"]
        if _decimal(data.get("tax_rate")) is None:
            suggested_rate = _tax_rate_from_evidence(resolved_tax["evidence"])
            if suggested_rate is not None:
                suggested_fields["tax_rate"] = _json_number(suggested_rate)
        treatment = resolved_tax["treatment"]
        tax_value = tax or Decimal("0")
        common_inputs = {base_key: base_amount, "discount": discount}

        without_tax_matches = resolved_tax["without_tax_matches"]
        with_tax_matches = resolved_tax["with_tax_matches"]
        if tax is None or treatment == "not_applicable" or (
            without_tax_matches and with_tax_matches
        ):
            calculation = _calculation(
                "document_total",
                "Document total",
                f"{base_key} - discount (no additional tax changes the total)",
                common_inputs,
                resolved_tax["without_tax_result"],
                total,
                "total_amount",
            )
            calculations.append(calculation)
        elif without_tax_matches and not with_tax_matches:
            calculation = _calculation(
                "document_total",
                "Document total",
                f"{base_key} - discount (tax is already included in the base)",
                {**common_inputs, "included_tax": tax_value},
                resolved_tax["without_tax_result"],
                total,
                "total_amount",
            )
            calculations.append(calculation)
        elif with_tax_matches and not without_tax_matches:
            tax_label = "included_tax" if treatment == "inclusive" else "tax"
            tax_note = (
                "included tax added to the net base"
                if treatment == "inclusive"
                else "tax added to the tax-exclusive base"
            )
            calculation = _calculation(
                "document_total",
                "Document total",
                f"{base_key} - discount + tax ({tax_note})",
                {**common_inputs, tax_label: tax_value},
                resolved_tax["with_tax_result"],
                total,
                "total_amount",
            )
            calculations.append(calculation)
        else:
            inclusive_candidate = _calculation(
                "document_total_inclusive_candidate",
                "Document total · tax-inclusive candidate",
                f"{base_key} - discount (tax already included)",
                {**common_inputs, "included_tax": tax_value},
                resolved_tax["without_tax_result"],
                total,
                "total_amount",
            )
            exclusive_candidate = _calculation(
                "document_total_exclusive_candidate",
                "Document total · tax-exclusive candidate",
                f"{base_key} - discount + tax",
                {**common_inputs, "tax": tax_value},
                resolved_tax["with_tax_result"],
                total,
                "total_amount",
            )
            inclusive_candidate["status"] = "candidate"
            exclusive_candidate["status"] = "candidate"
            calculations.extend((inclusive_candidate, exclusive_candidate))
            issues.append(_issue(
                "subtotal_basis_unknown",
                "Subtotal basis needs confirmation",
                "The extracted values do not establish whether the subtotal is net or already includes tax. Both formulas are shown without choosing one.",
                field_path="subtotal",
            ))

        for calculation in calculations:
            if calculation["code"].startswith("document_total"):
                calculation["tax_analysis"] = tax_analysis

        chosen_calculation = next(
            (item for item in calculations if item["code"] == "document_total"),
            None,
        )
        if chosen_calculation and chosen_calculation["status"] == "mismatch":
            difference = chosen_calculation["difference"]
            issues.append(_issue(
                "total_mismatch",
                "Document total does not reconcile",
                f"The selected formula differs from the extracted total by {difference:.2f}. Confirm discounts, service charges, rounding, and extracted amounts.",
                field_path="total_amount",
            ))

    for index, item in enumerate(data.get(table_key) or []):
        quantity = _decimal(item.get("quantity"))
        unit_price = _decimal(item.get("unit_price"))
        amount = _decimal(item.get("amount"))
        if quantity is None or unit_price is None:
            continue
        line_discount = _decimal(item.get("discount_amount")) or Decimal("0")
        line_tax = _decimal(item.get("tax_amount")) or Decimal("0")
        expected = quantity * unit_price - line_discount
        formula = "quantity × unit price"
        inputs = {"quantity": quantity, "unit_price": unit_price}
        if line_discount:
            formula += " - discount"
            inputs["discount"] = line_discount
        if line_tax:
            line_exclusive = expected + line_tax
            base_matches = _matches_money(expected, amount)
            gross_matches = _matches_money(line_exclusive, amount)
            if gross_matches and not base_matches:
                expected = line_exclusive
                formula += " + tax"
                inputs["tax"] = line_tax
            else:
                formula += " (tax shown separately)"
                inputs["disclosed_tax"] = line_tax
        calculation = _calculation(
            "line_amount",
            f"{table_key.replace('_', ' ').title()} row {index + 1}",
            formula,
            inputs,
            expected,
            amount,
            f"{table_key}[{index}].amount",
        )
        calculations.append(calculation)
        if calculation["status"] == "mismatch":
            issues.append(_issue(
                "line_amount_mismatch",
                f"Row {index + 1} amount needs checking",
                "Quantity multiplied by unit price does not match the extracted row amount. Confirm whether the document uses an inclusive tax or discount convention.",
                field_path=calculation["field_path"],
            ))

    if profile == "invoice":
        terms = str(data.get("payment_terms") or "")
        days_match = re.search(r"\b(\d{1,3})\s*days?\b", terms, re.IGNORECASE)
        try:
            invoice_date = date.fromisoformat(str(data.get("invoice_date") or "")[:10])
            due_date = date.fromisoformat(str(data.get("due_date") or "")[:10])
        except ValueError:
            invoice_date = due_date = None
        if days_match and invoice_date and due_date:
            days = int(days_match.group(1))
            expected_due = invoice_date + timedelta(days=days)
            matches = expected_due == due_date
            calculations.append({
                "code": "payment_due_date",
                "label": "Payment due date",
                "formula": "invoice date + payment-term days",
                "inputs": {"invoice_date": invoice_date.isoformat(), "days": days},
                "result": expected_due.isoformat(),
                "document_value": due_date.isoformat(),
                "difference": (due_date - expected_due).days,
                "status": "matches" if matches else "mismatch",
                "field_path": "due_date",
                "rounding": "not applicable",
            })
            if not matches:
                issues.append(_issue(
                    "due_date_mismatch",
                    "Due date differs from payment terms",
                    f"{days} days after {invoice_date.isoformat()} is {expected_due.isoformat()}, not {due_date.isoformat()}.",
                    field_path="due_date",
                ))

    for note in data.get("review_notes") or []:
        if str(note).strip():
            issues.append(_issue(
                "extraction_note",
                "OCR extraction note",
                str(note).strip(),
                severity="info",
                field_path="review_notes",
            ))

    duplicates = list(duplicate_candidates)
    for candidate in duplicates:
        if float(candidate.get("score") or 0) >= 0.90:
            issues.append(_issue(
                "possible_duplicate",
                "Possible duplicate document",
                f"{candidate['document_name']} matches on {', '.join(candidate.get('reasons') or [])}.",
                field_path=rules["reference"],
            ))

    source_flat = _flatten(source_data) if source_data is not None else _flatten(data)
    current_flat = _flatten(data)
    provenance: dict[str, dict] = {}
    missing_source = object()
    for path, value in current_flat.items():
        source_value = source_flat.get(path, missing_source)
        edited = (
            source_data is not None
            and (
                (source_value is missing_source and value not in (None, ""))
                or (source_value is not missing_source and source_value != value)
            )
        )
        source_page, excerpt = _find_evidence(value, page_list)
        provenance[path] = {
            "kind": "user_edited" if edited else "extracted",
            "label": "User edited" if edited else "Extracted",
            "approved": extraction_status == "approved",
            "source": "Mistral document annotation",
            "source_page": source_page,
            "source_excerpt": excerpt,
        }
    for path, value in suggested_fields.items():
        provenance[path] = {
            "kind": "derived",
            "label": "Derived from OCR",
            "approved": False,
            "source": "Deterministic tax reconciliation",
            "source_page": tax_analysis.get("source_page"),
            "source_excerpt": tax_analysis.get("evidence"),
        }

    warning_count = sum(1 for item in issues if item["severity"] == "warning")
    return {
        "applicable": True,
        "profile": profile,
        "summary": {
            "issue_count": len(issues),
            "warning_count": warning_count,
            "calculation_count": len(calculations),
            "duplicate_count": len(duplicates),
            "status": "check_required" if warning_count else "checks_passed",
        },
        "issues": issues,
        "calculations": calculations,
        "tax_analysis": tax_analysis,
        "suggested_fields": suggested_fields,
        "provenance": provenance,
        "duplicates": duplicates,
        "disclaimer": "Review assistance only. Confirm accounting classification, tax treatment, and approval independently.",
    }
