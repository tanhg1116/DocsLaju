function renderDocumentMath() {
  if (typeof globalThis.renderMathInElement !== "function") return;
  globalThis.renderMathInElement(document.querySelector("#printDocument"), {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "\\(", right: "\\)", display: false },
    ],
    throwOnError: false,
    strict: "ignore",
  });
}

async function waitForDocumentImages() {
  const images = [...document.images];
  const results = await Promise.all(images.map(async (image) => {
    if (image.complete) return image.naturalWidth > 0;
    return new Promise((resolve) => {
      image.addEventListener("load", () => resolve(true), { once: true });
      image.addEventListener("error", () => resolve(false), { once: true });
    });
  }));
  return results.every(Boolean);
}

function fitWideDisplayMath() {
  document.querySelectorAll(".katex-display > .katex").forEach((math) => {
    math.style.fontSize = "";
    const containerWidth = math.parentElement?.clientWidth || 0;
    const contentWidth = math.scrollWidth;
    if (!containerWidth || contentWidth <= containerWidth) return;
    const scale = Math.max(0.68, containerWidth / contentWidth);
    math.style.fontSize = `${scale}em`;
  });
}

function afterLayout() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

async function preparePrintDocument() {
  const printButton = document.querySelector("#printButton");
  const printStatus = document.querySelector("#printStatus");
  renderDocumentMath();
  const imagesLoaded = await waitForDocumentImages();
  if (document.fonts?.ready) await document.fonts.ready;
  await afterLayout();
  fitWideDisplayMath();
  await afterLayout();

  printButton.disabled = false;
  printStatus.textContent = imagesLoaded
    ? "Ready. Choose Save as PDF in the print dialog."
    : "Ready, but at least one image could not be loaded.";
  printButton.addEventListener("click", () => window.print());

  if (document.body.dataset.autoPrint === "true") {
    setTimeout(() => window.print(), 250);
  }
}

window.addEventListener("load", () => {
  preparePrintDocument().catch((error) => {
    const printButton = document.querySelector("#printButton");
    document.querySelector("#printStatus").textContent = `Could not finish preparing: ${error.message}`;
    printButton.disabled = false;
    printButton.addEventListener("click", () => window.print());
  });
});
