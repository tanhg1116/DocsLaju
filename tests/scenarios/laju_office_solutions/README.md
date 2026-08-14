# Laju Office Solutions scenario

This is a fully synthetic acceptance-test pack for DocsLaju. It represents Aina,
the accountant of **Laju Office Solutions Sdn. Bhd.**, closing August 2026 for a
25-person Kuala Lumpur SME. Every file belongs to that one story; no identifier,
address, bank account, or registration number is real.

## Scenario flows

1. **Accounts payable:** FurniPro quotation → purchase order → delivery order →
   supplier invoice → credit note → payment confirmation → bank entry.
2. **Employee claim:** one claim form and five supporting receipts, including a
   deliberately overstated meal claim and duplicate parking receipt variants.
3. **Accounts receivable:** customer quotation → invoice → partial payment advice
   → bank entry.
4. **Recurring costs:** historical electricity bills followed by an unusually
   large August bill, internet and software subscriptions.
5. **Controlled exceptions:** an arithmetic error, missing invoice reference,
   foreign-currency invoice, partial payment, damaged PDF, skewed/blurred images,
   and exact/visually similar duplicates.

Run `scripts/generate_accounting_scenario.py` to recreate the synthetic PDFs and
images under `documents/`. The generator never calls Mistral.

## Ground truth

- `scenario_manifest.json` links every document to its transaction and expected
  structured fields.
- `company_rules.json` contains test assumptions, not Malaysian tax advice.
- `expected_issues.json` lists deliberately planted review exceptions.
- `expected_calculations.json` records formulas and expected results.
- `bank_statement_august_2026.csv` supports future reconciliation testing.
- `reviewer_walkthrough.md` defines the acceptance-test actions and outcomes.

OCR quality itself is not the benchmark. The pack tests whether DocsLaju keeps
successful pages, prevents duplicate processing, preserves corrections, explains
calculations, surfaces known exceptions, and exports approved data reliably.
