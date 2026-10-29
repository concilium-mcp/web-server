/* Ícones Lucide (SVG inline) — carrega de static/icons/ sob demanda, com cache. */
/* ---------------------------------------------------------------- ícones Lucide (SVG inline) */

const iconCache = new Map();
const ICON_NAME_RE = /^[a-z0-9-]+$/;

// Só ícones da própria interface: o conteúdo de uma nota pode trazer data-icon (o DOMPurify
// mantém data-*), e um nome com "../" faria o fetch buscar outra rota e jogar a resposta crua no DOM.
const iconElements = (root) =>
  [...root.querySelectorAll("[data-icon]")].filter(
    (el) => ICON_NAME_RE.test(el.dataset.icon) && !el.closest(".toastui-editor-contents"),
  );

async function loadIcons(root = document) {
  const els = iconElements(root);
  const names = [...new Set(els.map((el) => el.dataset.icon))];
  await Promise.all(
    names.map(async (name) => {
      if (!iconCache.has(name)) {
        const r = await fetch(`static/icons/${name}.svg`);
        iconCache.set(name, r.ok ? await r.text() : "");
      }
    }),
  );
  for (const el of els) {
    el.innerHTML = iconCache.get(el.dataset.icon) || "";
    const svg = el.querySelector("svg");
    if (svg) {
      svg.setAttribute("width", el.dataset.size || "16");
      svg.setAttribute("height", el.dataset.size || "16");
      svg.removeAttribute("class");
    }
  }
}

const icon = (name, size = 16) => `<span data-icon="${name}" data-size="${size}"></span>`;

export { loadIcons, icon };
