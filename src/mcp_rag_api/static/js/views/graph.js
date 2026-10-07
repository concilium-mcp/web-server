/* Tela do grafo — force-graph da base RAG: filtros, legenda, destaques e modo conectar. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { graphTheme } from "../theme.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, debounce } from "../ui.js";
import { state, colorFor } from "../state.js";
import { on } from "../events.js";
import { closePanel, openConnectModal, openDocumentPanel } from "./docpanel.js";

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
    collections.map((c) => `<option value="${escHtml(c.name)}" ${c.name === current ? "selected" : ""}>${escHtml(c.name)} (${c.count})</option>`).join("");
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

// centraliza o nó no grafo (se o grafo estiver aberto) ao navegar por um link do painel
function focusGraphNode(docId) {
  const g = state.graphInstance;
  const node = g?.graphData().nodes.find((n) => n.id === docId);
  if (node) g.centerAt(node.x, node.y, 500);
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

// recarrega/foca o grafo a pedido de outros módulos (links criados pelo painel, p. ex.)
on("graph:reload", () => {
  if (document.getElementById("graph-wrap")) loadGraph();
});
on("graph:focus", ({ docId }) => focusGraphNode(docId));

export { graphTemplate, bindGraphControls, loadGraph, destroyGraph };
