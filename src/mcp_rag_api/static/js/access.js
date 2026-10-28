/* Acesso em linguagem simples — atalhos (só leitura / leitura e escrita / administrador) por cima dos
   escopos da API. Usado pelos formulários de chave e de agente e pelos chips das listas. */
import { t } from "./i18n.js";
import { escHtml } from "./ui.js";

// [escopo, descrição, rótulo curto]
const SCOPES = [
  ["read", "scope.read", "scope.short.read"],
  ["write", "scope.write", "scope.short.write"],
  ["agents:manage", "scope.agentsManage", "scope.short.agentsManage"],
  ["admin", "scope.admin", "scope.short.admin"],
];

// atalhos: cada um é um conjunto exato de escopos
const ACCESS_PRESETS = [
  { id: "read", scopes: ["read"] },
  { id: "write", scopes: ["read", "write"] },
  { id: "admin", scopes: ["read", "write", "agents:manage", "admin"] },
];

const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
// "admin" já é acesso total: qualquer combinação que o inclua conta como Administrador
const accessPreset = (scopes) => {
  const list = scopes || [];
  if (list.includes("admin")) return ACCESS_PRESETS[2];
  return ACCESS_PRESETS.find((p) => sameSet(p.scopes, list));
};

// chips para listas: o atalho em linguagem simples ou, fora deles, um chip curto por escopo (nome técnico no title)
function accessChips(scopes) {
  const list = scopes || [];
  const preset = accessPreset(list);
  if (preset) return `<span class="chip ${preset.id === "admin" ? "accent" : ""}" title="${escHtml(list.join(", "))}">${t(`access.${preset.id}`)}</span>`;
  return list
    .map((s) => {
      const def = SCOPES.find((x) => x[0] === s);
      return `<span class="chip ${s === "admin" ? "accent" : ""}" title="${escHtml(s)}">${def ? t(def[2]) : escHtml(s)}</span>`;
    })
    .join("");
}

// cartões de escolha única (atalhos). prefix isola os ids/names por formulário
function accessPresetsHtml(prefix, selected) {
  return `
    <div class="scope-grid three" id="${prefix}-access">
      ${ACCESS_PRESETS.map(
        (p) => `
        <label class="scope-option">
          <input type="radio" name="${prefix}-access" value="${p.id}" ${p.id === selected ? "checked" : ""} />
          <span><span>${t(`access.${p.id}`)}</span><span class="muted">${t(`access.${p.id}Desc`)}</span></span>
        </label>`,
      ).join("")}
    </div>`;
}

// escopos da API (fonte da verdade do formulário) — fica dentro do "Ver mais"
function scopesBlockHtml(prefix, initial) {
  return `
    <div class="form-field">
      <span class="form-label">${t("access.scopes")}</span>
      <div class="scope-grid two" id="${prefix}-scopes">
        ${SCOPES.map(
          ([sc, desc]) => `
          <label class="scope-option">
            <input type="checkbox" value="${sc}" ${initial.includes(sc) ? "checked" : ""} />
            <span><span class="mono">${sc}</span><span class="muted">${t(desc)}</span></span>
          </label>`,
        ).join("")}
      </div>
      <span class="form-help">${t("access.scopesHelp")}</span>
      <span class="form-help access-custom hidden" id="${prefix}-custom">${t("access.custom")}</span>
    </div>`;
}

// atalho marca os escopos; mexer nos escopos reseleciona o atalho correspondente (ou nenhum = personalizado)
function bindAccessPicker(form, prefix) {
  const radios = [...form.querySelectorAll(`input[name="${prefix}-access"]`)];
  const checks = [...form.querySelectorAll(`#${prefix}-scopes input`)];
  const custom = form.querySelector(`#${prefix}-custom`);
  const syncFromChecks = () => {
    const preset = accessPreset(checks.filter((c) => c.checked).map((c) => c.value));
    radios.forEach((r) => (r.checked = Boolean(preset) && r.value === preset.id));
    custom?.classList.toggle("hidden", Boolean(preset));
  };
  radios.forEach((r) =>
    r.addEventListener("change", () => {
      const preset = ACCESS_PRESETS.find((p) => p.id === r.value);
      checks.forEach((c) => (c.checked = preset.scopes.includes(c.value)));
      custom?.classList.add("hidden");
    }),
  );
  checks.forEach((c) => c.addEventListener("change", syncFromChecks));
  syncFromChecks();
}

const readScopes = (form, prefix) => [...form.querySelectorAll(`#${prefix}-scopes input:checked`)].map((i) => i.value);

export { SCOPES, ACCESS_PRESETS, accessPreset, accessChips, accessPresetsHtml, scopesBlockHtml, bindAccessPicker, readScopes };
