/* Tela "Autorizar aplicativo" do OAuth do MCP (plan-web-17) — servida em /oauth/consent?request=<id>.
   Página própria (fora da SPA): login da dash quando falta sessão, permissões marcáveis, pastas e,
   para admin, conectar usando o perfil de um agente. Autorizar/Recusar devolve a URL de volta ao app. */
import { t } from "./i18n.js";
import { icon, loadIcons } from "./icons.js";
import { brandLogo, enhancePasswordFields, escHtml } from "./ui.js";
import { syncFavicon } from "./brand.js";
import { mfaStepHtml, bindMfaStep } from "./mfa-step.js";

const requestId = new URLSearchParams(location.search).get("request") || "";
const app = document.getElementById("app");
syncFavicon(); // ícone da aba na cor do tema

// [escopo, chave do rótulo, chave da descrição]
const PERMISSIONS = [
  ["read", "oauth.perm.read", "oauth.perm.readHint"],
  ["write", "oauth.perm.write", "oauth.perm.writeHint"],
  ["agents:manage", "oauth.perm.agents", "oauth.perm.agentsHint"],
];

async function call(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: { "X-Requested-With": "fetch", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(String(r.status)), { status: r.status, detail: data.detail });
  return data;
}

const shell = (inner) => {
  app.innerHTML = `<div class="login-wrap consent-wrap"><div class="login-card consent-card">${inner}</div></div>`;
  loadIcons(app);
};

const header = (info) => `
  <div class="consent-badge">${icon("shield-check", 26)}</div>
  <h1 class="consent-title">${t("oauth.title", { app: escHtml(info.client_name) })}</h1>
  <p class="muted consent-intro">${t("oauth.intro", { app: escHtml(info.client_name) })}</p>
  ${info.redirect_host ? `<p class="consent-return">${icon("external-link", 13)}<span>${t("oauth.returnsTo", { host: `<strong>${escHtml(info.redirect_host)}</strong>` })}</span></p>` : ""}`;

function renderError(message) {
  shell(`
    <div class="brand">${brandLogo(26)}<span>Concilium</span></div>
    <p class="form-error">${escHtml(message)}</p>
    <p class="muted">${t("oauth.retry")}</p>`);
}

function renderLogin(info, message = "") {
  shell(`
    ${header(info)}
    <form id="consent-login" class="consent-login">
      <p class="muted">${t("oauth.loginFirst")}</p>
      <label class="form-label" for="login-user">${t("login.username")}</label>
      <input class="input" id="login-user" type="text" autocomplete="username" required />
      <label class="form-label" for="login-pass">${t("login.password")}</label>
      <input class="input" id="login-pass" type="password" autocomplete="current-password" required />
      <div class="form-error ${message ? "" : "hidden"}" id="login-error"></div>
      <button class="btn primary" type="submit">${icon("log-in")}<span>${t("login.submit")}</span></button>
    </form>`);
  enhancePasswordFields(app);
  const error = document.getElementById("login-error");
  error.textContent = message;
  document.getElementById("consent-login").addEventListener("submit", async (e) => {
    e.preventDefault();
    error.classList.add("hidden");
    try {
      const result = await call("POST", "/dash/api/auth/login", {
        username: document.getElementById("login-user").value,
        password: document.getElementById("login-pass").value,
      });
      if (result.mfa_required) {
        // 2FA ligado: o código entra no lugar do formulário de senha, abaixo do cabeçalho do app
        app.querySelector("#consent-login").outerHTML = `<div id="consent-mfa">${mfaStepHtml()}</div>`;
        bindMfaStep(app.querySelector("#consent-mfa"), {
          mfaToken: result.mfa_token,
          post: (body) => call("POST", "/dash/api/auth/login/mfa", body),
          onDone: load,
          onBack: (msg) => renderLogin(info, msg),
        });
        return;
      }
      await load();
    } catch (err) {
      error.textContent = err.status === 401 ? t("login.invalid") : err.status === 429 ? err.detail : t("login.failed");
      error.classList.remove("hidden");
    }
  });
}

function renderConsent(info) {
  const available = new Set(info.available_scopes);
  const requested = new Set(info.requested_scopes);
  // padrão: tudo o que o papel permite, menos gerir agentes (só se o app pediu explicitamente)
  const checked = (scope) => scope === "read" || (scope === "agents:manage" ? requested.has(scope) : true);
  const perms = PERMISSIONS.filter(([scope]) => available.has(scope))
    .map(
      ([scope, label, hint]) => `
      <label class="consent-perm">
        <input type="checkbox" name="scope" value="${scope}" ${checked(scope) ? "checked" : ""} ${scope === "read" ? "disabled" : ""} />
        <span><strong>${t(label)}</strong> — ${t(hint)}</span>
      </label>`,
    )
    .join("");
  const cols = info.collections
    .map(
      (c) => `
      <label class="consent-perm consent-col">
        <input type="checkbox" name="collection" value="${escHtml(c.name)}" />
        <span>${icon("folder", 14)} ${escHtml(c.name)}</span>
      </label>`,
    )
    .join("");
  const agents = info.agents.length
    ? `
    <div class="consent-section">
      <label class="form-label" for="consent-agent">${t("oauth.actAs")}</label>
      <select class="input" id="consent-agent">
        <option value="">${t("oauth.actAsMe", { user: escHtml(info.user.username) })}</option>
        ${info.agents.map((a) => `<option value="${escHtml(a.slug)}">${t("oauth.actAsAgent", { agent: escHtml(a.name) })}</option>`).join("")}
      </select>
      <p class="muted consent-note hidden" id="consent-agent-note">${t("oauth.actAsAgentNote")}</p>
    </div>`
    : "";

  shell(`
    ${header(info)}
    <div class="consent-who">
      <div><span class="muted">${t("oauth.connectedAs")}</span><br /><strong>${escHtml(info.user.username)}</strong>
        <span class="chip">${t(`oauth.role.${info.user.role}`)}</span></div>
      <button class="btn ghost sm" id="consent-logout" type="button">${t("oauth.notYou")}</button>
    </div>
    ${agents}
    <div class="consent-section" id="consent-own">
      <div class="form-label">${t("oauth.permissions")}</div>
      <div class="consent-list">${perms}</div>
      <div class="form-label">${t("oauth.folders")}</div>
      <div class="consent-list">
        <label class="consent-perm"><input type="radio" name="folders" value="all" checked /><span>${t("oauth.foldersAll")}</span></label>
        <label class="consent-perm"><input type="radio" name="folders" value="some" ${cols ? "" : "disabled"} /><span>${t("oauth.foldersSome")}</span></label>
        <div class="consent-cols hidden" id="consent-cols">${cols}</div>
      </div>
    </div>
    <p class="muted consent-note">${t("oauth.revokeNote")}</p>
    <div class="form-error hidden" id="consent-error"></div>
    <div class="consent-actions">
      <button class="btn" id="consent-deny" type="button">${t("oauth.deny")}</button>
      <button class="btn primary" id="consent-allow" type="button">${icon("check")}<span>${t("oauth.allow")}</span></button>
    </div>`);

  const $ = (sel) => app.querySelector(sel);
  const colsBox = $("#consent-cols");
  for (const radio of app.querySelectorAll('input[name="folders"]')) {
    radio.addEventListener("change", () => colsBox.classList.toggle("hidden", radio.value !== "some" || !radio.checked));
  }
  const agentSelect = $("#consent-agent");
  agentSelect?.addEventListener("change", () => {
    // usando o perfil de um agente, escopos e pastas vêm do cadastro dele
    const asAgent = Boolean(agentSelect.value);
    $("#consent-own").classList.toggle("hidden", asAgent);
    $("#consent-agent-note").classList.toggle("hidden", !asAgent);
  });
  $("#consent-logout").addEventListener("click", async () => {
    await call("POST", "/dash/api/auth/logout").catch(() => {});
    await load();
  });

  const error = $("#consent-error");
  const decide = async (approve) => {
    error.classList.add("hidden");
    const some = $('input[name="folders"]:checked').value === "some";
    const collections = some ? [...app.querySelectorAll('input[name="collection"]:checked')].map((el) => el.value) : [];
    if (approve && some && !collections.length && !agentSelect?.value) {
      error.textContent = t("oauth.pickFolder");
      error.classList.remove("hidden");
      return;
    }
    for (const b of app.querySelectorAll(".consent-actions .btn")) b.disabled = true;
    try {
      const { redirect } = await call("POST", `/oauth/api/requests/${encodeURIComponent(requestId)}/decision`, {
        approve,
        scopes: [...app.querySelectorAll('input[name="scope"]:checked')].map((el) => el.value),
        collections,
        agent_slug: agentSelect?.value || null,
      });
      shell(`
        ${header(info)}
        <p class="consent-done">${icon(approve ? "check" : "x")}<span>${approve ? t("oauth.done") : t("oauth.denied")}</span></p>`);
      location.href = redirect;
    } catch (err) {
      for (const b of app.querySelectorAll(".consent-actions .btn")) b.disabled = false;
      error.textContent = err.detail || t("common.opFailed");
      error.classList.remove("hidden");
    }
  };
  $("#consent-allow").addEventListener("click", () => decide(true));
  $("#consent-deny").addEventListener("click", () => decide(false));
}

async function load() {
  document.title = t("oauth.pageTitle");
  if (!requestId) return renderError(t("oauth.invalidRequest"));
  try {
    const info = await call("GET", `/oauth/api/requests/${encodeURIComponent(requestId)}`);
    if (info.user) renderConsent(info);
    else renderLogin(info);
  } catch (err) {
    renderError(err.detail || t("oauth.invalidRequest"));
  }
}

load();
