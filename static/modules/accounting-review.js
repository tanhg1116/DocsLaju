function accountingValue(value) {
  if (value === null || value === undefined || value === "") return "–";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

export function createProvenanceChip(provenance, path, { iconMarkup, onReveal, compact = false }) {
  if (!provenance) return null;
  const button = document.createElement("button");
  button.type = "button";
  button.className = `provenance-chip ${provenance.kind.replace("_", "-")}${provenance.approved ? " approved" : ""}`;
  button.innerHTML = iconMarkup(
    provenance.kind === "user_edited"
      ? "pencil"
      : provenance.kind === "derived"
        ? "calculator"
        : provenance.approved
          ? "shield-check"
          : "link",
  );
  const label = document.createElement("span");
  label.textContent = provenance.approved ? `${provenance.label} · approved` : provenance.label;
  button.append(label);
  button.setAttribute("aria-label", `${label.textContent}. Show source evidence.`);
  button.title = provenance.source_page
    ? `Source matched on page ${provenance.source_page}`
    : "Document-level annotation; source page was not identified";
  if (compact) button.classList.add("compact");
  button.addEventListener("click", () => onReveal(path));
  return button;
}

export function renderAccountingQueue(elements, items, currentDocumentId, { iconMarkup, onOpen }) {
  elements.list.replaceChildren();
  elements.count.textContent = String(items.length);
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "accounting-empty-checks";
    empty.textContent = "No other saved financial extraction is waiting for review.";
    elements.list.append(empty);
    return;
  }
  for (const item of items.slice(0, 12)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `accounting-queue-item${item.document_id === currentDocumentId ? " current" : ""}`;
    button.innerHTML = iconMarkup(item.warning_count ? "alert-triangle" : "circle-check");
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = item.document_name;
    const detail = document.createElement("small");
    detail.textContent = `${item.warning_count} warning${item.warning_count === 1 ? "" : "s"} · ${item.top_issue}`;
    copy.append(title, detail);
    button.append(copy);
    button.addEventListener("click", () => onOpen(item));
    elements.list.append(button);
  }
}

const relationshipLabels = {
  quotation_to_purchase_order: "Quotation",
  purchase_order_to_delivery: "Delivery order",
  purchase_order_to_invoice: "Invoice",
  purchase_order_to_credit_note: "Credit note",
  purchase_order_to_payment: "Payment",
};

export function renderAccountingMatches(
  elements,
  groups,
  currentDocumentId,
  { iconMarkup, onOpen, onDecision },
) {
  const relevant = groups.filter((group) => (
    group.anchor.document_id === currentDocumentId
    || group.links.some((link) => link.document.document_id === currentDocumentId)
    || (group.rejected_links || []).some((link) => link.document.document_id === currentDocumentId)
  ));
  elements.list.replaceChildren();
  elements.count.textContent = String(relevant.length);
  if (!relevant.length) {
    const empty = document.createElement("p");
    empty.className = "accounting-empty-checks";
    empty.textContent = "No purchasing bundle is linked to this document yet.";
    elements.list.append(empty);
    return;
  }

  for (const group of relevant) {
    const card = document.createElement("article");
    card.className = `accounting-match-group ${group.status}`;
    const header = document.createElement("div");
    header.className = "accounting-match-header";
    const titleButton = document.createElement("button");
    titleButton.type = "button";
    titleButton.className = "accounting-match-anchor";
    titleButton.innerHTML = iconMarkup("package");
    const titleCopy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = group.anchor.reference || group.anchor.document_name;
    const detail = document.createElement("small");
    detail.textContent = `${group.bundle?.document_count || 1} documents · ${group.status.replaceAll("_", " ")}`;
    titleCopy.append(title, detail);
    titleButton.append(titleCopy);
    titleButton.addEventListener("click", () => onOpen(group.anchor));
    const issueCount = document.createElement("span");
    issueCount.className = "accounting-match-issue-count";
    issueCount.textContent = group.issues.length ? `${group.issues.length} issue${group.issues.length === 1 ? "" : "s"}` : "";
    header.append(titleButton, issueCount);
    card.append(header);

    const bundle = group.bundle || {};
    const overview = document.createElement("div");
    overview.className = "accounting-match-overview";
    const invoiceDifference = bundle.invoice_total_difference;
    const stats = [
      [`${bundle.delivery_count || 0} / ${bundle.invoice_count || 0}`, "Deliveries / invoices"],
      [`${bundle.credit_note_count || 0} / ${bundle.payment_count || 0}`, "Credits / payments"],
      [`${bundle.fulfilled_line_count || 0} / ${bundle.ordered_line_count || 0}`, "Delivered lines"],
      [
        invoiceDifference === null || invoiceDifference === undefined
          ? "–"
          : `${bundle.currency || ""} ${accountingValue(invoiceDifference)}`.trim(),
        "Invoice variance",
      ],
      [
        bundle.net_payable === null || bundle.net_payable === undefined
          ? "–"
          : `${bundle.currency || ""} ${accountingValue(bundle.net_payable)}`.trim(),
        "Net payable",
      ],
      [
        bundle.outstanding_balance === null || bundle.outstanding_balance === undefined
          ? "–"
          : `${bundle.currency || ""} ${accountingValue(bundle.outstanding_balance)}`.trim(),
        `Outstanding · ${String(bundle.settlement_status || "unresolved").replaceAll("_", " ")}`,
      ],
    ];
    for (const [value, labelText] of stats) {
      const stat = document.createElement("div");
      const valueElement = document.createElement("strong");
      valueElement.textContent = value;
      const labelElement = document.createElement("span");
      labelElement.textContent = labelText;
      stat.append(valueElement, labelElement);
      overview.append(stat);
    }
    card.append(overview);

    for (const link of [...group.links, ...(group.rejected_links || [])]) {
      const row = document.createElement("div");
      row.className = `accounting-match-link ${link.decision}`;
      const open = document.createElement("button");
      open.type = "button";
      open.className = "accounting-match-document";
      open.innerHTML = iconMarkup(link.decision === "confirmed" ? "circle-check" : "link");
      const copy = document.createElement("span");
      const label = document.createElement("strong");
      label.textContent = `${relationshipLabels[link.relationship]} · ${link.document.reference || link.document.document_name}`;
      const reason = document.createElement("small");
      reason.textContent = `${Math.round(link.score * 100)}% signal score · ${link.reasons.join(" · ")}`;
      copy.append(label, reason);
      open.append(copy);
      open.addEventListener("click", () => onOpen(link.document));
      const actions = document.createElement("div");
      actions.className = "accounting-match-actions";
      for (const [decision, labelText, icon] of [
        ["confirmed", "Confirm match", "check"],
        ["rejected", "Reject match", "x"],
      ]) {
        const action = document.createElement("button");
        action.type = "button";
        action.className = `accounting-match-decision ${decision}`;
        action.innerHTML = iconMarkup(icon);
        action.setAttribute("aria-label", labelText);
        action.title = labelText;
        action.disabled = link.decision === decision;
        action.addEventListener("click", () => onDecision(group.anchor, link, decision));
        actions.append(action);
      }
      row.append(open, actions);
      card.append(row);
    }

    for (const issue of group.issues) {
      const warning = document.createElement("p");
      warning.className = "accounting-match-warning";
      warning.textContent = issue.detail;
      card.append(warning);
    }
    if (group.checks.length) {
      const details = document.createElement("details");
      details.className = "accounting-match-checks";
      const summary = document.createElement("summary");
      summary.textContent = `Calculation evidence · ${group.checks.length} checks`;
      details.append(summary);
      for (const check of group.checks) {
        const row = document.createElement("div");
        row.className = `accounting-match-check ${check.status}`;
        const heading = document.createElement("div");
        const label = document.createElement("strong");
        label.textContent = check.label;
        const status = document.createElement("span");
        status.textContent = check.status;
        heading.append(label, status);
        const evidence = document.createElement("small");
        if (check.formula) {
          const result = check.result === null || check.result === undefined
            ? ""
            : ` · result ${accountingValue(check.result)}`;
          const difference = check.difference === null || check.difference === undefined
            ? ""
            : ` · difference ${accountingValue(check.difference)}`;
          evidence.textContent = `${check.formula}${result}${difference}`;
        } else if (check.ordered_quantity !== undefined) {
          const actualKey = check.delivered_quantity !== undefined ? "delivered_quantity" : "invoiced_quantity";
          evidence.textContent = `Ordered ${accountingValue(check.ordered_quantity)} · ${actualKey.replace("_quantity", "")} ${accountingValue(check[actualKey])} · difference ${accountingValue(check.difference)}`;
        } else {
          evidence.textContent = check.detail || "Not enough reliable values to compare.";
        }
        row.append(heading, evidence);
        details.append(row);
      }
      card.append(details);
    }
    if (group.missing_profiles.length) {
      const missing = document.createElement("p");
      missing.className = "accounting-match-missing";
      missing.textContent = `Still missing: ${group.missing_profiles.map((profile) => profile.replaceAll("_", " ")).join(", ")}`;
      card.append(missing);
    }
    elements.list.append(card);
  }
}

export function renderAccountingReview(elements, review, { iconMarkup, onIssue }) {
  const applicable = Boolean(review?.applicable);
  elements.panel.hidden = !applicable;
  if (!applicable) return;

  elements.summary.replaceChildren();
  const stats = [
    [review.summary.warning_count, "Warnings"],
    [review.summary.calculation_count, "Calculations"],
    [review.summary.duplicate_count, "Similar docs"],
  ];
  for (const [value, label] of stats) {
    const card = document.createElement("div");
    card.className = "accounting-summary-stat";
    const number = document.createElement("strong");
    number.textContent = String(value);
    const caption = document.createElement("span");
    caption.textContent = label;
    card.append(number, caption);
    elements.summary.append(card);
  }

  elements.issueCount.textContent = String(review.issues.length);
  elements.issueList.replaceChildren();
  if (!review.issues.length) {
    const empty = document.createElement("p");
    empty.className = "accounting-empty-checks";
    empty.textContent = "Deterministic checks found no exception. This is not automatic accounting approval.";
    elements.issueList.append(empty);
  } else {
    for (const issue of review.issues) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `accounting-issue ${issue.severity}`;
      button.innerHTML = iconMarkup(issue.severity === "warning" ? "alert-triangle" : "link");
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = issue.title;
      const detail = document.createElement("small");
      detail.textContent = issue.detail;
      copy.append(title, detail);
      button.append(copy);
      button.addEventListener("click", () => onIssue(issue));
      elements.issueList.append(button);
    }
  }

  elements.calculationList.replaceChildren();
  if (!review.calculations.length) {
    const empty = document.createElement("p");
    empty.className = "accounting-empty-checks";
    empty.textContent = "There are not enough extracted values for a transparent calculation.";
    elements.calculationList.append(empty);
  } else {
    for (const calculation of review.calculations) {
      const details = document.createElement("details");
      details.className = "accounting-calculation";
      const summary = document.createElement("summary");
      const label = document.createElement("span");
      label.textContent = calculation.label;
      const status = document.createElement("span");
      status.className = `accounting-calculation-status ${calculation.status}`;
      status.textContent = calculation.status;
      summary.append(label, status);
      const body = document.createElement("div");
      body.className = "accounting-calculation-body";
      const formula = document.createElement("p");
      formula.className = "accounting-formula";
      formula.textContent = calculation.formula;
      body.append(formula);
      const taxAnalysis = calculation.tax_analysis;
      if (taxAnalysis) {
        const taxBasis = document.createElement("div");
        taxBasis.className = "accounting-tax-basis";
        const heading = document.createElement("strong");
        const treatment = taxAnalysis.treatment === "inclusive"
          ? "Included in total"
          : taxAnalysis.treatment === "exclusive"
            ? "Added to subtotal"
            : taxAnalysis.treatment === "not_applicable"
              ? "Not applicable"
              : "Unknown · compare both formulas";
        heading.textContent = `Tax treatment: ${treatment}`;
        const source = document.createElement("span");
        const subtotalBasis = taxAnalysis.subtotal_basis === "net"
          ? "net before tax"
          : taxAnalysis.subtotal_basis === "gross"
            ? "already includes tax"
            : taxAnalysis.subtotal_basis === "not_applicable"
              ? "no tax applied"
              : "unresolved";
        source.textContent = `Evidence: ${String(taxAnalysis.source || "unresolved").replaceAll("_", " ")} · confidence: ${String(taxAnalysis.confidence || "low").replaceAll("_", " ")} · subtotal: ${subtotalBasis}`;
        taxBasis.append(heading, source);
        if (taxAnalysis.evidence) {
          const evidence = document.createElement("q");
          evidence.textContent = taxAnalysis.evidence;
          if (taxAnalysis.source_page) evidence.title = `Found on page ${taxAnalysis.source_page}`;
          taxBasis.append(evidence);
        }
        body.append(taxBasis);
      }
      const values = document.createElement("dl");
      for (const [key, value] of Object.entries(calculation.inputs || {})) {
        const term = document.createElement("dt");
        term.textContent = key.replaceAll("_", " ");
        const definition = document.createElement("dd");
        definition.textContent = accountingValue(value);
        values.append(term, definition);
      }
      for (const [key, value] of [["Calculated", calculation.result], ["Document", calculation.document_value], ["Difference", calculation.difference]]) {
        if (value === null || value === undefined) continue;
        const term = document.createElement("dt");
        term.textContent = key;
        const definition = document.createElement("dd");
        definition.textContent = accountingValue(value);
        values.append(term, definition);
      }
      body.append(values);
      details.append(summary, body);
      elements.calculationList.append(details);
    }
  }
  elements.disclaimer.textContent = review.disclaimer || "";
}
