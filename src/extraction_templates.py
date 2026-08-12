from __future__ import annotations

from copy import deepcopy


DOCUMENT_ANNOTATION_PAGE_LIMIT = 8


def _nullable(kind: str) -> dict:
    return {"type": [kind, "null"]}


def _object(properties: dict[str, dict]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _array(properties: dict[str, dict]) -> dict:
    return {"type": "array", "items": _object(properties)}


COMMON_PROMPT = (
    "Extract only information explicitly present in the document. Never calculate, infer, "
    "or invent missing values. Return null for an absent or ambiguous scalar and an empty "
    "array when a repeated section is absent. Preserve identifiers as text. Put material "
    "uncertainties in review_notes only when source text is unreadable, conflicting, or could "
    "reasonably map to multiple fields. Do not critique the document, speculate about its "
    "claims, or report an ordinary missing field as a review note."
)


DOCUMENT_TEMPLATES: dict[str, dict] = {
    "invoice": {
        "id": "invoice",
        "label": "Invoice",
        "description": "Supplier, billing totals, payment terms, and invoice line items.",
        "schema_version": 1,
        "prompt": COMMON_PROMPT + " The selected document type is invoice.",
        "schema": _object({
            "document_type": {"type": "string", "enum": ["invoice"]},
            "supplier_name": _nullable("string"),
            "supplier_registration_number": _nullable("string"),
            "supplier_address": _nullable("string"),
            "customer_name": _nullable("string"),
            "invoice_number": _nullable("string"),
            "invoice_date": _nullable("string"),
            "due_date": _nullable("string"),
            "currency": _nullable("string"),
            "subtotal": _nullable("number"),
            "discount_amount": _nullable("number"),
            "tax_amount": _nullable("number"),
            "total_amount": _nullable("number"),
            "payment_terms": _nullable("string"),
            "line_items": _array({
                "description": _nullable("string"),
                "quantity": _nullable("number"),
                "unit_price": _nullable("number"),
                "tax_amount": _nullable("number"),
                "amount": _nullable("number"),
            }),
            "review_notes": {"type": "array", "items": {"type": "string"}},
        }),
        "layout": {
            "sections": [
                {
                    "title": "Invoice details",
                    "fields": [
                        {"key": "supplier_name", "label": "Supplier", "type": "text"},
                        {"key": "supplier_registration_number", "label": "Registration number", "type": "text"},
                        {"key": "supplier_address", "label": "Supplier address", "type": "long_text"},
                        {"key": "customer_name", "label": "Customer", "type": "text"},
                        {"key": "invoice_number", "label": "Invoice number", "type": "text"},
                        {"key": "invoice_date", "label": "Invoice date", "type": "date"},
                        {"key": "due_date", "label": "Due date", "type": "date"},
                        {"key": "currency", "label": "Currency", "type": "text"},
                        {"key": "payment_terms", "label": "Payment terms", "type": "long_text"},
                    ],
                },
                {
                    "title": "Totals",
                    "fields": [
                        {"key": "subtotal", "label": "Subtotal", "type": "number"},
                        {"key": "discount_amount", "label": "Discount", "type": "number"},
                        {"key": "tax_amount", "label": "Tax", "type": "number"},
                        {"key": "total_amount", "label": "Total", "type": "number"},
                    ],
                },
            ],
            "tables": [{
                "key": "line_items",
                "title": "Line items",
                "columns": [
                    {"key": "description", "label": "Description", "type": "text"},
                    {"key": "quantity", "label": "Qty", "type": "number"},
                    {"key": "unit_price", "label": "Unit price", "type": "number"},
                    {"key": "tax_amount", "label": "Tax", "type": "number"},
                    {"key": "amount", "label": "Amount", "type": "number"},
                ],
            }],
            "lists": [{"key": "review_notes", "label": "Review notes"}],
        },
    },
    "receipt": {
        "id": "receipt",
        "label": "Receipt",
        "description": "Merchant, transaction totals, payment details, and purchased items.",
        "schema_version": 1,
        "prompt": COMMON_PROMPT + " The selected document type is receipt.",
        "schema": _object({
            "document_type": {"type": "string", "enum": ["receipt"]},
            "merchant_name": _nullable("string"),
            "merchant_registration_number": _nullable("string"),
            "merchant_address": _nullable("string"),
            "receipt_number": _nullable("string"),
            "transaction_datetime": _nullable("string"),
            "currency": _nullable("string"),
            "subtotal": _nullable("number"),
            "discount_amount": _nullable("number"),
            "tax_amount": _nullable("number"),
            "total_amount": _nullable("number"),
            "payment_method": _nullable("string"),
            "purchased_items": _array({
                "description": _nullable("string"),
                "quantity": _nullable("number"),
                "unit_price": _nullable("number"),
                "amount": _nullable("number"),
            }),
            "review_notes": {"type": "array", "items": {"type": "string"}},
        }),
        "layout": {
            "sections": [
                {
                    "title": "Receipt details",
                    "fields": [
                        {"key": "merchant_name", "label": "Merchant", "type": "text"},
                        {"key": "merchant_registration_number", "label": "Registration number", "type": "text"},
                        {"key": "merchant_address", "label": "Merchant address", "type": "long_text"},
                        {"key": "receipt_number", "label": "Receipt number", "type": "text"},
                        {"key": "transaction_datetime", "label": "Date and time", "type": "text"},
                        {"key": "currency", "label": "Currency", "type": "text"},
                        {"key": "payment_method", "label": "Payment method", "type": "text"},
                    ],
                },
                {
                    "title": "Totals",
                    "fields": [
                        {"key": "subtotal", "label": "Subtotal", "type": "number"},
                        {"key": "discount_amount", "label": "Discount", "type": "number"},
                        {"key": "tax_amount", "label": "Tax", "type": "number"},
                        {"key": "total_amount", "label": "Total", "type": "number"},
                    ],
                },
            ],
            "tables": [{
                "key": "purchased_items",
                "title": "Purchased items",
                "columns": [
                    {"key": "description", "label": "Description", "type": "text"},
                    {"key": "quantity", "label": "Qty", "type": "number"},
                    {"key": "unit_price", "label": "Unit price", "type": "number"},
                    {"key": "amount", "label": "Amount", "type": "number"},
                ],
            }],
            "lists": [{"key": "review_notes", "label": "Review notes"}],
        },
    },
    "quotation": {
        "id": "quotation",
        "label": "Quotation",
        "description": "Supplier proposal, customer details, validity, terms, totals, and quoted items.",
        "schema_version": 1,
        "prompt": COMMON_PROMPT + (
            " The selected document type is quotation. Preserve quotation references and item "
            "codes as text. Extract validity, payment, delivery, and warranty terms only when "
            "explicitly printed. Line-item and total amounts must match the document without "
            "recalculation."
        ),
        "schema": _object({
            "document_type": {"type": "string", "enum": ["quotation"]},
            "supplier_name": _nullable("string"),
            "supplier_registration_number": _nullable("string"),
            "supplier_address": _nullable("string"),
            "supplier_contact": _nullable("string"),
            "customer_name": _nullable("string"),
            "customer_address": _nullable("string"),
            "quotation_number": _nullable("string"),
            "quotation_date": _nullable("string"),
            "valid_until": _nullable("string"),
            "subject": _nullable("string"),
            "currency": _nullable("string"),
            "subtotal": _nullable("number"),
            "discount_amount": _nullable("number"),
            "tax_amount": _nullable("number"),
            "total_amount": _nullable("number"),
            "payment_terms": _nullable("string"),
            "delivery_terms": _nullable("string"),
            "warranty_terms": _nullable("string"),
            "quoted_items": _array({
                "item_code": _nullable("string"),
                "description": _nullable("string"),
                "quantity": _nullable("number"),
                "unit": _nullable("string"),
                "unit_price": _nullable("number"),
                "discount_amount": _nullable("number"),
                "tax_amount": _nullable("number"),
                "amount": _nullable("number"),
            }),
            "review_notes": {"type": "array", "items": {"type": "string"}},
        }),
        "layout": {
            "sections": [
                {
                    "title": "Quotation details",
                    "fields": [
                        {"key": "supplier_name", "label": "Supplier", "type": "text"},
                        {"key": "supplier_registration_number", "label": "Registration number", "type": "text"},
                        {"key": "supplier_address", "label": "Supplier address", "type": "long_text"},
                        {"key": "supplier_contact", "label": "Supplier contact", "type": "text"},
                        {"key": "customer_name", "label": "Customer", "type": "text"},
                        {"key": "customer_address", "label": "Customer address", "type": "long_text"},
                        {"key": "quotation_number", "label": "Quotation number", "type": "text"},
                        {"key": "quotation_date", "label": "Quotation date", "type": "date"},
                        {"key": "valid_until", "label": "Valid until", "type": "date"},
                        {"key": "subject", "label": "Subject / project", "type": "long_text"},
                        {"key": "currency", "label": "Currency", "type": "text"},
                    ],
                },
                {
                    "title": "Commercial terms",
                    "fields": [
                        {"key": "payment_terms", "label": "Payment terms", "type": "long_text"},
                        {"key": "delivery_terms", "label": "Delivery terms", "type": "long_text"},
                        {"key": "warranty_terms", "label": "Warranty terms", "type": "long_text"},
                    ],
                },
                {
                    "title": "Totals",
                    "fields": [
                        {"key": "subtotal", "label": "Subtotal", "type": "number"},
                        {"key": "discount_amount", "label": "Discount", "type": "number"},
                        {"key": "tax_amount", "label": "Tax", "type": "number"},
                        {"key": "total_amount", "label": "Total", "type": "number"},
                    ],
                },
            ],
            "tables": [{
                "key": "quoted_items",
                "title": "Quoted items",
                "columns": [
                    {"key": "item_code", "label": "Item code", "type": "text"},
                    {"key": "description", "label": "Description", "type": "text"},
                    {"key": "quantity", "label": "Qty", "type": "number"},
                    {"key": "unit", "label": "Unit", "type": "text"},
                    {"key": "unit_price", "label": "Unit price", "type": "number"},
                    {"key": "discount_amount", "label": "Discount", "type": "number"},
                    {"key": "tax_amount", "label": "Tax", "type": "number"},
                    {"key": "amount", "label": "Amount", "type": "number"},
                ],
            }],
            "lists": [{"key": "review_notes", "label": "Review notes"}],
        },
    },
    "resume": {
        "id": "resume",
        "label": "CV / Résumé",
        "description": "Contact details, skills, employment, education, projects, and certifications.",
        "schema_version": 2,
        "prompt": COMMON_PROMPT + (
            " The selected document type is CV or resume. Keep degree or qualification names "
            "separate from fields of study. Treat an organization named in certification text "
            "as the issuer only when the document explicitly connects it to that certification. "
            "Return every achievement as a separate row and do not duplicate skills."
        ),
        "schema": _object({
            "document_type": {"type": "string", "enum": ["resume"]},
            "full_name": _nullable("string"),
            "professional_title": _nullable("string"),
            "email": _nullable("string"),
            "phone": _nullable("string"),
            "location": _nullable("string"),
            "linkedin_url": _nullable("string"),
            "github_url": _nullable("string"),
            "portfolio_url": _nullable("string"),
            "professional_summary": _nullable("string"),
            "skills": {"type": "array", "items": {"type": "string"}},
            "languages": _array({
                "language": _nullable("string"),
                "proficiency": _nullable("string"),
            }),
            "experience": _array({
                "employer": _nullable("string"),
                "job_title": _nullable("string"),
                "location": _nullable("string"),
                "start_date": _nullable("string"),
                "end_date": _nullable("string"),
                "description": _nullable("string"),
            }),
            "education": _array({
                "institution": _nullable("string"),
                "qualification": _nullable("string"),
                "field_of_study": _nullable("string"),
                "start_date": _nullable("string"),
                "end_date": _nullable("string"),
            }),
            "projects": _array({
                "name": _nullable("string"),
                "description": _nullable("string"),
                "technologies": _nullable("string"),
            }),
            "certifications": _array({
                "name": _nullable("string"),
                "issuer": _nullable("string"),
                "date": _nullable("string"),
            }),
            "achievements": _array({
                "title": _nullable("string"),
                "date": _nullable("string"),
                "description": _nullable("string"),
            }),
            "review_notes": {"type": "array", "items": {"type": "string"}},
        }),
        "layout": {
            "sections": [{
                "title": "Profile",
                "fields": [
                    {"key": "full_name", "label": "Full name", "type": "text"},
                    {"key": "professional_title", "label": "Professional title", "type": "text"},
                    {"key": "email", "label": "Email", "type": "text"},
                    {"key": "phone", "label": "Phone", "type": "text"},
                    {"key": "location", "label": "Location", "type": "text"},
                    {"key": "linkedin_url", "label": "LinkedIn", "type": "text"},
                    {"key": "github_url", "label": "GitHub", "type": "text"},
                    {"key": "portfolio_url", "label": "Portfolio / website", "type": "text"},
                    {"key": "professional_summary", "label": "Professional summary", "type": "long_text"},
                ],
            }],
            "tables": [
                {
                    "key": "experience",
                    "title": "Experience",
                    "columns": [
                        {"key": "employer", "label": "Employer", "type": "text"},
                        {"key": "job_title", "label": "Role", "type": "text"},
                        {"key": "location", "label": "Location", "type": "text"},
                        {"key": "start_date", "label": "Start", "type": "text"},
                        {"key": "end_date", "label": "End", "type": "text"},
                        {"key": "description", "label": "Description", "type": "text"},
                    ],
                },
                {
                    "key": "education",
                    "title": "Education",
                    "columns": [
                        {"key": "institution", "label": "Institution", "type": "text"},
                        {"key": "qualification", "label": "Qualification", "type": "text"},
                        {"key": "field_of_study", "label": "Field", "type": "text"},
                        {"key": "start_date", "label": "Start", "type": "text"},
                        {"key": "end_date", "label": "End", "type": "text"},
                    ],
                },
                {
                    "key": "projects",
                    "title": "Projects",
                    "columns": [
                        {"key": "name", "label": "Project", "type": "text"},
                        {"key": "description", "label": "Description", "type": "text"},
                        {"key": "technologies", "label": "Technologies", "type": "text"},
                    ],
                },
                {
                    "key": "certifications",
                    "title": "Certifications",
                    "columns": [
                        {"key": "name", "label": "Certification", "type": "text"},
                        {"key": "issuer", "label": "Issuer", "type": "text"},
                        {"key": "date", "label": "Date", "type": "text"},
                    ],
                },
                {
                    "key": "achievements",
                    "title": "Achievements",
                    "columns": [
                        {"key": "title", "label": "Achievement", "type": "text"},
                        {"key": "date", "label": "Date", "type": "text"},
                        {"key": "description", "label": "Description", "type": "text"},
                    ],
                },
                {
                    "key": "languages",
                    "title": "Languages",
                    "columns": [
                        {"key": "language", "label": "Language", "type": "text"},
                        {"key": "proficiency", "label": "Proficiency", "type": "text"},
                    ],
                },
            ],
            "lists": [
                {"key": "skills", "label": "Skills"},
                {"key": "review_notes", "label": "Review notes"},
            ],
        },
    },
}


def get_template(template_id: str) -> dict:
    try:
        return DOCUMENT_TEMPLATES[template_id]
    except KeyError as exc:
        raise KeyError("Unknown extraction template") from exc


def public_templates() -> list[dict]:
    return [
        {
            "id": template["id"],
            "label": template["label"],
            "description": template["description"],
            "schema_version": template["schema_version"],
            "max_pages": DOCUMENT_ANNOTATION_PAGE_LIMIT,
            "layout": deepcopy(template["layout"]),
        }
        for template in DOCUMENT_TEMPLATES.values()
    ]
