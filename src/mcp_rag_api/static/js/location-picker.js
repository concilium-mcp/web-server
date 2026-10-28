/* Seletor de local (plan-web-23) — "Toda a base" › coleção › pastas aninhadas, com nº de notas e busca.

   Substitui o <select> de coleção do grafo e entra na busca: com uma coleção só (caso do brian-king), o
   recorte útil são as pastas. Valor = { collection, folderId } (folderId inclui as subpastas no servidor).
   Uso: const picker = createLocationPicker(el, { value, onChange }); picker.set(value) atualiza sem disparar. */
import { api } from "./api.js";
import { t } from "./i18n.js";
import { icon, loadIcons } from "./icons.js";
import { escHtml } from "./ui.js";

const SEP = " › ";
// comparação sem acento e sem caixa ("reuniao" acha "Reunião")
const fold = (s) => String(s).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
// busca por palavras: todas precisam aparecer no caminho, em qualquer ordem/nível
// ("setor 3 area 2" acha "Botucatu › Setor 3 › Area 2", apesar dos separadores no meio)
const matches = (text, words) => words.every((w) => text.includes(w));
// ordem natural ("Setor 2" antes de "Setor 10"), sem diferenciar caixa/acento
const byName = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

let treeCache = null; // { collections, folders } da última abertura (recarrega a cada abertura)

function labelFor(value, data) {
  if (!value?.collection && !value?.folderId) return t("picker.all");
  const folder = value.folderId && data?.folders.find((f) => f.id === value.folderId);
  if (folder) return [folder.collection, ...folder.path.split("/")].join(SEP);
  return value.collection || t("picker.all");
}

function createLocationPicker(container, { value = {}, onChange } = {}) {
  let current = { collection: value.collection || "", folderId: value.folderId || null };
  const open = new Set(); // ids expandidos ("c:<coleção>" e ids de pasta)
  container.classList.add("loc-picker");
  container.innerHTML = `
    <button class="field loc-trigger" type="button" aria-haspopup="dialog" aria-expanded="false">
      ${icon("folder")}<span class="loc-key">${t("picker.label")}</span><span class="loc-value"></span>${icon("chevrons-up-down", 14)}
    </button>
    <div class="loc-pop hidden" role="dialog" aria-label="${t("picker.label")}">
      <input class="input loc-search" type="search" placeholder="${t("picker.search")}" autocomplete="off" />
      <div class="loc-list" role="tree"></div>
    </div>`;
  loadIcons(container);
  const trigger = container.querySelector(".loc-trigger");
  const pop = container.querySelector(".loc-pop");
  const search = container.querySelector(".loc-search");
  const list = container.querySelector(".loc-list");
  const setLabel = () => {
    const label = labelFor(current, treeCache);
    const el = container.querySelector(".loc-value");
    el.textContent = label;
    el.title = label;
  };
  setLabel();

  // abre o caminho até a seleção atual
  const expandToCurrent = (data) => {
    if (current.collection) open.add(`c:${current.collection}`);
    const byId = new Map(data.folders.map((f) => [f.id, f]));
    let f = current.folderId && byId.get(current.folderId);
    if (f) open.add(`c:${f.collection}`);
    while (f && f.parent_id) {
      open.add(f.parent_id);
      f = byId.get(f.parent_id);
    }
  };

  const row = ({ collection, folderId, name, count, depth, hasChildren, expandKey, path }) => {
    const active = (current.folderId || null) === (folderId || null) && (current.collection || "") === (collection || "");
    const caret = hasChildren
      ? `<span class="loc-caret ${open.has(expandKey) ? "open" : ""}" data-toggle="${escHtml(expandKey)}">${icon("chevron-right", 13)}</span>`
      : `<span class="loc-caret"></span>`;
    return `<button type="button" class="loc-row ${active ? "active" : ""}" role="treeitem" style="--depth:${depth}"
        data-collection="${escHtml(collection || "")}" data-folder="${escHtml(folderId || "")}" title="${escHtml(path || name)}">
        ${caret}${icon(folderId ? "folder" : collection ? "notebook-pen" : "layout-dashboard", 14)}
        <span class="grow loc-name">${escHtml(name)}</span><span class="count">${count}</span></button>`;
  };

  const render = () => {
    const data = treeCache;
    if (!data) return;
    const q = fold(search.value.trim());
    const words = q.split(/\s+/).filter(Boolean);
    const rows = [];
    const totalDocs = data.collections.reduce((s, c) => s + c.documents, 0);
    if (!q) rows.push(row({ name: t("picker.all"), count: totalDocs, depth: 0 }));
    const kids = new Map();
    for (const f of data.folders) {
      const key = `${f.collection}|${f.parent_id || ""}`;
      if (!kids.has(key)) kids.set(key, []);
      kids.get(key).push(f);
    }
    for (const list of kids.values()) list.sort((x, y) => byName.compare(x.name, y.name));
    if (q) {
      // busca: lista plana dos caminhos que batem (coleção ou qualquer nível da pasta)
      for (const c of data.collections) {
        if (matches(fold(c.name), words)) rows.push(row({ collection: c.name, name: c.name, count: c.documents, depth: 0 }));
      }
      for (const f of data.folders) {
        const full = [f.collection, ...f.path.split("/")].join(SEP);
        if (matches(fold(full), words)) {
          rows.push(row({ collection: f.collection, folderId: f.id, name: full, count: f.total, depth: 0, path: full }));
        }
      }
    } else {
      const walk = (collection, parentId, depth) => {
        for (const f of kids.get(`${collection}|${parentId || ""}`) || []) {
          const hasChildren = kids.has(`${collection}|${f.id}`);
          rows.push(row({ collection, folderId: f.id, name: f.name, count: f.total, depth, hasChildren, expandKey: f.id, path: f.path }));
          if (hasChildren && open.has(f.id)) walk(collection, f.id, depth + 1);
        }
      };
      for (const c of data.collections) {
        const key = `c:${c.name}`;
        const hasChildren = kids.has(`${c.name}|`);
        rows.push(row({ collection: c.name, name: c.name, count: c.documents, depth: 0, hasChildren, expandKey: key }));
        if (hasChildren && open.has(key)) walk(c.name, null, 1);
      }
    }
    list.innerHTML = rows.join("") || `<div class="loc-empty muted">${t("picker.empty")}</div>`;
    loadIcons(list);
  };

  const close = () => {
    pop.classList.add("hidden");
    trigger.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onOutside, true);
  };
  const onOutside = (e) => !container.contains(e.target) && close();
  const openPop = async () => {
    pop.classList.remove("hidden");
    trigger.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onOutside, true);
    search.value = "";
    search.focus();
    if (!treeCache) list.innerHTML = `<div class="loc-empty muted">${t("common.loading")}</div>`;
    try {
      treeCache = await api.get("/folders");
    } catch {
      if (!treeCache) list.innerHTML = `<div class="loc-empty muted">${t("picker.failed")}</div>`;
      return;
    }
    expandToCurrent(treeCache);
    setLabel();
    render();
    list.querySelector(".loc-row.active")?.scrollIntoView({ block: "nearest" });
  };

  trigger.addEventListener("click", () => (pop.classList.contains("hidden") ? openPop() : close()));
  search.addEventListener("input", render);
  list.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-toggle]");
    if (toggle) {
      e.stopPropagation();
      const key = toggle.dataset.toggle;
      open.has(key) ? open.delete(key) : open.add(key);
      render();
      return;
    }
    const pick = e.target.closest(".loc-row");
    if (!pick) return;
    current = { collection: pick.dataset.collection, folderId: pick.dataset.folder || null };
    setLabel();
    close();
    trigger.focus();
    onChange?.({ ...current });
  });
  // teclado: ↑/↓ entre as linhas (e do campo de busca para a lista), Enter escolhe, Esc fecha
  pop.addEventListener("keydown", (e) => {
    const rows = [...list.querySelectorAll(".loc-row")];
    const i = rows.indexOf(document.activeElement);
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
      trigger.focus();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      rows[Math.min(i + 1, rows.length - 1)]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (i <= 0) search.focus();
      else rows[i - 1].focus();
    } else if (e.key === "Enter" && document.activeElement === search) {
      e.preventDefault();
      rows[0]?.click();
    }
  });

  return {
    set(value) {
      current = { collection: value?.collection || "", folderId: value?.folderId || null };
      setLabel();
    },
    get: () => ({ ...current }),
  };
}

export { createLocationPicker };
