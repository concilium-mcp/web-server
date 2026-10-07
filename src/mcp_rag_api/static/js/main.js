/* Bootstrap da dashboard — login, observers e inicialização. Módulo de entrada. */
import { api } from "./api.js";
import { t } from "./i18n.js";
import { icon, loadIcons } from "./icons.js";
import { brandLogo, enhancePasswordFields } from "./ui.js";
import { state, store } from "./state.js";
import { on } from "./events.js";
import { notes, editedNote, DRAFT_PREFIX } from "./views/notes.js";
import { matchNoteRoute, renderShell, renderScreen } from "./router.js";

/* ---------------------------------------------------------------- login */

function renderLogin() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="login-wrap">
      <form class="login-card" id="login-form">
        <div class="brand">${brandLogo(30)}<span>Concilium</span></div>
        <p class="muted">${t("login.intro")}</p>
        <label class="form-label" for="login-user">${t("login.username")}</label>
        <input class="input" id="login-user" type="text" autocomplete="username" required />
        <label class="form-label" for="login-pass">${t("login.password")}</label>
        <input class="input" id="login-pass" type="password" autocomplete="current-password" required />
        <div class="form-error hidden" id="login-error"></div>
        <button class="btn primary" type="submit">${icon("log-in")}<span>${t("login.submit")}</span></button>
      </form>
    </div>`;
  loadIcons(app);
  const form = document.getElementById("login-form");
  const error = document.getElementById("login-error");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.classList.add("hidden");
    try {
      state.user = await api.post("/auth/login", {
        username: document.getElementById("login-user").value,
        password: document.getElementById("login-pass").value,
      });
      renderShell();
      renderScreen();
    } catch (err) {
      error.textContent = err.status === 401 ? t("login.invalid") : t("login.failed");
      error.classList.remove("hidden");
    }
  });
}

// alterações pendentes ao fechar a aba: só persiste o rascunho local (store = localStorage).
// o salvamento no servidor não acontece aqui — ele fica com o botão Salvar/autosave.
window.addEventListener("pagehide", () => {
  if (notes.dirty && notes.current) {
    store.set(DRAFT_PREFIX + notes.current.id, { base_version: notes.current.version, at: new Date().toISOString(), ...editedNote() });
  }
});

// Só varre a árvore quando entra um campo de senha novo (coalescido por requestAnimationFrame):
// digitar no editor de notas (Toast UI) dispara mutação a cada tecla, e uma querySelectorAll
// no documento a cada tecla travava a digitação.
let pwScanQueued = false;
function queuePasswordScan() {
  if (pwScanQueued) return;
  pwScanQueued = true;
  requestAnimationFrame(() => {
    pwScanQueued = false;
    enhancePasswordFields();
  });
}

new MutationObserver((mutations) => {
  for (const m of mutations) {
    for (const node of m.addedNodes) {
      if (!(node instanceof HTMLElement)) continue;
      if (
        node.matches('input[type="password"]:not([data-pw])') ||
        node.querySelector('input[type="password"]:not([data-pw])')
      ) {
        queuePasswordScan();
        return;
      }
    }
  }
}).observe(document.body, { childList: true, subtree: true });

/* ---------------------------------------------------------------- boot */

(async function boot() {
  try {
    state.user = await api.get("/auth/me");
  } catch {
    state.user = null;
  }
  if (!state.user) {
    renderLogin();
    return;
  }
  const deep = matchNoteRoute(location.hash);
  if (deep) {
    // link direto para uma nota abre a nota, não o Painel
    state.screen = "notes";
    notes.openId = deep[1];
  }
  renderShell();
  await renderScreen();
  await loadIcons(document);
})();

// sessão encerrada (logout no menu ou 401 da API): volta para o login
on("session:end", () => {
  state.user = null;
  renderLogin();
});
