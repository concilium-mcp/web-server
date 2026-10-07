/* Tela de usuários — papéis, senhas e ativação/desativação de contas da dashboard. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtDate, fmtAgo, modalShell, bindModal, uiConfirm, uiAlert, uiError } from "../ui.js";
import { state } from "../state.js";

/* ---------------------------------------------------------------- tela: usuários */

const ROLES = ["viewer", "editor", "admin"];
// papel desconhecido conta como leitor (o menos privilegiado)
const roleId = (role) => (ROLES.includes(role) ? role : "viewer");
const roleName = (role) => t(`role.${roleId(role)}`);

function userRow(u) {
  const disabled = Boolean(u.disabled_at);
  const self = u.username === state.user.username;
  return `
    <div class="trow users ${disabled ? "revoked" : ""}">
      <div class="tcell-main">
        <span class="avatar">${escHtml(u.username.slice(0, 1).toUpperCase())}</span>
        <div class="grow">
          <div class="row-title">${escHtml(u.username)} ${self ? `<span class="chip subtle">${t("users.you")}</span>` : ""}</div>
        </div>
      </div>
      <div class="tcell">
        <select class="input role-select" data-role="${u.id}" ${self || disabled ? "disabled" : ""}
          title="${self ? t("users.cantChangeOwn") : t("users.roleTitle")}">
          ${ROLES.map((v) => `<option value="${v}" ${u.role === v ? "selected" : ""}>${roleName(v)}</option>`).join("")}
        </select>
      </div>
      <div class="tcell"><span class="status ${disabled ? "" : "ok"}"><span class="status-dot"></span>${disabled ? t("users.disabled") : t("users.active")}</span></div>
      <div class="tcell muted" title="${t("users.createdAt", { date: fmtDate(u.created_at) })}">${fmtAgo(u.created_at)}</div>
      <div class="tcell-actions">
        ${disabled ? "" : `<button class="btn sm ghost" data-reset="${u.id}" title="${t("users.setPasswordTitle")}">${icon("key-round", 14)}<span>${t("users.password")}</span></button>`}
        ${
          self
            ? ""
            : `<button class="btn sm ghost ${disabled ? "" : "danger-text"}" data-toggle="${u.id}" data-disabled="${disabled}"
                title="${disabled ? t("users.reactivateTitle") : t("users.disableTitle")}">
                ${icon(disabled ? "refresh-cw" : "ban", 14)}<span>${disabled ? t("users.reactivate") : t("users.disable")}</span>
              </button>`
        }
      </div>
      <div class="reset-row hidden" id="reset-${u.id}">
        <input class="input" type="password" placeholder="${t("users.newPasswordPh", { name: escHtml(u.username) })}" id="reset-pass-${u.id}" minlength="8" />
        <button class="btn sm" type="button" data-reset="${u.id}">${t("common.cancel")}</button>
        <button class="btn sm primary" type="button" data-do-reset="${u.id}">${icon("check", 14)}<span>${t("users.savePassword")}</span></button>
      </div>
    </div>`;
}

function usersCreateForm() {
  return modalShell({
    id: "user",
    iconName: "users",
    title: t("users.new"),
    subtitle: t("users.newSub"),
    submitLabel: t("users.create"),
    width: 640,
    body: `
      <div class="create-grid">
        <label class="form-field">
          <span class="form-label">${t("users.username")}</span>
          <input class="input" type="text" id="u-username" placeholder="${t("users.usernamePh")}" autocomplete="off" required />
        </label>
        <label class="form-field">
          <span class="form-label">${t("users.initialPassword")}</span>
          <input class="input" type="password" id="u-password" placeholder="${t("users.passwordPh")}" minlength="8" autocomplete="new-password" required />
        </label>
      </div>
      <div class="form-field">
        <span class="form-label">${t("users.role")}</span>
        <div class="scope-grid two">
          ${ROLES.map(
            (v) => `
            <label class="scope-option">
              <input type="radio" name="u-role" value="${v}" ${v === "viewer" ? "checked" : ""} />
              <span><span>${roleName(v)}</span><span class="muted">${t(`role.${v}.desc`)}</span></span>
            </label>`,
          ).join("")}
        </div>
      </div>`,
  });
}

const usersHead = () => `
  <div class="trow users thead">
    <div>${t("users.col.user")}</div><div>${t("users.col.role")}</div><div>${t("users.col.status")}</div><div>${t("users.col.created")}</div><div></div>
  </div>`;

async function renderUsersList() {
  const body = document.getElementById("page-body");
  let users;
  try {
    users = await api.get("/users");
  } catch (err) {
    body.innerHTML = `<div class="results-empty">${err.status === 403 ? t("users.onlyAdmins") : t("users.loadFailed")}</div>`;
    return;
  }
  const active = users.filter((u) => !u.disabled_at).length;
  const admins = users.filter((u) => !u.disabled_at && u.role === "admin").length;
  body.innerHTML = `
    <div class="page-col">
      <div class="section-head">
        <span class="muted">${t("users.counts", { active, admins, disabled: users.length - active })}</span>
        <button class="btn primary" id="user-new">${icon("plus")}<span>${t("users.new")}</span></button>
      </div>
      ${usersCreateForm()}
      <div class="data-table">${usersHead()}${users.map(userRow).join("")}</div>
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
      await uiError(t("users.createFailed"), err);
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
      await uiError(t("users.roleFailed"), err);
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
          await uiAlert(t("users.shortPasswordTitle"), t("users.shortPasswordMsg"));
          return;
        }
        await api.patch(`/users/${doReset.dataset.doReset}`, { password: pass });
        await renderUsersList();
      } else if (toggle) {
        const disable = toggle.dataset.disabled !== "true";
        if (
          disable &&
          !(await uiConfirm({
            title: t("users.disableConfirmTitle"),
            message: t("users.disableConfirmMsg"),
            confirmLabel: t("users.disable"),
            tone: "danger",
          }))
        )
          return;
        await api.patch(`/users/${toggle.dataset.toggle}`, { disabled: disable });
        await renderUsersList();
      }
    } catch (err) {
      await uiError(t("common.opFailed"), err);
    }
  });
}

export { ROLES, roleId, roleName, renderUsersList };
