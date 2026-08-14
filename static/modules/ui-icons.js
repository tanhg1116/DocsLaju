export function iconMarkup(name, extraClass = "") {
  return `<svg class="ui-icon${extraClass ? ` ${extraClass}` : ""}" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
}

export function setIconButton(button, label, iconName = null, spinning = false) {
  button.setAttribute("aria-label", label);
  button.dataset.tooltip = label;
  if (iconName) button.querySelector("use")?.setAttribute("href", `#icon-${iconName}`);
  button.classList.toggle("icon-spinning", spinning);
}
