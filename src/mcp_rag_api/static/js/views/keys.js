/* Tela de chaves API — gestão visual das chaves Bearer (criar, renovar, revogar). */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtDate, fmtAgo, modalShell, bindModal, uiConfirm, uiAlert, uiError } from "../ui.js";
import { state } from "../state.js";

/* ---------------------------------------------------------------- tela: chaves API */

// [escopo, chave da descrição]
const SCOPES = [
  ["read", "scope.read"],
  ["write", "scope.write"],
  ["agents:manage", "scope.agentsManage"],
  ["admin", "scope.admin"],
];


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
            ${agents.map((a) => `<option value="${escHtml(a.slug)}">agent: ${escHtml(a.slug)}</option>`).join("")}
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

export { renderKeysList };
