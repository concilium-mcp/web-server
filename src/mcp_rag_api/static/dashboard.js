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
  del(path) {
    return this.req("DELETE", path);
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
  screen: "notes",
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
          <h1>${icon("search", 18)} Busca RAG</h1>
          <span class="sub">Busca híbrida real da base (semântica + full-text)</span>
        </header>
        <div class="search-col">
          <form class="composer" id="search-form">
            ${icon("search", 18)}
            <input type="text" id="s-query" placeholder="Pergunte algo ou busque um termo…" autocomplete="off" required />
            <label class="composer-k tech" title="Quantos trechos trazer">Top-k
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
          <span class="chip subtle tech">chunk #${r.chunk_index}</span>
        </div>
        <p class="snippet">${highlightTerms(snippet, state.lastSearch)}</p>
        <div class="result-meta">
          <span class="relbar" title="Relevância relativa ao 1º resultado"><span style="width:${rel}%"></span></span>
          <span class="tech">${sim > 0 ? `semântica ${sim}%` : "só full-text"}</span>
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
  { id: "notes", label: "Notas", iconName: "notebook-pen" },
  { id: "painel", label: "Painel", iconName: "layout-dashboard" },
  { id: "search", label: "Busca", iconName: "search" },
  { id: "graph", label: "Grafo", iconName: "network" },
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
  const roleLabel = ROLE_LABELS[u.role] || "Leitor";
  const item = (attrs, iconName, label, extra = "") =>
    `<button class="menu-item" role="menuitem" ${attrs}>${icon(iconName)}<span class="grow">${label}</span>${extra}</button>`;
  footer.innerHTML = `
    <div class="user-menu hidden" id="user-menu" role="menu">
      <div class="menu-head">
        <span class="menu-user">${escHtml(u.username)}</span>
        <span class="muted">${roleLabel}</span>
      </div>
      <div class="menu-sep"></div>
      ${item('data-goto="agents"', "bot", "Agents")}
      ${item('data-goto="keys"', "key-round", "Chaves API")}
      ${isAdmin ? item('data-goto="users"', "users", "Gerenciar usuários") : ""}
      <div class="menu-sep"></div>
      ${item('data-href="/docs"', "book-open", "Documentação da API", icon("external-link", 14))}
      <div class="menu-item static">${icon("activity")}<span class="grow">Status do servidor</span><span class="status" id="server-status"><span class="status-dot"></span>…</span></div>
      <div class="menu-sep"></div>
      ${item('data-logout', "log-out", "Sair")}
    </div>
    <button class="user-trigger" id="user-trigger" aria-haspopup="menu" aria-expanded="false">
      <span class="avatar">${escHtml(u.username.slice(0, 1).toUpperCase())}</span>
      <span class="who" title="${escHtml(u.username)}">${escHtml(u.username)} <span class="muted">· ${{ admin: "admin", editor: "editor" }[u.role] || "leitor"}</span></span>
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
      goTo(el.dataset.goto);
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

// troca de tela: a de notas guarda o endereço da nota aberta (#/notas/<id>), as outras limpam
async function goTo(screen, openId = null) {
  if (notes.editor) await notesLeave();
  state.screen = screen;
  notes.openId = openId;
  if (screen !== "notes" && location.hash) history.replaceState(null, "", location.pathname);
  renderShell();
  await renderScreen();
}

window.addEventListener("hashchange", () => {
  const m = location.hash.match(/^#\/notas\/([0-9a-f-]{36})$/);
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
  } else if (state.screen === "painel") {
    await renderPainel(main);
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
      <div class="data-table">
        ${KEYS_HEAD}
        ${active.map((k) => keyRow(k, isAdmin)).join("") || `<div class="results-empty">Nenhuma chave ativa.${isAdmin ? " Crie uma em “Nova chave”." : ""}</div>`}
      </div>
      ${
        revoked.length
          ? `<details class="revoked-block" ${state.showRevoked ? "open" : ""}>
              <summary>${icon("history", 14)} ${revoked.length} revogada(s) — mantidas para auditoria</summary>
              <div class="data-table">${KEYS_HEAD}${revoked.map((k) => keyRow(k, isAdmin)).join("")}</div>
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
  ["viewer", "Leitor", "Lê notas, busca, grafo, chaves e agents, sem alterar nada"],
  ["editor", "Editor", "Cria, edita, move e arquiva notas e pastas"],
  ["admin", "Administrador", "Tudo do editor + chaves, usuários, links e autonomia dos agents"],
];
const ROLE_LABELS = Object.fromEntries(ROLES.map(([value, label]) => [value, label]));

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
      <div class="data-table">${USERS_HEAD}${users.map(userRow).join("")}</div>
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

/* ---------------------------------------------------------------- tela: notas (plan-web-02) */

// Modelos v1 estáticos: não poluem a busca RAG com documentos-modelo.
const NOTE_TEMPLATES = [
  { id: "blank", label: "Em branco", desc: "Comece do zero", content: "" },
  {
    id: "client",
    label: "Perfil de cliente",
    desc: "Quem é, contexto, contatos e histórico",
    content:
      "## Resumo\n\nQuem é o cliente, segmento e porte.\n\n## Contatos\n\n- Nome — cargo — e-mail/telefone\n\n" +
      "## Contexto e dores\n\n- \n\n## O que já oferecemos\n\n- \n\n## Próximos passos\n\n- [ ] Próximo passo\n",
  },
  {
    id: "meeting",
    label: "Ata de reunião",
    desc: "Participantes, decisões e próximos passos",
    content:
      "**Data:** \n**Participantes:** \n\n## Pauta\n\n- \n\n## Decisões\n\n- \n\n" +
      "## Próximos passos\n\n- [ ] Responsável — tarefa — prazo\n",
  },
  {
    id: "proposal",
    label: "Proposta",
    desc: "Problema, solução, escopo e investimento",
    content:
      "## Problema\n\n\n## Solução proposta\n\n\n## Escopo\n\n- Inclui:\n- Não inclui:\n\n" +
      "## Investimento e prazos\n\n| Item | Valor | Prazo |\n| --- | --- | --- |\n|  |  |  |\n\n## Próximos passos\n\n- [ ] Próximo passo\n",
  },
  {
    id: "objections",
    label: "Playbook de objeções",
    desc: "Objeção, resposta e prova",
    content:
      "## Objeção: \n\n**Quando aparece:** \n\n**Resposta curta:** \n\n**Prova / caso:** \n\n" +
      "---\n\n## Objeção: \n\n**Quando aparece:** \n\n**Resposta curta:** \n\n**Prova / caso:** \n",
  },
];

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
          <div class="field notes-filter">${icon("search")}<input type="text" id="notes-filter" placeholder="Buscar nota…" autocomplete="off" /></div>
          ${
            canEdit()
              ? `<div class="notes-tree-actions">
                  <button class="btn sm primary" id="note-new" title="Nova nota (Alt+N)">${icon("plus", 14)}<span>Nota</span></button>
                  <button class="btn sm ghost" id="folder-new" title="Nova pasta">${icon("folder", 14)}<span>Pasta</span></button>
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
                  <span class="grow">${escHtml(n.title)}</span>${notes.current?.id === n.id && notes.dirty ? `<span class="dirty-dot" title="Alterações não salvas"></span>` : ""}</button>`,
              )
              .join("") || `<div class="tree-empty">Pasta vazia</div>`
          }
        </div>
      </div>`;
      })
      .join("") || `<div class="tree-empty">Nada encontrado para “${escHtml(notes.filter)}”.</div>`;
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
      <div class="results-empty-title">${canEdit() ? "Crie sua primeira nota" : "Nenhuma nota ainda"}</div>
      ${
        canEdit()
          ? `Tudo o que o time escreve aqui vira, na hora, base de conhecimento para os agentes.
             <div class="template-pick">${NOTE_TEMPLATES.map((t) => `<button class="btn" type="button" data-template="${t.id}">${escHtml(t.label)}</button>`).join("")}</div>`
          : "Peça a um editor ou admin para criar as primeiras notas."
      }
    </div>`;
  main.querySelectorAll("[data-template]").forEach((b) => b.addEventListener("click", () => openNewNoteModal(b.dataset.template)));
}

/* ---------- abrir / editar / salvar */

async function openNote(id) {
  if (notes.current?.id === id) return;
  if (notes.current && notes.dirty && !(await saveNote())) {
    const leave = await uiConfirm({
      title: "Não foi possível salvar",
      message: "Suas alterações continuam guardadas como rascunho neste navegador. Trocar de nota mesmo assim?",
      confirmLabel: "Trocar mesmo assim",
    });
    if (!leave) return;
  }
  let note;
  try {
    note = await api.get(`/notes/${id}`);
  } catch (err) {
    await uiError("Não foi possível abrir a nota", err);
    return;
  }
  notes.current = note;
  notes.tags = [...(note.tags || [])];
  notes.dirty = false;
  store.set("concilium:last-note", id);
  if (location.hash !== `#/notas/${id}`) history.replaceState(null, "", `#/notas/${id}`);
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
      <input class="note-title" id="note-title" value="${escHtml(n.title)}" placeholder="Sem título" ${editable ? "" : "readonly"} />
      <div class="note-actions">
        <span class="save-state" id="save-state"></span>
        <button class="btn sm ghost ${notes.side === "related" ? "active" : ""}" type="button" data-side="related">${icon("link", 14)}<span>Conexões</span></button>
        <button class="btn sm ghost ${notes.side === "history" ? "active" : ""}" type="button" data-side="history">${icon("history", 14)}<span>Histórico</span></button>
        ${
          editable
            ? `<button class="icon-btn" type="button" id="note-move" title="Mover para outra pasta">${icon("folder-input", 16)}</button>
               <button class="icon-btn danger" type="button" id="note-archive" title="Arquivar nota">${icon("archive", 16)}</button>
               <button class="btn sm primary" type="button" id="note-save" title="Salvar (Ctrl+S)">${icon("save", 14)}<span>Salvar</span></button>`
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
  const common = { el, initialValue: n.content, theme: "dark", usageStatistics: false };
  notes.editor = editable
    ? new toastui.Editor({
        ...common,
        height: "100%",
        initialEditType: "wysiwyg",
        previewStyle: "vertical",
        language: "pt-BR",
        placeholder: "Escreva aqui. Cite outra nota com [[Título]] para ligá-las.",
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
  if (box) box.textContent = `v${n.version} · atualizada ${fmtAgo(n.updated_at)}${n.updated_by ? ` por ${n.updated_by.replace(/^dash:/, "")}` : ""}`;
}

function renderTags() {
  const box = document.getElementById("note-tags");
  if (!box) return;
  const editable = canEdit();
  box.innerHTML =
    notes.tags
      .map((t, i) => `<span class="chip tag-chip">#${escHtml(t)}${editable ? `<button type="button" data-tag-del="${i}" title="Remover tag">×</button>` : ""}</span>`)
      .join("") + (editable ? `<input type="text" id="tag-add" placeholder="${notes.tags.length ? "+ tag" : "Adicionar tag…"}" />` : "");
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
    saved: `Salvo · v${notes.current?.version}`,
    dirty: "Alterações não salvas",
    saving: "Salvando…",
    error: "Erro ao salvar",
  };
  box.className = `save-state ${kind}`;
  box.textContent = canEdit() ? labels[kind] : "Somente leitura";
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
        title: "Esta nota mudou enquanto você editava",
        message: `${err.detail.message} Salvar a sua versão por cima, ou descartar suas alterações e carregar a versão nova?`,
        confirmLabel: "Salvar a minha por cima",
        cancelLabel: "Carregar a versão nova",
        tone: "danger",
      });
      if (overwrite) return saveNote(err.detail.current_version);
      store.del(DRAFT_PREFIX + n.id);
      notes.current = null;
      await openNote(n.id);
      return true;
    }
    await uiError("Não foi possível salvar a nota", err);
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
    title: "Recuperar rascunho não salvo?",
    message:
      `Há alterações desta nota guardadas neste navegador (${fmtAgo(draft.at)}) que não foram salvas.` +
      (stale ? ` Atenção: a nota foi salva por outra pessoa depois disso (agora está na v${n.version}).` : ""),
    confirmLabel: "Recuperar rascunho",
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
    await uiAlert("Crie uma pasta primeiro", "As notas ficam dentro de pastas (coleções). Use “Pasta” para criar a primeira.");
    return;
  }
  document.getElementById("note-new-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "note-new",
    iconName: "notebook-pen",
    title: "Nova nota",
    subtitle: "Ela vira conhecimento dos agentes assim que for criada.",
    submitLabel: "Criar nota",
    width: 640,
    body: `
      <div class="create-grid">
        <label class="form-field">
          <span class="form-label">Título</span>
          <input class="input" id="nn-title" placeholder="ex.: Cliente Acme — perfil" required />
        </label>
        <label class="form-field">
          <span class="form-label">Pasta</span>
          <select class="input" id="nn-collection">${collectionOptions(notes.current?.collection || notes.tree.collections[0].name)}</select>
        </label>
      </div>
      <div class="form-field">
        <span class="form-label">Modelo</span>
        <div class="scope-grid two">
          ${NOTE_TEMPLATES.map(
            (t) => `
            <label class="scope-option">
              <input type="radio" name="nn-template" value="${t.id}" ${t.id === templateId ? "checked" : ""} />
              <span><span>${escHtml(t.label)}</span><span class="muted">${escHtml(t.desc)}</span></span>
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
    const template = NOTE_TEMPLATES.find((t) => t.id === modal.querySelector("input[name=nn-template]:checked").value);
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
          title: "Já existe uma nota parecida",
          message: `“${sim.title}” tem conteúdo muito parecido (${Math.round(sim.similarity * 100)}%). Criar mesmo assim?`,
          confirmLabel: "Criar mesmo assim",
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
      await uiError("Não foi possível criar a nota", err);
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
    title: "Nova pasta",
    subtitle: "Pastas organizam as notas e definem o que cada agente pode acessar.",
    submitLabel: "Criar pasta",
    width: 520,
    body: `
      <label class="form-field">
        <span class="form-label">Nome</span>
        <input class="input" id="nf-name" placeholder="ex.: comercial, clientes, produto" required />
      </label>
      <label class="form-field">
        <span class="form-label">Descrição <span class="muted">(opcional)</span></span>
        <input class="input" id="nf-desc" placeholder="O que entra nesta pasta" />
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
      await uiError("Não foi possível criar a pasta", err);
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
    title: "Mover nota",
    subtitle: `“${escHtml(n.title)}” sai de <strong>${escHtml(n.collection)}</strong>.`,
    submitLabel: "Mover",
    submitIcon: "folder-input",
    width: 480,
    body: `
      <label class="form-field">
        <span class="form-label">Para a pasta</span>
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
      await uiError("Não foi possível mover a nota", err);
    }
  });
  setOpen(true);
}

async function archiveCurrentNote() {
  const n = notes.current;
  const ok = await uiConfirm({
    title: "Arquivar nota?",
    message: `“${n.title}” some da lista, da busca e dos agentes. O histórico é mantido e um admin pode recuperá-la.`,
    confirmLabel: "Arquivar",
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
    history.replaceState(null, "", "#/notas");
    await loadNotesTree();
    if (notes.tree.notes.length) await openNote(notes.tree.notes[0].id);
    else renderNotesEmpty();
  } catch (err) {
    await uiError("Não foi possível arquivar", err);
  }
}

/* ---------- painel lateral: conexões e histórico */

async function renderNoteSide(kind) {
  const side = document.getElementById("note-side");
  if (!side) return;
  side.classList.toggle("hidden", !kind);
  if (!kind || !notes.current) return;
  const n = notes.current;
  side.innerHTML = `<div class="muted">Carregando…</div>`;
  if (kind === "related") {
    let rel;
    try {
      rel = await api.get(`/notes/${n.id}/related`);
    } catch {
      side.innerHTML = `<div class="muted">Não foi possível carregar as conexões.</div>`;
      return;
    }
    const item = (d, extra = "") =>
      `<button class="side-item" type="button" data-open-note="${d.document_id}"><span class="dot" style="background:${colorFor(d.collection)}"></span><span class="grow">${escHtml(d.title)}</span>${extra}</button>`;
    side.innerHTML = `
      <div class="side-head"><h3>${icon("link", 15)} Conexões</h3>
        ${state.user?.role === "admin" ? `<button class="btn sm ghost" type="button" id="side-connect">${icon("plus", 13)}<span>Conectar a…</span></button>` : ""}
      </div>
      <div class="links-label">Links (${rel.links.length})</div>
      ${
        rel.links
          .map((l) => (l.pending ? `<div class="side-item pending">${escHtml(l.target_title)} <span class="muted">· ainda não existe</span></div>` : item(l)))
          .join("") || `<div class="muted links-empty">Escreva [[Título]] no texto para ligar outra nota.</div>`
      }
      <div class="links-label">Backlinks (${rel.backlinks.length})</div>
      ${rel.backlinks.map((b) => item(b)).join("") || `<div class="muted links-empty">Nenhuma nota aponta para esta.</div>`}
      <div class="links-label">Parecidas (${rel.semantic.length})</div>
      ${rel.semantic.map((s) => item(s, `<span class="muted">${Math.round(s.similarity * 100)}%</span>`)).join("") || `<div class="muted links-empty">Nenhuma ainda.</div>`}`;
    side.querySelector("#side-connect")?.addEventListener("click", () =>
      openConnectModal({ document_id: n.id, title: n.title }, null, () => renderNoteSide("related")),
    );
  } else {
    let versions;
    try {
      versions = await api.get(`/notes/${n.id}/versions`);
    } catch {
      side.innerHTML = `<div class="muted">Não foi possível carregar o histórico.</div>`;
      return;
    }
    side.innerHTML = `
      <div class="side-head"><h3>${icon("history", 15)} Histórico</h3></div>
      ${versions
        .map(
          (v) => `
        <button class="side-version ${v.version === n.version ? "current" : ""}" type="button" data-version="${v.version}">
          <span class="idx">v${v.version}</span>
          <span class="grow">${escHtml(v.change_note || "")}<span class="muted">${escHtml((v.changed_by || "—").replace(/^dash:/, ""))} · ${fmtAgo(v.created_at)}</span></span>
          ${v.version === n.version ? `<span class="chip subtle">atual</span>` : ""}
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
    await uiError("Não foi possível abrir a versão", err);
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
    submitLabel: isCurrent ? "Fechar" : "Restaurar esta versão",
    submitIcon: isCurrent ? "check" : "refresh-cw",
    width: 820,
    body: `<div class="version-viewer" id="version-viewer"></div>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("version-modal");
  await loadIcons(modal);
  const viewer = toastui.Editor.factory({ el: modal.querySelector("#version-viewer"), viewer: true, initialValue: v.content, theme: "dark", usageStatistics: false });
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
        title: "Descartar alterações não salvas?",
        message: "Restaurar substitui o conteúdo atual pela v" + version + ". Suas alterações não salvas serão perdidas.",
        confirmLabel: "Restaurar mesmo assim",
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
      await uiError("Não foi possível restaurar", err);
    }
  });
  setOpen(true);
}

/* ---------------------------------------------------------------- tela: painel (indicadores e gráficos) */

// Gráficos em SVG feito à mão (sem lib): uma série por gráfico, um tom só (--viz-accent, validado
// contra a superfície escura), barras <= 24px com ponta arredondada de 4px, grade hairline sólida,
// tooltip por marca e alternância "Tabela" em todo gráfico (o tooltip nunca é o único caminho).

const PERIODS = [7, 30, 90];
const nf = new Intl.NumberFormat("pt-BR");
const nfCompact = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 });
const fmtDay = (iso, opts = { day: "numeric", month: "short" }) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("pt-BR", opts).replace(".", "");
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

  for (const t of ticks) {
    const y = pad.top + plotH - (t / top) * plotH;
    svg.append(svgEl("line", { x1: pad.left, x2: W - pad.right, y1: y, y2: y, class: t === 0 ? "viz-axis" : "viz-gridline" }));
    const label = svgEl("text", { x: pad.left - 8, y: y + 4, class: "viz-tick", "text-anchor": "end" });
    label.textContent = nf.format(t);
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
      <div class="meter-foot muted">${nf.format(value)} de ${nf.format(total)} ${escHtml(hint)}</div>
    </div>`;
}

/* ---------- tela */

function chartCard(id, title, subtitle, { wide = false } = {}) {
  return `
    <section class="viz-card ${wide ? "wide" : ""}" id="${id}">
      <header class="viz-card-head">
        <div class="grow"><h2>${title}</h2>${subtitle ? `<span class="muted">${subtitle}</span>` : ""}</div>
        <button class="btn sm ghost" type="button" data-table-toggle="${id}" title="Ver os dados em tabela">${icon("table-2", 13)}<span>Tabela</span></button>
      </header>
      <div class="viz-body"></div>
      <div class="viz-table hidden"></div>
    </section>`;
}

function dataTable(head, rows) {
  return `<table class="dt"><thead><tr>${head.map((h, i) => `<th class="${i ? "num" : ""}">${escHtml(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i ? "num" : ""}">${escHtml(String(c))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function painelTemplate() {
  const days = state.insightsDays;
  return `
    <section class="page painel-page">
      <header class="page-header">
        <h1>${icon("layout-dashboard", 18)} Painel</h1>
        <span class="sub">Como a base de conhecimento está crescendo e sendo usada</span>
      </header>
      <div class="painel-col" id="painel">
        <div class="painel-filters">
          <span class="filter-label">${icon("calendar", 14)}Período</span>
          <div class="period-seg" id="period-seg" role="radiogroup" aria-label="Período">
            ${PERIODS.map((d) => `<button type="button" role="radio" aria-checked="${d === days}" data-days="${d}" class="${d === days ? "active" : ""}">${d} dias</button>`).join("")}
          </div>
          <span class="grow"></span>
          <span class="muted painel-updated" id="painel-updated"></span>
          <button class="icon-btn painel-refresh" type="button" id="painel-refresh" title="Atualizar agora">${icon("refresh-cw", 15)}</button>
        </div>
        <div class="kpis" id="kpis"></div>
        <div class="viz-grid">
          ${chartCard("viz-activity", "Atividade", `Edições por dia nos últimos ${days} dias`, { wide: true })}
          ${chartCard("viz-collections", "Notas por pasta", "Notas ativas em cada pasta")}
          ${chartCard("viz-contributors", "Quem mais edita", `Edições nos últimos ${days} dias`)}
          <section class="viz-card" id="viz-health">
            <header class="viz-card-head"><div class="grow"><h2>Saúde da base</h2><span class="muted">O que deixa as notas mais úteis para o time e os agentes</span></div></header>
            <div class="viz-body"></div>
          </section>
          <section class="viz-card" id="viz-recent">
            <header class="viz-card-head"><div class="grow"><h2>Atualizadas recentemente</h2><span class="muted">Clique para abrir a nota</span></div></header>
            <div class="viz-body"></div>
          </section>
        </div>
      </div>
    </section>`;
}

async function renderPainel(main) {
  main.innerHTML = painelTemplate();
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
  main.querySelector("#painel-refresh").addEventListener("click", () => loadInsights());
  main.querySelector("#painel").addEventListener("click", (e) => {
    const t = e.target.closest("[data-table-toggle]");
    if (t) {
      const card = document.getElementById(t.dataset.tableToggle);
      const showTable = card.querySelector(".viz-table").classList.toggle("hidden") === false;
      card.querySelector(".viz-body").classList.toggle("hidden", showTable);
      t.classList.toggle("active", showTable);
      t.querySelector("span").textContent = showTable ? "Gráfico" : "Tabela";
      return;
    }
    const open = e.target.closest("[data-open-note]");
    if (open) goTo("notes", open.dataset.openNote);
  });
  await loadInsights();
}

async function loadInsights() {
  const root = document.getElementById("painel");
  if (!root) return;
  root.classList.add("loading"); // recarga mantém o quadro anterior, só esmaecido
  const spinStart = performance.now();
  // o ícone completa ao menos a volta em andamento (0,7 s), mesmo com resposta instantânea
  const stopSpin = () =>
    setTimeout(() => document.getElementById("painel-refresh")?.classList.remove("spinning"), 700 - ((performance.now() - spinStart) % 700));
  document.getElementById("painel-refresh")?.classList.add("spinning");
  let data;
  try {
    data = await api.get("/insights", { days: state.insightsDays });
  } catch (err) {
    root.classList.remove("loading");
    stopSpin();
    await uiError("Não foi possível carregar o painel", err);
    return;
  }
  if (!document.getElementById("painel")) return;
  state.insights = data;
  root.classList.remove("loading");
  stopSpin();
  document.getElementById("painel-updated").textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
  root.querySelector("#viz-activity .muted").textContent = `Edições por dia nos últimos ${data.days} dias`;
  root.querySelector("#viz-contributors .muted").textContent = `Edições nos últimos ${data.days} dias`;
  renderKpis(data);
  renderPainelCharts();
  renderHealth(data);
  renderRecent(data);
}

function renderKpis(d) {
  const t = d.totals;
  const edits = d.activity.reduce((s, p) => s + p.edits, 0);
  const delta = d.created.current - d.created.previous;
  const novas = d.created.current;
  const notasSub = novas ? `+${nf.format(novas)} em ${d.days} dias` : `nenhuma nova em ${d.days} dias`;
  const notasTip = `${nf.format(novas)} nos últimos ${d.days} dias · ${nf.format(d.created.previous)} nos ${d.days} dias anteriores`;
  const tiles = [
    { label: "Notas", iconName: "notebook-pen", value: t.documents, sub: notasSub, subTip: notasTip, trend: delta > 0 ? "up" : delta < 0 ? "down" : "" },
    { label: `Edições · ${d.days} dias`, iconName: "activity", value: edits, spark: d.activity.map((p) => p.edits) },
    { label: "Pastas", iconName: "folder", value: t.collections },
    { label: "Conexões", iconName: "link", value: t.links, sub: t.pending_links ? `${nf.format(t.pending_links)} pendente(s)` : "links entre notas" },
    { label: "Agents", iconName: "bot", value: t.agents, sub: `${nf.format(t.memories)} memória(s)` },
    { label: "Trechos", iconName: "file-text", value: t.chunks, sub: "indexados para busca", tech: true },
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
function renderPainelCharts() {
  const d = state.insights;
  if (!d || !document.getElementById("painel")) return;
  const activity = d.activity.map((p) => ({
    label: fmtDay(p.day),
    value: p.edits,
    tip: `${fmtDay(p.day, { weekday: "short", day: "numeric", month: "short" })}${p.created ? ` · ${p.created} nota(s) nova(s)` : ""}`,
  }));
  const card = (id) => document.querySelector(`#${id}`);
  drawColumns(card("viz-activity").querySelector(".viz-body"), activity, { valueName: (v) => (v === 1 ? "edição" : "edições") });
  card("viz-activity").querySelector(".viz-table").innerHTML = dataTable(
    ["Dia", "Edições", "Notas novas"],
    d.activity.map((p) => [fmtDay(p.day, { day: "2-digit", month: "2-digit", year: "numeric" }), p.edits, p.created]),
  );

  drawBars(
    card("viz-collections").querySelector(".viz-body"),
    d.by_collection.map((c) => ({ label: c.name, value: c.documents, dot: colorFor(c.name) })),
    { valueName: (v) => (v === 1 ? "nota" : "notas"), empty: "Nenhuma pasta ainda." },
  );
  card("viz-collections").querySelector(".viz-table").innerHTML = dataTable(
    ["Pasta", "Notas"],
    d.by_collection.map((c) => [c.name, c.documents]),
  );

  drawBars(
    card("viz-contributors").querySelector(".viz-body"),
    d.contributors.map((c) => ({ label: actorName(c.actor), value: c.edits })),
    { valueName: (v) => (v === 1 ? "edição" : "edições"), empty: `Ninguém editou nos últimos ${d.days} dias.` },
  );
  card("viz-contributors").querySelector(".viz-table").innerHTML = dataTable(
    ["Quem", "Edições"],
    d.contributors.map((c) => [actorName(c.actor), c.edits]),
  );
}

function renderHealth(d) {
  const h = d.health;
  const t = d.totals;
  document.querySelector("#viz-health .viz-body").innerHTML = `
    <div class="meters">
      ${meter("Notas com tags", h.with_tags, h.documents, "notas têm ao menos uma tag")}
      ${meter("Notas conectadas", h.with_links, h.documents, "notas têm link ou backlink")}
      ${meter("Notas atualizadas nos últimos 90 dias", h.documents - h.stale, h.documents, "notas foram editadas há menos de 90 dias")}
    </div>
    <div class="health-facts">
      <span>${icon("archive", 13)} ${nf.format(t.archived)} arquivada(s)</span>
      <span>${icon("link", 13)} ${nf.format(t.pending_links)} link(s) pendente(s)</span>
      <span>${icon("users", 13)} ${nf.format(t.users)} pessoa(s) com acesso</span>
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
      .join("") || `<div class="viz-empty">Nenhuma nota ainda.</div>`;
}

const onPainelResize = debounce(() => state.screen === "painel" && renderPainelCharts(), 150);
window.addEventListener("resize", onPainelResize);

/* ---------------------------------------------------------------- tela: grafo */


function graphTemplate() {
  return `
    <div class="split">
      <section class="page">
        <header class="page-header">
          <h1>${icon("network", 18)} Grafo da base</h1>
          <span class="sub" id="graph-sub"></span>
        </header>
        <div class="toolbar">
          <div class="field">${icon("folder")}<label for="f-collection">Collection</label><select id="f-collection"><option value="">Todas</option></select></div>
          <div class="field">${icon("sliders-horizontal")}<label for="f-sim">Similaridade ≥ <span id="f-sim-val" class="mono">${state.minSimilarity.toFixed(2)}</span></label>
            <input type="range" id="f-sim" min="0" max="0.95" step="0.05" value="${state.minSimilarity}" />
          </div>
          <div class="field tech"><label for="f-k">Top-k</label>
            <select id="f-k">${[1, 2, 3, 5, 8, 12, 20].map((k) => `<option ${k === state.k ? "selected" : ""}>${k}</option>`).join("")}</select>
          </div>
          <div class="seg tech" id="f-level">
            <button data-level="documents" class="${state.level === "documents" ? "active" : ""}">Documentos</button>
            <button data-level="chunks" class="${state.level === "chunks" ? "active" : ""}">Chunks</button>
          </div>
          <button class="btn sm hidden" id="hl-clear">${icon("check")}<span>Limpar destaque</span></button>
          ${
            state.user?.role === "admin"
              ? `<button class="btn sm ghost" id="connect-mode" title="Clique na origem e depois no destino para ligar dois documentos">${icon("link", 14)}<span>Conectar</span></button>`
              : ""
          }
        </div>
        <div class="graph-wrap" id="graph-wrap">
          <!-- o force-graph limpa o elemento em que é montado: overlays ficam fora de #graph-canvas -->
          <div class="graph-canvas" id="graph-canvas"></div>
          <div class="empty-state" id="graph-empty">Carregando grafo…</div>
          <div class="legend hidden" id="graph-legend"></div>
          <div class="graph-notice hidden" id="graph-notice" role="status"></div>
          <div class="graph-hint hidden" id="graph-hint">Clique num nó para ver o documento · arraste para mover · role para zoom</div>
          <div class="graph-zoom hidden" id="graph-zoom">
            <button class="icon-btn" id="zoom-fit" title="Sincronizar e enquadrar">${icon("refresh-cw")}</button>
          </div>
        </div>
      </section>
      <aside class="panel hidden" id="panel"></aside>
    </div>`;
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
    <span>Nenhuma similaridade ≥ ${fmt(state.minSimilarity)}. A mais forte é <strong>${fmt(strongest)}</strong>.</span>
    <button class="btn sm" type="button" id="notice-lower">Mostrar a partir de ${fmt(target)}</button>`;
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
    `<option value="">Todas</option>` +
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

  const linkEdges = data.edges.filter((e) => e.kind === "link");
  const semanticEdges = data.edges.filter((e) => e.kind === "semantic");
  const visibleEdges = state.showSemantic ? data.edges : linkEdges;
  document.getElementById("graph-sub").textContent =
    `${nodes.length} nós · ${linkEdges.length} link(s) · ${semanticEdges.length} por similaridade · ` +
    `nível ${state.level === "documents" ? "documentos" : "chunks"}`;

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
    // link explícito = sólido coral; similaridade = tracejado cinza
    .linkColor((l) => {
      if (highlight && !(isHl(l.source) && isHl(l.target))) return "rgba(236, 236, 236, 0.04)";
      if (l.kind === "link") return highlight ? "rgba(217, 119, 87, 0.95)" : "rgba(217, 119, 87, 0.85)";
      if (highlight) return "rgba(217, 119, 87, 0.5)";
      return `rgba(236, 236, 236, ${Math.min(0.7, 0.28 + Math.max(0, l.similarity - state.minSimilarity) * 1.5)})`;
    })
    // espessura mínima visível mesmo para arestas logo acima do threshold
    .linkWidth((l) => (l.kind === "link" ? 2.2 : Math.min(4, 1.2 + Math.max(0, l.similarity - state.minSimilarity) * 8)))
    .linkLineDash((l) => (l.kind === "link" ? null : [4, 3]))
    .linkLabel((l) => {
      const sim = l.similarity != null ? `similaridade ${Number(l.similarity).toFixed(2)}` : "";
      if (l.kind !== "link") return sim;
      const how = l.link_kinds?.includes("wikilink") ? "[[wikilink]]" : "link manual";
      return [`Link explícito (${how})`, l.note ? `“${escHtml(l.note)}”` : "", sim].filter(Boolean).join("<br>");
    });
  // documentos ligados explicitamente ficam mais perto que os só parecidos
  g.d3Force("link")
    .distance((l) => (l.kind === "link" ? 45 : 90))
    .strength((l) => (l.kind === "link" ? 0.7 : 0.25));

  state.graphNeedsFit = true;
  g.graphData({ nodes, links: visibleEdges.map((e) => ({ ...e })) });

  const legend = document.getElementById("graph-legend");
  const unit = state.level === "documents" ? "doc" : "trecho";
  const plural = (n) => `${n} ${unit}${n === 1 ? "" : "s"}`;
  legend.innerHTML = `
    <div class="legend-head">Coleções</div>
    ${data.collections
      .map(
        (c) => `
      <button class="legend-row ${state.collection === c.name ? "active" : ""}" type="button" data-col="${escHtml(c.name)}"
        title="${state.collection === c.name ? "Coleção filtrada" : "Clique para ver só esta coleção"}">
        <span class="dot" style="background:${colorFor(c.name)}"></span>
        <span class="legend-name">${escHtml(c.name)}</span>
        <span class="count">${plural(c.count)}</span>
      </button>`,
      )
      .join("")}
    ${state.collection ? `<button class="legend-all" type="button" data-col="">${icon("x", 12)}<span>Ver todas as coleções</span></button>` : ""}
    ${
      state.level === "documents"
        ? `<div class="legend-head legend-sep">Conexões</div>
      <div class="legend-edge"><span class="edge-swatch link"></span><span class="legend-name">Link explícito</span>
        <span class="count">${linkEdges.length}</span></div>
      <button class="legend-edge toggle ${state.showSemantic ? "" : "off"}" type="button" data-toggle-semantic
        title="${state.showSemantic ? "Ocultar" : "Mostrar"} as arestas de similaridade">
        <span class="edge-swatch semantic"></span><span class="legend-name">Similaridade</span>
        <span class="count">${state.showSemantic ? semanticEdges.length : "oculta"}</span></button>`
        : ""
    }`;
  loadIcons(legend);
  for (const id of ["graph-legend", "graph-hint", "graph-zoom"]) document.getElementById(id).classList.remove("hidden");
}

/* ---------------------------------------------------------------- painel do documento */

async function openDocumentPanel(docId) {
  const panel = document.getElementById("panel");
  panel.classList.remove("hidden");
  panel.innerHTML = `<div class="muted">Carregando…</div>`;
  let doc, docLinks;
  try {
    [doc, docLinks] = await Promise.all([
      api.get(`/documents/${docId}`),
      api.get(`/documents/${docId}/links`).catch(() => ({ links: [], backlinks: [] })),
    ]);
  } catch {
    panel.innerHTML = `<div class="muted">Não foi possível carregar o documento.</div>`;
    return;
  }
  state.docPanelId = docId;
  panel.innerHTML = `
    <div class="panel-head">
      <h2>${escHtml(doc.title)}</h2>
      <button class="icon-btn" id="panel-close" title="Fechar">${icon("x", 16)}</button>
    </div>
    <div class="chip"><span class="dot" style="background:${colorFor(doc.collection)}"></span>${escHtml(doc.collection)}</div>
    <dl class="kv">
      <dt>Versão</dt><dd>v${doc.version} · ${doc.status === "active" ? "ativo" : "arquivado"}</dd>
      <dt>Criado por</dt><dd>${escHtml(doc.created_by ?? "—")}</dd>
      <dt>Atualizado</dt><dd>${fmtDate(doc.updated_at)}</dd>
      ${doc.source ? `<dt>Fonte</dt><dd>${escHtml(doc.source)}</dd>` : ""}
      ${doc.tags?.length ? `<dt>Tags</dt><dd>${doc.tags.map((t) => `<span class="chip">${escHtml(t)}</span>`).join(" ")}</dd>` : ""}
    </dl>
    ${linksSection(doc, docLinks)}
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
        title: "Remover link?",
        message: `A ligação manual com “${del.dataset.title}” será removida. Os documentos continuam iguais.`,
        confirmLabel: "Remover",
        tone: "danger",
      });
      if (!ok) return;
      try {
        await api.del(`/links/${del.dataset.unlink}`);
        openDocumentPanel(docId);
        if (document.getElementById("graph-wrap")) loadGraph();
      } catch (err) {
        await uiError("Não foi possível remover o link", err);
      }
    }
  });
}

/* ---------------------------------------------------------------- links explícitos (plan-web-03) */

function linksSection(doc, data) {
  const isAdmin = state.user?.role === "admin";
  const kindChip = (kind) => `<span class="chip subtle">${kind === "wikilink" ? "[[wikilink]]" : "manual"}</span>`;
  const outgoing = data.links || [];
  const backlinks = data.backlinks || [];
  const outItem = (l) =>
    l.pending
      ? `<div class="link-item pending" title="Nenhum documento com esse título ainda: o link se conecta sozinho quando ele for criado">
           ${icon("link", 13)}<span class="grow">${escHtml(l.target_title)} <span class="muted">· ainda não existe</span></span>${kindChip(l.kind)}
         </div>`
      : `<div class="link-item">
           ${icon("link", 13)}
           <button class="link-title grow" type="button" data-open-doc="${l.document_id}">${escHtml(l.title)}
             ${l.note ? `<span class="muted link-note">“${escHtml(l.note)}”</span>` : ""}</button>
           ${kindChip(l.kind)}
           ${
             isAdmin && l.kind === "manual"
               ? `<button class="icon-btn danger" type="button" data-unlink="${l.id}" data-title="${escHtml(l.title)}" title="Remover link manual">${icon("x", 13)}</button>`
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
      <h3>${icon("link")} Conexões
        ${isAdmin && doc.status === "active" ? `<button class="btn sm ghost connect-btn" type="button" id="connect-open">${icon("plus", 13)}<span>Conectar a…</span></button>` : ""}
      </h3>
      <div class="links-group">
        <div class="links-label">Links (${outgoing.length})</div>
        ${outgoing.map(outItem).join("") || `<div class="muted links-empty">Nenhum. Escreva [[Título]] no texto ou use “Conectar a…”.</div>`}
      </div>
      <div class="links-group">
        <div class="links-label">Backlinks (${backlinks.length})</div>
        ${backlinks.map(backItem).join("") || `<div class="muted links-empty">Nenhum documento aponta para este.</div>`}
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
    title: "Conectar documentos",
    subtitle: `Origem: <strong>${escHtml(source.title)}</strong> — o destino ganha um backlink.`,
    submitLabel: "Conectar",
    submitIcon: "link",
    width: 560,
    body: `
      <label class="form-field">
        <span class="form-label">Destino</span>
        <input class="input" type="text" id="connect-q" placeholder="Busque pelo título…" autocomplete="off" />
      </label>
      <div class="connect-results" id="connect-results" role="listbox"></div>
      <label class="form-field">
        <span class="form-label">Por que estão ligados? <span class="muted">(opcional)</span></span>
        <input class="input" type="text" id="connect-note" placeholder="ex.: o pitch usa os números desta análise" />
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
        .join("") || `<div class="muted links-empty">Nenhum documento encontrado.</div>`;
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
      await uiAlert("Escolha o destino", "Selecione na lista o documento que será ligado.");
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
      await uiError("Não foi possível conectar", err);
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
  connectNotice(on ? "Modo conectar: clique no documento de <strong>origem</strong>. Esc cancela." : null);
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
    connectNotice(`Origem: <strong>${escHtml(n.title)}</strong>. Agora clique no <strong>destino</strong>. Esc cancela.`);
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
  const deep = location.hash.match(/^#\/notas\/([0-9a-f-]{36})$/);
  if (deep) notes.openId = deep[1];
  renderShell();
  await renderScreen();
  await loadIcons(document);
})();
