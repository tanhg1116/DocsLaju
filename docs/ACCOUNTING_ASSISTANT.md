# Accounting review assistance

DocsLaju prepares financial documents for human review. It does **not** post
ledger entries, decide statutory tax eligibility, declare compliance, approve
payments, or replace an accountant. It only reconciles how the document displays
tax relative to its totals. The original document, source OCR Markdown, structured
model result, user corrections, and deterministic calculations remain distinct.

## Implemented foundation

For invoice, receipt, quotation, purchase-order, delivery-order, credit-note,
and payment-confirmation templates, the structured workspace now
provides:

- field provenance (`Extracted`, `User edited`, and approval state);
- source-page navigation when a value occurs on exactly one OCR page;
- required-field warnings that never invent missing values;
- line and document-total calculations with visible inputs, formula, rounding,
  result, document value, and difference;
- invoice due-date verification when both ISO dates and explicit `N days`
  payment terms are available;
- explainable possible-duplicate scoring from document reference, party, date,
  and total; and
- a workspace queue of saved financial extractions awaiting attention.

These checks run locally and make zero additional model calls. They are review
signals, not accounting conclusions.

## Provenance model

`document_extractions` remains the current editable structured result.
`document_extraction_revisions` appends an immutable snapshot on every model or
user save. The UI compares the current value with the latest model snapshot:

```text
original upload (unchanged)
  ├── source OCR Markdown (unchanged by rendering/accounting checks)
  ├── model structured revision
  ├── user structured revision(s)
  └── derived review report (never written into extracted values)
```

Evidence matching is deliberately conservative. A field links to a page only
when its value is found on one unique OCR page. If no unique page can be proven,
the UI says that the annotation is document-level rather than guessing.

## Deterministic review boundary

The accounting engine lives in `src/accounting/review.py`. It accepts structured
data and returns JSON containing:

```text
summary
issues[]
calculations[]
provenance{field_path}
duplicates[]
disclaimer
```

Calculations use decimal arithmetic and half-up rounding to two decimal places.
The current tolerance is 0.02 in the document currency. A future company-policy layer may make that
configurable, but document-specific tax eligibility must remain a human choice.
Financial templates preserve `tax_rate`, `tax_treatment`, and the shortest exact
`tax_evidence` phrase. The engine verifies that phrase against saved OCR
Markdown, scans for explicit inclusive/exclusive wording, and evaluates both
tax formulas. An unambiguous phrase or a single reconciling formula can select
the treatment; conflicting or insufficient signals remain `unknown` and both
candidates are shown. Tax is therefore never silently added twice.
Tax treatment and subtotal basis are separate concepts. A tax-inclusive final
price may be represented by either a gross subtotal that already contains tax,
or a net subtotal plus a disclosed included-tax component. The arithmetic engine
tests both representations and records `subtotal_basis` as `gross`, `net`, or
`unknown` instead of assuming that “inclusive” always means a gross subtotal.
When a saved extraction predates these fields, a supported treatment, rate, and
verbatim evidence phrase appear in the form as `Derived from OCR`. These are
view-only suggestions: the stored extraction and its revision history remain
unchanged until the user chooses Save or Approve.
Browser rendering for these results lives separately in
`static/modules/accounting-review.js`; `static/app.js` only supplies navigation
and current-workspace state.

## Phase 2 purchasing chain

Purchase orders now anchor deterministic quotation, delivery-order, and invoice
suggestions. The matcher uses explicit references first, then transparent
secondary signals such as supplier, currency, total, and item overlap. It also
accumulates ordered quantities across multiple delivery orders and invoices,
then checks quantities, unit prices, combined invoice value, and currency. Each
score, formula, input, difference, and mismatch stays visible in the bundle
review. Values in different currencies are never silently summed.

Suggestions are not approvals. Confirm/Reject is an explicit reviewer action
stored in `accounting_match_decisions`; it never changes OCR Markdown or the
structured extraction. Rejected links remain visible so the reviewer can undo a
mistake, and deleting either source document removes its decision by cascade.

Credit notes and payment confirmations extend the same PO-centred bundle.
Credit-note totals reduce the combined invoice value to a visible net payable.
Only payments whose extracted status explicitly indicates completion are
counted; pending or ambiguous statuses remain excluded and generate a warning.
The remaining balance is always shown as a formula with its source values.

## Acceptance scenario

`tests/scenarios/laju_office_solutions/` is the single coherent fixture pack.
It follows Aina, an SME accountant, through the August 2026 close of a fictional
Kuala Lumpur office-equipment company. The manifest ties every quotation,
purchase order, delivery order, invoice, credit note, payment, claim receipt,
bank entry, recurring bill, and controlled failure to that scenario.

Recreate its documents with:

```powershell
uv run --with-requirements requirements.txt python scripts/generate_accounting_scenario.py
```

The generator is local and does not call Mistral.

The explicit provider compatibility check is:

```powershell
py scripts/live_structured_smoke.py receipt.jpg --profile receipt --expect-treatment inclusive --expect-total-match
```

It performs one real OCR request, writes nothing to SQLite, and prints only
normalized tax/reconciliation metadata.

## Deferred features

The following require additional design and acceptance data:

- bank CSV/statement reconciliation and payment-to-bank evidence;
- perceptual-image duplicate scoring in the application (the fixture exists);
- supplier/category memory and user-defined company rules;
- historical anomaly detection;
- optional local classification and language-only LLM assistance.

The planned language-only path is documented under **Lightweight intelligence**
in [`ROADMAP.md`](ROADMAP.md). Its first candidate is DeepSeek V4 Flash in
non-thinking mode, consuming saved OCR Markdown and returning fixed-schema JSON.
It remains an evaluated, configurable provider—not part of OCR and not an
authority over accounting calculations.

Implement these only after the current deterministic review workflow has been
validated with an accountant or bookkeeper.
