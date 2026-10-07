/* Helpers de UI — escape, datas, debounce, moldura de modal, diálogos e campos de senha. */
import { t, locale } from "./i18n.js";
import { icon, loadIcons } from "./icons.js";

const escHtml = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString(locale()) : "—");

function fmtAgo(iso) {
  if (!iso) return t("ago.never");
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return t("ago.now");
  const m = Math.round(s / 60);
  if (m < 60) return t("ago.minutes", { count: m });
  const h = Math.round(m / 60);
  if (h < 24) return t("ago.hours", { count: h });
  const d = Math.round(h / 24);
  return d < 30 ? t("ago.days", { count: d }) : new Date(iso).toLocaleDateString(locale());
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/* ---------------------------------------------------------------- modais de formulário */

// Moldura padrão: cabeçalho (ícone, título, subtítulo, fechar), corpo rolável e rodapé com ações.
function modalShell({ id, iconName, title, subtitle = "", body, submitLabel, submitIcon = "plus", width = 880 }) {
  return `
    <div class="modal-backdrop hidden" id="${id}-modal">
      <form class="modal" id="${id}-form" role="dialog" aria-modal="true" aria-labelledby="${id}-title" style="width:min(${width}px, 100%)">
        <header class="modal-head">
          <span class="row-icon">${icon(iconName)}</span>
          <div class="grow">
            <h2 id="${id}-title">${title}</h2>
            ${subtitle ? `<span class="muted">${subtitle}</span>` : ""}
          </div>
          <button class="icon-btn" type="button" data-close-modal title="${t("common.closeEsc")}">${icon("x", 18)}</button>
        </header>
        <div class="modal-body">${body}</div>
        <footer class="modal-foot">
          <button class="btn" type="button" data-close-modal>${t("common.cancel")}</button>
          <button class="btn primary" type="submit">${icon(submitIcon)}<span>${submitLabel}</span></button>
        </footer>
      </form>
    </div>`;
}

// Liga abrir/fechar (botão, ×, Cancelar, Esc, clique fora). Devolve setOpen(bool).
function bindModal(id, { trigger, onOpen } = {}) {
  const modal = document.getElementById(`${id}-modal`);
  const onEsc = (e) => e.key === "Escape" && setOpen(false);
  function setOpen(open) {
    modal.classList.toggle("hidden", !open);
    if (open) {
      document.addEventListener("keydown", onEsc);
      onOpen?.();
      modal.querySelector("input, textarea, select")?.focus();
    } else {
      document.removeEventListener("keydown", onEsc);
    }
  }
  trigger?.addEventListener("click", () => setOpen(true));
  modal.querySelectorAll("[data-close-modal]").forEach((b) => b.addEventListener("click", () => setOpen(false)));
  // só fecha se o clique começou no fundo (não ao arrastar seleção de texto para fora)
  let downOnBackdrop = false;
  modal.addEventListener("mousedown", (e) => (downOnBackdrop = e.target === modal));
  modal.addEventListener("click", (e) => {
    if (downOnBackdrop && e.target === modal) setOpen(false);
  });
  return setOpen;
}

/* ---------------------------------------------------------------- diálogos (substituem alert/confirm) */

// Abre um diálogo no tema da dash e resolve true (confirmou) / false (cancelou, Esc, clique fora).
function uiDialog({ title, message = "", confirmLabel = t("common.ok"), cancelLabel = null, tone = "default", iconName = null }) {
  return new Promise((resolve) => {
    const prevFocus = document.activeElement;
    const root = document.createElement("div");
    root.className = "modal-backdrop dialog-backdrop";
    const ico = iconName || (tone === "danger" ? "triangle-alert" : null);
    root.innerHTML = `
      <div class="modal dialog" role="${cancelLabel ? "alertdialog" : "dialog"}" aria-modal="true" aria-labelledby="dlg-title" aria-describedby="dlg-msg">
        <div class="dialog-body">
          ${ico ? `<span class="dialog-icon ${tone}">${icon(ico, 18)}</span>` : ""}
          <div class="grow">
            <h2 id="dlg-title">${escHtml(title)}</h2>
            ${message ? `<p id="dlg-msg">${escHtml(message)}</p>` : ""}
          </div>
        </div>
        <footer class="modal-foot">
          ${cancelLabel ? `<button class="btn" type="button" data-answer="0">${escHtml(cancelLabel)}</button>` : ""}
          <button class="btn ${tone === "danger" ? "danger-solid" : "primary"}" type="button" data-answer="1">${escHtml(confirmLabel)}</button>
        </footer>
      </div>`;
    document.body.appendChild(root);
    loadIcons(root);

    const close = (answer) => {
      window.removeEventListener("keydown", onKey, true);
      root.remove();
      prevFocus?.focus?.();
      resolve(answer);
    };
    // captura: o Esc fecha só o diálogo, não o modal que estiver por baixo
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        e.preventDefault();
        close(false);
      }
    };
    window.addEventListener("keydown", onKey, true);
    root.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-answer]");
      if (btn) close(btn.dataset.answer === "1");
      else if (e.target === root) close(false);
    });
    root.querySelector('[data-answer="1"]').focus();
  });
}

const uiConfirm = (opts) => uiDialog({ cancelLabel: t("common.cancel"), ...opts });
const uiAlert = (title, message) => uiDialog({ title, message });
const uiError = (title, err) => uiDialog({ title, message: err?.detail || err?.message || String(err), tone: "danger" });

// logo da marca (static/brand/logo.svg); é imagem, não ícone Lucide: não herda currentColor
const brandLogo = (size = 22) => `<img class="brand-logo" src="static/brand/logo.svg" width="${size}" height="${size}" alt="" />`;

/* ---------------------------------------------------------------- campos de senha: botão de mostrar/ocultar */

// Envolve todo input[type=password] (inclusive os criados depois) com o botão de olho.
function enhancePasswordFields(root = document) {
  for (const input of root.querySelectorAll('input[type="password"]:not([data-pw])')) {
    input.dataset.pw = "1";
    const wrap = document.createElement("span");
    wrap.className = "pw-field";
    input.replaceWith(wrap);
    wrap.appendChild(input);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-toggle";
    btn.tabIndex = -1; // Tab segue do campo para o próximo controle do formulário
    btn.title = t("password.show");
    btn.setAttribute("aria-label", t("password.show"));
    btn.innerHTML = icon("eye");
    wrap.appendChild(btn);
    loadIcons(btn);
    btn.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.title = show ? t("password.hide") : t("password.show");
      btn.setAttribute("aria-label", btn.title);
      btn.innerHTML = icon(show ? "eye-off" : "eye");
      loadIcons(btn);
      input.focus();
    });
  }
}

export { escHtml, fmtDate, fmtAgo, debounce, modalShell, bindModal, uiDialog, uiConfirm, uiAlert, uiError, brandLogo, enhancePasswordFields };
