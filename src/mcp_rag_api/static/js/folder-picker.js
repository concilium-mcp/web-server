/* Seletor de pastas principais com busca (plan-web-36): substitui a parede de etiquetas. Lista rolável
   com as marcadas primeiro, campo de busca e o resumo "Todas as pastas" / "N selecionadas". Os valores
   continuam em <input type="checkbox" value="..."> dentro do contêiner com o id pedido, então quem lia
   `#id input:checked` segue igual. Comportamento por delegação: não precisa ligar nada depois do render. */
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { escHtml } from "./ui.js";
import { colorFor } from "./state.js";

function summary(count) {
  return count ? t("picker.selected", { count }) : t("picker.all");
}

function folderPickerHtml(id, collections, selected = []) {
  if (!collections.length) return `<div class="folder-picker empty" id="${id}"><span class="muted">${t("agents.noCollections")}</span></div>`;
  const chosen = new Set(selected);
  const sorted = [...collections].sort((a, b) => Number(chosen.has(b.name)) - Number(chosen.has(a.name)) || a.name.localeCompare(b.name));
  return `
    <div class="folder-picker" id="${id}">
      <div class="fp-head">
        <label class="fp-search">${icon("search", 14)}<input type="search" class="fp-q" placeholder="${t("picker.search")}" autocomplete="off" /></label>
        <span class="fp-count">${summary(collections.filter((c) => chosen.has(c.name)).length)}</span>
      </div>
      <div class="fp-list">
        ${sorted
          .map(
            (c) => `
          <label class="fp-row" data-name="${escHtml(c.name.toLowerCase())}">
            <input type="checkbox" value="${escHtml(c.name)}" ${chosen.has(c.name) ? "checked" : ""} />
            <span class="dot" style="background:${colorFor(c.name)}"></span>
            <span class="fp-name">${escHtml(c.name)}</span>
            <span class="fp-n">${c.documents}</span>
          </label>`,
          )
          .join("")}
        <div class="fp-empty hidden">${t("picker.none")}</div>
      </div>
    </div>`;
}

document.addEventListener("input", (e) => {
  const q = e.target.closest?.(".fp-q");
  if (!q) return;
  const box = q.closest(".folder-picker");
  const needle = q.value.trim().toLowerCase();
  let shown = 0;
  box.querySelectorAll(".fp-row").forEach((row) => {
    const hit = !needle || row.dataset.name.includes(needle);
    row.classList.toggle("hidden", !hit);
    shown += hit ? 1 : 0;
  });
  box.querySelector(".fp-empty").classList.toggle("hidden", shown > 0);
});

document.addEventListener("change", (e) => {
  const box = e.target.closest?.(".folder-picker");
  if (!box || e.target.type !== "checkbox") return;
  box.querySelector(".fp-count").textContent = summary(box.querySelectorAll(".fp-row input:checked").length);
});

export { folderPickerHtml };
