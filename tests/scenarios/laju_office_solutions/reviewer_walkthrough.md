# Acceptance walkthrough

1. Create a project named **August 2026 close** and upload the scenario documents.
2. Confirm exact duplicate upload reuses the existing document without an OCR
   call. Confirm the recompressed copy remains reviewable as a possible duplicate.
3. Run Auto-OCR, switch pages during processing, stop it, restart Flask, and
   resume. Completed pages must remain completed and must not be resubmitted.
4. Choose the appropriate structured type for supported invoices, receipts,
   quotations, purchase orders, delivery orders, credit notes, and payment
   confirmations. Original Markdown must remain unchanged when fields are edited.
5. Open **Review assistance**. Verify the manifest's missing fields and arithmetic
   mismatch appear, with exact formulas and document values.
6. Edit a field, save it, and confirm its provenance changes from **Extracted** to
   **User edited**. Approving it must add approval state without hiding provenance.
7. Use a provenance badge. DocsLaju should navigate to the uniquely matched page
   when possible; it must say document-level evidence when no unique page exists.
8. Confirm the workspace queue can navigate between unresolved financial files.
9. Open the FurniPro purchasing bundle. Verify the delivery shortfall remains
   visible while `3,564.00 - 345.60 = 3,218.40` reconciles to the completed
   payment with zero outstanding balance.
10. Delete a document and verify its OCR pages, extracted assets, structured
   revisions, jobs, and review-queue entry disappear through database cascades.
11. Export Markdown/PDF/combined output and confirm approved content and owned
    images remain available.

## Infrastructure acceptance

- No duplicate active claim exists for one document/page.
- A failed page does not erase successful pages.
- Queue priority still favours the manually requested current page.
- Scheduler telemetry survives a Flask restart.
- `/api/state` remains metadata-only and does not return document BLOBs.
- Accounting checks make zero additional model calls.
