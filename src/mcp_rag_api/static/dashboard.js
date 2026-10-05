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
      throw new Error("sessão expirada");
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
};

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
          <button class="icon-btn" type="button" data-close-modal title="Fechar (Esc)">${icon("x", 18)}</button>
        </header>
        <div class="modal-body">${body}</div>
        <footer class="modal-foot">
          <button class="btn" type="button" data-close-modal>Cancelar</button>
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
function uiDialog({ title, message = "", confirmLabel = "OK", cancelLabel = null, tone = "default", iconName = null }) {
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

const uiConfirm = (opts) => uiDialog({ cancelLabel: "Cancelar", ...opts });
const uiAlert = (title, message) => uiDialog({ title, message });
const uiError = (title, err) => uiDialog({ title, message: err?.detail || err?.message || String(err), tone: "danger" });

// logo da marca (static/brand/logo.svg); é imagem, não ícone Lucide: não herda currentColor
const brandLogo = (size = 22) => `<img class="brand-logo" src="static/brand/logo.svg" width="${size}" height="${size}" alt="" />`;

const escHtml = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR") : "—");

/* ---------------------------------------------------------------- estado */

const PALETTE = ["#e8b26a", "#7fb4ca", "#a9c181", "#d08770", "#b48ead", "#ebcb8b", "#88c0d0", "#a3be8c", "#d3869b", "#81a1c1"];

const state = {
  user: null,
  screen: "graph",
  level: "documents",
  collection: "",
  minSimilarity: 0.7,
  k: 5,
  colors: new Map(),
  graphInstance: null,
  graphObserver: null,
  graphNeedsFit: false,
  hoverNode: null,
  docPanelId: null,
  highlightDocs: null, // Set de document_id vindos do testador de busca
  lastSearchResults: [],
  lastSearch: null,
  lastSearchMs: null,
  searchK: 5,
  showRevoked: false,
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
          <h1>${icon("search", 18)} Busca RAG</h1>
          <span class="sub">Busca híbrida real da base (semântica + full-text)</span>
        </header>
        <div class="search-col">
          <form class="composer" id="search-form">
            ${icon("search", 18)}
            <input type="text" id="s-query" placeholder="Pergunte algo ou busque um termo…" autocomplete="off" required />
            <label class="composer-k" title="Quantos trechos trazer">Top-k
              <select id="s-k">${[3, 5, 8, 10].map((k) => `<option ${k === state.searchK ? "selected" : ""}>${k}</option>`).join("")}</select>
            </label>
            <button class="composer-send" type="submit" title="Buscar (Enter)">${icon("search", 16)}</button>
          </form>
          <div class="results-head hidden" id="s-head">
            <span class="muted" id="s-summary"></span>
            <button class="btn sm" id="s-highlight" type="button">${icon("network")}<span>Ver no grafo</span></button>
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
    results.innerHTML = `<div class="results-empty">Buscando…</div>`;
    const t0 = performance.now();
    try {
      const data = await api.post("/search-test", { query, top_k: state.searchK });
      state.lastSearch = query;
      state.lastSearchResults = data.results;
      state.lastSearchMs = Math.round(performance.now() - t0);
      await renderSearchResults();
    } catch (err) {
      results.innerHTML = `<div class="results-empty">A busca falhou: ${escHtml(err.detail || err.message)}</div>`;
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
  const terms = [...new Set(query.toLowerCase().split(/\s+/).filter((t) => t.length >= 3))];
  if (!terms.length) return escHtml(text);
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
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
        <div class="results-empty-title">O que você quer encontrar na base?</div>
        Teste a recuperação como um agente faria: o resultado é o mesmo do <span class="mono">search_knowledge</span> via MCP/REST.
      </div>`;
    return;
  }
  head.classList.toggle("hidden", !results.length);
  if (!results.length) {
    box.innerHTML = `<div class="results-empty">Nenhum resultado para “${escHtml(state.lastSearch)}”. Tente outros termos.</div>`;
    return;
  }
  const docs = new Set(results.map((r) => r.document_id)).size;
  document.getElementById("s-summary").textContent =
    `${results.length} trecho(s) de ${docs} documento(s) · ${state.lastSearchMs ?? "–"} ms`;
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
          <span class="chip subtle">chunk #${r.chunk_index}</span>
        </div>
        <p class="snippet">${highlightTerms(snippet, state.lastSearch)}</p>
        <div class="result-meta">
          <span class="relbar" title="Relevância relativa ao 1º resultado"><span style="width:${rel}%"></span></span>
          <span>${sim > 0 ? `semântica ${sim}%` : "só full-text"}</span>
          <span class="mono">score ${Number(r.score).toFixed(4)}</span>
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
        <p class="muted">Entre para acessar a dashboard da base de conhecimento.</p>
        <label class="form-label" for="login-user">Usuário</label>
        <input class="input" id="login-user" type="text" autocomplete="username" required />
        <label class="form-label" for="login-pass">Senha</label>
        <input class="input" id="login-pass" type="password" autocomplete="current-password" required />
        <div class="form-error hidden" id="login-error"></div>
        <button class="btn primary" type="submit">${icon("log-in")}<span>Entrar</span></button>
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
      error.textContent = err.status === 401 ? "Usuário ou senha inválidos." : "Não foi possível entrar. Tente de novo.";
      error.classList.remove("hidden");
    }
  });
}

/* ---------------------------------------------------------------- shell (sidebar + conteúdo) */

const NAV = [
  { id: "graph", label: "Grafo da base", iconName: "network" },
  { id: "search", label: "Busca RAG", iconName: "search" },
  { id: "keys", label: "Chaves API", iconName: "key-round" },
  { id: "agents", label: "Agents", iconName: "bot" },
  { id: "users", label: "Usuários", iconName: "users", adminOnly: true },
];

function navForRole() {
  return NAV.filter((n) => !n.adminOnly || state.user?.role === "admin");
}

function renderShell() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="app">
      <aside class="sidebar">
        <div class="brand">${brandLogo(24)}<span>Concilium</span></div>
        <nav id="nav">
          ${navForRole()
            .map(
              (n) => `
            <button class="nav-item ${state.screen === n.id ? "active" : ""}" data-nav="${n.id}">
              ${icon(n.iconName)}<span>${n.label}</span>
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
    if (btn) {
      state.screen = btn.dataset.nav;
      renderShell();
      renderScreen();
    }
  });
  loadIcons(app.querySelector(".sidebar"));
  renderUserFooter();
}

function renderUserFooter() {
  const footer = document.getElementById("user-footer");
  if (!footer || !state.user) return;
  const u = state.user;
  const isAdmin = u.role === "admin";
  const roleLabel = isAdmin ? "Administrador" : "Leitor";
  const item = (attrs, iconName, label, extra = "") =>
    `<button class="menu-item" role="menuitem" ${attrs}>${icon(iconName)}<span class="grow">${label}</span>${extra}</button>`;
  footer.innerHTML = `
    <div class="user-menu hidden" id="user-menu" role="menu">
      <div class="menu-head">
        <span class="menu-user">${escHtml(u.username)}</span>
        <span class="muted">${roleLabel}</span>
      </div>
      <div class="menu-sep"></div>
      ${item('data-goto="keys"', "key-round", "Chaves API")}
      ${item('data-goto="agents"', "bot", "Agents")}
      ${isAdmin ? item('data-goto="users"', "users", "Gerenciar usuários") : ""}
      <div class="menu-sep"></div>
      ${item('data-href="/docs"', "book-open", "Documentação da API", icon("external-link", 14))}
      <div class="menu-item static">${icon("activity")}<span class="grow">Status do servidor</span><span class="status" id="server-status"><span class="status-dot"></span>…</span></div>
      <div class="menu-sep"></div>
      ${item('data-logout', "log-out", "Sair")}
    </div>
    <button class="user-trigger" id="user-trigger" aria-haspopup="menu" aria-expanded="false">
      <span class="avatar">${escHtml(u.username.slice(0, 1).toUpperCase())}</span>
      <span class="who" title="${escHtml(u.username)}">${escHtml(u.username)} <span class="muted">· ${isAdmin ? "admin" : "leitor"}</span></span>
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
    const el = e.target.closest(".menu-item");
    if (!el) return;
    setOpen(false);
    if (el.dataset.goto) {
      state.screen = el.dataset.goto;
      renderShell();
      renderScreen();
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
  box.innerHTML = `<span class="status-dot"></span>${ok ? "OK" : "Sem informação"}`;
}

async function renderScreen() {
  const main = document.getElementById("main");
  closePanel();
  destroyGraph();
  if (state.screen === "graph") {
    main.innerHTML = graphTemplate();
    bindGraphControls();
    await loadIcons(main);
    await Promise.all([loadStats(), loadGraph()]);
  } else if (state.screen === "search") {
    main.innerHTML = searchTemplate();
    await loadIcons(main);
    bindSearch();
    await renderSearchResults();
  } else if (state.screen === "keys") {
    main.innerHTML = pageTemplate("key-round", "Chaves API", "Gestão das chaves Bearer de agentes e integrações");
    await loadIcons(main);
    await renderKeysList();
  } else if (state.screen === "agents") {
    main.innerHTML = pageTemplate("bot", "Agents", "Agentes cadastrados na base de conhecimento");
    await loadIcons(main);
    await renderAgentsList();
  } else if (state.screen === "users") {
    main.innerHTML = pageTemplate("users", "Usuários", "Acesso humano à dashboard (admin)");
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

const SCOPES = [
  ["read", "Consultar e buscar na base"],
  ["write", "Inserir e atualizar documentos"],
  ["agents:manage", "Gerenciar agentes e memórias"],
  ["admin", "Acesso total, inclusive chaves"],
];

function fmtAgo(iso) {
  if (!iso) return "nunca";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "agora";
  const m = Math.round(s / 60);
  if (m < 60) return `há ${m} min`;
  const h = Math.round(m / 60);
  if (h < 24) return `há ${h} h`;
  const d = Math.round(h / 24);
  return d < 30 ? `há ${d} dia(s)` : new Date(iso).toLocaleDateString("pt-BR");
}

function keysCreateForm(agents) {
  return modalShell({
    id: "key",
    iconName: "key-round",
    title: "Nova chave",
    subtitle: "Chave Bearer para a API/MCP. Ela é exibida uma única vez, logo após criar.",
    submitLabel: "Criar chave",
    submitIcon: "key-round",
    width: 720,
    body: `
      <div class="create-grid">
        <label class="form-field">
          <span class="form-label">Nome</span>
          <input class="input" type="text" id="key-label" placeholder="ex.: claude-desktop, integração CRM" required />
        </label>
        <label class="form-field">
          <span class="form-label">Dono</span>
          <select class="input" id="key-agent">
            <option value="">Chave humana (sem agente)</option>
            ${agents.map((a) => `<option value="${a.slug}">agent: ${escHtml(a.slug)}</option>`).join("")}
          </select>
        </label>
      </div>
      <div class="form-field">
        <span class="form-label">Escopos</span>
        <div class="scope-grid two" id="key-scopes">
          ${SCOPES.map(
            ([sc, desc]) => `
            <label class="scope-option">
              <input type="checkbox" value="${sc}" ${sc === "read" ? "checked" : ""} />
              <span><span class="mono">${sc}</span><span class="muted">${desc}</span></span>
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
      <div class="tcell muted">${k.agent ? `<span class="chip">${icon("bot", 12)} ${escHtml(k.agent)}</span>` : "humana"}</div>
      <div class="tcell muted" title="Criada em ${fmtDate(k.created_at)}">${revoked ? `revogada ${fmtAgo(k.revoked_at)}` : fmtAgo(k.created_at)}</div>
      <div class="tcell muted" title="${k.last_used_at ? fmtDate(k.last_used_at) : "Nunca usada"}">${fmtAgo(k.last_used_at)}</div>
      <div class="tcell-actions">
        ${
          isAdmin && !revoked
            ? `<button class="btn sm ghost" data-renew="${k.id}" title="Revoga a atual e emite uma chave nova">${icon("refresh-cw", 14)}<span>Renovar</span></button>
               <button class="btn sm ghost danger-text" data-revoke="${k.id}" title="Revogar chave">${icon("ban", 14)}<span>Revogar</span></button>`
            : ""
        }
      </div>
    </div>`;
}

const KEYS_HEAD = `
  <div class="trow thead">
    <div>Chave</div><div>Escopos</div><div>Dono</div><div>Criada</div><div>Último uso</div><div></div>
  </div>`;

async function renderKeysList() {
  const body = document.getElementById("page-body");
  const isAdmin = state.user.role === "admin";
  let keys, agents;
  try {
    [keys, agents] = await Promise.all([api.get("/keys"), api.get("/agents")]);
  } catch {
    body.innerHTML = `<div class="results-empty">Não foi possível carregar as chaves.</div>`;
    return;
  }
  const active = keys.filter((k) => !k.revoked_at);
  const revoked = keys.filter((k) => k.revoked_at);
  body.innerHTML = `
    <div class="page-col">
      <div class="section-head">
        <span class="muted">${active.length} ativa(s) · ${revoked.length} revogada(s)</span>
        ${isAdmin ? `<button class="btn primary" id="key-new">${icon("plus")}<span>Nova chave</span></button>` : ""}
      </div>
      ${isAdmin ? keysCreateForm(agents) : ""}
      <div id="key-banner"></div>
      <div class="table">
        ${KEYS_HEAD}
        ${active.map((k) => keyRow(k, isAdmin)).join("") || `<div class="results-empty">Nenhuma chave ativa.${isAdmin ? " Crie uma em “Nova chave”." : ""}</div>`}
      </div>
      ${
        revoked.length
          ? `<details class="revoked-block" ${state.showRevoked ? "open" : ""}>
              <summary>${icon("history", 14)} ${revoked.length} revogada(s) — mantidas para auditoria</summary>
              <div class="table">${KEYS_HEAD}${revoked.map((k) => keyRow(k, isAdmin)).join("")}</div>
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
      await uiAlert("Escolha um escopo", "A chave precisa de ao menos um escopo.");
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
      await uiError("Não foi possível criar a chave", err);
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
          title: "Renovar chave?",
          message: "A chave atual será revogada de imediato e uma nova será emitida. Atualize quem usa a chave antiga.",
          confirmLabel: "Renovar",
          iconName: "refresh-cw",
        });
        if (!ok) return;
        const key = await api.post(`/keys/${renew.dataset.renew}/renew`);
        await renderKeysList();
        showKeyBanner(key);
      } else if (revoke) {
        const ok = await uiConfirm({
          title: "Revogar chave?",
          message: "Agentes e integrações que usam esta chave perdem o acesso na hora. Não dá para desfazer.",
          confirmLabel: "Revogar",
          tone: "danger",
        });
        if (!ok) return;
        await api.post(`/keys/${revoke.dataset.revoke}/revoke`);
        await renderKeysList();
      }
    } catch (err) {
      await uiError("A operação falhou", err);
    }
  });
}

function showKeyBanner(key) {
  const box = document.getElementById("key-banner");
  box.innerHTML = `
    <div class="key-reveal">
      <div class="key-reveal-head">
        ${icon("check")}<strong>Chave criada</strong>
        <span class="muted">Copie agora — por segurança ela não será exibida de novo.</span>
      </div>
      <div class="key-reveal-row">
        <code class="mono">${escHtml(key.api_key)}</code>
        <button class="btn sm" id="copy-key" type="button">${icon("copy")}<span>Copiar</span></button>
      </div>
    </div>`;
  loadIcons(box);
  box.querySelector("#copy-key").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    await navigator.clipboard.writeText(key.api_key);
    btn.innerHTML = `${icon("check")}<span>Copiado</span>`;
    loadIcons(btn);
  });
}

/* ---------------------------------------------------------------- tela: agents */

const AGENT_SCOPES = [
  ["read", "Consultar e buscar na base"],
  ["write", "Inserir e atualizar documentos"],
  ["agents:manage", "Cadastrar e configurar outros agentes"],
  ["admin", "Acesso total, inclusive chaves"],
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
  const parts = [`Você é ${f.name || "um agente"}.${f.goal ? " " + f.goal.trim() : ""}`];
  if (f.tone.trim()) parts.push(`Tom e idioma: ${f.tone.trim()}.`);
  const always = lines(f.always);
  const never = lines(f.never);
  if (always.length) parts.push("Sempre:\n" + always.map((l) => `- ${l}`).join("\n"));
  if (never.length) parts.push("Nunca:\n" + never.map((l) => `- ${l}`).join("\n"));
  const scope = f.collections.length ? `nas coleções ${f.collections.join(", ")}` : "na base";
  parts.push(
    `Antes de responder, busque contexto ${scope} com search_knowledge e cite o documento de origem. ` +
      "Registre aprendizados duradouros com add_agent_memory.",
  );
  return parts.join("\n\n");
}

function agentCreateForm(collections) {
  return `
    ${modalShell({
      id: "agent",
      iconName: "bot",
      title: "Novo agente",
      subtitle: 'O mesmo cadastro do <span class="mono">create_agent</span> via MCP: gera a versão 1 e a chave do agente.',
      submitLabel: "Criar agente",
      submitIcon: "bot",
      body: `
      <div class="form-section">
        <div class="form-section-title"><span class="step">1</span>Identidade</div>
        <div class="create-grid three">
          <label class="form-field">
            <span class="form-label">Nome</span>
            <input class="input" id="a-name" placeholder="ex.: Assistente de suporte" required />
          </label>
          <label class="form-field">
            <span class="form-label">Slug <span class="muted">(usado na conexão MCP)</span></span>
            <input class="input mono" id="a-slug" placeholder="assistente-suporte" pattern="[a-z0-9][a-z0-9-]{1,62}" required />
          </label>
          <label class="form-field">
            <span class="form-label">Descrição curta</span>
            <input class="input" id="a-desc" placeholder="Responde dúvidas sobre o produto" />
          </label>
        </div>
      </div>

      <div class="form-section">
        <div class="form-section-title"><span class="step">2</span>Comportamento</div>
        <div class="create-grid">
          <label class="form-field">
            <span class="form-label">Objetivo — o que faz e para quem</span>
            <textarea class="input" id="a-goal" rows="3" placeholder="Você ajuda o time de suporte a responder clientes com base na documentação do produto."></textarea>
          </label>
          <label class="form-field">
            <span class="form-label">Tom e idioma</span>
            <textarea class="input" id="a-tone" rows="3">português do Brasil, direto e cordial</textarea>
          </label>
          <label class="form-field">
            <span class="form-label">Sempre <span class="muted">(uma regra por linha)</span></span>
            <textarea class="input" id="a-always" rows="3" placeholder="confirmar o plano do cliente antes de responder"></textarea>
          </label>
          <label class="form-field">
            <span class="form-label">Nunca <span class="muted">(uma regra por linha)</span></span>
            <textarea class="input" id="a-never" rows="3" placeholder="prometer prazos ou descontos"></textarea>
          </label>
        </div>
        <label class="form-field">
          <span class="form-label prompt-label">System prompt
            <span class="muted" id="a-prompt-state">montado automaticamente a partir dos campos acima</span>
            <button class="link-btn hidden" type="button" id="a-prompt-regen">Remontar</button>
          </span>
          <textarea class="input mono prompt-box" id="a-prompt" rows="9" required></textarea>
        </label>
      </div>

      <div class="form-section">
        <div class="form-section-title"><span class="step">3</span>Acesso</div>
        <span class="form-label">Coleções <span class="muted">(nenhuma marcada = todas)</span></span>
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
              : `<span class="muted">Nenhuma coleção ainda — o agente terá acesso a todas.</span>`
          }
        </div>
        <span class="form-label">Permissões</span>
        <div class="scope-grid" id="a-scopes">
          ${AGENT_SCOPES.map(
            ([s, desc]) => `
            <label class="scope-option">
              <input type="checkbox" value="${s}" ${s === "read" || s === "write" ? "checked" : ""} />
              <span><span class="mono">${s}</span><span class="muted">${desc}</span></span>
            </label>`,
          ).join("")}
        </div>
        <label class="scope-option toggle-option">
          <input type="checkbox" id="a-auto" />
          <span><span>Autonomia</span><span class="muted">O agente aplica as próprias propostas de mudança de perfil sem revisão humana. Recomendado: desligado.</span></span>
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
            ${archived ? `<span class="chip danger-chip">arquivado</span>` : ""}
            ${a.proposals ? `<span class="chip accent">${icon("git-pull-request-arrow", 12)} ${a.proposals} proposta(s)</span>` : ""}
          </div>
          ${a.description ? `<div class="muted">${escHtml(a.description)}</div>` : ""}
        </div>
        ${
          isAdmin && !archived
            ? `<button class="btn sm ${a.auto_apply_updates ? "primary" : "ghost"}" data-autonomy="${a.slug}" data-on="${a.auto_apply_updates}"
                 title="Autonomia: aplicar propostas do próprio agente sem revisão">
                 ${icon("zap", 14)}<span>Autonomia ${a.auto_apply_updates ? "ligada" : "desligada"}</span>
               </button>`
            : `<span class="muted">${a.auto_apply_updates ? "autônomo" : "sob revisão"}</span>`
        }
      </div>
      <div class="agent-meta">
        <span>${a.scopes.map((s) => `<span class="chip ${s === "admin" ? "accent" : ""}">${s}</span>`).join(" ")}</span>
        <span>${
          a.allowed_collections.length
            ? a.allowed_collections.map((c) => `<span class="chip"><span class="dot" style="background:${colorFor(c)}"></span>${escHtml(c)}</span>`).join(" ")
            : `<span class="chip subtle">todas as coleções</span>`
        }</span>
        <span class="grow"></span>
        <span class="muted">${icon("key-round", 12)} ${a.active_keys} chave(s)</span>
        <span class="muted mono">v${a.version}</span>
        <span class="muted" title="${fmtDate(a.updated_at)}">atualizado ${fmtAgo(a.updated_at)}</span>
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
    body.innerHTML = `<div class="results-empty">Não foi possível carregar os agents.</div>`;
    return;
  }
  const active = list.filter((a) => a.status === "active").length;
  body.innerHTML = `
    <div class="page-col">
      <div class="section-head">
        <span class="muted">${active} ativo(s) · ${list.length - active} arquivado(s)</span>
        ${isAdmin ? `<button class="btn primary" id="agent-new">${icon("plus")}<span>Novo agente</span></button>` : ""}
      </div>
      ${isAdmin ? agentCreateForm(collections) : ""}
      <div id="agent-banner"></div>
      <div class="agent-list">
        ${
          list.map((a) => agentCard(a, isAdmin)).join("") ||
          `<div class="results-empty">
             <div class="results-empty-title">Nenhum agente ainda</div>
             ${isAdmin ? "Crie o primeiro em “Novo agente” — ou peça a um Claude conectado via MCP (prompt <span class=\"mono\">design_agent</span>)." : "Peça a um administrador para cadastrar um agente."}
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
      title: `${turnOn ? "Ligar" : "Desligar"} autonomia de ${btn.dataset.autonomy}?`,
      message: turnOn
        ? "Com autonomia, as propostas de mudança de perfil do próprio agente se aplicam sem revisão humana."
        : "As próximas propostas do agente voltam a precisar de aprovação humana.",
      confirmLabel: turnOn ? "Ligar autonomia" : "Desligar autonomia",
      tone: turnOn ? "danger" : "default",
      iconName: "zap",
    });
    if (!ok) return;
    try {
      await api.post(`/agents/${btn.dataset.autonomy}/autonomy`, { auto_apply_updates: turnOn });
      await renderAgentsList();
    } catch (err) {
      await uiError("Não foi possível mudar a autonomia", err);
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
    $("a-prompt-state").textContent = v ? "editado manualmente" : "montado automaticamente a partir dos campos acima";
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
      await uiAlert("Escolha uma permissão", "O agente precisa de ao menos uma permissão.");
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
      await uiError("Não foi possível criar o agente", err);
    }
  });
}

function showAgentBanner(created) {
  const box = document.getElementById("agent-banner");
  const copyRow = (id, value) => `
    <div class="key-reveal-row">
      <code class="mono">${escHtml(value)}</code>
      <button class="btn sm" type="button" data-copy="${id}">${icon("copy")}<span>Copiar</span></button>
    </div>`;
  box.innerHTML = `
    <div class="key-reveal">
      <div class="key-reveal-head">
        ${icon("check")}<strong>Agente ${escHtml(created.agent.name)} criado</strong>
        <span class="muted">Copie agora — a chave não será exibida de novo.</span>
      </div>
      <span class="form-label">API key do agente</span>
      ${copyRow("key", created.api_key)}
      <span class="form-label">Conectar no Claude Code</span>
      ${copyRow("cmd", created.connect_command)}
      <span class="muted">Depois, numa conversa nova, use o prompt <span class="mono">start_as_agent</span> com slug <span class="mono">${escHtml(created.agent.slug)}</span>.</span>
    </div>`;
  loadIcons(box);
  const values = { key: created.api_key, cmd: created.connect_command };
  box.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    await navigator.clipboard.writeText(values[btn.dataset.copy]);
    btn.innerHTML = `${icon("check")}<span>Copiado</span>`;
    loadIcons(btn);
  });
}

/* ---------------------------------------------------------------- tela: usuários */

const ROLES = [
  ["viewer", "Leitor", "Vê grafo, busca, chaves e agents, sem alterar nada"],
  ["admin", "Administrador", "Gerencia chaves, usuários e autonomia dos agents"],
];

function userRow(u) {
  const disabled = Boolean(u.disabled_at);
  const self = u.username === state.user.username;
  return `
    <div class="trow users ${disabled ? "revoked" : ""}">
      <div class="tcell-main">
        <span class="avatar">${escHtml(u.username.slice(0, 1).toUpperCase())}</span>
        <div class="grow">
          <div class="row-title">${escHtml(u.username)} ${self ? `<span class="chip subtle">você</span>` : ""}</div>
        </div>
      </div>
      <div class="tcell">
        <select class="input role-select" data-role="${u.id}" ${self || disabled ? "disabled" : ""}
          title="${self ? "Você não pode mudar o próprio papel" : "Papel do usuário"}">
          ${ROLES.map(([v, label]) => `<option value="${v}" ${u.role === v ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </div>
      <div class="tcell"><span class="status ${disabled ? "" : "ok"}"><span class="status-dot"></span>${disabled ? "Desativado" : "Ativo"}</span></div>
      <div class="tcell muted" title="Criado em ${fmtDate(u.created_at)}">${fmtAgo(u.created_at)}</div>
      <div class="tcell-actions">
        ${disabled ? "" : `<button class="btn sm ghost" data-reset="${u.id}" title="Definir uma nova senha">${icon("key-round", 14)}<span>Senha</span></button>`}
        ${
          self
            ? ""
            : `<button class="btn sm ghost ${disabled ? "" : "danger-text"}" data-toggle="${u.id}" data-disabled="${disabled}"
                title="${disabled ? "Reativar acesso" : "Desativar e encerrar sessões"}">
                ${icon(disabled ? "refresh-cw" : "ban", 14)}<span>${disabled ? "Reativar" : "Desativar"}</span>
              </button>`
        }
      </div>
      <div class="reset-row hidden" id="reset-${u.id}">
        <input class="input" type="password" placeholder="Nova senha para ${escHtml(u.username)} (mín. 8 caracteres)" id="reset-pass-${u.id}" minlength="8" />
        <button class="btn sm" type="button" data-reset="${u.id}">Cancelar</button>
        <button class="btn sm primary" type="button" data-do-reset="${u.id}">${icon("check", 14)}<span>Salvar senha</span></button>
      </div>
    </div>`;
}

function usersCreateForm() {
  return modalShell({
    id: "user",
    iconName: "users",
    title: "Novo usuário",
    subtitle: "Acesso humano à dashboard. A pessoa pode trocar a senha depois com um admin.",
    submitLabel: "Criar usuário",
    width: 640,
    body: `
      <div class="create-grid">
        <label class="form-field">
          <span class="form-label">Username</span>
          <input class="input" type="text" id="u-username" placeholder="ex.: maria" autocomplete="off" required />
        </label>
        <label class="form-field">
          <span class="form-label">Senha inicial</span>
          <input class="input" type="password" id="u-password" placeholder="mín. 8 caracteres" minlength="8" autocomplete="new-password" required />
        </label>
      </div>
      <div class="form-field">
        <span class="form-label">Papel</span>
        <div class="scope-grid two">
          ${ROLES.map(
            ([v, label, desc]) => `
            <label class="scope-option">
              <input type="radio" name="u-role" value="${v}" ${v === "viewer" ? "checked" : ""} />
              <span><span>${label}</span><span class="muted">${desc}</span></span>
            </label>`,
          ).join("")}
        </div>
      </div>`,
  });
}

const USERS_HEAD = `
  <div class="trow users thead">
    <div>Usuário</div><div>Papel</div><div>Status</div><div>Criado</div><div></div>
  </div>`;

async function renderUsersList() {
  const body = document.getElementById("page-body");
  let users;
  try {
    users = await api.get("/users");
  } catch (err) {
    body.innerHTML = `<div class="results-empty">${err.status === 403 ? "Só administradores gerenciam usuários." : "Não foi possível carregar os usuários."}</div>`;
    return;
  }
  const active = users.filter((u) => !u.disabled_at).length;
  const admins = users.filter((u) => !u.disabled_at && u.role === "admin").length;
  body.innerHTML = `
    <div class="page-col">
      <div class="section-head">
        <span class="muted">${active} ativo(s) · ${admins} admin(s) · ${users.length - active} desativado(s)</span>
        <button class="btn primary" id="user-new">${icon("plus")}<span>Novo usuário</span></button>
      </div>
      ${usersCreateForm()}
      <div class="table">${USERS_HEAD}${users.map(userRow).join("")}</div>
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
      await uiError("Não foi possível criar o usuário", err);
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
      await uiError("Não foi possível mudar o papel", err);
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
          await uiAlert("Senha curta demais", "A senha precisa ter ao menos 8 caracteres.");
          return;
        }
        await api.patch(`/users/${doReset.dataset.doReset}`, { password: pass });
        await renderUsersList();
      } else if (toggle) {
        const disable = toggle.dataset.disabled !== "true";
        if (
          disable &&
          !(await uiConfirm({
            title: "Desativar usuário?",
            message: "O usuário perde o acesso à dashboard e as sessões abertas são encerradas. Dá para reativar depois.",
            confirmLabel: "Desativar",
            tone: "danger",
          }))
        )
          return;
        await api.patch(`/users/${toggle.dataset.toggle}`, { disabled: disable });
        await renderUsersList();
      }
    } catch (err) {
      await uiError("A operação falhou", err);
    }
  });
}

/* ---------------------------------------------------------------- tela: grafo */

const STATS = [
  ["documents", "Documentos", "file-text"],
  ["chunks", "Chunks", "activity"],
  ["collections", "Collections", "folder"],
  ["agents", "Agents", "bot"],
  ["memories", "Memórias", "history"],
  ["proposals", "Propostas", "git-pull-request-arrow"],
  ["active_keys", "Chaves ativas", "key-round"],
];

function graphTemplate() {
  return `
    <div class="split">
      <section class="page">
        <header class="page-header">
          <h1>${icon("network", 18)} Grafo da base</h1>
          <span class="sub" id="graph-sub"></span>
        </header>
        <div class="stats" id="stats">
          ${STATS.map(
            ([key, label, iconName]) => `
            <div class="stat" data-stat="${key}">
              <span class="stat-label">${icon(iconName, 14)}${label}</span>
              <span class="stat-num">–</span>
            </div>`,
          ).join("")}
        </div>
        <div class="toolbar">
          <div class="field">${icon("folder")}<label for="f-collection">Collection</label><select id="f-collection"><option value="">Todas</option></select></div>
          <div class="field">${icon("sliders-horizontal")}<label for="f-sim">Similaridade ≥ <span id="f-sim-val" class="mono">${state.minSimilarity.toFixed(2)}</span></label>
            <input type="range" id="f-sim" min="0" max="0.95" step="0.05" value="${state.minSimilarity}" />
          </div>
          <div class="field"><label for="f-k">Top-k</label>
            <select id="f-k">${[1, 2, 3, 5, 8, 12, 20].map((k) => `<option ${k === state.k ? "selected" : ""}>${k}</option>`).join("")}</select>
          </div>
          <div class="seg" id="f-level">
            <button data-level="documents" class="${state.level === "documents" ? "active" : ""}">Documentos</button>
            <button data-level="chunks" class="${state.level === "chunks" ? "active" : ""}">Chunks</button>
          </div>
          <button class="btn sm hidden" id="hl-clear">${icon("check")}<span>Limpar destaque</span></button>
        </div>
        <div class="graph-wrap" id="graph-wrap">
          <!-- o force-graph limpa o elemento em que é montado: overlays ficam fora de #graph-canvas -->
          <div class="graph-canvas" id="graph-canvas"></div>
          <div class="empty-state" id="graph-empty">Carregando grafo…</div>
          <div class="legend hidden" id="graph-legend"></div>
          <div class="graph-hint hidden" id="graph-hint">Clique num nó para ver o documento · arraste para mover · role para zoom</div>
          <div class="graph-zoom hidden" id="graph-zoom">
            <button class="icon-btn" id="zoom-fit" title="Enquadrar tudo">${icon("refresh-cw")}</button>
          </div>
        </div>
      </section>
      <aside class="panel hidden" id="panel"></aside>
    </div>`;
}

async function loadStats() {
  const box = document.getElementById("stats");
  if (!box) return;
  let s = {};
  try {
    s = await api.get("/stats");
  } catch {
    // mantém "–" nos cards
  }
  for (const el of box.querySelectorAll("[data-stat]")) {
    el.querySelector(".stat-num").textContent = s[el.dataset.stat] ?? "–";
  }
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
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
  main.querySelector("#hl-clear").addEventListener("click", () => {
    state.highlightDocs = null;
    loadGraph();
  });
  main.querySelector("#zoom-fit").addEventListener("click", fitGraph);
}

let graphRequest = 0;

async function loadGraph() {
  const req = ++graphRequest;
  let data;
  try {
    data = await api.get("/graph", {
      level: state.level,
      collection: state.collection,
      min_similarity: state.minSimilarity,
      k: state.k,
    });
  } catch {
    if (req === graphRequest) graphMessage("Não foi possível carregar o grafo.");
    return;
  }
  // resposta atrasada de um filtro anterior: descarta
  if (req !== graphRequest || !document.getElementById("graph-wrap")) return;
  fillCollectionFilter(data.collections);
  if (!data.nodes.length) {
    graphMessage("Nenhum documento indexado ainda. Ingeste documentos pela API/MCP para ver o grafo.");
    return;
  }
  drawGraph(data);
}

function destroyGraph() {
  if (state.graphObserver) state.graphObserver.disconnect();
  if (state.graphInstance) {
    state.graphInstance.pauseAnimation();
    state.graphInstance._destructor && state.graphInstance._destructor();
  }
  state.graphObserver = null;
  state.graphInstance = null;
}

function graphMessage(msg) {
  destroyGraph();
  const canvas = document.getElementById("graph-canvas");
  if (canvas) canvas.innerHTML = "";
  for (const id of ["graph-legend", "graph-hint", "graph-zoom"]) {
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
    `<option value="">Todas</option>` +
    collections.map((c) => `<option value="${c.name}" ${c.name === current ? "selected" : ""}>${c.name} (${c.count})</option>`).join("");
}

function hexAlpha(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function fitGraph() {
  const g = state.graphInstance;
  if (!g) return;
  g.zoomToFit(400, 80);
  // poucos nós: o zoomToFit aproxima demais; limita o zoom
  setTimeout(() => state.graphInstance === g && g.zoom() > 2.2 && g.zoom(2.2, 300), 420);
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
    .onNodeClick((n) => openDocumentPanel(state.level === "documents" ? n.id : n.document_id))
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

  const highlight = state.highlightDocs && state.highlightDocs.size ? state.highlightDocs : null;
  const clearBtn = document.getElementById("hl-clear");
  clearBtn.classList.toggle("hidden", !highlight);
  if (highlight) clearBtn.querySelector("span").textContent = `Limpar destaque (${highlight.size})`;

  const nodeKey = (n) => (state.level === "documents" ? n.id : n.document_id);
  const isHl = (n) => Boolean(highlight && highlight.has(nodeKey(n)));
  const nodes = data.nodes.map((n) => ({
    ...n,
    color: colorFor(n.collection),
    label: state.level === "documents" ? n.title : `${n.title} · #${n.chunk_index}`,
  }));

  document.getElementById("graph-sub").textContent =
    `${nodes.length} nós · ${data.edges.length} arestas · nível ${state.level === "documents" ? "documentos" : "chunks"}`;

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
      const dim = highlight && !isHl(n);
      const hovered = state.hoverNode === n;
      const color = highlight ? (isHl(n) ? "#d97757" : hexAlpha(n.color, 0.25)) : n.color;
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
      ctx.strokeStyle = "rgba(23, 22, 20, 0.9)";
      ctx.stroke();
      // rótulo: tamanho fixo na tela; some quando o zoom está muito longe (exceto no hover)
      if (scale < 0.6 && !hovered) return;
      const fontSize = 12 / scale;
      const text = n.label.length > 36 ? n.label.slice(0, 35) + "…" : n.label;
      ctx.font = `${hovered ? 600 : 500} ${fontSize}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = dim ? "rgba(155, 152, 147, 0.35)" : hovered ? "#ececec" : "#9b9893";
      ctx.fillText(text, n.x, n.y + r + 6 / scale);
    })
    .nodePointerAreaPaint((n, color, ctx) => {
      ctx.beginPath();
      ctx.arc(n.x, n.y, radius(n) + 4, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
    })
    .linkColor((l) =>
      highlight
        ? isHl(l.source) && isHl(l.target)
          ? "rgba(217, 119, 87, 0.5)"
          : "rgba(236, 236, 236, 0.04)"
        : `rgba(236, 236, 236, ${0.08 + Math.max(0, l.similarity - state.minSimilarity) * 0.8})`,
    )
    .linkWidth((l) => Math.max(0.6, (l.similarity - state.minSimilarity) * 10));

  state.graphNeedsFit = true;
  g.graphData({ nodes, links: data.edges.map((e) => ({ ...e })) });

  const legend = document.getElementById("graph-legend");
  legend.innerHTML = data.collections
    .map((c) => `<div class="row"><span class="dot" style="background:${colorFor(c.name)}"></span>${escHtml(c.name)}<span class="count">${c.count}</span></div>`)
    .join("");
  for (const id of ["graph-legend", "graph-hint", "graph-zoom"]) document.getElementById(id).classList.remove("hidden");
}

/* ---------------------------------------------------------------- painel do documento */

async function openDocumentPanel(docId) {
  const panel = document.getElementById("panel");
  panel.classList.remove("hidden");
  panel.innerHTML = `<div class="muted">Carregando…</div>`;
  let doc;
  try {
    doc = await api.get(`/documents/${docId}`);
  } catch {
    panel.innerHTML = `<div class="muted">Não foi possível carregar o documento.</div>`;
    return;
  }
  state.docPanelId = docId;
  panel.innerHTML = `
    <div class="panel-head">
      <h2>${escHtml(doc.title)}</h2>
      <button class="icon-btn" id="panel-close" title="Fechar">${icon("plus")}</button>
    </div>
    <div class="chip"><span class="dot" style="background:${colorFor(doc.collection)}"></span>${escHtml(doc.collection)}</div>
    <dl class="kv">
      <dt>Versão</dt><dd>v${doc.version} · ${doc.status === "active" ? "ativo" : "arquivado"}</dd>
      <dt>Criado por</dt><dd>${escHtml(doc.created_by ?? "—")}</dd>
      <dt>Atualizado</dt><dd>${fmtDate(doc.updated_at)}</dd>
      ${doc.source ? `<dt>Fonte</dt><dd>${escHtml(doc.source)}</dd>` : ""}
      ${doc.tags?.length ? `<dt>Tags</dt><dd>${doc.tags.map((t) => `<span class="chip">${escHtml(t)}</span>`).join(" ")}</dd>` : ""}
    </dl>
    <div class="section">
      <h3>${icon("file-text")} Conteúdo</h3>
      <div class="doc-content">${escHtml(doc.content)}</div>
    </div>
    <div class="section">
      <h3>${icon("folder")} Chunks (${doc.chunks.length})</h3>
      ${doc.chunks.map((c) => `<div class="chunk-item"><span class="idx">#${c.chunk_index}</span>${c.word_count} palavras<p>${escHtml(c.content.slice(0, 140))}${c.content.length > 140 ? "…" : ""}</p></div>`).join("")}
    </div>
    <div class="section">
      <h3>${icon("history")} Versões (${doc.versions.length})</h3>
      ${doc.versions.map((v) => `<div class="version-item"><span class="idx">v${v.version}</span>${escHtml(v.change_note ?? "")}<p>${escHtml(v.changed_by ?? "—")} · ${fmtDate(v.created_at)}</p></div>`).join("")}
    </div>`;
  await loadIcons(panel);
  // o ícone "plus" vira um X com rotação simples via estilo inline
  const close = panel.querySelector("#panel-close svg");
  if (close) close.style.transform = "rotate(45deg)";
  panel.querySelector("#panel-close").addEventListener("click", closePanel);
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
    btn.title = "Mostrar senha";
    btn.setAttribute("aria-label", "Mostrar senha");
    btn.innerHTML = icon("eye");
    wrap.appendChild(btn);
    loadIcons(btn);
    btn.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.title = show ? "Ocultar senha" : "Mostrar senha";
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
  renderShell();
  await renderScreen();
  await loadIcons(document);
})();
