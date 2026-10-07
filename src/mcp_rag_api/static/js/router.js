/* Roteador da dashboard — troca de telas e hash routing (#/notes/<id>, ex-#/notas/). */
import { t } from "./i18n.js";
import { icon, loadIcons } from "./icons.js";
import { state } from "./state.js";
import { on } from "./events.js";
import { renderShell } from "./shell.js";
import { notes, notesLeave, openNote, renderNotesScreen } from "./views/notes.js";
import { renderOverview, hideTip } from "./views/insights.js";
import { graphTemplate, bindGraphControls, loadGraph, destroyGraph } from "./views/graph.js";
import { searchTemplate, bindSearch, renderSearchResults } from "./views/search.js";
import { renderKeysList } from "./views/keys.js";
import { renderAgentsList } from "./views/agents.js";
import { renderUsersList } from "./views/users.js";
import { closePanel } from "./views/docpanel.js";

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

// navegação pedida por outros módulos (menu do usuário, Painel, busca) sem criar ciclos de import
on("navigate", ({ screen, openId = null }) => goTo(screen, openId));
on("screen:show", (screen) => {
  state.screen = screen;
  renderShell();
  renderScreen();
});

export { goTo, renderShell, renderScreen, matchNoteRoute };
