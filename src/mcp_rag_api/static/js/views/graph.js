/* Tela do grafo — force-graph (2D) e 3d-force-graph (3D) da base RAG: filtros, legenda, destaques e modo conectar. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { graphTheme } from "../theme.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, debounce, scrollArrows } from "../ui.js";
import { state, colorFor, groupColor, store } from "../state.js";
import { on } from "../events.js";
import { closePanel, openConnectModal, openDocumentPanel } from "./docpanel.js";
import { loaderHtml } from "../loader.js";
import { createLocationPicker } from "../location-picker.js";

/* ---------------------------------------------------------------- preferências do grafo (por navegador) */

const MODE_KEY = "concilium:graph-mode";
const LINK_SCALE_KEY = "concilium:graph-link-scale";
// posições do layout 2D por nível ({ id: [x, y] }): reabrir o grafo parte do layout pronto
const POSITIONS_KEY = "concilium:graph-positions";
const POSITIONS_MAX = 20000;
const LEGEND_KEY = "concilium:graph-legend-collapsed";
const LINK_SCALE_MIN = 0.3;
const LINK_SCALE_MAX = 3;

function loadGraphPrefs() {
  const mode = store.get(MODE_KEY);
  if (mode === "2d" || mode === "3d") state.graphMode = mode;
  const scale = Number(store.get(LINK_SCALE_KEY));
  if (scale >= LINK_SCALE_MIN && scale <= LINK_SCALE_MAX) state.linkScale = scale;
  // legenda recolhida: o que a pessoa escolheu; sem escolha, começa recolhida no celular (cobre o grafo)
  const collapsed = store.get(LEGEND_KEY);
  state.legendCollapsed = typeof collapsed === "boolean" ? collapsed : matchMedia("(max-width: 760px)").matches;
}

const fmtScale = (v) => `${v.toFixed(1)}×`;

// Salva onde cada nó parou (2D). Junta com o que já havia (filtros mostram partes da base);
// acima do teto recomeça só com os nós atuais, para não crescer sem limite no localStorage.
function savePositions(nodes) {
  const key = `${POSITIONS_KEY}:${state.level}`;
  let saved = store.get(key) || {};
  if (Object.keys(saved).length + nodes.length > POSITIONS_MAX) saved = {};
  for (const n of nodes) if (Number.isFinite(n.x) && Number.isFinite(n.y)) saved[n.id] = [Math.round(n.x), Math.round(n.y)];
  store.set(key, saved);
}

/* ---------------------------------------------------------------- tela: grafo */


function graphTemplate() {
  loadGraphPrefs();
  return `
    <div class="split">
      <section class="page">
        <header class="page-header">
          <h1>${icon("network", 18)} ${t("graph.title")}</h1>
          <span class="sub" id="graph-sub"></span>
        </header>
        <div class="strip-wrap">
          <div class="toolbar graph-toolbar" id="graph-toolbar">
            <div id="f-location"></div>
            <div class="seg hidden" id="f-view" role="radiogroup" aria-label="${t("graph.view")}">
              <button data-view="groups" role="radio" aria-checked="false" title="${t("graph.viewGroupsTitle")}">${t("graph.viewGroups")}</button>
              <button data-view="all" role="radio" aria-checked="false">${t("graph.viewAll")}</button>
            </div>
            <div class="field">${icon("sliders-horizontal")}<label for="f-sim">${t("graph.similarity")} <span id="f-sim-val" class="mono">${state.minSimilarity.toFixed(2)}</span></label>
              <input type="range" id="f-sim" min="0" max="0.95" step="0.05" value="${state.minSimilarity}" />
            </div>
            <div class="field">${icon("spline")}<label for="f-width">${t("graph.lineWidth")} <span id="f-width-val" class="mono">${fmtScale(state.linkScale)}</span></label>
              <input type="range" id="f-width" min="${LINK_SCALE_MIN}" max="${LINK_SCALE_MAX}" step="0.1" value="${state.linkScale}" />
            </div>
            <div class="field tech"><label for="f-k">Top-k</label>
              <select id="f-k">${[1, 2, 3, 5, 8, 12, 20].map((k) => `<option ${k === state.k ? "selected" : ""}>${k}</option>`).join("")}</select>
            </div>
            <div class="seg tech" id="f-level">
              <button data-level="documents" class="${state.level === "documents" ? "active" : ""}">${t("graph.documents")}</button>
              <button data-level="chunks" class="${state.level === "chunks" ? "active" : ""}">${t("graph.chunks")}</button>
            </div>
            <div class="seg" id="f-mode" role="radiogroup" aria-label="${t("graph.view")}">
              <button data-mode="2d" role="radio" aria-checked="${state.graphMode === "2d"}" class="${state.graphMode === "2d" ? "active" : ""}">2D</button>
              <button data-mode="3d" role="radio" aria-checked="${state.graphMode === "3d"}" class="${state.graphMode === "3d" ? "active" : ""}" title="${t("graph.mode3dTitle")}">${icon("box", 14)}<span>3D</span></button>
            </div>
            <button class="btn sm hidden" id="hl-clear">${icon("check")}<span>${t("graph.clearHighlight")}</span></button>
            ${
              state.user?.role === "admin"
                ? `<button class="btn sm ghost" id="connect-mode" title="${t("graph.connectTitle")}">${icon("link", 14)}<span>${t("graph.connect")}</span></button>`
                : ""
            }
          </div>
        </div>
        <div class="graph-wrap" id="graph-wrap">
          <!-- o force-graph limpa o elemento em que é montado: overlays ficam fora de #graph-canvas -->
          <div class="graph-canvas" id="graph-canvas"></div>
          <div class="empty-state" id="graph-empty">${loaderHtml(t("graph.loading"))}</div>
          <div class="legend hidden" id="graph-legend"></div>
          <div class="graph-notice hidden" id="graph-notice" role="status"></div>
          <div class="graph-crumb hidden" id="graph-crumb"></div>
          <div class="graph-hint hidden" id="graph-hint">${state.graphMode === "3d" ? t("graph.hint3d") : t("graph.hint")}</div>
          <div class="graph-zoom hidden" id="graph-zoom">
            <button class="icon-btn" id="zoom-fit" title="${t("graph.sync")}">${icon("refresh-cw")}</button>
          </div>
        </div>
      </section>
      <aside class="panel hidden" id="panel"></aside>
    </div>`;
}


function bindGraphControls() {
  const main = document.getElementById("main");
  const reloadSoon = debounce(loadGraph, 250);
  scrollArrows(main.querySelector("#graph-toolbar"));
  // local: coleção ou pasta (com subpastas) — com uma coleção só, o recorte útil são as pastas
  state.graphPicker = createLocationPicker(main.querySelector("#f-location"), {
    value: { collection: state.collection, folderId: state.folderId },
    onChange: ({ collection, folderId }) => {
      state.collection = collection;
      state.folderId = folderId;
      state.graphGroup = null;
      loadGraph();
    },
  });
  main.querySelector("#f-sim").addEventListener("input", (e) => {
    state.minSimilarity = Number(e.target.value);
    main.querySelector("#f-sim-val").textContent = state.minSimilarity.toFixed(2);
    reloadSoon();
  });
  // espessura: só restiliza (não recarrega do servidor); no 3D refaz as geometrias, então com debounce curto
  const restyleSoon = debounce(refreshGraphStyles, state.graphMode === "3d" ? 120 : 0);
  main.querySelector("#f-width").addEventListener("input", (e) => {
    state.linkScale = Number(e.target.value);
    main.querySelector("#f-width-val").textContent = fmtScale(state.linkScale);
    store.set(LINK_SCALE_KEY, state.linkScale);
    restyleSoon();
  });
  main.querySelector("#f-k").addEventListener("change", (e) => {
    state.k = Number(e.target.value);
    loadGraph();
  });
  // base grande: grupos por assunto (padrão) ou todas as notas
  main.querySelector("#f-view").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-view]");
    if (!btn) return;
    state.graphView = btn.dataset.view;
    state.graphGroup = null;
    loadGraph();
  });
  main.querySelector("#graph-crumb").addEventListener("click", (e) => {
    if (!e.target.closest("[data-back-groups]")) return;
    state.graphGroup = null;
    state.graphView = "groups";
    loadGraph();
  });
  main.querySelector("#f-level").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-level]");
    if (!btn) return;
    state.level = btn.dataset.level;
    main.querySelectorAll("#f-level button").forEach((b) => b.classList.toggle("active", b === btn));
    loadGraph();
  });
  main.querySelector("#f-mode").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mode]");
    if (!btn || btn.dataset.mode === state.graphMode) return;
    state.graphMode = btn.dataset.mode;
    store.set(MODE_KEY, state.graphMode);
    main.querySelectorAll("#f-mode button").forEach((b) => {
      b.classList.toggle("active", b === btn);
      b.setAttribute("aria-checked", String(b === btn));
    });
    main.querySelector("#graph-hint").textContent = state.graphMode === "3d" ? t("graph.hint3d") : t("graph.hint");
    // 2D e 3D são instâncias diferentes: troca o renderizador e redesenha
    destroyGraph();
    document.getElementById("graph-canvas").innerHTML = "";
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
    refreshGraphStyles();
  };
  legend.addEventListener("mouseover", (e) => setLegendHover(e.target.closest(".legend-row")?.dataset.col || null));
  legend.addEventListener("mouseleave", () => setLegendHover(null));
  legend.addEventListener("click", (e) => {
    if (e.target.closest("[data-legend-toggle]")) {
      state.legendCollapsed = !state.legendCollapsed;
      store.set(LEGEND_KEY, state.legendCollapsed);
      applyLegendCollapsed(legend);
      return;
    }
    if (e.target.closest("[data-toggle-semantic]")) {
      state.showSemantic = !state.showSemantic;
      loadGraph();
      return;
    }
    const btn = e.target.closest("[data-col]");
    if (!btn) return;
    setLegendHover(null);
    state.collection = btn.dataset.col;
    state.folderId = null; // a legenda filtra por coleção inteira
    state.graphPicker?.set({ collection: state.collection, folderId: null });
    loadGraph();
  });
}

function applyLegendCollapsed(legend) {
  const collapsed = Boolean(state.legendCollapsed);
  legend.classList.toggle("collapsed", collapsed);
  const toggle = legend.querySelector("[data-legend-toggle]");
  if (!toggle) return;
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.title = collapsed ? t("legend.expand") : t("legend.collapse");
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
    probe = await api.get("/graph", { level: state.level, collection: state.collection, folder_id: state.folderId || "", min_similarity: 0, k: 1 });
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
    const [resp] = await Promise.all([
      api.get("/graph", {
        level: state.level,
        collection: state.collection,
        folder_id: state.folderId || "",
        min_similarity: state.minSimilarity,
        k: state.k,
        // destaque da busca precisa das notas, não dos grupos
        view: state.highlightDocs?.size ? "all" : state.graphView,
        group: state.graphGroup ? state.graphGroup.id : "",
      }),
      state.graphMode === "3d" ? load3dLibrary() : null,
    ]);
    data = resp;
  } catch {
    if (req === graphRequest) graphMessage(state.graphMode === "3d" && !window.ForceGraph3D ? t("graph.mode3dFailed") : t("graph.loadFailed"));
    return;
  }
  // resposta atrasada de um filtro anterior: descarta
  if (req !== graphRequest || !document.getElementById("graph-wrap")) return;
  if (!data.nodes.length) {
    graphMessage(t("graph.empty"));
    return;
  }
  if (data.view === "group") state.graphGroup = data.group;
  drawGraph(data);
  if (data.view !== "groups") explainMissingEdges(data, req);
}

// O 3D (three.js embutido, ~1,4 MB) só é baixado quando a pessoa escolhe 3D. O caminho é derivado
// do <script> do force-graph para herdar o prefixo versionado dos estáticos (cache imutável).
let load3dPromise = null;
function load3dLibrary() {
  if (window.ForceGraph3D) return Promise.resolve();
  if (!load3dPromise) {
    load3dPromise = new Promise((resolve, reject) => {
      const base = document.querySelector('script[src*="force-graph.min.js"]')?.getAttribute("src") || "static/vendor/force-graph.min.js";
      const s = document.createElement("script");
      s.src = base.replace(/force-graph\.min\.js$/, "3d-force-graph/3d-force-graph.min.js");
      s.onload = () => resolve();
      s.onerror = () => {
        load3dPromise = null; // permite tentar de novo
        s.remove();
        reject(new Error("3d-force-graph"));
      };
      document.head.appendChild(s);
    });
  }
  return load3dPromise;
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
  state.graphInstanceMode = null;
  state.hoverNode = null;
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
  if (state.graphInstanceMode === "3d") {
    g.zoomToFit(600, 10);
    setTimeout(() => setGraphSyncing(false), 600);
    return;
  }
  // calcula o enquadramento instantâneo, volta à vista atual e anima até o alvo
  // com o zoom limitado (com poucos nós o zoomToFit aproxima demais)
  const fromZoom = g.zoom();
  const fromCenter = g.centerAt();
  g.zoomToFit(0, 80);
  const toZoom = Math.min(g.zoom(), MAX_FIT_ZOOM);
  if (view) view.fitZoom = toZoom;
  const toCenter = g.centerAt();
  g.zoom(fromZoom, 0).centerAt(fromCenter.x, fromCenter.y, 0);
  g.centerAt(toCenter.x, toCenter.y, 400).zoom(toZoom, 400);
  setTimeout(() => setGraphSyncing(false), 400);
}

/* ---------------------------------------------------------------- estilo (compartilhado pelo 2D e pelo 3D) */

// Calculado a cada drawGraph: vizinhança (para o foco do hover), grau (tamanho e prioridade do rótulo)
// e as funções de cor/espessura que os dois renderizadores usam.
let view = null;

// Grafo grande (ex.: base inteira com milhares de notas): o force-graph agrupa as arestas por cor×espessura
// a cada quadro e repinta tudo num canvas oculto para detectar hover. Acima destes limites as arestas
// ficam retas, sem hover/tooltip próprio e com cor/espessura em poucos degraus.
const BIG_NODES = 1500;
const BIG_EDGES = 4000;
// grafo grande visto de longe: só as arestas de similaridade mais fortes (+ todos os links explícitos);
// a partir de LOD_ZOOM × o zoom de enquadramento entram todas (o recorte da tela já reduz a conta)
const LOD_EDGES = 3000;
const LOD_ZOOM = 2.5;
const STRENGTH_STEPS = 5;
const NO_PAINT = () => {};

function buildView(data) {
  const colors = graphTheme();
  const highlight = state.highlightDocs && state.highlightDocs.size ? state.highlightDocs : null;
  const nodeKey = (n) => (state.level === "documents" ? n.id : n.document_id);
  const isHl = (n) => Boolean(highlight && highlight.has(nodeKey(n)));
  const visibleEdges = state.showSemantic ? data.edges : data.edges.filter((e) => e.kind === "link");
  const big = data.nodes.length > BIG_NODES || visibleEdges.length > BIG_EDGES;
  // visão de grupos: um nó por assunto (tamanho = nº de notas), arestas = quantas ligações entre grupos
  const groups = data.view === "groups";
  // posições vindas do servidor (plan-web-26): sem física no navegador
  const serverLayout = data.nodes.length > 0 && data.nodes.every((n) => Number.isFinite(n.x) && Number.isFinite(n.y));
  const maxSize = groups ? Math.max(1, ...data.nodes.map((n) => n.size || 1)) : 1;
  const maxWeight = groups ? Math.max(1, ...visibleEdges.map((e) => e.weight || 1)) : 1;

  const degree = new Map();
  const neighbors = new Map();
  for (const e of visibleEdges) {
    for (const [a, b] of [
      [e.source, e.target],
      [e.target, e.source],
    ]) {
      degree.set(a, (degree.get(a) || 0) + 1);
      if (!neighbors.has(a)) neighbors.set(a, new Set());
      neighbors.get(a).add(b);
    }
  }
  const idOf = (x) => (x && typeof x === "object" ? x.id : x);

  // força da similaridade relativa ao threshold atual: 0 (no limite) .. 1 (idêntico), em poucos degraus —
  // cada cor/espessura distinta vira um stroke() separado por quadro. O degrau fica gravado na aresta (__lvl).
  const strengthLevel = (l) =>
    l.similarity == null ? STRENGTH_STEPS : Math.round(Math.max(0, Math.min(1, (l.similarity - state.minSimilarity) / (1 - state.minSimilarity || 1))) * STRENGTH_STEPS);
  const levelOf = (l) => l.__lvl ?? (l.__lvl = strengthLevel(l));
  const semanticColor = [...Array(STRENGTH_STEPS + 1)].map((_, k) => `rgba(${colors.edgeRgb}, ${(0.1 + (k / STRENGTH_STEPS) * 0.4).toFixed(3)})`);
  const semanticWidth = [...Array(STRENGTH_STEPS + 1)].map((_, k) => 0.6 + (k / STRENGTH_STEPS) * 1.6);
  const C = {
    faint: `rgba(${colors.edgeRgb}, 0.04)`,
    legendFaint: `rgba(${colors.edgeRgb}, 0.05)`,
    link: `rgba(${colors.accentRgb}, 0.85)`,
    linkFocus: `rgba(${colors.accentRgb}, 1)`,
    accent: `rgba(${colors.accentRgb}, 0.6)`,
    group: `rgba(${colors.edgeRgb}, 0.32)`,
  };

  // foco: nó em hover (ou origem do modo conectar) + vizinhos diretos
  const focusId = () => idOf(state.hoverNode || state.connectFrom || null);
  const inFocus = (n) => {
    const f = focusId();
    return f != null && (n.id === f || neighbors.get(f)?.has(n.id));
  };
  const linkTouchesFocus = (l) => {
    const f = focusId();
    return f != null && (idOf(l.source) === f || idOf(l.target) === f);
  };

  const radius = (n) => {
    if (n.__r !== undefined) return n.__r;
    if (groups) return 8 + 30 * Math.sqrt((n.size || 1) / maxSize);
    const deg = degree.get(n.id) || 0;
    const base = state.level === "documents" ? Math.min(14, 3.5 + 1.7 * Math.sqrt(deg) + 0.6 * Math.sqrt(n.chunks || 1)) : 3.5 + Math.sqrt(deg);
    return isHl(n) ? base * 1.4 : base;
  };

  const nodeDimmed = (n) => {
    if (state.legendHover && n.collection !== state.legendHover) return true;
    if (highlight && !isHl(n)) return true;
    return focusId() != null && !inFocus(n);
  };

  // cores esmaecidas pré-calculadas por nó em drawGraph (__dim); o 3D e nós avulsos caem no cálculo
  const nodeColor = (n) => {
    if (highlight && isHl(n)) return colors.accent;
    return nodeDimmed(n) ? n.__dim || hexAlpha(n.color, 0.2) : n.color;
  };

  const linkColor = (l) => {
    const f = focusId() != null;
    if (f && !linkTouchesFocus(l)) return C.faint;
    if (highlight && !(isHl(l.source) && isHl(l.target))) return C.faint;
    if (state.legendHover && !(l.source?.collection === state.legendHover && l.target?.collection === state.legendHover)) return C.legendFaint;
    if (l.kind === "group") return f ? C.accent : C.group;
    if (l.kind === "link") return f ? C.linkFocus : C.link;
    if (f || highlight) return C.accent;
    // similaridade: discreta, mais visível quanto mais forte
    return semanticColor[levelOf(l)];
  };

  const linkWidth = (l) => {
    const base = l.kind === "group" ? 1 + 4 * Math.sqrt((l.weight || 1) / maxWeight) : l.kind === "link" ? 1.8 : semanticWidth[levelOf(l)];
    return base * state.linkScale * (linkTouchesFocus(l) ? 1.5 : 1);
  };

  return { colors, highlight, isHl, nodeKey, visibleEdges, big, groups, serverLayout, linkTouchesFocus, degree, neighbors, radius, nodeDimmed, nodeColor, linkColor, linkWidth, inFocus, focusId };
}

// Reaplica cores/espessuras sem recarregar do servidor (hover, legenda, slider de espessura).
function refreshGraphStyles() {
  const g = state.graphInstance;
  if (!g || !view) return;
  if (state.graphInstanceMode === "3d") {
    g.nodeColor(view.nodeColor).linkColor(view.linkColor).linkWidth(view.linkWidth);
    return;
  }
  // 2D: reatribuir o acessor força o redesenho mesmo com a simulação pausada
  g.linkWidth(view.linkWidth).linkColor(view.linkColor);
}

/* ---------------------------------------------------------------- renderizador 2D (canvas) */

function create2dGraph(el) {
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
      refreshGraphStyles();
    })
    .onNodeClick((n) => onGraphNodeClick(n))
    .onBackgroundClick(() => closePanel())
    // área visível em coordenadas do grafo, a cada quadro: nós e arestas fora da tela não são pintados
    .onRenderFramePre(() => {
      const a = g.screen2GraphCoords(0, 0);
      const b = g.screen2GraphCoords(g.width(), g.height());
      const m = 40 / (g.zoom() || 1);
      viewBounds = { x0: a.x - m, y0: a.y - m, x1: b.x + m, y1: b.y + m };
    })
    .onEngineStop(() => {
      if (!view?.serverLayout) savePositions(g.graphData().nodes);
      if (state.graphSettling) {
        // grafo grande assentou: agora entram as arestas de similaridade (reatribuir força o redesenho)
        state.graphSettling = false;
        g.linkVisibility(g.linkVisibility());
      }
      if (state.graphNeedsFit) {
        state.graphNeedsFit = false;
        fitGraph();
      }
    });
  g.d3Force("charge").strength(-180);
  const resize = () => g.width(el.clientWidth).height(el.clientHeight);
  resize();
  state.graphObserver = new ResizeObserver(resize);
  state.graphObserver.observe(el);
  return g;
}

const LABEL_FONT = 'Inter, system-ui, -apple-system, "Segoe UI", sans-serif';

// Rótulos num passe próprio, depois de nós e arestas: ficam por cima de tudo e entram em ordem de
// importância (foco > destaque > mais conexões). Rótulo que colidiria com um já desenhado é pulado —
// sem isso, com dezenas de nós, os títulos viravam uma mancha ilegível no centro.
// Desempenho (plan-web-22, bases com ~2 mil nós): a ordem por grau é calculada uma vez por carga
// (labelOrder), só entram nós dentro da tela, há teto de rótulos por quadro e a largura do texto fica
// em cache (mede a 12px uma vez; no zoom só escala). A colisão usa uma grade (cada caixa só é comparada
// com as das células que cobre), não a lista inteira de rótulos já desenhados.
const MAX_LABELS = 140;

// área visível em coordenadas do grafo (atualizada em onRenderFramePre); nó ainda sem posição conta como visível
let viewBounds = null;
const onScreen = (n) =>
  !viewBounds || n.x === undefined || (n.x >= viewBounds.x0 && n.x <= viewBounds.x1 && n.y >= viewBounds.y0 && n.y <= viewBounds.y1);
const labelWidth = new Map();

function measureLabel(ctx, text, weight) {
  const key = `${weight}|${text}`;
  let w = labelWidth.get(key);
  if (w === undefined) {
    ctx.font = `${weight} 12px ${LABEL_FONT}`;
    w = ctx.measureText(text).width;
    if (labelWidth.size > 20000) labelWidth.clear();
    labelWidth.set(key, w);
  }
  return w;
}

function drawLabels2d(ctx, scale, g) {
  const v = view;
  if (!v || !v.labelOrder || (v.big && state.graphSettling)) return;
  const focused = v.focusId() != null;
  // área visível em coordenadas do grafo (com folga para rótulos que começam fora da borda)
  const topLeft = g.screen2GraphCoords(0, 0);
  const bottomRight = g.screen2GraphCoords(g.width(), g.height());
  const margin = 80 / scale;
  const labelInView = (n) =>
    n.x >= topLeft.x - margin && n.x <= bottomRight.x + margin && n.y >= topLeft.y - margin && n.y <= bottomRight.y + margin;
  // foco primeiro (poucos nós), depois a ordem fixa por grau
  const focusFirst = focused ? v.labelOrder.filter((n) => v.inFocus(n)) : [];
  const hoveredNode = state.hoverNode || state.connectFrom;
  if (hoveredNode) focusFirst.sort((a, b) => (b === hoveredNode) - (a === hoveredNode));
  const candidates = focused ? focusFirst : v.labelOrder;
  const grid = new Map();
  const cell = 160 / scale;
  let drawn = 0;
  const cellsOf = (b, fn) => {
    for (let cx = Math.floor(b.x / cell); cx <= Math.floor((b.x + b.w) / cell); cx++) {
      for (let cy = Math.floor(b.y / cell); cy <= Math.floor((b.y + b.h) / cell); cy++) fn(`${cx},${cy}`);
    }
  };
  const collides = (box) => {
    let hit = false;
    cellsOf(box, (k) => {
      if (!hit) hit = (grid.get(k) || []).some((b) => box.x < b.x + b.w && box.x + box.w > b.x && box.y < b.y + b.h && box.y + box.h > b.y);
    });
    return hit;
  };
  const fontSize = 12 / scale;
  const pad = 3 / scale;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.lineJoin = "round";
  ctx.lineWidth = 3 / scale;
  ctx.strokeStyle = v.colors.bg;
  for (const n of candidates) {
    if (drawn >= MAX_LABELS) break;
    if (!labelInView(n)) continue;
    const isFocus = focused && v.inFocus(n);
    const hovered = n === hoveredNode;
    // sem foco e longe demais: só os nós grandes
    if (!v.groups && !focused && scale < 0.55 && (v.degree.get(n.id) || 0) < 6) continue;
    const max = hovered ? 64 : v.groups ? 44 : 30;
    const text = n.label.length > max ? n.label.slice(0, max - 1) + "…" : n.label;
    const weight = hovered ? 600 : 500;
    const w = measureLabel(ctx, text, weight) / scale;
    const r = n.__r;
    const box = { x: n.x - w / 2 - pad, y: n.y + r + 4 / scale - pad, w: w + 2 * pad, h: fontSize + 2 * pad };
    if (!hovered && collides(box)) continue;
    cellsOf(box, (k) => (grid.get(k) || grid.set(k, []).get(k)).push(box));
    drawn++;
    ctx.font = `${weight} ${fontSize}px ${LABEL_FONT}`;
    // contorno na cor do fundo: o texto continua legível por cima das arestas
    ctx.strokeText(text, n.x, n.y + r + 4 / scale);
    ctx.fillStyle = hovered ? v.colors.labelHover : v.nodeDimmed(n) && !isFocus ? v.colors.labelDim : v.colors.label;
    ctx.fillText(text, n.x, n.y + r + 4 / scale);
  }
}

// Grafo grande: o custo é pintar arestas (dezenas de milhares por quadro). Enquanto assenta, nenhuma;
// com um nó em foco, só as dele; de longe, as mais fortes; e só as que têm uma ponta na tela.
function bigLinkVisible(l) {
  if (state.graphSettling) return false;
  if (view.focusId() != null) return view.linkTouchesFocus(l);
  if (!onScreen(l.source) && !onScreen(l.target)) return false;
  return l.__lod || (state.graphInstance?.zoom() || 0) >= (view.fitZoom || 0) * LOD_ZOOM;
}

function draw2d(g, nodes, links) {
  const v = view;
  g.nodeVal((n) => (v.radius(n) / 5) ** 2)
    .nodeCanvasObjectMode(() => "replace")
    .nodeCanvasObject((n, ctx, scale) => {
      if (v.big && !onScreen(n)) return;
      // grafo grande visto de longe: nó com pelo menos ~2 px na tela (senão vira poeira)
      const r = v.big ? Math.max(n.__r, 2 / scale) : n.__r;
      const hovered = state.hoverNode === n || state.connectFrom === n;
      const dim = v.nodeDimmed(n);
      // halo: discreto em todos, forte no foco (cores montadas uma vez por carga em drawGraph).
      // Grafo grande: só no nó em foco ou destacado — o halo dobra o número de círculos pintados
      if (!dim && (!v.big || hovered || (v.highlight && v.isHl(n)))) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + (hovered ? 7 : 3.5), 0, 2 * Math.PI);
        ctx.fillStyle = hovered ? n.__haloHover : n.__halo;
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = v.nodeColor(n);
      ctx.fill();
      // grafo grande: nó com menos de ~3 px na tela fica sem contorno (um traço a menos por nó)
      if (v.big && r * scale < 3) return;
      ctx.lineWidth = 1.5 / scale;
      ctx.strokeStyle = v.colors.nodeStroke;
      // contorno esmaece junto com o nó (no tema claro o anel escuro pesava sobre o nó apagado)
      ctx.globalAlpha = dim ? 0.25 : 1;
      ctx.stroke();
      ctx.globalAlpha = 1;
    })
    .nodePointerAreaPaint((n, color, ctx) => {
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.__r + 4, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
    })
    .onRenderFramePost((ctx, scale) => drawLabels2d(ctx, scale, g))
    .linkColor(v.linkColor)
    .linkWidth(v.linkWidth)
    .linkLineDash(null)
    // arestas levemente curvas: com muitas ligações, linhas retas sobrepostas viram uma malha.
    // Grafo grande: retas (a curva é recalculada por aresta a cada quadro) e sem hover de aresta
    // (o canvas oculto de hover pinta cada aresta com uma cor única = um stroke() por aresta)
    .linkCurvature(v.big ? 0 : (l) => (l.kind === "link" ? 0 : 0.12))
    .linkLabel(v.big ? null : linkLabel)
    .linkPointerAreaPaint(v.big ? NO_PAINT : null)
    .linkVisibility(v.big ? bigLinkVisible : true)
    // grafo grande: parte da física roda antes do 1º quadro (sem pintar) e o resfriamento é mais rápido —
    // menos ticks no total (cada tick custa ~20 ms com ~6 mil nós). Com o layout salvo as forças já estão
    // quase em equilíbrio: poucos ticks bastam
    .warmupTicks(v.serverLayout || v.restored ? 0 : v.big ? 40 : 0)
    .cooldownTicks(v.serverLayout ? 0 : v.restored ? (v.big ? 20 : 40) : v.big ? 50 : 120)
    .d3AlphaDecay(v.big ? 0.035 : 0.0228)
    // layout do servidor: o mapa é fixo (arrastar exigiria rodar a física)
    .enableNodeDrag(!v.serverLayout)
    .nodeLabel(v.groups ? groupTooltip : () => "");
  // repulsão (Barnes-Hut) é o grosso da física: no grafo grande agrupa nós mais distantes (theta),
  // ~25% mais barato por tick, quase sem diferença visual
  g.d3Force("charge").theta(v.big ? 1.2 : 0.9);
  state.graphSettling = v.big && !v.serverLayout;
  setLinkForces(g);
  g.graphData({ nodes, links });
}

/* ---------------------------------------------------------------- renderizador 3D (WebGL) */

function create3dGraph(el) {
  const g = ForceGraph3D({ controlType: "orbit" })(el)
    .backgroundColor("rgba(0,0,0,0)")
    .showNavInfo(false)
    .nodeId("id")
    .nodeRelSize(4)
    .nodeOpacity(0.95)
    .nodeResolution(16)
    .linkOpacity(1) // a opacidade vem do alpha de cada cor (linkColor)
    .cooldownTicks(120)
    .onNodeHover((n) => {
      state.hoverNode = n || null;
      el.style.cursor = n ? "pointer" : "";
      refreshGraphStyles();
    })
    .onNodeClick((n) => onGraphNodeClick(n))
    .onBackgroundClick(() => closePanel())
    .onEngineStop(() => {
      if (state.graphNeedsFit) {
        state.graphNeedsFit = false;
        fitGraph();
      }
    });
  g.d3Force("charge").strength(-140);
  const resize = () => g.width(el.clientWidth).height(el.clientHeight);
  resize();
  state.graphObserver = new ResizeObserver(resize);
  state.graphObserver.observe(el);
  return g;
}

function draw3d(g, nodes, links) {
  const v = view;
  g.nodeVal((n) => (v.radius(n) / 4) ** 3)
    .nodeColor(v.nodeColor)
    // no 3D o título aparece no hover (tooltip): rótulo fixo em WebGL poluiria a cena
    .nodeLabel((n) => {
      const deg = v.degree.get(n.id) || 0;
      return `<strong>${escHtml(n.label)}</strong><br><span class="muted">${escHtml(n.collection)} · ${t("graph.connections", { count: deg })}</span>`;
    })
    .linkColor(v.linkColor)
    .linkWidth(v.linkWidth)
    // curva em WebGL vira tubo segmentado: só vale a pena com poucas arestas
    .linkCurvature((l) => (l.kind === "link" || links.length > 300 ? 0 : 0.12))
    .linkLabel(linkLabel);
  setLinkForces(g);
  g.graphData({ nodes, links });
}

/* ---------------------------------------------------------------- desenho */

function linkLabel(l) {
  const sim = l.similarity != null ? t("edge.similarity", { value: Number(l.similarity).toFixed(2) }) : "";
  if (l.kind !== "link") return sim;
  const how = l.link_kinds?.includes("wikilink") ? "[[wikilink]]" : t("edge.manual");
  return [t("edge.explicit", { how }), l.note ? `“${escHtml(l.note)}”` : "", sim].filter(Boolean).join("<br>");
}

// documentos ligados explicitamente ficam mais perto que os só parecidos
function setLinkForces(g) {
  g.d3Force("link")
    .distance((l) => (l.kind === "link" ? 45 : 95))
    .strength((l) => (l.kind === "link" ? 0.7 : 0.2));
}

function drawGraph(data) {
  const el = document.getElementById("graph-canvas");
  document.getElementById("graph-empty").classList.add("hidden");

  view = buildView(data);
  const clearBtn = document.getElementById("hl-clear");
  clearBtn.classList.toggle("hidden", !view.highlight);
  if (view.highlight) clearBtn.querySelector("span").textContent = t("graph.clearHighlightN", { count: view.highlight.size });

  // 2D sem layout do servidor (ex.: nível chunks): nós voltam para onde estavam da última vez (navegador)
  const saved = state.graphMode === "3d" || view.serverLayout ? {} : store.get(`${POSITIONS_KEY}:${state.level}`) || {};
  let restored = 0;
  // grupos: as posições vêm na escala do mapa de notas (milhares de unidades); reescala para um desenho
  // do tamanho dos grupos, senão as bolhas (raio 8–38) somem no enquadramento
  if (view.groups && data.nodes.length > 1) {
    const xs = data.nodes.map((n) => n.x);
    const ys = data.nodes.map((n) => n.y);
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) || 1;
    const k = (140 * Math.sqrt(data.nodes.length)) / span;
    data.nodes = data.nodes.map((n) => ({ ...n, x: n.x * k, y: n.y * k }));
  }
  const nodes = data.nodes.map((n) => {
    const color = view.groups ? groupColor(n.group) : colorFor(n.collection);
    const label = view.groups ? `${n.title} · ${n.size}` : state.level === "documents" ? n.title : `${n.title} · #${n.chunk_index}`;
    const node = { ...n, color, label, isGroup: view.groups };
    // por carga, não por quadro: cor esmaecida, halos e raio (grau e destaque não mudam até a próxima carga)
    const haloBase = view.highlight && view.isHl(node) ? view.colors.accent : color;
    node.__dim = hexAlpha(color, 0.2);
    node.__halo = hexAlpha(haloBase, 0.12);
    node.__haloHover = hexAlpha(haloBase, 0.3);
    node.__r = view.radius(node);
    const pos = saved[n.id];
    if (pos) {
      [node.x, node.y] = pos;
      restored++;
    }
    return node;
  });
  view.restored = nodes.length > 0 && restored / nodes.length >= 0.8;
  if (view.groups) separateBubbles(nodes);
  const linkEdges = data.edges.filter((e) => e.kind === "link");
  const semanticEdges = data.edges.filter((e) => e.kind === "semantic");
  document.getElementById("graph-sub").textContent = view.groups
    ? t("graph.subGroups", { groups: nodes.length, notes: data.total_nodes })
    : t("graph.sub", {
        nodes: nodes.length,
        links: linkEdges.length,
        semantic: semanticEdges.length,
        level: state.level === "documents" ? t("graph.levelDocuments") : t("graph.levelChunks"),
      });
  renderViewControls(data);

  // instância do modo errado (troca 2D ↔ 3D): recria
  if (state.graphInstance && state.graphInstanceMode !== state.graphMode) {
    destroyGraph();
    el.innerHTML = "";
  }
  if (!state.graphInstance) {
    state.graphInstance = state.graphMode === "3d" ? create3dGraph(el) : create2dGraph(el);
    state.graphInstanceMode = state.graphMode;
  }
  state.hoverNode = null;
  state.graphNeedsFit = true;
  const links = view.visibleEdges.map((e) => ({ ...e }));
  if (view.big) {
    // as LOD_EDGES similaridades mais fortes aparecem de longe; links explícitos sempre
    const sims = links.filter((l) => l.kind !== "link").map((l) => l.similarity).sort((a, b) => b - a);
    const cut = sims.length > LOD_EDGES ? sims[LOD_EDGES - 1] : -Infinity;
    for (const l of links) l.__lod = l.kind === "link" || l.similarity >= cut;
  }
  // destacados (testador de busca) primeiro, depois por nº de conexões (grupos: os maiores)
  view.labelOrder = view.groups
    ? [...nodes].sort((a, b) => b.size - a.size)
    : [...nodes].sort((a, b) => view.isHl(b) - view.isHl(a) || (view.degree.get(b.id) || 0) - (view.degree.get(a.id) || 0));
  if (state.graphMode === "3d") draw3d(state.graphInstance, nodes, links);
  else draw2d(state.graphInstance, nodes, links);

  const legend = document.getElementById("graph-legend");
  const plural = (count) => t(state.level === "documents" ? "legend.docs" : "legend.chunks", { count });
  legend.innerHTML = `
    <button class="legend-head legend-toggle" type="button" data-legend-toggle>
      <span class="grow">${t("legend.collections")}</span>${icon("chevron-right", 14)}
    </button>
    <div class="legend-body">
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
      state.level === "documents" && !view.groups
        ? `<div class="legend-head legend-sep">${t("legend.connections")}</div>
      <div class="legend-edge"><span class="edge-swatch link"></span><span class="legend-name">${t("legend.explicit")}</span>
        <span class="count">${linkEdges.length}</span></div>
      <button class="legend-edge toggle ${state.showSemantic ? "" : "off"}" type="button" data-toggle-semantic
        title="${state.showSemantic ? t("legend.hideSemantic") : t("legend.showSemantic")}">
        <span class="edge-swatch semantic"></span><span class="legend-name">${t("legend.similarity")}</span>
        <span class="count">${state.showSemantic ? semanticEdges.length : t("legend.hidden")}</span></button>`
        : ""
    }
    </div>`;
  applyLegendCollapsed(legend);
  loadIcons(legend);
  for (const id of ["graph-legend", "graph-hint", "graph-zoom"]) document.getElementById(id).classList.remove("hidden");
}

// Grupos vizinhos no mapa podem se sobrepor: afasta pares de bolhas que se tocam (poucas dezenas de
// grupos: O(g²) por passo é desprezível). Mantém a vizinhança, só abre espaço entre elas.
function separateBubbles(nodes, gap = 6) {
  for (let pass = 0; pass < 80; pass++) {
    let moved = false;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 0.01;
        const need = a.__r + b.__r + gap;
        if (dist >= need) continue;
        if (dist === 0.01) dx = 1;
        const push = (need - dist) / 2;
        dx /= dist;
        dy /= dist;
        a.x -= dx * push;
        a.y -= dy * push;
        b.x += dx * push;
        b.y += dy * push;
        moved = true;
      }
    }
    if (!moved) break;
  }
}

// Seletor Grupos / Todas as notas (só em base grande ou com um grupo aberto) e o "voltar" do grupo aberto.
function renderViewControls(data) {
  const seg = document.getElementById("f-view");
  const groupsShown = data.view === "groups" || data.view === "group";
  // grupos só existem no nível documentos (o de chunks não tem layout do servidor)
  seg.classList.toggle("hidden", state.level !== "documents" || (!groupsShown && (data.total_nodes || 0) <= BIG_NODES));
  seg.querySelectorAll("[data-view]").forEach((b) => {
    const on = (b.dataset.view === "groups") === groupsShown;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", String(on));
  });
  const crumb = document.getElementById("graph-crumb");
  crumb.classList.toggle("hidden", data.view !== "group");
  if (data.view === "group") {
    crumb.innerHTML = `
      <button class="btn sm" type="button" data-back-groups>${icon("chevron-left", 14)}<span>${t("graph.backToGroups")}</span></button>
      <span class="graph-crumb-label"><span class="dot" style="background:${groupColor(data.group.id)}"></span>${escHtml(data.group.label)}
        <span class="muted">· ${t("graph.groupNotes", { count: data.group.size })}</span></span>`;
    loadIcons(crumb);
  }
  document.getElementById("graph-hint").textContent = state.graphMode === "3d" ? t("graph.hint3d") : data.view === "groups" ? t("graph.hintGroups") : t("graph.hint");
}

// tooltip do grupo: rótulo, tamanho e as notas mais conectadas
function groupTooltip(n) {
  const top = (n.top || []).map((title) => `<br>· ${escHtml(title.length > 60 ? title.slice(0, 59) + "…" : title)}`).join("");
  return `<strong>${escHtml(n.title)}</strong><br><span class="muted">${t("graph.groupNotes", { count: n.size })} · ${t("graph.clickToOpen")}</span>${top}`;
}

// centraliza o nó no grafo (se o grafo estiver aberto) ao navegar por um link do painel
function focusGraphNode(docId) {
  const g = state.graphInstance;
  const node = g?.graphData().nodes.find((n) => n.id === docId);
  if (!node) return;
  if (state.graphInstanceMode === "3d") {
    // câmera a uma distância fixa do nó, olhando para ele
    const dist = 140;
    const len = Math.hypot(node.x, node.y, node.z) || 1;
    const k = 1 + dist / len;
    g.cameraPosition({ x: node.x * k, y: node.y * k, z: node.z * k }, node, 800);
    return;
  }
  g.centerAt(node.x, node.y, 500);
}

// modo conectar no grafo: 1º clique = origem (destacada), 2º = destino (abre o modal já preenchido)
function setConnectMode(on) {
  state.connectMode = on;
  state.connectFrom = null;
  const btn = document.getElementById("connect-mode");
  btn?.classList.toggle("active", on);
  btn?.classList.toggle("ghost", !on);
  connectNotice(on ? t("connect.modeStart") : null);
  refreshGraphStyles();
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
  if (n.isGroup) {
    state.graphGroup = { id: n.group, label: n.title, size: n.size };
    loadGraph();
    return;
  }
  const docId = state.level === "documents" ? n.id : n.document_id;
  if (!state.connectMode) {
    openDocumentPanel(docId);
    return;
  }
  if (!state.connectFrom) {
    state.connectFrom = n;
    connectNotice(t("connect.modeTarget", { title: escHtml(n.title) }));
    refreshGraphStyles();
    return;
  }
  if (state.connectFrom.id === n.id) return;
  const from = state.connectFrom;
  setConnectMode(false);
  openConnectModal({ document_id: from.id, title: from.title }, { document_id: n.id, title: n.title, collection: n.collection });
}

// recarrega/foca o grafo a pedido de outros módulos (links criados pelo painel, p. ex.)
on("graph:reload", () => {
  if (document.getElementById("graph-wrap")) loadGraph();
});
on("graph:focus", ({ docId }) => focusGraphNode(docId));

export { graphTemplate, bindGraphControls, loadGraph, destroyGraph };
