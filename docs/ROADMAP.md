# Accounting assistant roadmap

This roadmap keeps DocsLaju review-first: the software prepares evidence and
calculations, while an accountant confirms every accounting relationship.

## Phase 1 — document review foundation (complete)

Completed and regression-tested:

- versioned structured extraction for invoices, receipts, and quotations;
- immutable model/user revisions and field provenance;
- required-field, line-total, document-total, date, and duplicate checks;
- tax-inclusive/exclusive evidence with separate net/gross subtotal handling;
- display-only derived tax suggestions that persist only after Save/Approve;
- source-page navigation and a workspace exception queue; and
- a local SME acceptance scenario plus a live provider smoke test.

On 15 August 2026, the real `receipt.jpg` smoke test returned tax 3.85 at 7.6%,
identified the tax as included, reconciled the net subtotal to the 54.50 total,
and produced zero warnings. Normal tests remain provider-free.

## Phase 2 — purchasing document chain (complete)

The first usable vertical slice is implemented:

- Purchase Order and Delivery Order structured templates;
- invoice purchase-order reference extraction;
- PO-centred quotation, delivery, and invoice suggestions;
- explainable scores from explicit references, supplier, currency, totals, and
  item overlap;
- ordered-versus-delivered/invoiced quantity checks; and
- durable Confirm/Reject decisions stored separately from extracted fields.

The second vertical slice is also implemented:

- quantities accumulate across multiple partial delivery orders and invoices;
- combined invoice totals are compared with the PO using the declared 0.02
  tolerance;
- unit-price, quantity, total, and currency conflicts become explicit issues;
- cross-currency values are never silently added; and
- the structured panel presents one consolidated bundle with its document
  counts, fulfilment progress, invoice variance, and expandable calculation
  evidence.

The settlement slice completes Phase 2:

- Credit Note and Payment Confirmation fixed templates;
- invoice-reference and credit-reference matching inside the PO bundle;
- combined invoice total minus credit-note total produces net payable;
- only explicitly completed payments contribute to paid value;
- net payable minus completed payments produces the outstanding balance;
- partial payment, overpayment, unresolved status, missing values, and currency
  conflicts remain visible review issues; and
- the FurniPro acceptance chain reconciles MYR 3,564.00 minus MYR 345.60 to the
  MYR 3,218.40 payment while independently retaining its delivery shortfall.

Phase 3 is the next implementation phase.

## Later phases

- **Phase 3:** bank CSV reconciliation and outstanding-balance tracking.
- **Phase 4:** supplier/category memory and user-controlled company rules.
- **Phase 5:** historical anomaly signals and the lightweight-intelligence
  capabilities described below.
- **Production track (separate):** authentication, tenancy, security, durable
  multi-process workers, storage policies, and deployment.

## Lightweight intelligence

Use small or economical models only where deterministic code is insufficient.
The first planned capability is low-cost structured extraction from existing
OCR Markdown:

```text
PDF or image
  → Mistral OCR once
  → saved, unchanged OCR Markdown
  → DeepSeek V4 Flash (non-thinking mode)
  → fixed document-type JSON schema
  → local normalization and deterministic accounting checks
  → DeepSeek V4 Pro or existing Mistral extraction only on validated failure
```

This design avoids a second OCR pass. Flash is the default candidate because
structured field mapping usually does not require a reasoning-heavy model. A
fallback is allowed only for explicit conditions such as invalid JSON, a schema
failure, missing essential fields, or a difficult table—not merely because a
document is long. The model must never edit raw OCR Markdown, perform accounting
approval, or override deterministic calculations.

Before enabling DeepSeek by default, compare it with the existing Mistral
structured extraction on the scenario documents. Record schema-valid rate,
required-field accuracy, exact numeric accuracy, latency, token cost, and
fallback frequency. Keep the provider configurable because this introduces a
second external data processor and separate credentials/data-handling terms.

Other later lightweight-intelligence candidates include user-confirmed supplier
or category suggestions, explanation of deterministic exceptions, and anomaly
triage. Calculators, reference matching, and accounting reconciliation remain
local deterministic code.
