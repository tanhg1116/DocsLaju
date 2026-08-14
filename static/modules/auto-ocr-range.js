export function parseAutoOcrRange(value, totalPages) {
  const source = value.trim();
  if (!source) throw new Error("Enter a page or page range");
  const pages = new Set();
  for (const rawPart of source.split(",")) {
    const part = rawPart.trim();
    const match = part.match(/^(\d+)\s*(?:-\s*(\d+))?$/);
    if (!match) throw new Error("Use ranges such as 1-3, 5, 8-10");
    let start = Number(match[1]);
    let end = Number(match[2] || match[1]);
    if (start > end) [start, end] = [end, start];
    if (start < 1 || end > totalPages) {
      throw new Error(`Pages must be between 1 and ${totalPages}`);
    }
    for (let page = start; page <= end; page += 1) pages.add(page);
  }
  return [...pages].sort((a, b) => a - b);
}

export function fullDocumentRange(document) {
  return document.num_pages === 1 ? "1" : `1-${document.num_pages}`;
}
