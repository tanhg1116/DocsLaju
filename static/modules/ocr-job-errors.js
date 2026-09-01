function pageRanges(pageNumbers) {
  const pages = [...new Set(pageNumbers)]
    .filter((page) => Number.isInteger(page))
    .sort((left, right) => left - right);
  const ranges = [];
  for (let index = 0; index < pages.length; index += 1) {
    const start = pages[index];
    let end = start;
    while (index + 1 < pages.length && pages[index + 1] === end + 1) {
      index += 1;
      end = pages[index];
    }
    ranges.push(start === end ? `${start}` : `${start}–${end}`);
  }
  return ranges.join(", ");
}

export function ocrRecoveryMessage(job) {
  const unfinished = (job?.pages || []).filter((page) =>
    ["failed", "cancelled"].includes(page.status)
  );
  const count = unfinished.length || (job?.failed_pages || 0) + (job?.cancelled_pages || 0);
  const headline = `${count} page${count === 1 ? "" : "s"} did not finish.`;
  const groupedErrors = new Map();

  for (const page of unfinished) {
    const detail = String(page.error || "").trim()
      || (page.status === "cancelled" ? "OCR was cancelled before this page completed." : "No error detail was provided.");
    if (!groupedErrors.has(detail)) groupedErrors.set(detail, []);
    groupedErrors.get(detail).push(Number(page.page_number));
  }

  // Older persisted jobs may only have a job-level error.
  if (!groupedErrors.size && job?.error) {
    groupedErrors.set(String(job.error).trim(), []);
  }

  const details = [...groupedErrors].map(([error, pages]) => {
    const range = pageRanges(pages);
    return range ? `Page${pages.length === 1 ? "" : "s"} ${range}: ${error}` : error;
  });
  return [headline, ...details, "Resolve the error, then retry only those pages."].join("\n");
}
