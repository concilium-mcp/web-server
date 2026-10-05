/* Concilium Dashboard — SPA sem build. Fatia 1: grafo da base RAG (force-graph vendored). */

const api = {
  async get(path, params) {
    const qs = params ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "")) : "";
    const r = await fetch(`/dash/api${path}${qs}`);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
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

/* ---------------------------------------------------------------- estado */

const PALETTE = ["#e8b26a", "#7fb4ca", "#a9c181", "#d08770", "#b48ead", "#ebcb8b", "#88c0d0", "#a3be8c", "#d3869b", "#81a1c1"];

const state = {
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

/* ---------------------------------------------------------------- shell (sidebar + conteúdo) */

const NAV = [{ id: "graph", label: "Grafo da base", iconName: "network" }];

function renderShell() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="app">
      <aside class="sidebar">
        <div class="brand">${icon("network", 20)}<span>Concilium</span></div>
        <nav id="nav">
          ${NAV.map(
            (n) => `
            <button class="nav-item ${state.screen === n.id ? "active" : ""}" data-nav="${n.id}">
              ${icon(n.iconName)}<span>${n.label}</span>
            </button>`,
          ).join("")}
        </nav>
        <div class="nav-spacer"></div>
        <div id="user-footer"></div>
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
}

async function renderScreen() {
  const main = document.getElementById("main");
  if (state.screen === "graph") {
    main.innerHTML = graphTemplate();
    bindGraphControls();
    await loadIcons(main);
    await loadGraph();
  }
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
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  panel.innerHTML = `
    <div class="panel-head">
      <h2>${esc(doc.title)}</h2>
      <button class="icon-btn" id="panel-close" title="Fechar">${icon("plus")}</button>
    </div>
    <div class="chip"><span class="dot" style="background:${colorFor(doc.collection)}"></span>${esc(doc.collection)}</div>
    <dl class="kv">
      <dt>Versão</dt><dd>v${doc.version} · ${doc.status === "active" ? "ativo" : "arquivado"}</dd>
      <dt>Criado por</dt><dd>${esc(doc.created_by ?? "—")}</dd>
      <dt>Atualizado</dt><dd>${new Date(doc.updated_at).toLocaleString("pt-BR")}</dd>
      ${doc.source ? `<dt>Fonte</dt><dd>${esc(doc.source)}</dd>` : ""}
      ${doc.tags?.length ? `<dt>Tags</dt><dd>${doc.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join(" ")}</dd>` : ""}
    </dl>
    <div class="section">
      <h3>${icon("file-text")} Conteúdo</h3>
      <div class="doc-content">${esc(doc.content)}</div>
    </div>
    <div class="section">
      <h3>${icon("folder")} Chunks (${doc.chunks.length})</h3>
      ${doc.chunks.map((c) => `<div class="chunk-item"><span class="idx">#${c.chunk_index}</span>${c.word_count} palavras<p>${esc(c.content.slice(0, 140))}${c.content.length > 140 ? "…" : ""}</p></div>`).join("")}
    </div>
    <div class="section">
      <h3>${icon("history")} Versões (${doc.versions.length})</h3>
      ${doc.versions.map((v) => `<div class="version-item"><span class="idx">v${v.version}</span>${esc(v.change_note ?? "")}<p>${esc(v.changed_by ?? "—")} · ${new Date(v.created_at).toLocaleString("pt-BR")}</p></div>`).join("")}
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
  renderShell();
  document.getElementById("user-footer").innerHTML = `<span class="who muted">dashboard</span>`;
  await renderScreen();
  await loadIcons(document);
})();
