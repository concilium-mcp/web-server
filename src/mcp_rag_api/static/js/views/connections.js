/* Tela Conexões — apps autorizados pelo OAuth do MCP (Claude etc.), com Revogar (plan-web-17). */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtDate, fmtAgo, uiConfirm, uiError } from "../ui.js";
import { state } from "../state.js";
import { accessChips } from "../access.js";

function grantRow(g, showUser) {
  const who = g.agent_slug
    ? `<span class="chip">${icon("bot", 12)}<span class="chip-text">${escHtml(g.agent_name || g.agent_slug)}</span></span>`
    : `<span class="tcell-who">${icon("circle-user-round", 14)}<span class="chip-text">${escHtml(showUser ? g.username : t("connections.asYou"))}</span></span>`;
  const folders = g.collections.length ? g.collections.join(", ") : t("connections.allFolders");
  return `
    <div class="trow">
      <div class="tcell-main">
        <span class="row-icon">${icon("plug")}</span>
        <div class="grow">
          <div class="row-title">${escHtml(g.client_name)}</div>
          <div class="muted" title="${escHtml(folders)}">${escHtml(g.redirect_host || "")} · ${escHtml(folders)}</div>
        </div>
      </div>
      <div class="tcell">${g.agent_slug ? `<span class="muted">${t("connections.agentProfile")}</span>` : accessChips(g.scopes)}</div>
      <div class="tcell">${who}</div>
      <div class="tcell" title="${fmtDate(g.created_at)}">${fmtAgo(g.created_at)}</div>
      <div class="tcell ${g.last_used_at ? "" : "muted"}" title="${g.last_used_at ? fmtDate(g.last_used_at) : t("keys.neverUsed")}">${g.last_used_at ? fmtAgo(g.last_used_at) : t("keys.neverUsed")}</div>
      <div class="tcell-actions">
        <button class="btn sm ghost danger-text" data-revoke-grant="${g.id}" data-name="${escHtml(g.client_name)}">${icon("ban", 14)}<span>${t("keys.revoke")}</span></button>
      </div>
    </div>`;
}

async function renderConnectionsList() {
  const body = document.getElementById("page-body");
  const isAdmin = state.user.role === "admin";
  const all = isAdmin && state.connectionsAll === true;
  let grants;
  try {
    grants = await api.get("/oauth/grants", all ? { all: "true" } : undefined);
  } catch {
    body.innerHTML = `<div class="results-empty">${t("connections.loadFailed")}</div>`;
    return;
  }
  body.innerHTML = `
    <div class="page-col">
      <div class="section-head">
        <span class="muted">${t("connections.count", { count: grants.length })}</span>
        ${isAdmin ? `<label class="consent-perm"><input type="checkbox" id="connections-all" ${all ? "checked" : ""} /><span>${t("connections.showAll")}</span></label>` : ""}
      </div>
      <div class="data-table">
        <div class="trow thead">
          <div>${t("connections.col.app")}</div><div>${t("keys.col.scopes")}</div><div>${t("connections.col.as")}</div><div>${t("connections.col.connected")}</div><div>${t("keys.col.lastUsed")}</div><div></div>
        </div>
        ${grants.map((g) => grantRow(g, all)).join("") || `<div class="results-empty">${t("connections.empty")}</div>`}
      </div>
      <p class="muted">${t("connections.howTo", { url: `<code>${escHtml(location.origin)}/mcp</code>` })}</p>
    </div>`;
  await loadIcons(body);
  body.querySelector("#connections-all")?.addEventListener("change", (e) => {
    state.connectionsAll = e.target.checked;
    renderConnectionsList();
  });
  for (const btn of body.querySelectorAll("[data-revoke-grant]")) {
    btn.addEventListener("click", async () => {
      const ok = await uiConfirm({
        title: t("connections.revokeTitle"),
        message: t("connections.revokeMsg", { app: btn.dataset.name }),
        confirmLabel: t("keys.revoke"),
        tone: "danger",
      });
      if (!ok) return;
      try {
        await api.post(`/oauth/grants/${btn.dataset.revokeGrant}/revoke`);
        await renderConnectionsList();
      } catch (err) {
        await uiError(t("connections.revokeFailed"), err);
      }
    });
  }
}

export { renderConnectionsList };
