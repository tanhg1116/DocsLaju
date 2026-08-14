export function renderMath(root) {
  if (typeof globalThis.renderMathInElement !== "function") return;
  globalThis.renderMathInElement(root, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "\\(", right: "\\)", display: false },
    ],
    throwOnError: false,
    strict: "ignore",
  });
}

export function formatNumberedEquations(root) {
  for (const paragraph of root.querySelectorAll("p")) {
    const elementChildren = [...paragraph.children];
    if (elementChildren.length !== 1) continue;

    const mathHost = elementChildren[0];
    const katex = mathHost.matches(".katex") ? mathHost : mathHost.querySelector(".katex");
    if (!katex || mathHost.matches(".katex-display") || mathHost.closest(".katex-display")) continue;

    const nodes = [...paragraph.childNodes];
    const mathIndex = nodes.indexOf(mathHost);
    if (mathIndex < 0) continue;
    const beforeNodes = nodes.slice(0, mathIndex);
    const afterNodes = nodes.slice(mathIndex + 1);
    if (![...beforeNodes, ...afterNodes].every((node) => node.nodeType === 3)) continue;

    const before = beforeNodes.map((node) => node.textContent).join("");
    const after = afterNodes.map((node) => node.textContent).join("");
    const labelMatch = before.match(/^\s*(\(?\d+(?:\.\d+)+\)?)\s+$/);
    const punctuationMatch = after.match(/^\s*([.,;:!?]?)\s*$/);
    if (!labelMatch || !punctuationMatch) continue;

    const label = document.createElement("span");
    label.className = "numbered-equation-label";
    label.textContent = labelMatch[1];
    const body = document.createElement("span");
    body.className = "numbered-equation-body";
    body.append(mathHost);
    if (punctuationMatch[1]) body.append(document.createTextNode(punctuationMatch[1]));
    paragraph.classList.add("numbered-equation");
    paragraph.replaceChildren(label, body);
  }
}

function orderedListEnd(list) {
  let value = Number.parseInt(list.getAttribute("start") || "1", 10) - 1;
  for (const item of [...list.children].filter((child) => child.tagName === "LI")) {
    value = item.hasAttribute("value")
      ? Number.parseInt(item.getAttribute("value"), 10)
      : value + 1;
  }
  return value;
}

function formatLetteredSubparts(root) {
  for (const paragraph of root.querySelectorAll("li p")) {
    const directText = [...paragraph.childNodes]
      .filter((node) => node.nodeType === 3)
      .map((node) => node.textContent)
      .join("");
    if (!/^\s*\([a-z]\)\s/i.test(directText)) continue;

    const rows = [];
    let body = null;
    let invalid = false;
    for (const node of [...paragraph.childNodes]) {
      if (node.nodeType !== 3) {
        if (!body) {
          invalid = true;
          break;
        }
        body.append(node);
        continue;
      }

      const text = node.textContent || "";
      const markerPattern = /\(([a-z])\)(?=\s)/gi;
      let cursor = 0;
      let match;
      while ((match = markerPattern.exec(text)) !== null) {
        const preceding = text.slice(cursor, match.index);
        if (body) body.append(document.createTextNode(preceding));
        else if (preceding.trim()) invalid = true;

        const row = document.createElement("span");
        row.className = "exercise-subpart";
        const label = document.createElement("span");
        label.className = "exercise-subpart-label";
        label.textContent = `(${match[1].toLowerCase()})`;
        body = document.createElement("span");
        body.className = "exercise-subpart-body";
        row.append(label, body);
        rows.push(row);
        cursor = markerPattern.lastIndex;
      }
      if (body) body.append(document.createTextNode(text.slice(cursor)));
      else if (text.trim()) invalid = true;
      if (invalid) break;
    }

    if (invalid || !rows.length) continue;
    paragraph.classList.add("exercise-subparts");
    paragraph.replaceChildren(...rows);
  }
}

export function normalizeRenderedLists(root) {
  const lists = [...root.children].filter((child) => child.tagName === "OL");
  for (const list of lists) {
    if (!list.isConnected || list.parentElement !== root) continue;
    while (true) {
      const between = [];
      let next = list.nextElementSibling;
      let blocked = false;
      while (next && next.tagName !== "OL") {
        if (/^H[1-6]$/.test(next.tagName) || next.tagName === "HR") {
          blocked = true;
          break;
        }
        between.push(next);
        next = next.nextElementSibling;
      }
      if (blocked || !next) break;

      const nextStart = Number.parseInt(next.getAttribute("start") || "1", 10);
      if (nextStart !== orderedListEnd(list) + 1) break;
      const lastItem = list.lastElementChild;
      if (!lastItem || lastItem.tagName !== "LI") break;
      for (const block of between) lastItem.append(block);
      for (const item of [...next.children]) list.append(item);
      next.remove();
    }
  }

  for (const list of [...root.children].filter((child) => child.tagName === "OL")) {
    const lastItem = list.lastElementChild;
    if (!lastItem || !/(?:[:;,]|\b(?:then|is|are|equals|where|by))\s*$/i.test(lastItem.textContent.trim())) continue;
    let block = list.nextElementSibling;
    if (!block || block.tagName !== "P" || block.children.length !== 1 || !block.querySelector(".katex-display")) continue;
    let following = block.nextElementSibling;
    lastItem.append(block);
    block = following;
    while (block && block.tagName === "P" && /^[a-z]/.test(block.textContent.trim())) {
      following = block.nextElementSibling;
      lastItem.append(block);
      block = following;
    }
  }

  formatLetteredSubparts(root);
}
