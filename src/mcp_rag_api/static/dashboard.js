/* Concilium Dashboard — SPA sem build. Fatia 1: grafo da base RAG (force-graph vendored). */

const api = {
  async req(method, path, body) {
    const r = await fetch(`/dash/api${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401 && state.user) {
      // sessão caiu (expirou/revogada): volta para o login
      state.user = null;
      renderLogin();
      throw new Error(t("common.sessionExpired"));
    }
    if (!r.ok) throw Object.assign(new Error(`${r.status}`), { status: r.status, detail: (await r.json().catch(() => ({}))).detail });
    return r.status === 204 ? null : r.json();
  },
  get(path, params) {
    const qs = params ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "")) : "";
    return this.req("GET", `${path}${qs}`);
  },
  post(path, body) {
    return this.req("POST", path, body ?? {});
  },
  patch(path, body) {
    return this.req("PATCH", path, body ?? {});
  },
  del(path) {
    return this.req("DELETE", path);
  },
};

/* ---------------------------------------------------------------- idioma (pt-BR / en / es) */

// dicionários em static/i18n/<idioma>.js; chave ausente cai no pt-BR e, por fim, na própria chave.
// A escolha fica no navegador; sem escolha salva, segue o idioma do navegador (o <head> aplica).
const LANG_KEY = "concilium:lang";
const LANGS = [
  { id: "pt-BR", label: "PT", name: "Português" },
  { id: "en", label: "EN", name: "English" },
  { id: "es", label: "ES", name: "Español" },
];
// locale do Intl (datas/números) e do editor Toast UI para cada idioma da interface
const LOCALES = { "pt-BR": "pt-BR", en: "en-US", es: "es-ES" };

function currentLang() {
  const lang = document.documentElement.lang;
  return LANGS.some((l) => l.id === lang) ? lang : "pt-BR";
}

const locale = () => LOCALES[currentLang()];

function setLang(lang) {
  document.documentElement.lang = lang;
  try {
    localStorage.setItem(LANG_KEY, JSON.stringify(lang));
  } catch {
    /* sem armazenamento local: vale só nesta aba */
  }
}

// t("keys.counts", { active: 2 }): {nome} vira o valor; mensagem { one, other } escolhe a forma por vars.count.
// Não escapa nada: quem interpola dado do usuário passa o valor já com escHtml.
function t(key, vars = {}) {
  const messages = window.I18N || {};
  let msg = messages[currentLang()]?.[key] ?? messages["pt-BR"]?.[key] ?? key;
  if (typeof msg === "object") msg = msg[new Intl.PluralRules(locale()).select(vars.count ?? 0)] ?? msg.other;
  return msg.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
}

/* ---------------------------------------------------------------- tema (escuro/claro) */

// a escolha fica no navegador; sem escolha salva, segue o sistema. O <head> aplica antes do 1º paint.
const THEME_KEY = "concilium:theme";

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, JSON.stringify(theme));
  } catch {
    /* sem armazenamento local: vale só nesta aba */
  }
}

// cores do canvas do grafo vêm dos tokens CSS (lidas uma vez por render, não por frame)
function graphTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return {
    label: v("--graph-label"),
    labelHover: v("--graph-label-hover"),
    labelDim: v("--graph-label-dim"),
    nodeStroke: v("--graph-node-stroke"),
    edgeRgb: v("--graph-edge-rgb"),
  };
}

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

const escHtml = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString(locale()) : "—");

/* ---------------------------------------------------------------- estado */

const PALETTE = ["#e8b26a", "#7fb4ca", "#a9c181", "#d08770", "#b48ead", "#ebcb8b", "#88c0d0", "#a3be8c", "#d3869b", "#81a1c1"];

const state = {
  user: null,
  screen: "overview", // tela inicial ao entrar
  insightsDays: 30, // período do Painel (7 | 30 | 90)
  insights: null,
  level: "documents",
  collection: "",
  minSimilarity: 0.5, // calibrado para bge-m3: docs relacionados ficam ~0.5–0.7
  k: 5,
  colors: new Map(),
  graphInstance: null,
  graphObserver: null,
  graphNeedsFit: false,
  hoverNode: null,
  legendHover: null,
  docPanelId: null,
  highlightDocs: null, // Set de document_id vindos do testador de busca
  lastSearchResults: [],
  lastSearch: null,
  lastSearchMs: null,
  searchK: 5,
  showRevoked: false,
  connectMode: false, // grafo: modo "clique na origem e no destino"
  connectFrom: null, // nó de origem escolhido no modo conectar
  showSemantic: true, // grafo: mostrar arestas de similaridade além dos links explícitos
};

function colorFor(collection) {
  if (!state.colors.has(collection)) {
    state.colors.set(collection, PALETTE[state.colors.size % PALETTE.length]);
  }
  return state.colors.get(collection);
}

/* ---------------------------------------------------------------- tela: busca RAG (testador) */

function searchTemplate() {
  return `
    <div class="split">
      <section class="page search-page">
        <header class="page-header">
          <h1>${icon("search", 18)} ${t("search.title")}</h1>
          <span class="sub">${t("search.sub")}</span>
        </header>
        <div class="search-col">
          <form class="composer" id="search-form">
            ${icon("search", 18)}
            <input type="text" id="s-query" placeholder="${t("search.placeholder")}" autocomplete="off" required />
            <label class="composer-k tech" title="${t("search.topkTitle")}">Top-k
              <select id="s-k">${[3, 5, 8, 10].map((k) => `<option ${k === state.searchK ? "selected" : ""}>${k}</option>`).join("")}</select>
            </label>
            <button class="composer-send" type="submit" title="${t("search.submit")}">${icon("search", 16)}</button>
          </form>
          <div class="results-head hidden" id="s-head">
            <span class="muted" id="s-summary"></span>
            <button class="btn sm" id="s-highlight" type="button">${icon("network")}<span>${t("search.showInGraph")}</span></button>
          </div>
          <div class="results" id="s-results"></div>
        </div>
      </section>
      <aside class="panel hidden" id="panel"></aside>
    </div>`;
}

function bindSearch() {
  const form = document.getElementById("search-form");
  const input = document.getElementById("s-query");
  if (state.lastSearch) input.value = state.lastSearch;
  input.focus();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const query = input.value.trim();
    if (!query) return;
    state.searchK = Number(document.getElementById("s-k").value);
    const results = document.getElementById("s-results");
    form.classList.add("busy");
    results.innerHTML = `<div class="results-empty">${t("search.searching")}</div>`;
    const t0 = performance.now();
    try {
      const data = await api.post("/search-test", { query, top_k: state.searchK });
      state.lastSearch = query;
      state.lastSearchResults = data.results;
      state.lastSearchMs = Math.round(performance.now() - t0);
      await renderSearchResults();
    } catch (err) {
      results.innerHTML = `<div class="results-empty">${t("search.failed", { error: escHtml(err.detail || err.message) })}</div>`;
    } finally {
      form.classList.remove("busy");
    }
  });
  document.getElementById("s-highlight").addEventListener("click", () => {
    state.highlightDocs = new Set(state.lastSearchResults.map((r) => r.document_id));
    state.screen = "graph";
    renderShell();
    renderScreen();
  });
  document.getElementById("s-results").addEventListener("click", (e) => {
    const card = e.target.closest("[data-doc]");
    if (!card) return;
    document.querySelectorAll(".result.selected").forEach((el) => el.classList.remove("selected"));
    card.classList.add("selected");
    openDocumentPanel(card.dataset.doc);
  });
}

// destaca os termos da busca no trecho (escapando o texto fora dos destaques)
function highlightTerms(text, query) {
  const terms = [...new Set(query.toLowerCase().split(/\s+/).filter((term) => term.length >= 3))];
  if (!terms.length) return escHtml(text);
  const re = new RegExp(`(${terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text
    .split(re)
    .map((part, i) => (i % 2 ? `<mark>${escHtml(part)}</mark>` : escHtml(part)))
    .join("");
}

async function renderSearchResults() {
  const box = document.getElementById("s-results");
  if (!box) return;
  const results = state.lastSearchResults;
  const head = document.getElementById("s-head");
  if (!state.lastSearch) {
    head.classList.add("hidden");
    box.innerHTML = `
      <div class="results-empty">
        <div class="results-empty-title">${t("search.emptyTitle")}</div>
        ${t("search.emptyBody", { tool: '<span class="mono">search_knowledge</span>' })}
      </div>`;
    return;
  }
  head.classList.toggle("hidden", !results.length);
  if (!results.length) {
    box.innerHTML = `<div class="results-empty">${t("search.noResults", { query: escHtml(state.lastSearch) })}</div>`;
    return;
  }
  const docs = new Set(results.map((r) => r.document_id)).size;
  document.getElementById("s-summary").textContent =
    t("search.summary", { chunks: results.length, docs, ms: state.lastSearchMs ?? "–" });
  const maxScore = Math.max(...results.map((r) => r.score)) || 1;
  box.innerHTML = results
    .map((r, i) => {
      const rel = Math.round((r.score / maxScore) * 100);
      const sim = Math.round(r.similarity * 100);
      const snippet = r.content.length > 320 ? r.content.slice(0, 320) + "…" : r.content;
      return `
    <button class="result ${state.docPanelId === r.document_id ? "selected" : ""}" data-doc="${r.document_id}" type="button">
      <span class="rank">${i + 1}</span>
      <div class="grow">
        <div class="result-title">
          <span class="title">${escHtml(r.title)}</span>
          <span class="chip"><span class="dot" style="background:${colorFor(r.collection)}"></span>${escHtml(r.collection)}</span>
          <span class="chip subtle tech">chunk #${r.chunk_index}</span>
        </div>
        <p class="snippet">${highlightTerms(snippet, state.lastSearch)}</p>
        <div class="result-meta">
          <span class="relbar" title="${t("search.relevance")}"><span style="width:${rel}%"></span></span>
          <span class="tech">${sim > 0 ? t("search.semantic", { pct: sim }) : t("search.fulltextOnly")}</span>
          <span class="mono tech">score ${Number(r.score).toFixed(4)}</span>
        </div>
      </div>
    </button>`;
    })
    .join("");
  await loadIcons(box);
}

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
    if (btn) goTo(btn.dataset.nav);
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
      goTo(state.screen, notes.openId);
      return;
    }
    const el = e.target.closest(".menu-item");
    if (!el || el.classList.contains("static")) return;
    setOpen(false);
    if (el.dataset.goto) {
      goTo(el.dataset.goto);
    } else if ("themeToggle" in el.dataset) {
      setTheme(currentTheme() === "dark" ? "light" : "dark");
      // re-renderiza a tela: editor e grafo leem o tema na criação
      goTo(state.screen, notes.openId);
    } else if (el.dataset.href) {
      window.open(el.dataset.href, "_blank", "noopener");
    } else if ("logout" in el.dataset) {
      await api.post("/auth/logout").catch(() => {});
      state.user = null;
      renderLogin();
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

// troca de tela: a de notas guarda o endereço da nota aberta (#/notes/<id>), as outras limpam
// rota de nota: #/notes/<id>; aceita também #/notas/<id> (links compartilhados antes da troca de nome)
function matchNoteRoute(hash) {
  return hash.match(/^#\/(?:notes|notas)\/([0-9a-f-]{36})$/);
}

async function goTo(screen, openId = null) {
  if (notes.editor) await notesLeave();
  state.screen = screen;
  notes.openId = openId;
  if (screen !== "notes" && location.hash) history.replaceState(null, "", location.pathname);
  renderShell();
  await renderScreen();
}

window.addEventListener("hashchange", () => {
  const m = matchNoteRoute(location.hash);
  if (!m || !state.user) return;
  if (state.screen === "notes" && notes.tree) openNote(m[1]);
  else goTo("notes", m[1]);
});

// alterações pendentes ao fechar a aba: o rascunho já está no navegador; tenta salvar também
window.addEventListener("pagehide", () => {
  if (notes.dirty && notes.current) {
    store.set(DRAFT_PREFIX + notes.current.id, { base_version: notes.current.version, at: new Date().toISOString(), ...editedNote() });
  }
});

async function renderScreen() {
  const main = document.getElementById("main");
  closePanel();
  destroyGraph();
  hideTip();
  if (state.screen === "notes") {
    await renderNotesScreen(main, notes.openId);
  } else if (state.screen === "overview") {
    await renderOverview(main);
  } else if (state.screen === "graph") {
    main.innerHTML = graphTemplate();
    bindGraphControls();
    await loadIcons(main);
    await loadGraph();
  } else if (state.screen === "search") {
    main.innerHTML = searchTemplate();
    await loadIcons(main);
    bindSearch();
    await renderSearchResults();
  } else if (state.screen === "keys") {
    main.innerHTML = pageTemplate("key-round", t("keys.title"), t("keys.sub"));
    await loadIcons(main);
    await renderKeysList();
  } else if (state.screen === "agents") {
    main.innerHTML = pageTemplate("bot", t("agents.title"), t("agents.sub"));
    await loadIcons(main);
    await renderAgentsList();
  } else if (state.screen === "users") {
    main.innerHTML = pageTemplate("users", t("users.title"), t("users.sub"));
    await loadIcons(main);
    await renderUsersList();
  }
}

function pageTemplate(iconName, title, sub) {
  return `
    <section class="page">
      <header class="page-header">
        <h1>${icon(iconName, 18)} ${title}</h1>
        <span class="sub">${sub}</span>
      </header>
      <div id="page-body"></div>
    </section>`;
}

/* ---------------------------------------------------------------- tela: chaves API */

// [escopo, chave da descrição]
const SCOPES = [
  ["read", "scope.read"],
  ["write", "scope.write"],
  ["agents:manage", "scope.agentsManage"],
  ["admin", "scope.admin"],
];

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

function keysCreateForm(agents) {
  return modalShell({
    id: "key",
    iconName: "key-round",
    title: t("keys.new"),
    subtitle: t("keys.newSub"),
    submitLabel: t("keys.create"),
    submitIcon: "key-round",
    width: 720,
    body: `
      <div class="create-grid">
        <label class="form-field">
          <span class="form-label">${t("common.name")}</span>
          <input class="input" type="text" id="key-label" placeholder="${t("keys.labelPh")}" required />
        </label>
        <label class="form-field">
          <span class="form-label">${t("keys.owner")}</span>
          <select class="input" id="key-agent">
            <option value="">${t("keys.humanKey")}</option>
            ${agents.map((a) => `<option value="${a.slug}">agent: ${escHtml(a.slug)}</option>`).join("")}
          </select>
        </label>
      </div>
      <div class="form-field">
        <span class="form-label">${t("keys.scopes")}</span>
        <div class="scope-grid two" id="key-scopes">
          ${SCOPES.map(
            ([sc, desc]) => `
            <label class="scope-option">
              <input type="checkbox" value="${sc}" ${sc === "read" ? "checked" : ""} />
              <span><span class="mono">${sc}</span><span class="muted">${t(desc)}</span></span>
            </label>`,
          ).join("")}
        </div>
      </div>`,
  });
}

function keyRow(k, isAdmin) {
  const revoked = Boolean(k.revoked_at);
  return `
    <div class="trow ${revoked ? "revoked" : ""}">
      <div class="tcell-main">
        <span class="row-icon">${icon(k.agent ? "bot" : "key-round")}</span>
        <div class="grow">
          <div class="row-title">${escHtml(k.label ?? "—")}</div>
          <div class="mono muted">${escHtml(k.prefix)}…</div>
        </div>
      </div>
      <div class="tcell">${(k.scopes || []).map((s) => `<span class="chip ${s === "admin" ? "accent" : ""}">${s}</span>`).join("")}</div>
      <div class="tcell muted">${k.agent ? `<span class="chip">${icon("bot", 12)} ${escHtml(k.agent)}</span>` : t("keys.human")}</div>
      <div class="tcell muted" title="${t("keys.createdAt", { date: fmtDate(k.created_at) })}">${revoked ? t("keys.revokedAgo", { ago: fmtAgo(k.revoked_at) }) : fmtAgo(k.created_at)}</div>
      <div class="tcell muted" title="${k.last_used_at ? fmtDate(k.last_used_at) : t("keys.neverUsed")}">${fmtAgo(k.last_used_at)}</div>
      <div class="tcell-actions">
        ${
          isAdmin && !revoked
            ? `<button class="btn sm ghost" data-renew="${k.id}" title="${t("keys.renewTitle")}">${icon("refresh-cw", 14)}<span>${t("keys.renew")}</span></button>
               <button class="btn sm ghost danger-text" data-revoke="${k.id}" title="${t("keys.revokeTitle")}">${icon("ban", 14)}<span>${t("keys.revoke")}</span></button>`
            : ""
        }
      </div>
    </div>`;
}

const keysHead = () => `
  <div class="trow thead">
    <div>${t("keys.col.key")}</div><div>${t("keys.col.scopes")}</div><div>${t("keys.col.owner")}</div><div>${t("keys.col.created")}</div><div>${t("keys.col.lastUsed")}</div><div></div>
  </div>`;

async function renderKeysList() {
  const body = document.getElementById("page-body");
  const isAdmin = state.user.role === "admin";
  let keys, agents;
  try {
    [keys, agents] = await Promise.all([api.get("/keys"), api.get("/agents")]);
  } catch {
    body.innerHTML = `<div class="results-empty">${t("keys.loadFailed")}</div>`;
    return;
  }
  const active = keys.filter((k) => !k.revoked_at);
  const revoked = keys.filter((k) => k.revoked_at);
  body.innerHTML = `
    <div class="page-col">
      <div class="section-head">
        <span class="muted">${t("keys.counts", { active: active.length, revoked: revoked.length })}</span>
        ${isAdmin ? `<button class="btn primary" id="key-new">${icon("plus")}<span>${t("keys.new")}</span></button>` : ""}
      </div>
      ${isAdmin ? keysCreateForm(agents) : ""}
      <div id="key-banner"></div>
      <div class="data-table">
        ${keysHead()}
        ${active.map((k) => keyRow(k, isAdmin)).join("") || `<div class="results-empty">${t("keys.noneActive")}${isAdmin ? t("keys.noneActiveHint") : ""}</div>`}
      </div>
      ${
        revoked.length
          ? `<details class="revoked-block" ${state.showRevoked ? "open" : ""}>
              <summary>${icon("history", 14)} ${t("keys.revokedBlock", { count: revoked.length })}</summary>
              <div class="data-table">${keysHead()}${revoked.map((k) => keyRow(k, isAdmin)).join("")}</div>
            </details>`
          : ""
      }
    </div>`;
  await loadIcons(body);
  body.querySelector(".revoked-block")?.addEventListener("toggle", (e) => (state.showRevoked = e.target.open));
  if (isAdmin) bindKeysCreate();
  bindKeyActions();
}

function bindKeysCreate() {
  const form = document.getElementById("key-form");
  const setOpen = bindModal("key", { trigger: document.getElementById("key-new") });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const scopes = [...document.querySelectorAll("#key-scopes input:checked")].map((i) => i.value);
    if (!scopes.length) {
      await uiAlert(t("keys.pickScopeTitle"), t("keys.pickScopeMsg"));
      return;
    }
    try {
      const key = await api.post("/keys", {
        label: document.getElementById("key-label").value,
        scopes,
        agent_slug: document.getElementById("key-agent").value || null,
      });
      setOpen(false);
      await renderKeysList();
      showKeyBanner(key);
    } catch (err) {
      await uiError(t("keys.createFailed"), err);
    }
  });
}

function bindKeyActions() {
  const body = document.getElementById("page-body");
  // o #page-body sobrevive aos re-renders da lista: liga o handler uma vez só
  if (body.dataset.keysBound) return;
  body.dataset.keysBound = "1";
  body.addEventListener("click", async (e) => {
    const renew = e.target.closest("[data-renew]");
    const revoke = e.target.closest("[data-revoke]");
    try {
      if (renew) {
        const ok = await uiConfirm({
          title: t("keys.renewConfirmTitle"),
          message: t("keys.renewConfirmMsg"),
          confirmLabel: t("keys.renew"),
          iconName: "refresh-cw",
        });
        if (!ok) return;
        const key = await api.post(`/keys/${renew.dataset.renew}/renew`);
        await renderKeysList();
        showKeyBanner(key);
      } else if (revoke) {
        const ok = await uiConfirm({
          title: t("keys.revokeConfirmTitle"),
          message: t("keys.revokeConfirmMsg"),
          confirmLabel: t("keys.revoke"),
          tone: "danger",
        });
        if (!ok) return;
        await api.post(`/keys/${revoke.dataset.revoke}/revoke`);
        await renderKeysList();
      }
    } catch (err) {
      await uiError(t("common.opFailed"), err);
    }
  });
}

function showKeyBanner(key) {
  const box = document.getElementById("key-banner");
  box.innerHTML = `
    <div class="key-reveal">
      <div class="key-reveal-head">
        ${icon("check")}<strong>${t("keys.created")}</strong>
        <span class="muted">${t("keys.copyNow")}</span>
      </div>
      <div class="key-reveal-row">
        <code class="mono">${escHtml(key.api_key)}</code>
        <button class="btn sm" id="copy-key" type="button">${icon("copy")}<span>${t("common.copy")}</span></button>
      </div>
    </div>`;
  loadIcons(box);
  box.querySelector("#copy-key").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    await navigator.clipboard.writeText(key.api_key);
    btn.innerHTML = `${icon("check")}<span>${t("common.copied")}</span>`;
    loadIcons(btn);
  });
}

/* ---------------------------------------------------------------- tela: agents */

const AGENT_SCOPES = [
  ["read", "scope.read"],
  ["write", "scope.write"],
  ["agents:manage", "scope.agentsManageAgent"],
  ["admin", "scope.admin"],
];

const slugify = (s) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);

const lines = (s) =>
  s
    .split("\n")
    .map((l) => l.replace(/^[-•*]\s*/, "").trim())
    .filter(Boolean);

// mesmo roteiro do prompt design_agent do MCP: objetivo, tom, sempre/nunca, coleções
function buildSystemPrompt(f) {
  const parts = [t("prompt.youAre", { name: f.name || t("prompt.anAgent") }) + (f.goal ? " " + f.goal.trim() : "")];
  if (f.tone.trim()) parts.push(t("prompt.tone", { tone: f.tone.trim() }));
  const always = lines(f.always);
  const never = lines(f.never);
  if (always.length) parts.push(t("prompt.always") + "\n" + always.map((l) => `- ${l}`).join("\n"));
  if (never.length) parts.push(t("prompt.never") + "\n" + never.map((l) => `- ${l}`).join("\n"));
  const scope = f.collections.length ? t("prompt.scopeCollections", { collections: f.collections.join(", ") }) : t("prompt.scopeBase");
  parts.push(t("prompt.search", { scope }) + t("prompt.memory"));
  return parts.join("\n\n");
}

function agentCreateForm(collections) {
  return `
    ${modalShell({
      id: "agent",
      iconName: "bot",
      title: t("agents.new"),
      subtitle: t("agents.newSub", { tool: '<span class="mono">create_agent</span>' }),
      submitLabel: t("agents.create"),
      submitIcon: "bot",
      body: `
      <div class="form-section">
        <div class="form-section-title"><span class="step">1</span>${t("agents.identity")}</div>
        <div class="create-grid three">
          <label class="form-field">
            <span class="form-label">${t("common.name")}</span>
            <input class="input" id="a-name" placeholder="${t("agents.namePh")}" required />
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.slug")} <span class="muted">${t("agents.slugHint")}</span></span>
            <input class="input mono" id="a-slug" placeholder="${t("agents.slugPh")}" pattern="[a-z0-9][a-z0-9-]{1,62}" required />
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.desc")}</span>
            <input class="input" id="a-desc" placeholder="${t("agents.descPh")}" />
          </label>
        </div>
      </div>

      <div class="form-section">
        <div class="form-section-title"><span class="step">2</span>${t("agents.behavior")}</div>
        <div class="create-grid">
          <label class="form-field">
            <span class="form-label">${t("agents.goal")}</span>
            <textarea class="input" id="a-goal" rows="3" placeholder="${t("agents.goalPh")}"></textarea>
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.tone")}</span>
            <textarea class="input" id="a-tone" rows="3">${t("agents.toneDefault")}</textarea>
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.always")} <span class="muted">${t("agents.onePerLine")}</span></span>
            <textarea class="input" id="a-always" rows="3" placeholder="${t("agents.alwaysPh")}"></textarea>
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.never")} <span class="muted">${t("agents.onePerLine")}</span></span>
            <textarea class="input" id="a-never" rows="3" placeholder="${t("agents.neverPh")}"></textarea>
          </label>
        </div>
        <label class="form-field">
          <span class="form-label prompt-label">System prompt
            <span class="muted" id="a-prompt-state">${t("agents.promptAuto")}</span>
            <button class="link-btn hidden" type="button" id="a-prompt-regen">${t("agents.regen")}</button>
          </span>
          <textarea class="input mono prompt-box" id="a-prompt" rows="9" required></textarea>
        </label>
      </div>

      <div class="form-section">
        <div class="form-section-title"><span class="step">3</span>${t("agents.access")}</div>
        <span class="form-label">${t("agents.collections")} <span class="muted">${t("agents.collectionsHint")}</span></span>
        <div class="chip-picks" id="a-cols">
          ${
            collections.length
              ? collections
                  .map(
                    (c) => `
              <label class="chip-pick"><input type="checkbox" value="${escHtml(c.name)}" />
                <span class="dot" style="background:${colorFor(c.name)}"></span>${escHtml(c.name)}<span class="muted">${c.documents}</span>
              </label>`,
                  )
                  .join("")
              : `<span class="muted">${t("agents.noCollections")}</span>`
          }
        </div>
        <span class="form-label">${t("agents.permissions")}</span>
        <div class="scope-grid" id="a-scopes">
          ${AGENT_SCOPES.map(
            ([s, desc]) => `
            <label class="scope-option">
              <input type="checkbox" value="${s}" ${s === "read" || s === "write" ? "checked" : ""} />
              <span><span class="mono">${s}</span><span class="muted">${t(desc)}</span></span>
            </label>`,
          ).join("")}
        </div>
        <label class="scope-option toggle-option">
          <input type="checkbox" id="a-auto" />
          <span><span>${t("agents.autonomy")}</span><span class="muted">${t("agents.autonomyDesc")}</span></span>
        </label>
      </div>`,
    })}`;
}

function agentCard(a, isAdmin) {
  const archived = a.status !== "active";
  return `
    <div class="agent-card ${archived ? "revoked" : ""}">
      <div class="agent-head">
        <span class="row-icon">${icon("bot")}</span>
        <div class="grow">
          <div class="row-title">${escHtml(a.name)} <span class="muted mono">${escHtml(a.slug)}</span>
            ${archived ? `<span class="chip danger-chip">${t("agents.archived")}</span>` : ""}
            ${a.proposals ? `<span class="chip accent">${icon("git-pull-request-arrow", 12)} ${t("agents.proposals", { count: a.proposals })}</span>` : ""}
          </div>
          ${a.description ? `<div class="muted">${escHtml(a.description)}</div>` : ""}
        </div>
        ${
          isAdmin && !archived
            ? `<button class="btn sm ${a.auto_apply_updates ? "primary" : "ghost"}" data-autonomy="${a.slug}" data-on="${a.auto_apply_updates}"
                 title="${t("agents.autonomyBtnTitle")}">
                 ${icon("zap", 14)}<span>${a.auto_apply_updates ? t("agents.autonomyOn") : t("agents.autonomyOff")}</span>
               </button>`
            : `<span class="muted">${a.auto_apply_updates ? t("agents.autonomous") : t("agents.underReview")}</span>`
        }
      </div>
      <div class="agent-meta">
        <span>${a.scopes.map((s) => `<span class="chip ${s === "admin" ? "accent" : ""}">${s}</span>`).join(" ")}</span>
        <span>${
          a.allowed_collections.length
            ? a.allowed_collections.map((c) => `<span class="chip"><span class="dot" style="background:${colorFor(c)}"></span>${escHtml(c)}</span>`).join(" ")
            : `<span class="chip subtle">${t("agents.allCollections")}</span>`
        }</span>
        <span class="grow"></span>
        <span class="muted">${icon("key-round", 12)} ${t("agents.keysCount", { count: a.active_keys })}</span>
        <span class="muted mono">v${a.version}</span>
        <span class="muted" title="${fmtDate(a.updated_at)}">${t("agents.updatedAgo", { ago: fmtAgo(a.updated_at) })}</span>
      </div>
    </div>`;
}

async function renderAgentsList(created) {
  const body = document.getElementById("page-body");
  const isAdmin = state.user.role === "admin";
  let list, collections;
  try {
    [list, collections] = await Promise.all([api.get("/agents"), isAdmin ? api.get("/collections") : []]);
  } catch {
    body.innerHTML = `<div class="results-empty">${t("agents.loadFailed")}</div>`;
    return;
  }
  const active = list.filter((a) => a.status === "active").length;
  body.innerHTML = `
    <div class="page-col">
      <div class="section-head">
        <span class="muted">${t("agents.counts", { active, archived: list.length - active })}</span>
        ${isAdmin ? `<button class="btn primary" id="agent-new">${icon("plus")}<span>${t("agents.new")}</span></button>` : ""}
      </div>
      ${isAdmin ? agentCreateForm(collections) : ""}
      <div id="agent-banner"></div>
      <div class="agent-list">
        ${
          list.map((a) => agentCard(a, isAdmin)).join("") ||
          `<div class="results-empty">
             <div class="results-empty-title">${t("agents.emptyTitle")}</div>
             ${isAdmin ? t("agents.emptyAdmin", { prompt: '<span class="mono">design_agent</span>' }) : t("agents.emptyViewer")}
           </div>`
        }
      </div>
    </div>`;
  await loadIcons(body);
  if (isAdmin) bindAgentCreate();
  if (created) showAgentBanner(created);
  // #page-body sobrevive aos re-renders da lista: liga o handler uma vez só
  if (body.dataset.agentsBound) return;
  body.dataset.agentsBound = "1";
  body.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-autonomy]");
    if (!btn) return;
    const turnOn = btn.dataset.on !== "true";
    const ok = await uiConfirm({
      title: t(turnOn ? "agents.autonomyConfirmOn" : "agents.autonomyConfirmOff", { slug: btn.dataset.autonomy }),
      message: turnOn ? t("agents.autonomyMsgOn") : t("agents.autonomyMsgOff"),
      confirmLabel: turnOn ? t("agents.autonomyTurnOn") : t("agents.autonomyTurnOff"),
      tone: turnOn ? "danger" : "default",
      iconName: "zap",
    });
    if (!ok) return;
    try {
      await api.post(`/agents/${btn.dataset.autonomy}/autonomy`, { auto_apply_updates: turnOn });
      await renderAgentsList();
    } catch (err) {
      await uiError(t("agents.autonomyFailed"), err);
    }
  });
}

function bindAgentCreate() {
  const form = document.getElementById("agent-form");
  const $ = (id) => document.getElementById(id);
  let slugTouched = false;
  let promptTouched = false;

  const fields = () => ({
    name: $("a-name").value.trim(),
    goal: $("a-goal").value,
    tone: $("a-tone").value,
    always: $("a-always").value,
    never: $("a-never").value,
    collections: [...form.querySelectorAll("#a-cols input:checked")].map((i) => i.value),
  });
  const syncPrompt = () => {
    if (!promptTouched) $("a-prompt").value = buildSystemPrompt(fields());
  };
  const setPromptTouched = (v) => {
    promptTouched = v;
    $("a-prompt-state").textContent = v ? t("agents.promptManual") : t("agents.promptAuto");
    $("a-prompt-regen").classList.toggle("hidden", !v);
  };
  const setOpen = bindModal("agent", { trigger: $("agent-new"), onOpen: syncPrompt });

  $("a-name").addEventListener("input", () => {
    if (!slugTouched) $("a-slug").value = slugify($("a-name").value);
  });
  $("a-slug").addEventListener("input", () => (slugTouched = true));
  form.addEventListener("input", (e) => {
    if (e.target.id === "a-prompt") setPromptTouched(true);
    else syncPrompt();
  });
  $("a-prompt-regen").addEventListener("click", () => {
    setPromptTouched(false);
    syncPrompt();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const scopes = [...form.querySelectorAll("#a-scopes input:checked")].map((i) => i.value);
    if (!scopes.length) {
      await uiAlert(t("agents.pickScopeTitle"), t("agents.pickScopeMsg"));
      return;
    }
    const f = fields();
    try {
      const created = await api.post("/agents", {
        slug: $("a-slug").value.trim(),
        name: f.name,
        description: $("a-desc").value.trim() || null,
        system_prompt: $("a-prompt").value.trim(),
        allowed_collections: f.collections,
        scopes,
        auto_apply_updates: $("a-auto").checked,
      });
      setOpen(false);
      await renderAgentsList(created);
    } catch (err) {
      await uiError(t("agents.createFailed"), err);
    }
  });
}

function showAgentBanner(created) {
  const box = document.getElementById("agent-banner");
  const copyRow = (id, value) => `
    <div class="key-reveal-row">
      <code class="mono">${escHtml(value)}</code>
      <button class="btn sm" type="button" data-copy="${id}">${icon("copy")}<span>${t("common.copy")}</span></button>
    </div>`;
  box.innerHTML = `
    <div class="key-reveal">
      <div class="key-reveal-head">
        ${icon("check")}<strong>${t("agents.createdTitle", { name: escHtml(created.agent.name) })}</strong>
        <span class="muted">${t("agents.copyNow")}</span>
      </div>
      <span class="form-label">${t("agents.apiKey")}</span>
      ${copyRow("key", created.api_key)}
      <span class="form-label">${t("agents.connectClaudeCode")}</span>
      ${copyRow("cmd", created.connect_command)}
      <span class="muted">${t("agents.nextStep", { prompt: '<span class="mono">start_as_agent</span>', slug: `<span class="mono">${escHtml(created.agent.slug)}</span>` })}</span>
    </div>`;
  loadIcons(box);
  const values = { key: created.api_key, cmd: created.connect_command };
  box.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    await navigator.clipboard.writeText(values[btn.dataset.copy]);
    btn.innerHTML = `${icon("check")}<span>${t("common.copied")}</span>`;
    loadIcons(btn);
  });
}

/* ---------------------------------------------------------------- tela: usuários */

const ROLES = ["viewer", "editor", "admin"];
// papel desconhecido conta como leitor (o menos privilegiado)
const roleId = (role) => (ROLES.includes(role) ? role : "viewer");
const roleName = (role) => t(`role.${roleId(role)}`);

function userRow(u) {
  const disabled = Boolean(u.disabled_at);
  const self = u.username === state.user.username;
  return `
    <div class="trow users ${disabled ? "revoked" : ""}">
      <div class="tcell-main">
        <span class="avatar">${escHtml(u.username.slice(0, 1).toUpperCase())}</span>
        <div class="grow">
          <div class="row-title">${escHtml(u.username)} ${self ? `<span class="chip subtle">${t("users.you")}</span>` : ""}</div>
        </div>
      </div>
      <div class="tcell">
        <select class="input role-select" data-role="${u.id}" ${self || disabled ? "disabled" : ""}
          title="${self ? t("users.cantChangeOwn") : t("users.roleTitle")}">
          ${ROLES.map((v) => `<option value="${v}" ${u.role === v ? "selected" : ""}>${roleName(v)}</option>`).join("")}
        </select>
      </div>
      <div class="tcell"><span class="status ${disabled ? "" : "ok"}"><span class="status-dot"></span>${disabled ? t("users.disabled") : t("users.active")}</span></div>
      <div class="tcell muted" title="${t("users.createdAt", { date: fmtDate(u.created_at) })}">${fmtAgo(u.created_at)}</div>
      <div class="tcell-actions">
        ${disabled ? "" : `<button class="btn sm ghost" data-reset="${u.id}" title="${t("users.setPasswordTitle")}">${icon("key-round", 14)}<span>${t("users.password")}</span></button>`}
        ${
          self
            ? ""
            : `<button class="btn sm ghost ${disabled ? "" : "danger-text"}" data-toggle="${u.id}" data-disabled="${disabled}"
                title="${disabled ? t("users.reactivateTitle") : t("users.disableTitle")}">
                ${icon(disabled ? "refresh-cw" : "ban", 14)}<span>${disabled ? t("users.reactivate") : t("users.disable")}</span>
              </button>`
        }
      </div>
      <div class="reset-row hidden" id="reset-${u.id}">
        <input class="input" type="password" placeholder="${t("users.newPasswordPh", { name: escHtml(u.username) })}" id="reset-pass-${u.id}" minlength="8" />
        <button class="btn sm" type="button" data-reset="${u.id}">${t("common.cancel")}</button>
        <button class="btn sm primary" type="button" data-do-reset="${u.id}">${icon("check", 14)}<span>${t("users.savePassword")}</span></button>
      </div>
    </div>`;
}

function usersCreateForm() {
  return modalShell({
    id: "user",
    iconName: "users",
    title: t("users.new"),
    subtitle: t("users.newSub"),
    submitLabel: t("users.create"),
    width: 640,
    body: `
      <div class="create-grid">
        <label class="form-field">
          <span class="form-label">${t("users.username")}</span>
          <input class="input" type="text" id="u-username" placeholder="${t("users.usernamePh")}" autocomplete="off" required />
        </label>
        <label class="form-field">
          <span class="form-label">${t("users.initialPassword")}</span>
          <input class="input" type="password" id="u-password" placeholder="${t("users.passwordPh")}" minlength="8" autocomplete="new-password" required />
        </label>
      </div>
      <div class="form-field">
        <span class="form-label">${t("users.role")}</span>
        <div class="scope-grid two">
          ${ROLES.map(
            (v) => `
            <label class="scope-option">
              <input type="radio" name="u-role" value="${v}" ${v === "viewer" ? "checked" : ""} />
              <span><span>${roleName(v)}</span><span class="muted">${t(`role.${v}.desc`)}</span></span>
            </label>`,
          ).join("")}
        </div>
      </div>`,
  });
}

const usersHead = () => `
  <div class="trow users thead">
    <div>${t("users.col.user")}</div><div>${t("users.col.role")}</div><div>${t("users.col.status")}</div><div>${t("users.col.created")}</div><div></div>
  </div>`;

async function renderUsersList() {
  const body = document.getElementById("page-body");
  let users;
  try {
    users = await api.get("/users");
  } catch (err) {
    body.innerHTML = `<div class="results-empty">${err.status === 403 ? t("users.onlyAdmins") : t("users.loadFailed")}</div>`;
    return;
  }
  const active = users.filter((u) => !u.disabled_at).length;
  const admins = users.filter((u) => !u.disabled_at && u.role === "admin").length;
  body.innerHTML = `
    <div class="page-col">
      <div class="section-head">
        <span class="muted">${t("users.counts", { active, admins, disabled: users.length - active })}</span>
        <button class="btn primary" id="user-new">${icon("plus")}<span>${t("users.new")}</span></button>
      </div>
      ${usersCreateForm()}
      <div class="data-table">${usersHead()}${users.map(userRow).join("")}</div>
    </div>`;
  await loadIcons(body);
  bindUserActions();
}

function bindUserActions() {
  const body = document.getElementById("page-body");
  const form = document.getElementById("user-form");
  const setOpen = bindModal("user", { trigger: document.getElementById("user-new") });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.post("/users", {
        username: document.getElementById("u-username").value.trim(),
        password: document.getElementById("u-password").value,
        role: form.querySelector("input[name=u-role]:checked").value,
      });
      setOpen(false);
      await renderUsersList();
    } catch (err) {
      await uiError(t("users.createFailed"), err);
    }
  });
  // o form é recriado a cada render; os handlers delegados no #page-body, só uma vez
  if (body.dataset.usersBound) return;
  body.dataset.usersBound = "1";
  body.addEventListener("change", async (e) => {
    const sel = e.target.closest("[data-role]");
    if (!sel) return;
    try {
      await api.patch(`/users/${sel.dataset.role}`, { role: sel.value });
      await renderUsersList();
    } catch (err) {
      await uiError(t("users.roleFailed"), err);
      await renderUsersList();
    }
  });
  body.addEventListener("click", async (e) => {
    const reset = e.target.closest("[data-reset]");
    const doReset = e.target.closest("[data-do-reset]");
    const toggle = e.target.closest("[data-toggle]");
    try {
      if (reset) {
        const row = document.getElementById(`reset-${reset.dataset.reset}`);
        row.classList.toggle("hidden");
        if (!row.classList.contains("hidden")) row.querySelector("input").focus();
      } else if (doReset) {
        const pass = document.getElementById(`reset-pass-${doReset.dataset.doReset}`).value;
        if (pass.length < 8) {
          await uiAlert(t("users.shortPasswordTitle"), t("users.shortPasswordMsg"));
          return;
        }
        await api.patch(`/users/${doReset.dataset.doReset}`, { password: pass });
        await renderUsersList();
      } else if (toggle) {
        const disable = toggle.dataset.disabled !== "true";
        if (
          disable &&
          !(await uiConfirm({
            title: t("users.disableConfirmTitle"),
            message: t("users.disableConfirmMsg"),
            confirmLabel: t("users.disable"),
            tone: "danger",
          }))
        )
          return;
        await api.patch(`/users/${toggle.dataset.toggle}`, { disabled: disable });
        await renderUsersList();
      }
    } catch (err) {
      await uiError(t("common.opFailed"), err);
    }
  });
}

/* ---------------------------------------------------------------- tela: notas (plan-web-02) */

// Modelos v1 estáticos: não poluem a busca RAG com documentos-modelo. Textos no dicionário (tpl.<id>.*).
const NOTE_TEMPLATE_IDS = ["blank", "client", "meeting", "proposal", "objections"];
const noteTemplates = () =>
  NOTE_TEMPLATE_IDS.map((id) => ({
    id,
    label: t(`tpl.${id}.label`),
    desc: t(`tpl.${id}.desc`),
    content: id === "blank" ? "" : t(`tpl.${id}.content`),
  }));

const IDLE_SAVE_MS = 20000; // salva sozinho depois de ~20 s sem digitar
const DRAFT_PREFIX = "concilium:draft:";

const notes = {
  tree: null, // { collections, notes } — sem conteúdo
  current: null, // nota aberta, como está salva no banco
  editor: null, // instância Toast UI (editor ou viewer)
  tags: [], // tags em edição
  dirty: false,
  saving: false,
  idleTimer: null,
  draftTimer: null,
  filter: "",
  side: null, // "related" | "history" | null
};

const canEdit = () => ["admin", "editor"].includes(state.user?.role);

// localStorage pode falhar (aba privada, bloqueio): rascunho é conveniência, nunca obrigatório
const store = {
  get(key) {
    try {
      return JSON.parse(localStorage.getItem(key));
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* sem armazenamento local: segue sem rascunho */
    }
  },
  del(key) {
    try {
      localStorage.removeItem(key);
    } catch {
      /* idem */
    }
  },
};

function notesTemplate() {
  return `
    <div class="notes-layout">
      <aside class="notes-tree">
        <div class="notes-tree-head">
          <div class="field notes-filter">${icon("search")}<input type="text" id="notes-filter" placeholder="${t("notes.filterPh")}" autocomplete="off" /></div>
          ${
            canEdit()
              ? `<div class="notes-tree-actions">
                  <button class="btn sm primary" id="note-new" title="${t("notes.newNoteTitle")}">${icon("plus", 14)}<span>${t("notes.newNoteBtn")}</span></button>
                  <button class="btn sm ghost" id="folder-new" title="${t("notes.newFolderTitle")}">${icon("folder", 14)}<span>${t("notes.newFolderBtn")}</span></button>
                </div>`
              : ""
          }
        </div>
        <div class="notes-tree-list" id="notes-tree-list"></div>
      </aside>
      <section class="note-main" id="note-main"></section>
      <aside class="note-side hidden" id="note-side"></aside>
    </div>`;
}

async function renderNotesScreen(main, openId) {
  main.innerHTML = notesTemplate();
  await loadIcons(main);
  main.querySelector("#notes-filter").addEventListener("input", (e) => {
    notes.filter = e.target.value;
    renderNotesTree();
  });
  main.querySelector("#note-new")?.addEventListener("click", () => openNewNoteModal());
  main.querySelector("#folder-new")?.addEventListener("click", () => openNewFolderModal());
  main.querySelector("#notes-tree-list").addEventListener("click", async (e) => {
    const folder = e.target.closest("[data-toggle-folder]");
    const note = e.target.closest("[data-note]");
    if (folder) {
      const collapsed = new Set(store.get("concilium:collapsed") || []);
      const name = folder.dataset.toggleFolder;
      collapsed.has(name) ? collapsed.delete(name) : collapsed.add(name);
      store.set("concilium:collapsed", [...collapsed]);
      renderNotesTree();
    } else if (note) {
      await openNote(note.dataset.note);
    }
  });
  document.addEventListener("keydown", notesKeys);
  await loadNotesTree();
  const target = openId || store.get("concilium:last-note");
  const exists = notes.tree.notes.some((n) => n.id === target);
  if (exists) await openNote(target);
  else if (notes.tree.notes.length) await openNote(notes.tree.notes[0].id);
  else renderNotesEmpty();
}

// sai da tela de notas: salva o que estiver pendente e desmonta o editor
async function notesLeave() {
  document.removeEventListener("keydown", notesKeys);
  if (notes.current && notes.dirty) await saveNote();
  clearTimeout(notes.idleTimer);
  notes.editor?.destroy();
  notes.editor = null;
  notes.current = null;
  notes.dirty = false;
}

function notesKeys(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    saveNote();
  } else if (e.altKey && e.key.toLowerCase() === "n" && canEdit()) {
    e.preventDefault(); // Ctrl+N é do navegador (nova janela) e não pode ser capturado
    openNewNoteModal();
  }
}

async function loadNotesTree() {
  notes.tree = await api.get("/notes/tree");
  renderNotesTree();
}

function renderNotesTree() {
  const box = document.getElementById("notes-tree-list");
  if (!box || !notes.tree) return;
  const q = titleKey(notes.filter);
  const collapsed = new Set(store.get("concilium:collapsed") || []);
  const byCol = new Map(notes.tree.collections.map((c) => [c.name, []]));
  for (const n of notes.tree.notes) {
    if (!q || titleKey(n.title).includes(q)) byCol.get(n.collection)?.push(n);
  }
  box.innerHTML =
    [...byCol.entries()]
      .filter(([, items]) => !q || items.length)
      .map(([name, items]) => {
        const closed = collapsed.has(name) && !q;
        return `
      <div class="tree-folder ${closed ? "collapsed" : ""}">
        <button class="tree-folder-head" type="button" data-toggle-folder="${escHtml(name)}">
          <span class="tree-chevron">${icon("chevron-right", 13)}</span>
          <span class="dot" style="background:${colorFor(name)}"></span>
          <span class="grow">${escHtml(name)}</span><span class="count">${items.length}</span>
        </button>
        <div class="tree-notes">
          ${
            items
              .map(
                (n) => `<button class="tree-note ${notes.current?.id === n.id ? "active" : ""}" type="button" data-note="${n.id}" title="${escHtml(n.title)}">
                  <span class="grow">${escHtml(n.title)}</span>${notes.current?.id === n.id && notes.dirty ? `<span class="dirty-dot" title="${t("notes.unsaved")}"></span>` : ""}</button>`,
              )
              .join("") || `<div class="tree-empty">${t("notes.emptyFolder")}</div>`
          }
        </div>
      </div>`;
      })
      .join("") || `<div class="tree-empty">${t("notes.nothingFound", { query: escHtml(notes.filter) })}</div>`;
  loadIcons(box);
}

// mesma regra do kb_title_key do banco: minúsculas, sem acento
const titleKey = (s) =>
  String(s || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();

function renderNotesEmpty() {
  const main = document.getElementById("note-main");
  main.innerHTML = `
    <div class="results-empty notes-empty">
      <div class="results-empty-title">${canEdit() ? t("notes.firstNote") : t("notes.noneYet")}</div>
      ${
        canEdit()
          ? `${t("notes.emptyBody")}
             <div class="template-pick">${noteTemplates().map((tpl) => `<button class="btn" type="button" data-template="${tpl.id}">${escHtml(tpl.label)}</button>`).join("")}</div>`
          : t("notes.askEditor")
      }
    </div>`;
  main.querySelectorAll("[data-template]").forEach((b) => b.addEventListener("click", () => openNewNoteModal(b.dataset.template)));
}

/* ---------- abrir / editar / salvar */

async function openNote(id) {
  if (notes.current?.id === id) return;
  if (notes.current && notes.dirty && !(await saveNote())) {
    const leave = await uiConfirm({
      title: t("notes.saveFailedTitle"),
      message: t("notes.switchAnywayMsg"),
      confirmLabel: t("notes.switchAnyway"),
    });
    if (!leave) return;
  }
  let note;
  try {
    note = await api.get(`/notes/${id}`);
  } catch (err) {
    await uiError(t("notes.openFailed"), err);
    return;
  }
  notes.current = note;
  notes.tags = [...(note.tags || [])];
  notes.dirty = false;
  store.set("concilium:last-note", id);
  if (location.hash !== `#/notes/${id}`) history.replaceState(null, "", `#/notes/${id}`);
  renderNoteMain();
  renderNotesTree();
  if (notes.side) renderNoteSide(notes.side);
  await offerDraftRecovery();
}

function renderNoteMain() {
  const n = notes.current;
  const main = document.getElementById("note-main");
  const editable = canEdit();
  main.innerHTML = `
    <div class="note-head">
      <input class="note-title" id="note-title" value="${escHtml(n.title)}" placeholder="${t("notes.untitled")}" ${editable ? "" : "readonly"} />
      <div class="note-actions">
        <span class="save-state" id="save-state"></span>
        <button class="btn sm ghost ${notes.side === "related" ? "active" : ""}" type="button" data-side="related">${icon("link", 14)}<span>${t("notes.connections")}</span></button>
        <button class="btn sm ghost ${notes.side === "history" ? "active" : ""}" type="button" data-side="history">${icon("history", 14)}<span>${t("notes.history")}</span></button>
        ${
          editable
            ? `<button class="icon-btn" type="button" id="note-move" title="${t("notes.moveTitle")}">${icon("folder-input", 16)}</button>
               <button class="icon-btn danger" type="button" id="note-archive" title="${t("notes.archiveTitle")}">${icon("archive", 16)}</button>
               <button class="btn sm primary" type="button" id="note-save" title="${t("notes.saveTitle")}">${icon("save", 14)}<span>${t("notes.save")}</span></button>`
            : ""
        }
      </div>
    </div>
    <div class="note-meta">
      <span class="chip"><span class="dot" style="background:${colorFor(n.collection)}"></span>${escHtml(n.collection)}</span>
      <span class="muted" id="note-version"></span>
      <div class="tag-input" id="note-tags"></div>
    </div>
    <div class="note-editor" id="note-editor"></div>`;
  loadIcons(main);
  renderVersionLine();
  renderTags();

  notes.editor?.destroy();
  const el = main.querySelector("#note-editor");
  const common = { el, initialValue: n.content, theme: currentTheme(), usageStatistics: false };
  notes.editor = editable
    ? new toastui.Editor({
        ...common,
        height: "100%",
        initialEditType: "wysiwyg",
        previewStyle: "vertical",
        language: locale(),
        placeholder: t("notes.editorPh"),
      })
    : toastui.Editor.factory({ ...common, viewer: true });
  if (editable) {
    notes.editor.on("change", markDirty);
    main.querySelector("#note-title").addEventListener("input", markDirty);
    main.querySelector("#note-save").addEventListener("click", () => saveNote());
    main.querySelector("#note-move").addEventListener("click", openMoveNoteModal);
    main.querySelector("#note-archive").addEventListener("click", archiveCurrentNote);
  }
  main.querySelectorAll("[data-side]").forEach((b) =>
    b.addEventListener("click", () => {
      notes.side = notes.side === b.dataset.side ? null : b.dataset.side;
      main.querySelectorAll("[data-side]").forEach((x) => x.classList.toggle("active", x.dataset.side === notes.side));
      renderNoteSide(notes.side);
    }),
  );
  setSaveState(notes.dirty ? "dirty" : "saved");
}

function renderVersionLine() {
  const n = notes.current;
  const box = document.getElementById("note-version");
  if (box) box.textContent = t("notes.versionLine", { version: n.version, ago: fmtAgo(n.updated_at) }) + (n.updated_by ? t("notes.versionBy", { name: n.updated_by.replace(/^dash:/, "") }) : "");
}

function renderTags() {
  const box = document.getElementById("note-tags");
  if (!box) return;
  const editable = canEdit();
  box.innerHTML =
    notes.tags
      .map((tag, i) => `<span class="chip tag-chip">#${escHtml(tag)}${editable ? `<button type="button" data-tag-del="${i}" title="${t("notes.removeTag")}">×</button>` : ""}</span>`)
      .join("") + (editable ? `<input type="text" id="tag-add" placeholder="${notes.tags.length ? "+ tag" : t("notes.addTag")}" />` : "");
  if (!editable) return;
  const input = box.querySelector("#tag-add");
  input.addEventListener("keydown", (e) => {
    const value = input.value.trim().replace(/^#/, "").replace(/,$/, "");
    if ((e.key === "Enter" || e.key === ",") && value) {
      e.preventDefault();
      if (!notes.tags.includes(value)) notes.tags.push(value);
      renderTags();
      document.getElementById("tag-add").focus();
      markDirty();
    } else if (e.key === "Backspace" && !input.value && notes.tags.length) {
      notes.tags.pop();
      renderTags();
      document.getElementById("tag-add").focus();
      markDirty();
    }
  });
  box.querySelectorAll("[data-tag-del]").forEach((b) =>
    b.addEventListener("click", () => {
      notes.tags.splice(Number(b.dataset.tagDel), 1);
      renderTags();
      markDirty();
    }),
  );
}

function editedNote() {
  return {
    title: document.getElementById("note-title")?.value.trim() || notes.current.title,
    content: notes.editor?.getMarkdown() ?? notes.current.content,
    tags: [...notes.tags],
  };
}

function hasChanges() {
  const n = notes.current;
  const e = editedNote();
  return e.title !== n.title || e.content.trim() !== (n.content || "").trim() || JSON.stringify(e.tags) !== JSON.stringify(n.tags || []);
}

function markDirty() {
  if (!notes.current) return;
  const was = notes.dirty;
  notes.dirty = hasChanges();
  if (was !== notes.dirty) renderNotesTree();
  setSaveState(notes.dirty ? "dirty" : "saved");
  // rascunho local contínuo (não perde nada se a aba fechar) + salvamento automático depois de parado
  clearTimeout(notes.draftTimer);
  clearTimeout(notes.idleTimer);
  if (!notes.dirty) {
    store.del(DRAFT_PREFIX + notes.current.id);
    return;
  }
  notes.draftTimer = setTimeout(() => {
    store.set(DRAFT_PREFIX + notes.current.id, { base_version: notes.current.version, at: new Date().toISOString(), ...editedNote() });
  }, 400);
  notes.idleTimer = setTimeout(() => saveNote(), IDLE_SAVE_MS);
}

function setSaveState(kind) {
  const box = document.getElementById("save-state");
  if (!box) return;
  const labels = {
    saved: t("save.saved", { version: notes.current?.version }),
    dirty: t("save.dirty"),
    saving: t("save.saving"),
    error: t("save.error"),
  };
  box.className = `save-state ${kind}`;
  box.textContent = canEdit() ? labels[kind] : t("save.readOnly");
}

// Salva no banco (gera versão). Devolve true se não sobrou nada pendente.
async function saveNote(force = false) {
  if (!notes.current || !canEdit()) return true;
  if (!notes.dirty || notes.saving) return !notes.dirty;
  clearTimeout(notes.idleTimer);
  notes.saving = true;
  setSaveState("saving");
  const n = notes.current;
  const edited = editedNote();
  const content = edited.content.trim() ? edited.content : n.content; // conteúdo vazio não é aceito pela base
  try {
    const result = await api.patch(`/notes/${n.id}`, {
      base_version: force ? force : n.version,
      title: edited.title,
      content,
      tags: edited.tags,
    });
    Object.assign(n, { ...edited, content, version: result.version ?? n.version, updated_at: new Date().toISOString(), updated_by: `dash:${state.user.username}` });
    notes.dirty = hasChanges(); // o usuário pode ter digitado durante o salvamento
    if (!notes.dirty) store.del(DRAFT_PREFIX + n.id);
    const item = notes.tree?.notes.find((x) => x.id === n.id);
    if (item) Object.assign(item, { title: n.title, tags: n.tags, version: n.version });
    renderNotesTree();
    renderVersionLine();
    setSaveState(notes.dirty ? "dirty" : "saved");
    if (notes.side === "history") renderNoteSide("history");
    return !notes.dirty;
  } catch (err) {
    setSaveState("error");
    if (err.status === 409 && err.detail?.current_version) {
      notes.saving = false;
      const overwrite = await uiDialog({
        title: t("notes.conflictTitle"),
        message: t("notes.conflictMsg", { message: err.detail.message }),
        confirmLabel: t("notes.conflictOverwrite"),
        cancelLabel: t("notes.conflictReload"),
        tone: "danger",
      });
      if (overwrite) return saveNote(err.detail.current_version);
      store.del(DRAFT_PREFIX + n.id);
      notes.current = null;
      await openNote(n.id);
      return true;
    }
    await uiError(t("notes.saveNoteFailed"), err);
    return false;
  } finally {
    notes.saving = false;
  }
}

async function offerDraftRecovery() {
  const n = notes.current;
  const draft = store.get(DRAFT_PREFIX + n.id);
  if (!draft || !canEdit()) return;
  const same = draft.title === n.title && draft.content.trim() === (n.content || "").trim() && JSON.stringify(draft.tags) === JSON.stringify(n.tags || []);
  if (same) {
    store.del(DRAFT_PREFIX + n.id);
    return;
  }
  const stale = draft.base_version !== n.version;
  const recover = await uiConfirm({
    title: t("draft.title"),
    message: t("draft.msg", { ago: fmtAgo(draft.at) }) + (stale ? t("draft.stale", { version: n.version }) : ""),
    confirmLabel: t("draft.recover"),
  });
  if (!recover) {
    store.del(DRAFT_PREFIX + n.id);
    return;
  }
  document.getElementById("note-title").value = draft.title;
  notes.tags = [...draft.tags];
  renderTags();
  notes.editor.setMarkdown(draft.content, false);
  markDirty();
}

/* ---------- criar / mover / arquivar / pastas */

function collectionOptions(selected) {
  return notes.tree.collections
    .map((c) => `<option value="${escHtml(c.name)}" ${c.name === selected ? "selected" : ""}>${escHtml(c.name)}</option>`)
    .join("");
}

async function openNewNoteModal(templateId = "blank") {
  if (!notes.tree.collections.length) {
    await uiAlert(t("notes.needFolderTitle"), t("notes.needFolderMsg"));
    return;
  }
  document.getElementById("note-new-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "note-new",
    iconName: "notebook-pen",
    title: t("notes.new"),
    subtitle: t("notes.newSub"),
    submitLabel: t("notes.create"),
    width: 640,
    body: `
      <div class="create-grid">
        <label class="form-field">
          <span class="form-label">${t("notes.titleLabel")}</span>
          <input class="input" id="nn-title" placeholder="${t("notes.titlePh")}" required />
        </label>
        <label class="form-field">
          <span class="form-label">${t("notes.folder")}</span>
          <select class="input" id="nn-collection">${collectionOptions(notes.current?.collection || notes.tree.collections[0].name)}</select>
        </label>
      </div>
      <div class="form-field">
        <span class="form-label">${t("notes.template")}</span>
        <div class="scope-grid two">
          ${noteTemplates().map(
            (tpl) => `
            <label class="scope-option">
              <input type="radio" name="nn-template" value="${tpl.id}" ${tpl.id === templateId ? "checked" : ""} />
              <span><span>${escHtml(tpl.label)}</span><span class="muted">${escHtml(tpl.desc)}</span></span>
            </label>`,
          ).join("")}
        </div>
      </div>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("note-new-modal");
  await loadIcons(modal);
  const setOpen = bindModal("note-new");
  modal.querySelector("#note-new-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = modal.querySelector("#nn-title").value.trim();
    const template = noteTemplates().find((tpl) => tpl.id === modal.querySelector("input[name=nn-template]:checked").value);
    const body = {
      collection: modal.querySelector("#nn-collection").value,
      title,
      content: template.content || `# ${title}\n`,
    };
    try {
      let created = await api.post("/notes", body);
      if (!created.created && created.reason === "duplicate_suspected") {
        const sim = created.similar_document;
        const go = await uiConfirm({
          title: t("notes.duplicateTitle"),
          message: t("notes.duplicateMsg", { title: sim.title, pct: Math.round(sim.similarity * 100) }),
          confirmLabel: t("notes.createAnyway"),
        });
        if (!go) return;
        created = await api.post("/notes", { ...body, force: true });
      }
      setOpen(false);
      modal.remove();
      await loadNotesTree();
      notes.current = notes.current?.id === created.document_id ? null : notes.current;
      await openNote(created.document_id);
      notes.editor?.focus?.();
    } catch (err) {
      await uiError(t("notes.createFailed"), err);
    }
  });
  setOpen(true);
}

async function openNewFolderModal() {
  document.getElementById("folder-new-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "folder-new",
    iconName: "folder",
    title: t("folders.new"),
    subtitle: t("folders.newSub"),
    submitLabel: t("folders.create"),
    width: 520,
    body: `
      <label class="form-field">
        <span class="form-label">${t("common.name")}</span>
        <input class="input" id="nf-name" placeholder="${t("folders.namePh")}" required />
      </label>
      <label class="form-field">
        <span class="form-label">${t("folders.desc")} <span class="muted">${t("common.optional")}</span></span>
        <input class="input" id="nf-desc" placeholder="${t("folders.descPh")}" />
      </label>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("folder-new-modal");
  await loadIcons(modal);
  const setOpen = bindModal("folder-new");
  modal.querySelector("#folder-new-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.post("/collections", {
        name: modal.querySelector("#nf-name").value.trim(),
        description: modal.querySelector("#nf-desc").value.trim() || null,
      });
      setOpen(false);
      modal.remove();
      await loadNotesTree();
    } catch (err) {
      await uiError(t("folders.createFailed"), err);
    }
  });
  setOpen(true);
}

async function openMoveNoteModal() {
  const n = notes.current;
  if (!(await saveNote())) return;
  document.getElementById("note-move-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "note-move",
    iconName: "folder-input",
    title: t("move.title"),
    subtitle: t("move.sub", { title: escHtml(n.title), folder: escHtml(n.collection) }),
    submitLabel: t("move.submit"),
    submitIcon: "folder-input",
    width: 480,
    body: `
      <label class="form-field">
        <span class="form-label">${t("move.to")}</span>
        <select class="input" id="nm-collection">${collectionOptions(n.collection)}</select>
      </label>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("note-move-modal");
  await loadIcons(modal);
  const setOpen = bindModal("note-move");
  modal.querySelector("#note-move-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const destination = modal.querySelector("#nm-collection").value;
    try {
      await api.post(`/notes/${n.id}/move`, { collection: destination });
      setOpen(false);
      modal.remove();
      n.collection = destination;
      await loadNotesTree();
      renderNoteMain();
    } catch (err) {
      await uiError(t("move.failed"), err);
    }
  });
  setOpen(true);
}

async function archiveCurrentNote() {
  const n = notes.current;
  const ok = await uiConfirm({
    title: t("archive.title"),
    message: t("archive.msg", { title: n.title }),
    confirmLabel: t("archive.confirm"),
    tone: "danger",
  });
  if (!ok) return;
  try {
    await api.post(`/notes/${n.id}/archive`, {});
    store.del(DRAFT_PREFIX + n.id);
    notes.dirty = false;
    notes.editor?.destroy();
    notes.editor = null;
    notes.current = null;
    history.replaceState(null, "", "#/notes");
    await loadNotesTree();
    if (notes.tree.notes.length) await openNote(notes.tree.notes[0].id);
    else renderNotesEmpty();
  } catch (err) {
    await uiError(t("archive.failed"), err);
  }
}

/* ---------- painel lateral: conexões e histórico */

async function renderNoteSide(kind) {
  const side = document.getElementById("note-side");
  if (!side) return;
  side.classList.toggle("hidden", !kind);
  if (!kind || !notes.current) return;
  const n = notes.current;
  side.innerHTML = `<div class="muted">${t("common.loading")}</div>`;
  if (kind === "related") {
    let rel;
    try {
      rel = await api.get(`/notes/${n.id}/related`);
    } catch {
      side.innerHTML = `<div class="muted">${t("side.relatedFailed")}</div>`;
      return;
    }
    const item = (d, extra = "") =>
      `<button class="side-item" type="button" data-open-note="${d.document_id}"><span class="dot" style="background:${colorFor(d.collection)}"></span><span class="grow">${escHtml(d.title)}</span>${extra}</button>`;
    side.innerHTML = `
      <div class="side-head"><h3>${icon("link", 15)} ${t("notes.connections")}</h3>
        ${state.user?.role === "admin" ? `<button class="btn sm ghost" type="button" id="side-connect">${icon("plus", 13)}<span>${t("side.connectTo")}</span></button>` : ""}
      </div>
      <div class="links-label">${t("side.links", { count: rel.links.length })}</div>
      ${
        rel.links
          .map((l) => (l.pending ? `<div class="side-item pending">${escHtml(l.target_title)} <span class="muted">${t("side.notYet")}</span></div>` : item(l)))
          .join("") || `<div class="muted links-empty">${t("side.linksEmpty")}</div>`
      }
      <div class="links-label">${t("side.backlinks", { count: rel.backlinks.length })}</div>
      ${rel.backlinks.map((b) => item(b)).join("") || `<div class="muted links-empty">${t("side.backlinksEmpty")}</div>`}
      <div class="links-label">${t("side.similar", { count: rel.semantic.length })}</div>
      ${rel.semantic.map((s) => item(s, `<span class="muted">${Math.round(s.similarity * 100)}%</span>`)).join("") || `<div class="muted links-empty">${t("side.similarEmpty")}</div>`}`;
    side.querySelector("#side-connect")?.addEventListener("click", () =>
      openConnectModal({ document_id: n.id, title: n.title }, null, () => renderNoteSide("related")),
    );
  } else {
    let versions;
    try {
      versions = await api.get(`/notes/${n.id}/versions`);
    } catch {
      side.innerHTML = `<div class="muted">${t("side.historyFailed")}</div>`;
      return;
    }
    side.innerHTML = `
      <div class="side-head"><h3>${icon("history", 15)} ${t("notes.history")}</h3></div>
      ${versions
        .map(
          (v) => `
        <button class="side-version ${v.version === n.version ? "current" : ""}" type="button" data-version="${v.version}">
          <span class="idx">v${v.version}</span>
          <span class="grow">${escHtml(v.change_note || "")}<span class="muted">${escHtml((v.changed_by || "—").replace(/^dash:/, ""))} · ${fmtAgo(v.created_at)}</span></span>
          ${v.version === n.version ? `<span class="chip subtle">${t("side.current")}</span>` : ""}
        </button>`,
        )
        .join("")}`;
  }
  await loadIcons(side);
  side.querySelectorAll("[data-open-note]").forEach((b) => b.addEventListener("click", () => openNote(b.dataset.openNote)));
  side.querySelectorAll("[data-version]").forEach((b) => b.addEventListener("click", () => openVersionPreview(Number(b.dataset.version))));
}

async function openVersionPreview(version) {
  const n = notes.current;
  let v;
  try {
    v = await api.get(`/notes/${n.id}/versions/${version}`);
  } catch (err) {
    await uiError(t("version.openFailed"), err);
    return;
  }
  const isCurrent = version === n.version;
  document.getElementById("version-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "version",
    iconName: "history",
    title: `${escHtml(v.title)} — v${version}`,
    subtitle: `${escHtml((v.changed_by || "—").replace(/^dash:/, ""))} · ${fmtDate(v.created_at)}${v.change_note ? ` · “${escHtml(v.change_note)}”` : ""}`,
    submitLabel: isCurrent ? t("common.close") : t("version.restore"),
    submitIcon: isCurrent ? "check" : "refresh-cw",
    width: 820,
    body: `<div class="version-viewer" id="version-viewer"></div>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("version-modal");
  await loadIcons(modal);
  const viewer = toastui.Editor.factory({ el: modal.querySelector("#version-viewer"), viewer: true, initialValue: v.content, theme: currentTheme(), usageStatistics: false });
  const setOpen = bindModal("version");
  if (!canEdit() && !isCurrent) modal.querySelector("button[type=submit]").remove();
  modal.querySelector("#version-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (isCurrent) {
      setOpen(false);
      return;
    }
    if (notes.dirty) {
      const discard = await uiConfirm({
        title: t("version.discardTitle"),
        message: t("version.discardMsg", { version }),
        confirmLabel: t("version.restoreAnyway"),
        tone: "danger",
      });
      if (!discard) return;
    }
    try {
      await api.post(`/notes/${n.id}/restore/${version}`);
      viewer.destroy();
      setOpen(false);
      modal.remove();
      store.del(DRAFT_PREFIX + n.id);
      notes.dirty = false;
      notes.current = null;
      await loadNotesTree();
      await openNote(n.id);
    } catch (err) {
      await uiError(t("version.restoreFailed"), err);
    }
  });
  setOpen(true);
}

/* ---------------------------------------------------------------- tela: painel (indicadores e gráficos) */

// Gráficos em SVG feito à mão (sem lib): uma série por gráfico, um tom só (--viz-accent, validado
// contra a superfície escura), barras <= 24px com ponta arredondada de 4px, grade hairline sólida,
// tooltip por marca e alternância "Tabela" em todo gráfico (o tooltip nunca é o único caminho).

const PERIODS = [7, 30, 90];
// formatadores seguem o idioma atual (criados na hora: a troca de idioma vale sem recarregar)
const nf = { format: (v) => new Intl.NumberFormat(locale()).format(v) };
const nfCompact = { format: (v) => new Intl.NumberFormat(locale(), { notation: "compact", maximumFractionDigits: 1 }).format(v) };
const fmtDay = (iso, opts = { day: "numeric", month: "short" }) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(locale(), opts).replace(".", "");
const actorName = (a) => (a || "—").replace(/^dash:/, "");
const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

// eixo "limpo": 0 / 2 / 4… até cobrir o máximo, com no máximo ~4 divisões
function niceTicks(max) {
  if (max <= 0) return [0, 1];
  const raw = max / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw);
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Math.round(v * 1000) / 1000);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

// coluna com ponta superior arredondada (4px) e base reta
function columnPath(x, y, w, h, r = 4) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

// barra horizontal com ponta direita arredondada (4px) e base reta à esquerda
function barPath(x, y, w, h, r = 4) {
  r = Math.min(r, h / 2, w);
  return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`;
}

/* ---------- tooltip único (conteúdo sempre via textContent: rótulos vêm do banco) */

function vizTip() {
  let tip = document.getElementById("viz-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "viz-tip";
    tip.className = "viz-tip hidden";
    tip.setAttribute("role", "status");
    document.body.appendChild(tip);
  }
  return tip;
}

function showTip(evt, value, label) {
  const tip = vizTip();
  tip.replaceChildren();
  const strong = document.createElement("strong");
  strong.textContent = value;
  const small = document.createElement("span");
  small.textContent = label;
  tip.append(strong, small);
  tip.classList.remove("hidden");
  const r = evt.target.getBoundingClientRect?.() || { left: evt.clientX, top: evt.clientY, width: 0 };
  const x = evt.clientX ?? r.left + r.width / 2;
  const y = evt.clientY ?? r.top;
  tip.style.left = `${Math.min(window.innerWidth - tip.offsetWidth - 8, Math.max(8, x - tip.offsetWidth / 2))}px`;
  tip.style.top = `${Math.max(8, y - tip.offsetHeight - 12)}px`;
}

function hideTip() {
  document.getElementById("viz-tip")?.classList.add("hidden");
}

function bindTip(el, value, label) {
  el.setAttribute("tabindex", "0");
  el.setAttribute("aria-label", `${label}: ${value}`);
  el.addEventListener("pointermove", (e) => showTip(e, value, label));
  el.addEventListener("pointerleave", hideTip);
  el.addEventListener("focus", (e) => {
    const r = e.target.getBoundingClientRect();
    showTip({ target: e.target, clientX: r.left + r.width / 2, clientY: r.top }, value, label);
  });
  el.addEventListener("blur", hideTip);
}

/* ---------- gráficos */

// Colunas por dia (série única). points: [{label, value, tip}]
function drawColumns(box, points, { valueName }) {
  box.replaceChildren();
  const W = Math.max(280, box.clientWidth);
  const H = 200;
  const pad = { top: 18, right: 8, bottom: 26, left: 34 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;
  const max = Math.max(...points.map((p) => p.value), 0);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const slot = plotW / points.length;
  const barW = Math.max(2, Math.min(24, slot - 2)); // 2px de superfície entre colunas vizinhas
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img" });

  for (const tick of ticks) {
    const y = pad.top + plotH - (tick / top) * plotH;
    svg.append(svgEl("line", { x1: pad.left, x2: W - pad.right, y1: y, y2: y, class: tick === 0 ? "viz-axis" : "viz-gridline" }));
    const label = svgEl("text", { x: pad.left - 8, y: y + 4, class: "viz-tick", "text-anchor": "end" });
    label.textContent = nf.format(tick);
    svg.append(label);
  }
  // rótulos do eixo X esparsos: ~6 ao longo do período
  const every = Math.max(1, Math.ceil(points.length / 6));
  const maxIdx = points.findIndex((p) => p.value === max && max > 0);
  points.forEach((p, i) => {
    const x = pad.left + i * slot + (slot - barW) / 2;
    const h = top ? (p.value / top) * plotH : 0;
    const y = pad.top + plotH - h;
    // área de hover = a fatia inteira do dia (maior que a marca)
    const hit = svgEl("rect", { x: pad.left + i * slot, y: pad.top, width: slot, height: plotH, class: "viz-hit" });
    const g = svgEl("g", { class: "viz-col" });
    if (p.value > 0) g.append(svgEl("path", { d: columnPath(x, y, barW, h), class: "viz-mark" }));
    g.append(hit);
    bindTip(g, `${nf.format(p.value)} ${valueName(p.value)}`, p.tip);
    svg.append(g);
    if (i % every === 0 || i === points.length - 1) {
      const lx = svgEl("text", { x: x + barW / 2, y: H - 8, class: "viz-tick", "text-anchor": "middle" });
      lx.textContent = p.label;
      svg.append(lx);
    }
    if (i === maxIdx) {
      // rótulo direto só no pico (nunca um número em cada coluna)
      const lv = svgEl("text", { x: x + barW / 2, y: y - 6, class: "viz-value", "text-anchor": "middle" });
      lv.textContent = nf.format(p.value);
      svg.append(lv);
    }
  });
  box.append(svg);
}

// Barras horizontais (série única). rows: [{label, value, dot?, tip?}]
function drawBars(box, rows, { valueName, empty }) {
  box.replaceChildren();
  if (!rows.length || rows.every((r) => !r.value)) {
    box.innerHTML = `<div class="viz-empty">${escHtml(empty)}</div>`;
    return;
  }
  const W = Math.max(260, box.clientWidth);
  const rowH = 30;
  const barH = 12;
  const labelW = Math.min(150, Math.round(W * 0.38));
  const valueW = 44;
  const plotW = W - labelW - valueW - 8;
  const max = Math.max(...rows.map((r) => r.value));
  const H = rows.length * rowH;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img" });
  svg.append(svgEl("line", { x1: labelW, x2: labelW, y1: 0, y2: H, class: "viz-axis" }));
  rows.forEach((r, i) => {
    const cy = i * rowH + rowH / 2;
    const w = max ? Math.max(r.value ? 3 : 0, (r.value / max) * plotW) : 0;
    let tx = 0;
    if (r.dot) {
      svg.append(svgEl("circle", { cx: 5, cy, r: 4, fill: r.dot }));
      tx = 16;
    }
    const label = svgEl("text", { x: tx, y: cy + 4, class: "viz-label" });
    const maxChars = Math.floor((labelW - tx - 8) / 7);
    label.textContent = r.label.length > maxChars ? `${r.label.slice(0, maxChars - 1)}…` : r.label;
    svg.append(label);
    const g = svgEl("g", { class: "viz-col" });
    if (w > 0) g.append(svgEl("path", { d: barPath(labelW + 1, cy - barH / 2, w, barH), class: "viz-mark" }));
    g.append(svgEl("rect", { x: 0, y: i * rowH, width: W, height: rowH, class: "viz-hit" }));
    bindTip(g, `${nf.format(r.value)} ${valueName(r.value)}`, r.tip || r.label);
    svg.append(g);
    const v = svgEl("text", { x: labelW + 1 + w + 6, y: cy + 4, class: "viz-value" });
    v.textContent = nf.format(r.value);
    svg.append(v);
  });
  box.append(svg);
}

function sparkline(values) {
  const W = 96;
  const H = 28;
  const max = Math.max(...values, 1);
  const step = values.length > 1 ? W / (values.length - 1) : W;
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(H - 3 - (v / max) * (H - 6)).toFixed(1)}`);
  const last = pts[pts.length - 1].split(",");
  return `<svg class="sparkline" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true">
    <polyline points="${pts.join(" ")}" />
    <circle cx="${last[0]}" cy="${last[1]}" r="3" /></svg>`;
}

function meter(label, value, total, hint) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  return `
    <div class="meter">
      <div class="meter-head"><span>${escHtml(label)}</span><strong>${pct}%</strong></div>
      <div class="meter-track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="${escHtml(label)}">
        <span style="width:${pct}%"></span>
      </div>
      <div class="meter-foot muted">${t("meter.foot", { value: nf.format(value), total: nf.format(total), hint: escHtml(hint) })}</div>
    </div>`;
}

/* ---------- tela */

function chartCard(id, title, subtitle, { wide = false } = {}) {
  return `
    <section class="viz-card ${wide ? "wide" : ""}" id="${id}">
      <header class="viz-card-head">
        <div class="grow"><h2>${title}</h2>${subtitle ? `<span class="muted">${subtitle}</span>` : ""}</div>
        <button class="btn sm ghost" type="button" data-table-toggle="${id}" title="${t("chart.tableTitle")}">${icon("table-2", 13)}<span>${t("chart.table")}</span></button>
      </header>
      <div class="viz-body"></div>
      <div class="viz-table hidden"></div>
    </section>`;
}

function dataTable(head, rows) {
  return `<table class="dt"><thead><tr>${head.map((h, i) => `<th class="${i ? "num" : ""}">${escHtml(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i ? "num" : ""}">${escHtml(String(c))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function overviewTemplate() {
  const days = state.insightsDays;
  return `
    <section class="page overview-page">
      <header class="page-header">
        <h1>${icon("layout-dashboard", 18)} ${t("overview.title")}</h1>
        <span class="sub">${t("overview.sub")}</span>
      </header>
      <div class="overview-col" id="overview">
        <div class="overview-filters">
          <span class="filter-label">${icon("calendar", 14)}${t("overview.period")}</span>
          <div class="period-seg" id="period-seg" role="radiogroup" aria-label="${t("overview.period")}">
            ${PERIODS.map((d) => `<button type="button" role="radio" aria-checked="${d === days}" data-days="${d}" class="${d === days ? "active" : ""}">${t("overview.periodDays", { count: d })}</button>`).join("")}
          </div>
          <span class="grow"></span>
          <span class="muted overview-updated" id="overview-updated"></span>
          <button class="icon-btn overview-refresh" type="button" id="overview-refresh" title="${t("overview.refresh")}">${icon("refresh-cw", 15)}</button>
        </div>
        <div class="kpis" id="kpis"></div>
        <div class="viz-grid">
          ${chartCard("viz-activity", t("overview.activity"), t("overview.activitySub", { days }), { wide: true })}
          ${chartCard("viz-collections", t("overview.byFolder"), t("overview.byFolderSub"))}
          ${chartCard("viz-contributors", t("overview.contributors"), t("overview.contributorsSub", { days }))}
          <section class="viz-card" id="viz-health">
            <header class="viz-card-head"><div class="grow"><h2>${t("overview.health")}</h2><span class="muted">${t("overview.healthSub")}</span></div></header>
            <div class="viz-body"></div>
          </section>
          <section class="viz-card" id="viz-recent">
            <header class="viz-card-head"><div class="grow"><h2>${t("overview.recent")}</h2><span class="muted">${t("overview.recentSub")}</span></div></header>
            <div class="viz-body"></div>
          </section>
        </div>
      </div>
    </section>`;
}

async function renderOverview(main) {
  main.innerHTML = overviewTemplate();
  await loadIcons(main);
  main.querySelector("#period-seg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-days]");
    if (!b || Number(b.dataset.days) === state.insightsDays) return;
    state.insightsDays = Number(b.dataset.days);
    main.querySelectorAll("#period-seg button").forEach((x) => {
      x.classList.toggle("active", x === b);
      x.setAttribute("aria-checked", String(x === b));
    });
    loadInsights();
  });
  main.querySelector("#overview-refresh").addEventListener("click", () => loadInsights());
  main.querySelector("#overview").addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-table-toggle]");
    if (toggle) {
      const card = document.getElementById(toggle.dataset.tableToggle);
      const showTable = card.querySelector(".viz-table").classList.toggle("hidden") === false;
      card.querySelector(".viz-body").classList.toggle("hidden", showTable);
      toggle.classList.toggle("active", showTable);
      toggle.querySelector("span").textContent = showTable ? t("chart.chart") : t("chart.table");
      return;
    }
    const open = e.target.closest("[data-open-note]");
    if (open) goTo("notes", open.dataset.openNote);
  });
  await loadInsights();
}

async function loadInsights() {
  const root = document.getElementById("overview");
  if (!root) return;
  root.classList.add("loading"); // recarga mantém o quadro anterior, só esmaecido
  const spinStart = performance.now();
  // o ícone completa ao menos a volta em andamento (0,7 s), mesmo com resposta instantânea
  const stopSpin = () =>
    setTimeout(() => document.getElementById("overview-refresh")?.classList.remove("spinning"), 700 - ((performance.now() - spinStart) % 700));
  document.getElementById("overview-refresh")?.classList.add("spinning");
  let data;
  try {
    data = await api.get("/insights", { days: state.insightsDays });
  } catch (err) {
    root.classList.remove("loading");
    stopSpin();
    await uiError(t("overview.loadFailed"), err);
    return;
  }
  if (!document.getElementById("overview")) return;
  state.insights = data;
  root.classList.remove("loading");
  stopSpin();
  document.getElementById("overview-updated").textContent = t("overview.updatedAt", {
    time: new Date().toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }),
  });
  root.querySelector("#viz-activity .muted").textContent = t("overview.activitySub", { days: data.days });
  root.querySelector("#viz-contributors .muted").textContent = t("overview.contributorsSub", { days: data.days });
  renderKpis(data);
  renderOverviewCharts();
  renderHealth(data);
  renderRecent(data);
}

function renderKpis(d) {
  const totals = d.totals;
  const edits = d.activity.reduce((s, p) => s + p.edits, 0);
  const delta = d.created.current - d.created.previous;
  const newCount = d.created.current;
  const notesSub = newCount ? t("kpi.notesNew", { count: nf.format(newCount), days: d.days }) : t("kpi.notesNone", { days: d.days });
  const notesTip = t("kpi.notesTip", { count: nf.format(newCount), previous: nf.format(d.created.previous), days: d.days });
  const tiles = [
    { label: t("kpi.notes"), iconName: "notebook-pen", value: totals.documents, sub: notesSub, subTip: notesTip, trend: delta > 0 ? "up" : delta < 0 ? "down" : "" },
    { label: t("kpi.edits", { days: d.days }), iconName: "activity", value: edits, spark: d.activity.map((p) => p.edits) },
    { label: t("kpi.folders"), iconName: "folder", value: totals.collections },
    {
      label: t("kpi.links"),
      iconName: "link",
      value: totals.links,
      sub: totals.pending_links ? t("kpi.pending", { count: nf.format(totals.pending_links) }) : t("kpi.linksSub"),
    },
    { label: t("kpi.agents"), iconName: "bot", value: totals.agents, sub: t("kpi.memories", { count: nf.format(totals.memories) }) },
    { label: t("kpi.chunks"), iconName: "file-text", value: totals.chunks, sub: t("kpi.chunksSub"), tech: true },
  ];
  document.getElementById("kpis").innerHTML = tiles
    .map(
      (k) => `
      <div class="kpi ${k.tech ? "tech" : ""}">
        <span class="kpi-label"><span class="kpi-icon">${icon(k.iconName, 14)}</span>${escHtml(k.label)}</span>
        <div class="kpi-row">
          <span class="kpi-value" title="${nf.format(k.value)}">${k.value >= 10000 ? nfCompact.format(k.value) : nf.format(k.value)}</span>
          ${k.spark ? sparkline(k.spark) : ""}
        </div>
        ${k.sub ? `<span class="kpi-sub ${k.trend || ""}" ${k.subTip ? `title="${escHtml(k.subTip)}"` : ""}>${k.trend === "up" ? "▲ " : k.trend === "down" ? "▼ " : ""}${escHtml(k.sub)}</span>` : ""}
      </div>`,
    )
    .join("");
  loadIcons(document.getElementById("kpis"));
}

// redesenha só os gráficos (usado também no resize)
function renderOverviewCharts() {
  const d = state.insights;
  if (!d || !document.getElementById("overview")) return;
  const activity = d.activity.map((p) => ({
    label: fmtDay(p.day),
    value: p.edits,
    tip: fmtDay(p.day, { weekday: "short", day: "numeric", month: "short" }) + (p.created ? t("chart.newNotes", { count: p.created }) : ""),
  }));
  const card = (id) => document.querySelector(`#${id}`);
  drawColumns(card("viz-activity").querySelector(".viz-body"), activity, { valueName: (v) => t("unit.edit", { count: v }) });
  card("viz-activity").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.day"), t("col.edits"), t("col.newNotes")],
    d.activity.map((p) => [fmtDay(p.day, { day: "2-digit", month: "2-digit", year: "numeric" }), p.edits, p.created]),
  );

  drawBars(
    card("viz-collections").querySelector(".viz-body"),
    d.by_collection.map((c) => ({ label: c.name, value: c.documents, dot: colorFor(c.name) })),
    { valueName: (v) => t("unit.note", { count: v }), empty: t("chart.noFolders") },
  );
  card("viz-collections").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.folder"), t("col.notes")],
    d.by_collection.map((c) => [c.name, c.documents]),
  );

  drawBars(
    card("viz-contributors").querySelector(".viz-body"),
    d.contributors.map((c) => ({ label: actorName(c.actor), value: c.edits })),
    { valueName: (v) => t("unit.edit", { count: v }), empty: t("chart.noEdits", { days: d.days }) },
  );
  card("viz-contributors").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.who"), t("col.edits")],
    d.contributors.map((c) => [actorName(c.actor), c.edits]),
  );
}

function renderHealth(d) {
  const h = d.health;
  const totals = d.totals;
  document.querySelector("#viz-health .viz-body").innerHTML = `
    <div class="meters">
      ${meter(t("health.tags"), h.with_tags, h.documents, t("health.tagsHint"))}
      ${meter(t("health.linked"), h.with_links, h.documents, t("health.linkedHint"))}
      ${meter(t("health.fresh"), h.documents - h.stale, h.documents, t("health.freshHint"))}
    </div>
    <div class="health-facts">
      <span>${icon("archive", 13)} ${t("health.archived", { count: nf.format(totals.archived) })}</span>
      <span>${icon("link", 13)} ${t("health.pending", { count: nf.format(totals.pending_links) })}</span>
      <span>${icon("users", 13)} ${t("health.people", { count: nf.format(totals.users) })}</span>
    </div>`;
  loadIcons(document.querySelector("#viz-health"));
}

function renderRecent(d) {
  const box = document.querySelector("#viz-recent .viz-body");
  box.innerHTML =
    d.recent
      .map(
        (n) => `
      <button class="recent-row" type="button" data-open-note="${n.id}">
        <span class="dot" style="background:${colorFor(n.collection)}"></span>
        <span class="grow"><span class="recent-title">${escHtml(n.title)}</span>
          <span class="muted">${escHtml(n.collection)} · v${n.version} · ${escHtml(actorName(n.updated_by))}</span></span>
        <span class="muted recent-when">${fmtAgo(n.updated_at)}</span>
      </button>`,
      )
      .join("") || `<div class="viz-empty">${t("overview.recentEmpty")}</div>`;
}

const onOverviewResize = debounce(() => state.screen === "overview" && renderOverviewCharts(), 150);
window.addEventListener("resize", onOverviewResize);

/* ---------------------------------------------------------------- tela: grafo */


function graphTemplate() {
  return `
    <div class="split">
      <section class="page">
        <header class="page-header">
          <h1>${icon("network", 18)} ${t("graph.title")}</h1>
          <span class="sub" id="graph-sub"></span>
        </header>
        <div class="toolbar">
          <div class="field">${icon("folder")}<label for="f-collection">${t("graph.collection")}</label><select id="f-collection"><option value="">${t("graph.all")}</option></select></div>
          <div class="field">${icon("sliders-horizontal")}<label for="f-sim">${t("graph.similarity")} <span id="f-sim-val" class="mono">${state.minSimilarity.toFixed(2)}</span></label>
            <input type="range" id="f-sim" min="0" max="0.95" step="0.05" value="${state.minSimilarity}" />
          </div>
          <div class="field tech"><label for="f-k">Top-k</label>
            <select id="f-k">${[1, 2, 3, 5, 8, 12, 20].map((k) => `<option ${k === state.k ? "selected" : ""}>${k}</option>`).join("")}</select>
          </div>
          <div class="seg tech" id="f-level">
            <button data-level="documents" class="${state.level === "documents" ? "active" : ""}">${t("graph.documents")}</button>
            <button data-level="chunks" class="${state.level === "chunks" ? "active" : ""}">${t("graph.chunks")}</button>
          </div>
          <button class="btn sm hidden" id="hl-clear">${icon("check")}<span>${t("graph.clearHighlight")}</span></button>
          ${
            state.user?.role === "admin"
              ? `<button class="btn sm ghost" id="connect-mode" title="${t("graph.connectTitle")}">${icon("link", 14)}<span>${t("graph.connect")}</span></button>`
              : ""
          }
        </div>
        <div class="graph-wrap" id="graph-wrap">
          <!-- o force-graph limpa o elemento em que é montado: overlays ficam fora de #graph-canvas -->
          <div class="graph-canvas" id="graph-canvas"></div>
          <div class="empty-state" id="graph-empty">${t("graph.loading")}</div>
          <div class="legend hidden" id="graph-legend"></div>
          <div class="graph-notice hidden" id="graph-notice" role="status"></div>
          <div class="graph-hint hidden" id="graph-hint">${t("graph.hint")}</div>
          <div class="graph-zoom hidden" id="graph-zoom">
            <button class="icon-btn" id="zoom-fit" title="${t("graph.sync")}">${icon("refresh-cw")}</button>
          </div>
        </div>
      </section>
      <aside class="panel hidden" id="panel"></aside>
    </div>`;
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function bindGraphControls() {
  const main = document.getElementById("main");
  const reloadSoon = debounce(loadGraph, 250);
  main.querySelector("#f-collection").addEventListener("change", (e) => {
    state.collection = e.target.value;
    loadGraph();
  });
  main.querySelector("#f-sim").addEventListener("input", (e) => {
    state.minSimilarity = Number(e.target.value);
    main.querySelector("#f-sim-val").textContent = state.minSimilarity.toFixed(2);
    reloadSoon();
  });
  main.querySelector("#f-k").addEventListener("change", (e) => {
    state.k = Number(e.target.value);
    loadGraph();
  });
  main.querySelector("#f-level").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-level]");
    if (!btn) return;
    state.level = btn.dataset.level;
    main.querySelectorAll("#f-level button").forEach((b) => b.classList.toggle("active", b === btn));
    loadGraph();
  });
  main.querySelector("#connect-mode")?.addEventListener("click", () => setConnectMode(!state.connectMode));
  main.querySelector("#hl-clear").addEventListener("click", () => {
    state.highlightDocs = null;
    loadGraph();
  });
  // sincronizar: recarrega o grafo do servidor e reenquadra (o ícone gira até terminar)
  main.querySelector("#zoom-fit").addEventListener("click", () => loadGraph());

  // legenda: hover destaca a coleção no grafo, clique filtra (ou volta para todas)
  const legend = main.querySelector("#graph-legend");
  const setLegendHover = (name) => {
    if (state.legendHover === name) return;
    state.legendHover = name;
    // o force-graph pausa o redesenho quando a simulação para; liga durante o hover
    state.graphInstance?.autoPauseRedraw(!name);
  };
  legend.addEventListener("mouseover", (e) => setLegendHover(e.target.closest(".legend-row")?.dataset.col || null));
  legend.addEventListener("mouseleave", () => setLegendHover(null));
  legend.addEventListener("click", (e) => {
    if (e.target.closest("[data-toggle-semantic]")) {
      state.showSemantic = !state.showSemantic;
      loadGraph();
      return;
    }
    const btn = e.target.closest("[data-col]");
    if (!btn) return;
    setLegendHover(null);
    state.collection = btn.dataset.col;
    main.querySelector("#f-collection").value = state.collection;
    loadGraph();
  });
}

let graphRequest = 0;

// Grafo com nós mas sem arestas: descobre a conexão mais forte (consulta com threshold 0, k=1)
// e oferece baixar o slider até ela — senão parece que o grafo "quebrou".
async function explainMissingEdges(data, req) {
  const notice = document.getElementById("graph-notice");
  notice.classList.add("hidden");
  if (!state.showSemantic || data.edges.some((e) => e.kind === "semantic") || data.nodes.length < 2) return;
  let probe;
  try {
    probe = await api.get("/graph", { level: state.level, collection: state.collection, min_similarity: 0, k: 1 });
  } catch {
    return;
  }
  if (req !== graphRequest) return; // o usuário já mudou o filtro
  const strongest = Math.max(0, ...probe.edges.filter((e) => e.kind === "semantic").map((e) => e.similarity));
  if (!strongest) return;
  // passo do slider é 0.05: arredonda para baixo para a aresta mais forte aparecer
  const target = Math.floor(strongest * 20) / 20;
  const fmt = (v) => v.toFixed(2);
  notice.innerHTML = `
    <span>${t("graph.noEdges", { min: fmt(state.minSimilarity), max: fmt(strongest) })}</span>
    <button class="btn sm" type="button" id="notice-lower">${t("graph.showFrom", { value: fmt(target) })}</button>`;
  notice.classList.remove("hidden");
  notice.querySelector("#notice-lower").addEventListener("click", () => {
    state.minSimilarity = target;
    document.getElementById("f-sim").value = target;
    document.getElementById("f-sim-val").textContent = fmt(target);
    loadGraph();
  });
}

// Animação do botão de sincronizar: gira enquanto carrega/assenta/enquadra o grafo.
// Ao parar, completa a volta em andamento para o ícone não "pular".
const SYNC_TURN_MS = 700;
let syncSince = 0;
let syncTimer = null;
let syncSafety = null;

function setGraphSyncing(on) {
  const btn = document.getElementById("zoom-fit");
  if (!btn) return;
  clearTimeout(syncTimer);
  clearTimeout(syncSafety);
  if (on) {
    if (!btn.classList.contains("syncing")) syncSince = performance.now();
    btn.classList.add("syncing");
    btn.setAttribute("aria-busy", "true");
    // segurança: nunca girar para sempre se a simulação não sinalizar o fim
    syncSafety = setTimeout(() => setGraphSyncing(false), 10000);
    return;
  }
  const rest = SYNC_TURN_MS - ((performance.now() - syncSince) % SYNC_TURN_MS);
  syncTimer = setTimeout(() => {
    btn.classList.remove("syncing");
    btn.removeAttribute("aria-busy");
  }, rest);
}

async function loadGraph() {
  const req = ++graphRequest;
  setGraphSyncing(true);
  let data;
  try {
    data = await api.get("/graph", {
      level: state.level,
      collection: state.collection,
      min_similarity: state.minSimilarity,
      k: state.k,
    });
  } catch {
    if (req === graphRequest) graphMessage(t("graph.loadFailed"));
    return;
  }
  // resposta atrasada de um filtro anterior: descarta
  if (req !== graphRequest || !document.getElementById("graph-wrap")) return;
  fillCollectionFilter(data.collections);
  if (!data.nodes.length) {
    graphMessage(t("graph.empty"));
    return;
  }
  drawGraph(data);
  explainMissingEdges(data, req);
}

function destroyGraph() {
  if (state.connectMode) setConnectMode(false);
  if (state.graphObserver) state.graphObserver.disconnect();
  if (state.graphInstance) {
    state.graphInstance.pauseAnimation();
    state.graphInstance._destructor && state.graphInstance._destructor();
  }
  state.graphObserver = null;
  state.graphInstance = null;
}

function graphMessage(msg) {
  setGraphSyncing(false);
  destroyGraph();
  const canvas = document.getElementById("graph-canvas");
  if (canvas) canvas.innerHTML = "";
  for (const id of ["graph-legend", "graph-hint", "graph-zoom", "graph-notice"]) {
    document.getElementById(id)?.classList.add("hidden");
  }
  const empty = document.getElementById("graph-empty");
  if (empty) {
    empty.textContent = msg;
    empty.classList.remove("hidden");
  }
  const sub = document.getElementById("graph-sub");
  if (sub) sub.textContent = "";
}

function fillCollectionFilter(collections) {
  const sel = document.getElementById("f-collection");
  const current = state.collection;
  sel.innerHTML =
    `<option value="">${t("graph.all")}</option>` +
    collections.map((c) => `<option value="${c.name}" ${c.name === current ? "selected" : ""}>${c.name} (${c.count})</option>`).join("");
}

function hexAlpha(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

const MAX_FIT_ZOOM = 2.2;

function fitGraph() {
  const g = state.graphInstance;
  if (!g) return;
  // calcula o enquadramento instantâneo, volta à vista atual e anima até o alvo
  // com o zoom limitado (com poucos nós o zoomToFit aproxima demais)
  const fromZoom = g.zoom();
  const fromCenter = g.centerAt();
  g.zoomToFit(0, 80);
  const toZoom = Math.min(g.zoom(), MAX_FIT_ZOOM);
  const toCenter = g.centerAt();
  g.zoom(fromZoom, 0).centerAt(fromCenter.x, fromCenter.y, 0);
  g.centerAt(toCenter.x, toCenter.y, 400).zoom(toZoom, 400);
  setTimeout(() => setGraphSyncing(false), 400);
}

function createGraph(el) {
  const g = ForceGraph()(el)
    .backgroundColor("rgba(0,0,0,0)")
    .nodeId("id")
    .nodeRelSize(5)
    .nodeLabel(() => "")
    .linkDirectionalParticles(0)
    .cooldownTicks(120)
    .onNodeHover((n) => {
      state.hoverNode = n || null;
      el.style.cursor = n ? "pointer" : "";
    })
    .onNodeClick((n) => onGraphNodeClick(n))
    .onBackgroundClick(() => closePanel())
    .onEngineStop(() => {
      if (state.graphNeedsFit) {
        state.graphNeedsFit = false;
        fitGraph();
      }
    });
  g.d3Force("charge").strength(-160);
  const resize = () => g.width(el.clientWidth).height(el.clientHeight);
  resize();
  state.graphObserver = new ResizeObserver(resize);
  state.graphObserver.observe(el);
  return g;
}

function drawGraph(data) {
  const el = document.getElementById("graph-canvas");
  document.getElementById("graph-empty").classList.add("hidden");

  const colors = graphTheme();
  const highlight = state.highlightDocs && state.highlightDocs.size ? state.highlightDocs : null;
  const clearBtn = document.getElementById("hl-clear");
  clearBtn.classList.toggle("hidden", !highlight);
  if (highlight) clearBtn.querySelector("span").textContent = t("graph.clearHighlightN", { count: highlight.size });

  const nodeKey = (n) => (state.level === "documents" ? n.id : n.document_id);
  const isHl = (n) => Boolean(highlight && highlight.has(nodeKey(n)));
  const nodes = data.nodes.map((n) => ({
    ...n,
    color: colorFor(n.collection),
    label: state.level === "documents" ? n.title : `${n.title} · #${n.chunk_index}`,
  }));

  const linkEdges = data.edges.filter((e) => e.kind === "link");
  const semanticEdges = data.edges.filter((e) => e.kind === "semantic");
  const visibleEdges = state.showSemantic ? data.edges : linkEdges;
  document.getElementById("graph-sub").textContent = t("graph.sub", {
    nodes: nodes.length,
    links: linkEdges.length,
    semantic: semanticEdges.length,
    level: state.level === "documents" ? t("graph.levelDocuments") : t("graph.levelChunks"),
  });

  if (!state.graphInstance) state.graphInstance = createGraph(el);
  const g = state.graphInstance;
  const radius = (n) => {
    const base = state.level === "documents" ? 4 + 2.2 * Math.sqrt(n.chunks || 1) : 4;
    return isHl(n) ? base * 1.5 : base;
  };

  g.nodeVal((n) => (radius(n) / 5) ** 2)
    .nodeCanvasObjectMode(() => "replace")
    .nodeCanvasObject((n, ctx, scale) => {
      const r = radius(n);
      // hover na legenda: destaca a coleção e apaga as outras
      const legendDim = state.legendHover && n.collection !== state.legendHover;
      const dim = (highlight && !isHl(n)) || legendDim;
      const hovered = state.hoverNode === n || state.connectFrom === n;
      const color = legendDim
        ? hexAlpha(n.color, 0.18)
        : highlight
          ? isHl(n)
            ? "#d97757"
            : hexAlpha(n.color, 0.25)
          : n.color;
      // halo suave
      if (!dim) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + (hovered ? 6 : 4), 0, 2 * Math.PI);
        ctx.fillStyle = hexAlpha(highlight ? "#d97757" : n.color, hovered ? 0.28 : 0.14);
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 1.5 / scale;
      ctx.strokeStyle = colors.nodeStroke;
      ctx.stroke();
      // rótulo: tamanho fixo na tela; some quando o zoom está muito longe (exceto no hover)
      if (scale < 0.6 && !hovered) return;
      const fontSize = 12 / scale;
      const text = n.label.length > 36 ? n.label.slice(0, 35) + "…" : n.label;
      ctx.font = `${hovered ? 600 : 500} ${fontSize}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = dim ? colors.labelDim : hovered ? colors.labelHover : colors.label;
      ctx.fillText(text, n.x, n.y + r + 6 / scale);
    })
    .nodePointerAreaPaint((n, color, ctx) => {
      ctx.beginPath();
      ctx.arc(n.x, n.y, radius(n) + 4, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
    })
    // link explícito = sólido coral; similaridade = tracejado cinza
    .linkColor((l) => {
      if (highlight && !(isHl(l.source) && isHl(l.target))) return `rgba(${colors.edgeRgb}, 0.04)`;
      if (l.kind === "link") return highlight ? "rgba(217, 119, 87, 0.95)" : "rgba(217, 119, 87, 0.85)";
      if (highlight) return "rgba(217, 119, 87, 0.5)";
      return `rgba(${colors.edgeRgb}, ${Math.min(0.7, 0.28 + Math.max(0, l.similarity - state.minSimilarity) * 1.5)})`;
    })
    // espessura mínima visível mesmo para arestas logo acima do threshold
    .linkWidth((l) => (l.kind === "link" ? 2.2 : Math.min(4, 1.2 + Math.max(0, l.similarity - state.minSimilarity) * 8)))
    .linkLineDash((l) => (l.kind === "link" ? null : [4, 3]))
    .linkLabel((l) => {
      const sim = l.similarity != null ? t("edge.similarity", { value: Number(l.similarity).toFixed(2) }) : "";
      if (l.kind !== "link") return sim;
      const how = l.link_kinds?.includes("wikilink") ? "[[wikilink]]" : t("edge.manual");
      return [t("edge.explicit", { how }), l.note ? `“${escHtml(l.note)}”` : "", sim].filter(Boolean).join("<br>");
    });
  // documentos ligados explicitamente ficam mais perto que os só parecidos
  g.d3Force("link")
    .distance((l) => (l.kind === "link" ? 45 : 90))
    .strength((l) => (l.kind === "link" ? 0.7 : 0.25));

  state.graphNeedsFit = true;
  g.graphData({ nodes, links: visibleEdges.map((e) => ({ ...e })) });

  const legend = document.getElementById("graph-legend");
  const plural = (count) => t(state.level === "documents" ? "legend.docs" : "legend.chunks", { count });
  legend.innerHTML = `
    <div class="legend-head">${t("legend.collections")}</div>
    ${data.collections
      .map(
        (c) => `
      <button class="legend-row ${state.collection === c.name ? "active" : ""}" type="button" data-col="${escHtml(c.name)}"
        title="${state.collection === c.name ? t("legend.filtered") : t("legend.clickToFilter")}">
        <span class="dot" style="background:${colorFor(c.name)}"></span>
        <span class="legend-name">${escHtml(c.name)}</span>
        <span class="count">${plural(c.count)}</span>
      </button>`,
      )
      .join("")}
    ${state.collection ? `<button class="legend-all" type="button" data-col="">${icon("x", 12)}<span>${t("legend.allCollections")}</span></button>` : ""}
    ${
      state.level === "documents"
        ? `<div class="legend-head legend-sep">${t("legend.connections")}</div>
      <div class="legend-edge"><span class="edge-swatch link"></span><span class="legend-name">${t("legend.explicit")}</span>
        <span class="count">${linkEdges.length}</span></div>
      <button class="legend-edge toggle ${state.showSemantic ? "" : "off"}" type="button" data-toggle-semantic
        title="${state.showSemantic ? t("legend.hideSemantic") : t("legend.showSemantic")}">
        <span class="edge-swatch semantic"></span><span class="legend-name">${t("legend.similarity")}</span>
        <span class="count">${state.showSemantic ? semanticEdges.length : t("legend.hidden")}</span></button>`
        : ""
    }`;
  loadIcons(legend);
  for (const id of ["graph-legend", "graph-hint", "graph-zoom"]) document.getElementById(id).classList.remove("hidden");
}

/* ---------------------------------------------------------------- painel do documento */

async function openDocumentPanel(docId) {
  const panel = document.getElementById("panel");
  panel.classList.remove("hidden");
  panel.innerHTML = `<div class="muted">${t("common.loading")}</div>`;
  let doc, docLinks;
  try {
    [doc, docLinks] = await Promise.all([
      api.get(`/documents/${docId}`),
      api.get(`/documents/${docId}/links`).catch(() => ({ links: [], backlinks: [] })),
    ]);
  } catch {
    panel.innerHTML = `<div class="muted">${t("doc.loadFailed")}</div>`;
    return;
  }
  state.docPanelId = docId;
  panel.innerHTML = `
    <div class="panel-head">
      <h2>${escHtml(doc.title)}</h2>
      <button class="icon-btn" id="panel-close" title="${t("common.close")}">${icon("x", 16)}</button>
    </div>
    <div class="chip"><span class="dot" style="background:${colorFor(doc.collection)}"></span>${escHtml(doc.collection)}</div>
    <dl class="kv">
      <dt>${t("doc.version")}</dt><dd>v${doc.version} · ${doc.status === "active" ? t("doc.active") : t("doc.archived")}</dd>
      <dt>${t("doc.createdBy")}</dt><dd>${escHtml(doc.created_by ?? "—")}</dd>
      <dt>${t("doc.updated")}</dt><dd>${fmtDate(doc.updated_at)}</dd>
      ${doc.source ? `<dt>${t("doc.source")}</dt><dd>${escHtml(doc.source)}</dd>` : ""}
      ${doc.tags?.length ? `<dt>${t("doc.tags")}</dt><dd>${doc.tags.map((tag) => `<span class="chip">${escHtml(tag)}</span>`).join(" ")}</dd>` : ""}
    </dl>
    ${linksSection(doc, docLinks)}
    <div class="section">
      <h3>${icon("file-text")} ${t("doc.content")}</h3>
      <div class="doc-content">${escHtml(doc.content)}</div>
    </div>
    <div class="section">
      <h3>${icon("folder")} ${t("doc.chunks", { count: doc.chunks.length })}</h3>
      ${doc.chunks.map((c) => `<div class="chunk-item"><span class="idx">#${c.chunk_index}</span>${t("doc.words", { count: c.word_count })}<p>${escHtml(c.content.slice(0, 140))}${c.content.length > 140 ? "…" : ""}</p></div>`).join("")}
    </div>
    <div class="section">
      <h3>${icon("history")} ${t("doc.versions", { count: doc.versions.length })}</h3>
      ${doc.versions.map((v) => `<div class="version-item"><span class="idx">v${v.version}</span>${escHtml(v.change_note ?? "")}<p>${escHtml(v.changed_by ?? "—")} · ${fmtDate(v.created_at)}</p></div>`).join("")}
    </div>`;
  await loadIcons(panel);
  panel.querySelector("#panel-close").addEventListener("click", closePanel);
  panel.querySelector("#connect-open")?.addEventListener("click", () =>
    openConnectModal({ document_id: docId, title: doc.title }),
  );
  panel.querySelector(".links-section")?.addEventListener("click", async (e) => {
    const go = e.target.closest("[data-open-doc]");
    const del = e.target.closest("[data-unlink]");
    if (go) {
      focusGraphNode(go.dataset.openDoc);
      openDocumentPanel(go.dataset.openDoc);
    } else if (del) {
      const ok = await uiConfirm({
        title: t("unlink.title"),
        message: t("unlink.msg", { title: del.dataset.title }),
        confirmLabel: t("unlink.confirm"),
        tone: "danger",
      });
      if (!ok) return;
      try {
        await api.del(`/links/${del.dataset.unlink}`);
        openDocumentPanel(docId);
        if (document.getElementById("graph-wrap")) loadGraph();
      } catch (err) {
        await uiError(t("unlink.failed"), err);
      }
    }
  });
}

/* ---------------------------------------------------------------- links explícitos (plan-web-03) */

function linksSection(doc, data) {
  const isAdmin = state.user?.role === "admin";
  const kindChip = (kind) => `<span class="chip subtle">${kind === "wikilink" ? "[[wikilink]]" : t("links.manual")}</span>`;
  const outgoing = data.links || [];
  const backlinks = data.backlinks || [];
  const outItem = (l) =>
    l.pending
      ? `<div class="link-item pending" title="${t("links.pendingTitle")}">
           ${icon("link", 13)}<span class="grow">${escHtml(l.target_title)} <span class="muted">${t("side.notYet")}</span></span>${kindChip(l.kind)}
         </div>`
      : `<div class="link-item">
           ${icon("link", 13)}
           <button class="link-title grow" type="button" data-open-doc="${l.document_id}">${escHtml(l.title)}
             ${l.note ? `<span class="muted link-note">“${escHtml(l.note)}”</span>` : ""}</button>
           ${kindChip(l.kind)}
           ${
             isAdmin && l.kind === "manual"
               ? `<button class="icon-btn danger" type="button" data-unlink="${l.id}" data-title="${escHtml(l.title)}" title="${t("links.removeManual")}">${icon("x", 13)}</button>`
               : ""
           }
         </div>`;
  const backItem = (b) => `
    <div class="link-item">
      ${icon("link", 13)}
      <button class="link-title grow" type="button" data-open-doc="${b.document_id}">${escHtml(b.title)}
        ${b.note ? `<span class="muted link-note">“${escHtml(b.note)}”</span>` : ""}</button>
      ${kindChip(b.kind)}
    </div>`;
  return `
    <div class="section links-section">
      <h3>${icon("link")} ${t("links.connections")}
        ${isAdmin && doc.status === "active" ? `<button class="btn sm ghost connect-btn" type="button" id="connect-open">${icon("plus", 13)}<span>${t("side.connectTo")}</span></button>` : ""}
      </h3>
      <div class="links-group">
        <div class="links-label">${t("side.links", { count: outgoing.length })}</div>
        ${outgoing.map(outItem).join("") || `<div class="muted links-empty">${t("links.noneOutgoing")}</div>`}
      </div>
      <div class="links-group">
        <div class="links-label">${t("side.backlinks", { count: backlinks.length })}</div>
        ${backlinks.map(backItem).join("") || `<div class="muted links-empty">${t("links.noneBacklinks")}</div>`}
      </div>
    </div>`;
}

// centraliza o nó no grafo (se o grafo estiver aberto) ao navegar por um link do painel
function focusGraphNode(docId) {
  const g = state.graphInstance;
  const node = g?.graphData().nodes.find((n) => n.id === docId);
  if (node) g.centerAt(node.x, node.y, 500);
}

// "Conectar a…": escolhe o destino por autocomplete (ou já vem do modo conectar) e uma nota opcional
async function openConnectModal(source, preselected = null, onDone = null) {
  document.getElementById("connect-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "connect",
    iconName: "link",
    title: t("connect.title"),
    subtitle: t("connect.sub", { title: escHtml(source.title) }),
    submitLabel: t("connect.submit"),
    submitIcon: "link",
    width: 560,
    body: `
      <label class="form-field">
        <span class="form-label">${t("connect.target")}</span>
        <input class="input" type="text" id="connect-q" placeholder="${t("connect.searchPh")}" autocomplete="off" />
      </label>
      <div class="connect-results" id="connect-results" role="listbox"></div>
      <label class="form-field">
        <span class="form-label">${t("connect.why")} <span class="muted">${t("common.optional")}</span></span>
        <input class="input" type="text" id="connect-note" placeholder="${t("connect.notePh")}" />
      </label>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("connect-modal");
  await loadIcons(modal);
  const setOpen = bindModal("connect");
  let chosen = preselected;
  const results = modal.querySelector("#connect-results");
  const render = (items) => {
    const list = items.filter((d) => d.document_id !== source.document_id);
    results.innerHTML =
      list
        .map(
          (d) => `
        <button type="button" class="connect-option ${chosen?.document_id === d.document_id ? "selected" : ""}" data-pick="${d.document_id}" data-title="${escHtml(d.title)}">
          <span class="dot" style="background:${colorFor(d.collection)}"></span>
          <span class="grow">${escHtml(d.title)}</span><span class="muted">${escHtml(d.collection)}</span>
        </button>`,
        )
        .join("") || `<div class="muted links-empty">${t("connect.noneFound")}</div>`;
  };
  const search = debounce(async (q) => render(await api.get("/documents/titles", { q, limit: 8 }).catch(() => [])), 180);
  modal.querySelector("#connect-q").addEventListener("input", (e) => search(e.target.value));
  results.addEventListener("click", (e) => {
    const opt = e.target.closest("[data-pick]");
    if (!opt) return;
    chosen = { document_id: opt.dataset.pick, title: opt.dataset.title };
    results.querySelectorAll(".connect-option").forEach((b) => b.classList.toggle("selected", b === opt));
  });
  modal.querySelector("#connect-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!chosen) {
      await uiAlert(t("connect.pickTitle"), t("connect.pickMsg"));
      return;
    }
    try {
      await api.post(`/documents/${source.document_id}/links`, {
        target_id: chosen.document_id,
        note: modal.querySelector("#connect-note").value.trim() || null,
      });
      setOpen(false);
      modal.remove();
      if (onDone) return onDone();
      if (document.getElementById("graph-wrap")) loadGraph();
      openDocumentPanel(source.document_id);
    } catch (err) {
      await uiError(t("connect.failed"), err);
    }
  });
  setOpen(true);
  if (preselected) {
    modal.querySelector("#connect-q").value = preselected.title;
    render([preselected]);
    modal.querySelector("#connect-note").focus();
  } else {
    render(await api.get("/documents/titles", { q: "", limit: 8 }).catch(() => []));
  }
}

// modo conectar no grafo: 1º clique = origem (destacada), 2º = destino (abre o modal já preenchido)
function setConnectMode(on) {
  state.connectMode = on;
  state.connectFrom = null;
  const btn = document.getElementById("connect-mode");
  btn?.classList.toggle("active", on);
  btn?.classList.toggle("ghost", !on);
  connectNotice(on ? t("connect.modeStart") : null);
  state.graphInstance?.autoPauseRedraw(!on);
  if (on) document.addEventListener("keydown", connectEsc);
  else document.removeEventListener("keydown", connectEsc);
}

const connectEsc = (e) => e.key === "Escape" && setConnectMode(false);

function connectNotice(html) {
  const notice = document.getElementById("graph-notice");
  if (!notice) return;
  notice.classList.toggle("hidden", !html);
  notice.classList.toggle("connect", Boolean(html));
  notice.innerHTML = html ? `${icon("link", 14)}<span>${html}</span>` : "";
  if (html) loadIcons(notice);
}

function onGraphNodeClick(n) {
  const docId = state.level === "documents" ? n.id : n.document_id;
  if (!state.connectMode) {
    openDocumentPanel(docId);
    return;
  }
  if (!state.connectFrom) {
    state.connectFrom = n;
    connectNotice(t("connect.modeTarget", { title: escHtml(n.title) }));
    return;
  }
  if (state.connectFrom.id === n.id) return;
  const from = state.connectFrom;
  setConnectMode(false);
  openConnectModal({ document_id: from.id, title: from.title }, { document_id: n.id, title: n.title, collection: n.collection });
}

function closePanel() {
  const panel = document.getElementById("panel");
  if (panel) panel.classList.add("hidden");
  state.docPanelId = null;
  document.querySelectorAll(".result.selected").forEach((el) => el.classList.remove("selected"));
}

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

new MutationObserver(() => enhancePasswordFields()).observe(document.body, { childList: true, subtree: true });

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
