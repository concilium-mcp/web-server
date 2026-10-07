/* Shell da dashboard — sidebar, navegação principal e menu do usuário. */
import { api } from "./api.js";
import { t, LANGS, currentLang, setLang } from "./i18n.js";
import { currentTheme, setTheme } from "./theme.js";
import { icon, loadIcons } from "./icons.js";
import { escHtml, brandLogo } from "./ui.js";
import { state } from "./state.js";
import { emit } from "./events.js";
import { notes } from "./views/notes.js";
import { roleName, roleId } from "./views/users.js";

/* ---------------------------------------------------------------- shell (sidebar + conteúdo) */

const NAV = [
  { id: "overview", iconName: "layout-dashboard" },
  { id: "notes", iconName: "notebook-pen" },
  { id: "search", iconName: "search" },
  { id: "graph", iconName: "network" },
  { id: "agents", iconName: "bot" },
];

function navForRole() {
  return NAV.filter((n) => !n.adminOnly || state.user?.role === "admin");
}

function renderShell() {
  const app = document.getElementById("app");
  document.body.dataset.role = state.user?.role || "";
  app.innerHTML = `
    <div class="app">
      <aside class="sidebar">
        <div class="brand">${brandLogo(24)}<span>Concilium</span></div>
        <nav id="nav">
          ${navForRole()
            .map(
              (n) => `
            <button class="nav-item ${state.screen === n.id ? "active" : ""}" data-nav="${n.id}">
              ${icon(n.iconName)}<span>${t(`nav.${n.id}`)}</span>
            </button>`,
            )
            .join("")}
        </nav>
        <div class="nav-spacer"></div>
        <div class="user-footer" id="user-footer"></div>
      </aside>
      <main class="content" id="main"></main>
    </div>`;
  document.getElementById("nav").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-nav]");
    if (btn) emit("navigate", { screen: btn.dataset.nav });
  });
  loadIcons(app.querySelector(".sidebar"));
  renderUserFooter();
}

function renderUserFooter() {
  const footer = document.getElementById("user-footer");
  if (!footer || !state.user) return;
  const u = state.user;
  const isAdmin = u.role === "admin";
  const roleLabel = roleName(u.role);
  const item = (attrs, iconName, label, extra = "") =>
    `<button class="menu-item" role="menuitem" ${attrs}>${icon(iconName)}<span class="grow">${label}</span>${extra}</button>`;
  footer.innerHTML = `
    <div class="user-menu hidden" id="user-menu" role="menu">
      <div class="menu-head">
        <span class="menu-user">${escHtml(u.username)}</span>
        <span class="muted">${roleLabel}</span>
      </div>
      <div class="menu-sep"></div>
      ${item('data-goto="keys"', "key-round", t("menu.keys"))}
      ${isAdmin ? item('data-goto="users"', "users", t("menu.users")) : ""}
      <div class="menu-sep"></div>
      ${item("data-theme-toggle", currentTheme() === "dark" ? "sun" : "moon", currentTheme() === "dark" ? t("menu.lightTheme") : t("menu.darkTheme"))}
      <div class="menu-item static lang-row">${icon("languages")}<span class="grow">${t("menu.language")}</span>
        <span class="lang-seg" role="radiogroup" aria-label="${t("menu.language")}">${LANGS.map(
          (l) => `<button type="button" role="radio" aria-checked="${l.id === currentLang()}" class="${l.id === currentLang() ? "active" : ""}" data-lang="${l.id}" title="${l.name}">${l.label}</button>`,
        ).join("")}</span>
      </div>
      ${item('data-href="/docs"', "book-open", t("menu.docs"), icon("external-link", 14))}
      <div class="menu-item static">${icon("activity")}<span class="grow">${t("menu.status")}</span><span class="status" id="server-status"><span class="status-dot"></span>…</span></div>
      <div class="menu-sep"></div>
      ${item('data-logout', "log-out", t("menu.logout"))}
    </div>
    <button class="user-trigger" id="user-trigger" aria-haspopup="menu" aria-expanded="false">
      <span class="avatar">${escHtml(u.username.slice(0, 1).toUpperCase())}</span>
      <span class="who" title="${escHtml(u.username)}">${escHtml(u.username)} <span class="muted">· ${t(`roleShort.${roleId(u.role)}`)}</span></span>
      ${icon("chevrons-up-down", 14)}
    </button>`;
  loadIcons(footer);

  const menu = footer.querySelector("#user-menu");
  const trigger = footer.querySelector("#user-trigger");
  const setOpen = (open) => {
    if (open) refreshServerStatus();
    menu.classList.toggle("hidden", !open);
    trigger.classList.toggle("open", open);
    trigger.setAttribute("aria-expanded", String(open));
  };
  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(menu.classList.contains("hidden"));
  });
  // fecha ao clicar fora ou com Esc (listeners no document trocados a cada render)
  document.removeEventListener("click", state.menuOutside);
  document.removeEventListener("keydown", state.menuEsc);
  state.menuOutside = (e) => !footer.contains(e.target) && setOpen(false);
  state.menuEsc = (e) => e.key === "Escape" && setOpen(false);
  document.addEventListener("click", state.menuOutside);
  document.addEventListener("keydown", state.menuEsc);

  menu.addEventListener("click", async (e) => {
    const lang = e.target.closest("[data-lang]");
    if (lang) {
      setOpen(false);
      if (lang.dataset.lang === currentLang()) return;
      setLang(lang.dataset.lang);
      // re-renderiza a tela inteira no novo idioma (o editor também lê o idioma na criação)
      emit("navigate", { screen: state.screen, openId: notes.openId });
      return;
    }
    const el = e.target.closest(".menu-item");
    if (!el || el.classList.contains("static")) return;
    setOpen(false);
    if (el.dataset.goto) {
      emit("navigate", { screen: el.dataset.goto });
    } else if ("themeToggle" in el.dataset) {
      setTheme(currentTheme() === "dark" ? "light" : "dark");
      // re-renderiza a tela: editor e grafo leem o tema na criação
      emit("navigate", { screen: state.screen, openId: notes.openId });
    } else if (el.dataset.href) {
      window.open(el.dataset.href, "_blank", "noopener");
    } else if ("logout" in el.dataset) {
      await api.post("/auth/logout").catch(() => {});
      emit("session:end");
    }
  });
}

// só informativo: /health responde {"status":"ok"} quando a API está de pé
async function refreshServerStatus() {
  const box = document.getElementById("server-status");
  if (!box) return;
  let ok = false;
  try {
    const r = await fetch("/health", { cache: "no-store" });
    ok = r.ok && (await r.json()).status === "ok";
  } catch {
    ok = false;
  }
  box.classList.toggle("ok", ok);
  box.innerHTML = `<span class="status-dot"></span>${ok ? t("status.ok") : t("status.unknown")}`;
}

export { renderShell };
