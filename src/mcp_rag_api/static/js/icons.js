/* Ícones Lucide (SVG inline) — carrega de static/icons/ sob demanda, com cache. */
/* ---------------------------------------------------------------- ícones Lucide (SVG inline) */

const iconCache = new Map();

async function loadIcons(root = document) {
  const names = [...new Set([...root.querySelectorAll("[data-icon]")].map((el) => el.dataset.icon))];
  await Promise.all(
    names.map(async (name) => {
      if (!iconCache.has(name)) {
        const r = await fetch(`static/icons/${name}.svg`);
        iconCache.set(name, r.ok ? await r.text() : "");
      }
    }),
  );
  for (const el of root.querySelectorAll("[data-icon]")) {
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
