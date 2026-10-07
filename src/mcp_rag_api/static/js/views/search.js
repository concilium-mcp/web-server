/* Tela de busca RAG (testador) — resultados híbridos com destaque de termos. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml } from "../ui.js";
import { state, colorFor } from "../state.js";
import { emit } from "../events.js";
import { openDocumentPanel } from "./docpanel.js";

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
    // troca para o grafo sem limpar o hash (equivale ao goto direto do monolito)
    emit("screen:show", "graph");
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

export { searchTemplate, bindSearch, renderSearchResults };
