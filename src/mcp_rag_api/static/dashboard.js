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
  docPanelId: null,
};

function colorFor(collection) {
  if (!state.colors.has(collection)) {
    state.colors.set(collection, PALETTE[state.colors.size % PALETTE.length]);
  }
  return state.colors.get(collection);
}

/* ---------------------------------------------------------------- login */

function renderLogin() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="login-wrap">
      <form class="login-card" id="login-form">
        <div class="brand">${icon("network", 22)}<span>Concilium</span></div>
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
        <div class="brand">${icon("network", 20)}<span>Concilium</span></div>
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
  renderUserFooter();
}

function renderUserFooter() {
  const footer = document.getElementById("user-footer");
  if (!footer || !state.user) return;
  footer.innerHTML = `
    ${icon("circle-user-round", 18)}
    <span class="who" title="${state.user.username}">${state.user.username}${state.user.role === "admin" ? ' <span class="chip accent">admin</span>' : ""}</span>
    <button class="icon-btn" id="btn-logout" title="Sair">${icon("log-out")}</button>`;
  loadIcons(footer);
  footer.querySelector("#btn-logout").addEventListener("click", async () => {
    await api.post("/auth/logout").catch(() => {});
    state.user = null;
    renderLogin();
  });
}

async function renderScreen() {
  const main = document.getElementById("main");
  closePanel();
  if (state.screen === "graph") {
    main.innerHTML = graphTemplate();
    bindGraphControls();
    await loadIcons(main);
    await loadGraph();
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

const SCOPES = ["read", "write", "agents:manage", "admin"];

function keysCreateForm(agents) {
  return `
    <form class="form-grid" id="key-form">
      <div class="field">${icon("key-round")}<input type="text" id="key-label" placeholder="Label da chave" required /></div>
      <div class="field" id="key-scopes">
        ${SCOPES.map((s) => `<label class="check"><input type="checkbox" value="${s}" ${s === "read" ? "checked" : ""}/> ${s}</label>`).join("")}
      </div>
      <div class="field">${icon("bot")}
        <select id="key-agent"><option value="">Chave humana</option>
          ${agents.map((a) => `<option value="${a.slug}">agent:${a.slug}</option>`).join("")}
        </select>
      </div>
      <button class="btn primary" type="submit">${icon("plus")}<span>Criar chave</span></button>
    </form>`;
}

function keyRow(k, isAdmin) {
  const revoked = Boolean(k.revoked_at);
  return `
    <div class="card ${revoked ? "revoked" : ""}">
      <div class="card-row">
        ${icon("key-round", 18)}
        <div class="grow">
          <div class="card-title">${escHtml(k.label ?? "—")}
            ${k.agent ? `<span class="chip">${icon("bot")} ${escHtml(k.agent)}</span>` : `<span class="chip">humana</span>`}
            ${revoked ? `<span class="chip danger-chip">revogada</span>` : `<span class="chip accent">ativa</span>`}
          </div>
          <div class="muted mono">${escHtml(k.prefix)}…</div>
          <div class="muted">${(k.scopes || []).map((s) => `<span class="chip">${s}</span>`).join(" ")}
            · criada ${fmtDate(k.created_at)} · último uso ${fmtDate(k.last_used_at)}</div>
        </div>
        ${
          isAdmin && !revoked
            ? `<div class="row-actions">
            <button class="icon-btn" data-renew="${k.id}" title="Renovar: revoga e emite uma chave nova">${icon("refresh-cw")}</button>
            <button class="icon-btn danger" data-revoke="${k.id}" title="Revogar chave">${icon("ban")}</button>
          </div>`
            : ""
        }
      </div>
    </div>`;
}

async function renderKeysList() {
  const body = document.getElementById("page-body");
  const isAdmin = state.user.role === "admin";
  let keys, agents;
  try {
    [keys, agents] = await Promise.all([api.get("/keys"), api.get("/agents")]);
  } catch {
    body.innerHTML = `<div class="empty-state">Não foi possível carregar as chaves.</div>`;
    return;
  }
  const active = keys.filter((k) => !k.revoked_at);
  const revoked = keys.filter((k) => k.revoked_at);
  body.innerHTML = `
    ${isAdmin ? keysCreateForm(agents) : ""}
    <div id="key-banner"></div>
    <div class="list">
      ${active.map((k) => keyRow(k, isAdmin)).join("") || `<div class="empty-state">Nenhuma chave ativa.</div>`}
      ${
        revoked.length
          ? `<div class="list-sep muted">${revoked.length} revogada(s) — exibidas para auditoria</div>` +
            revoked.map((k) => keyRow(k, isAdmin)).join("")
          : ""
      }
    </div>`;
  await loadIcons(body);
  if (isAdmin) bindKeysCreate();
  bindKeyActions();
}

function bindKeysCreate() {
  document.getElementById("key-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const scopes = [...document.querySelectorAll("#key-scopes input:checked")].map((i) => i.value);
    try {
      const key = await api.post("/keys", {
        label: document.getElementById("key-label").value,
        scopes,
        agent_slug: document.getElementById("key-agent").value || null,
      });
      await renderKeysList();
      showKeyBanner(key);
    } catch (err) {
      alert(`Não foi possível criar a chave: ${err.detail || err.message}`);
    }
  });
}

function bindKeyActions() {
  const body = document.getElementById("page-body");
  body.addEventListener("click", async (e) => {
    const renew = e.target.closest("[data-renew]");
    const revoke = e.target.closest("[data-revoke]");
    try {
      if (renew && confirm("Renovar a chave? A atual será revogada de imediato.")) {
        const key = await api.post(`/keys/${renew.dataset.renew}/renew`);
        await renderKeysList();
        showKeyBanner(key);
      } else if (revoke && confirm("Revogar a chave? Agentes/integrações que usam ela perdem o acesso.")) {
        await api.post(`/keys/${revoke.dataset.revoke}/revoke`);
        await renderKeysList();
      }
    } catch (err) {
      alert(`Falhou: ${err.detail || err.message}`);
    }
  });
}

function showKeyBanner(key) {
  const box = document.getElementById("key-banner");
  box.innerHTML = `
    <div class="banner">
      <span class="grow">Chave pronta. Guarde agora — ela não será exibida de novo:</span>
      <code class="mono">${escHtml(key.api_key)}</code>
      <button class="btn sm" id="copy-key" type="button">${icon("copy")}<span>Copiar</span></button>
    </div>`;
  loadIcons(box);
  box.querySelector("#copy-key").addEventListener("click", async (e) => {
    await navigator.clipboard.writeText(key.api_key);
    e.currentTarget.innerHTML = `${icon("check")}<span>Copiado</span>`;
    loadIcons(e.currentTarget);
  });
}

/* ---------------------------------------------------------------- tela: agents */

function agentCard(a, isAdmin) {
  return `
    <div class="card">
      <div class="card-row">
        ${icon("bot", 18)}
        <div class="grow">
          <div class="card-title">${escHtml(a.name)} <span class="muted mono">${escHtml(a.slug)}</span>
            ${a.status !== "active" ? `<span class="chip danger-chip">arquivado</span>` : ""}
            ${
              a.proposals
                ? `<span class="chip accent">${icon("git-pull-request-arrow")} ${a.proposals} proposta(s) pendente(s)</span>`
                : ""
            }
          </div>
          <div class="muted">${escHtml(a.description ?? "")}</div>
          <div class="muted">${a.scopes.map((s) => `<span class="chip">${s}</span>`).join(" ")}
            · ${a.allowed_collections.length ? a.allowed_collections.map((c) => `<span class="chip">${icon("folder")} ${escHtml(c)}</span>`).join(" ") : "todas as coleções"}
            · ${a.active_keys} chave(s) ativa(s) · v${a.version} · ${a.auto_apply_updates ? "autônomo" : "sob revisão"}</div>
        </div>
        ${
          isAdmin && a.status === "active"
            ? `<button class="btn sm ${a.auto_apply_updates ? "primary" : ""}" data-autonomy="${a.slug}" data-on="${a.auto_apply_updates}">
              ${icon("zap")}<span>Autonomia ${a.auto_apply_updates ? "ligada" : "desligada"}</span>
            </button>`
            : ""
        }
      </div>
    </div>`;
}

async function renderAgentsList() {
  const body = document.getElementById("page-body");
  const isAdmin = state.user.role === "admin";
  let agents;
  try {
    agents = await api.get("/agents");
  } catch {
    body.innerHTML = `<div class="empty-state">Não foi possível carregar os agents.</div>`;
    return;
  }
  body.innerHTML = `<div class="list">${agents.map((a) => agentCard(a, isAdmin)).join("") || `<div class="empty-state">Nenhum agente cadastrado.</div>`}</div>`;
  await loadIcons(body);
  body.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-autonomy]");
    if (!btn) return;
    const turnOn = btn.dataset.on !== "true";
    if (!confirm(`${turnOn ? "Ligar" : "Desligar"} a autonomia de '${btn.dataset.autonomy}'? Com autonomia, propostas do próprio agente se aplicam sem revisão humana.`)) return;
    try {
      await api.post(`/agents/${btn.dataset.autonomy}/autonomy`, { auto_apply_updates: turnOn });
      await renderAgentsList();
    } catch (err) {
      alert(`Falhou: ${err.detail || err.message}`);
    }
  });
}

/* ---------------------------------------------------------------- tela: usuários */

function userRow(u) {
  const disabled = Boolean(u.disabled_at);
  return `
    <div class="card ${disabled ? "revoked" : ""}" data-user-card="${u.id}">
      <div class="card-row">
        ${icon("circle-user-round", 18)}
        <div class="grow">
          <div class="card-title">${escHtml(u.username)}
            ${disabled ? `<span class="chip danger-chip">desativado</span>` : `<span class="chip accent">ativo</span>`}
            <span class="chip">${u.role}</span>
          </div>
          <div class="muted">criado ${fmtDate(u.created_at)}</div>
        </div>
        <div class="row-actions">
          <select class="input role-select" data-role="${u.id}">
            <option value="viewer" ${u.role === "viewer" ? "selected" : ""}>viewer</option>
            <option value="admin" ${u.role === "admin" ? "selected" : ""}>admin</option>
          </select>
          <button class="btn sm" data-reset="${u.id}">${icon("refresh-cw")}<span>Redefinir senha</span></button>
          <button class="btn sm ${disabled ? "" : "danger"}" data-toggle="${u.id}" data-disabled="${disabled}">
            ${disabled ? "Reativar" : "Desativar"}
          </button>
        </div>
      </div>
      <div class="reset-row hidden" id="reset-${u.id}">
        <input class="input" type="password" placeholder="Nova senha (mín. 8 caracteres)" id="reset-pass-${u.id}" />
        <button class="btn sm primary" data-do-reset="${u.id}">${icon("check")}<span>Salvar senha</span></button>
      </div>
    </div>`;
}

async function renderUsersList() {
  const body = document.getElementById("page-body");
  let users;
  try {
    users = await api.get("/users");
  } catch (err) {
    body.innerHTML = `<div class="empty-state">${err.status === 403 ? "Só administradores gerenciam usuários." : "Não foi possível carregar os usuários."}</div>`;
    return;
  }
  body.innerHTML = `
    <form class="form-grid" id="user-form">
      <div class="field">${icon("circle-user-round")}<input type="text" id="u-username" placeholder="Username" required /></div>
      <div class="field">${icon("key-round")}<input type="password" id="u-password" placeholder="Senha (mín. 8)" required /></div>
      <div class="field"><select id="u-role"><option value="viewer">viewer</option><option value="admin">admin</option></select></div>
      <button class="btn primary" type="submit">${icon("plus")}<span>Criar usuário</span></button>
    </form>
    <div class="list">${users.map(userRow).join("")}</div>`;
  await loadIcons(body);
  bindUserActions();
}

function bindUserActions() {
  const body = document.getElementById("page-body");
  document.getElementById("user-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.post("/users", {
        username: document.getElementById("u-username").value,
        password: document.getElementById("u-password").value,
        role: document.getElementById("u-role").value,
      });
      await renderUsersList();
    } catch (err) {
      alert(`Não foi possível criar o usuário: ${err.detail || err.message}`);
    }
  });
  body.addEventListener("change", async (e) => {
    const sel = e.target.closest("[data-role]");
    if (!sel) return;
    try {
      await api.patch(`/users/${sel.dataset.role}`, { role: sel.value });
      await renderUsersList();
    } catch (err) {
      alert(`Falhou: ${err.detail || err.message}`);
      await renderUsersList();
    }
  });
  body.addEventListener("click", async (e) => {
    const reset = e.target.closest("[data-reset]");
    const doReset = e.target.closest("[data-do-reset]");
    const toggle = e.target.closest("[data-toggle]");
    try {
      if (reset) {
        document.getElementById(`reset-${reset.dataset.reset}`).classList.toggle("hidden");
      } else if (doReset) {
        const pass = document.getElementById(`reset-pass-${doReset.dataset.doReset}`).value;
        await api.patch(`/users/${doReset.dataset.doReset}`, { password: pass });
        await renderUsersList();
      } else if (toggle) {
        const disable = toggle.dataset.disabled !== "true";
        if (disable && !confirm("Desativar o usuário? As sessões dele serão encerradas.")) return;
        await api.patch(`/users/${toggle.dataset.toggle}`, { disabled: disable });
        await renderUsersList();
      }
    } catch (err) {
      alert(`Falhou: ${err.detail || err.message}`);
    }
  });
}

/* ---------------------------------------------------------------- tela: grafo */

function graphTemplate() {
  return `
    <section class="page">
      <header class="page-header">
        <h1>${icon("network", 18)} Grafo da base</h1>
        <span class="sub" id="graph-sub"></span>
      </header>
      <div class="toolbar">
        <div class="field">${icon("folder")}<label>Collection</label><select id="f-collection"><option value="">Todas</option></select></div>
        <div class="field">${icon("sliders-horizontal")}<label>Similaridade ≥ <span id="f-sim-val">0.70</span></label>
          <input type="range" id="f-sim" min="0" max="0.95" step="0.05" value="${state.minSimilarity}" />
        </div>
        <div class="field"><label>Top-k</label>
          <select id="f-k">${[1, 2, 3, 5, 8, 12, 20].map((k) => `<option ${k === state.k ? "selected" : ""}>${k}</option>`).join("")}</select>
        </div>
        <div class="seg" id="f-level">
          <button data-level="documents" class="${state.level === "documents" ? "active" : ""}">Documentos</button>
          <button data-level="chunks" class="${state.level === "chunks" ? "active" : ""}">Chunks</button>
        </div>
      </div>
      <div class="graph-wrap" id="graph-wrap">
        <div class="empty-state" id="graph-empty">Carregando grafo…</div>
      </div>
    </section>
    <aside class="panel hidden" id="panel"></aside>`;
}

function bindGraphControls() {
  const main = document.getElementById("main");
  main.querySelector("#f-collection").addEventListener("change", (e) => {
    state.collection = e.target.value;
    loadGraph();
  });
  main.querySelector("#f-sim").addEventListener("input", (e) => {
    state.minSimilarity = Number(e.target.value);
    main.querySelector("#f-sim-val").textContent = state.minSimilarity.toFixed(2);
    loadGraph();
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
}

async function loadGraph() {
  const wrap = document.getElementById("graph-wrap");
  const empty = document.getElementById("graph-empty");
  try {
    const data = await api.get("/graph", {
      level: state.level,
      collection: state.collection,
      min_similarity: state.minSimilarity,
      k: state.k,
    });
    fillCollectionFilter(data.collections);
    if (!data.nodes.length) {
      emptyState("Nenhum documento indexado ainda. Ingeste documentos pela API/MCP para ver o grafo.");
      return;
    }
    empty.classList.add("hidden");
    drawGraph(data);
  } catch {
    emptyState("Não foi possível carregar o grafo.");
  }
}

function emptyState(msg) {
  const empty = document.getElementById("graph-empty");
  if (state.graphInstance) {
    state.graphInstance._destructor && state.graphInstance._destructor();
    state.graphInstance = null;
  }
  document.getElementById("graph-wrap").querySelectorAll("canvas").forEach((c) => c.remove());
  empty.textContent = msg;
  empty.classList.remove("hidden");
}

function fillCollectionFilter(collections) {
  const sel = document.getElementById("f-collection");
  const current = state.collection;
  sel.innerHTML =
    `<option value="">Todas</option>` +
    collections.map((c) => `<option value="${c.name}" ${c.name === current ? "selected" : ""}>${c.name} (${c.count})</option>`).join("");
}

function drawGraph(data) {
  const wrap = document.getElementById("graph-wrap");
  wrap.querySelectorAll("canvas").forEach((c) => c.remove());
  wrap.querySelectorAll(".legend,.graph-hint").forEach((el) => el.remove());

  const nodes = data.nodes.map((n) => ({
    ...n,
    color: colorFor(n.collection),
    label: state.level === "documents" ? n.title : `${n.title} · #${n.chunk_index}`,
  }));

  document.getElementById("graph-sub").textContent =
    `${nodes.length} nós · ${data.edges.length} arestas · nível ${state.level === "documents" ? "documentos" : "chunks"}`;

  state.graphInstance = ForceGraph()(wrap)
    .backgroundColor("transparent")
    .graphData({ nodes, links: data.edges.map((e) => ({ ...e })) })
    .nodeId("id")
    .nodeVal((n) => (state.level === "documents" ? Math.max(1, Math.sqrt(n.chunks)) : 1))
    .nodeLabel((n) => n.label)
    .nodeColor((n) => n.color)
    .nodeCanvasObjectMode(() => "after")
    .nodeCanvasObject((n, ctx) => {
      ctx.fillStyle = "rgba(23, 22, 20, 0.9)";
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(n.label.length > 32 ? n.label.slice(0, 31) + "…" : n.label, n.x, n.y + 14);
    })
    .linkColor(() => "rgba(236, 236, 236, 0.18)")
    .linkWidth((l) => Math.max(0.5, (l.similarity - state.minSimilarity) * 12))
    .linkDirectionalParticles(0)
    .onNodeClick((n) => openDocumentPanel(state.level === "documents" ? n.id : n.document_id))
    .onBackgroundClick(() => closePanel())
    .width(wrap.clientWidth)
    .height(wrap.clientHeight);

  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML = data.collections
    .map((c) => `<div class="row"><span class="dot" style="background:${colorFor(c.name)}"></span>${c.name} · ${c.count}</div>`)
    .join("");
  wrap.appendChild(legend);

  const hint = document.createElement("div");
  hint.className = "graph-hint";
  hint.textContent = "Clique num nó para ver o documento";
  wrap.appendChild(hint);
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
}

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
