/* Seletor de temas (plan-web-21) — modal com miniatura de cada tema; aplica na hora e salva no navegador. */
import { t } from "./i18n.js";
import { icon, loadIcons } from "./icons.js";
import { THEMES } from "./themes.js";
import { savedThemeChoice, setThemeChoice } from "./theme.js";
import { resetCollectionColors } from "./state.js";

// nome do tema: nome próprio do catálogo (Dracula, Ayu…) ou rótulo traduzido (Concilium, preto e branco)
const themeLabel = (theme) => theme.name || t(`theme.${theme.id}`);

function themeCard(theme, active) {
  const [frame, panel, accent, text] = theme.swatch;
  return `
    <button type="button" class="theme-card ${active ? "active" : ""}" data-theme-id="${theme.id}" aria-pressed="${active}">
      <span class="theme-preview" style="background:${frame}">
        <span class="tp-panel" style="background:${panel}">
          <span class="tp-line" style="background:${text}"></span>
          <span class="tp-line short" style="background:${text}"></span>
          <span class="tp-pill" style="background:${accent}"></span>
        </span>
      </span>
      <span class="theme-name">${themeLabel(theme)}${active ? icon("check", 14) : ""}</span>
    </button>`;
}

function pickerBody(choice) {
  const group = (mode, title) => `
    <div class="theme-group-title">${title}</div>
    <div class="theme-grid">${THEMES.filter((th) => th.mode === mode).map((th) => themeCard(th, th.id === choice)).join("")}</div>`;
  return `
    <button type="button" class="theme-auto ${choice === "auto" ? "active" : ""}" data-theme-id="auto" aria-pressed="${choice === "auto"}">
      ${icon("refresh-cw", 15)}<span class="grow"><strong>${t("theme.auto")}</strong> <span class="muted">${t("theme.autoHint")}</span></span>
      ${choice === "auto" ? icon("check", 15) : ""}
    </button>
    ${group("dark", t("theme.groupDark"))}
    ${group("light", t("theme.groupLight"))}`;
}

// onChange: chamado a cada troca (a tela redesenha para editor/grafo lerem as cores novas)
function openThemePicker(onChange) {
  document.getElementById("theme-picker")?.remove();
  const prevFocus = document.activeElement;
  const root = document.createElement("div");
  root.className = "modal-backdrop";
  root.id = "theme-picker";
  root.innerHTML = `
    <div class="modal theme-picker" role="dialog" aria-modal="true" aria-labelledby="tp-title" style="width:min(760px, 100%)">
      <header class="modal-head">
        <span class="row-icon">${icon("palette")}</span>
        <div class="grow">
          <h2 id="tp-title">${t("theme.title")}</h2>
          <span class="muted">${t("theme.sub")}</span>
        </div>
        <button class="icon-btn" type="button" data-close title="${t("common.closeEsc")}">${icon("x", 18)}</button>
      </header>
      <div class="modal-body" id="tp-body">${pickerBody(savedThemeChoice())}</div>
    </div>`;
  document.body.appendChild(root);
  loadIcons(root);

  const close = () => {
    document.removeEventListener("keydown", onKey, true);
    root.remove();
    prevFocus?.focus?.();
  };
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
    }
  };
  document.addEventListener("keydown", onKey, true);
  root.addEventListener("click", (e) => {
    if (e.target === root || e.target.closest("[data-close]")) return close();
    const pick = e.target.closest("[data-theme-id]");
    if (!pick) return;
    setThemeChoice(pick.dataset.themeId);
    resetCollectionColors();
    const body = root.querySelector("#tp-body");
    body.innerHTML = pickerBody(savedThemeChoice());
    loadIcons(body);
    body.querySelector(`[data-theme-id="${pick.dataset.themeId}"]`)?.focus();
    onChange?.();
  });
  (root.querySelector(".theme-card.active, .theme-auto.active") || root.querySelector("[data-theme-id]"))?.focus();
}

export { openThemePicker, themeLabel };
