export function normalizedTemplateSearch(value) {
  return value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
}

export function templateSearchText(template) {
  const aliases = template.id === "resume" ? "cv resume curriculum vitae" : "";
  return normalizedTemplateSearch(
    `${template.id} ${template.label} ${template.description} ${aliases}`,
  );
}

export function numberOrNull(value) {
  return value === "" ? null : Number(value);
}
