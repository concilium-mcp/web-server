/* Tela Segurança — autenticação em dois fatores (TOTP) da própria conta: ativar com QR code,
   recovery codes, gerar códigos novos e desativar. Mudanças pedem senha e/ou código atual. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtDate, uiConfirm, uiError } from "../ui.js";

const codeInput = (id) =>
  `<input class="input mfa-code" id="${id}" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="11" placeholder="123456" required />`;

async function renderSecurityPage() {
  const body = document.getElementById("page-body");
  let status;
  try {
    status = await api.get("/account/2fa");
  } catch {
    body.innerHTML = `<div class="results-empty">${t("mfa.loadFailed")}</div>`;
    return;
  }
  body.innerHTML = `
    <div class="page-col mfa-page">
      <div class="card mfa-card">
        <div class="card-row">
          <span class="row-icon">${icon("shield-check")}</span>
          <div class="grow">
            <div class="card-title">${t("mfa.title")}
              <span class="status ${status.enabled ? "ok" : ""}"><span class="status-dot"></span>${status.enabled ? t("mfa.on") : t("mfa.off")}</span>
            </div>
            <div class="muted">${status.enabled ? t("mfa.onSince", { date: fmtDate(status.enabled_at) }) : t("mfa.offHint")}</div>
          </div>
        </div>
        ${status.enabled ? enabledHtml(status) : disabledHtml()}
      </div>
      <div id="mfa-reveal"></div>
    </div>`;
  await loadIcons(body);
  if (status.enabled) bindEnabled(body);
  else bindDisabled(body);
}

/* ---------------------------------------------------------------- 2FA desligado: ativar */

function disabledHtml() {
  return `
    <form class="mfa-step" id="mfa-start">
      <label class="form-field">
        <span class="form-label">${t("mfa.confirmPassword")}</span>
        <input class="input" type="password" id="mfa-start-pass" autocomplete="current-password" required />
      </label>
      <div><button class="btn primary" type="submit">${icon("shield-check")}<span>${t("mfa.enable")}</span></button></div>
    </form>
    <div class="mfa-step hidden" id="mfa-setup"></div>`;
}

function bindDisabled(body) {
  body.querySelector("#mfa-start").addEventListener("submit", async (e) => {
    e.preventDefault();
    let setup;
    try {
      setup = await api.post("/account/2fa/setup", { password: body.querySelector("#mfa-start-pass").value });
    } catch (err) {
      await uiError(t("mfa.setupFailed"), err);
      return;
    }
    e.target.classList.add("hidden");
    const box = body.querySelector("#mfa-setup");
    box.classList.remove("hidden");
    box.innerHTML = `
      <ol class="mfa-steps">
        <li>${t("mfa.step1")}</li>
        <li>${t("mfa.step2")}</li>
      </ol>
      <div class="mfa-qr-row">
        <img class="mfa-qr" alt="${t("mfa.qrAlt")}" src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(setup.qr_svg)}" />
        <div class="mfa-manual">
          <span class="muted">${t("mfa.manual")}</span>
          <div class="key-reveal-row">
            <code class="mono">${escHtml(setup.secret.replace(/(.{4})/g, "$1 ").trim())}</code>
            <button class="btn sm" type="button" id="mfa-copy-secret">${icon("copy")}<span>${t("common.copy")}</span></button>
          </div>
        </div>
      </div>
      <form class="mfa-confirm" id="mfa-confirm">
        <label class="form-field">
          <span class="form-label">${t("mfa.step3")}</span>
          ${codeInput("mfa-confirm-code")}
        </label>
        <div><button class="btn primary" type="submit">${icon("check")}<span>${t("mfa.activate")}</span></button></div>
      </form>`;
    await loadIcons(box);
    box.querySelector("#mfa-copy-secret").addEventListener("click", (ev) => copy(ev.currentTarget, setup.secret));
    box.querySelector("#mfa-confirm-code").focus();
    box.querySelector("#mfa-confirm").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        const { recovery_codes } = await api.post("/account/2fa/enable", { code: box.querySelector("#mfa-confirm-code").value });
        await renderSecurityPage();
        showRecoveryCodes(recovery_codes, t("mfa.enabledNow"));
      } catch (err) {
        await uiError(t("mfa.invalid"), err);
      }
    });
  });
}

/* ---------------------------------------------------------------- 2FA ligado: códigos e desligar */

function enabledHtml(status) {
  return `
    <div class="mfa-actions">
      <span class="muted">${t("mfa.recoveryLeft", { count: status.recovery_codes_left })}</span>
      <div class="row-actions">
        <button class="btn sm" type="button" id="mfa-regen-open">${icon("refresh-cw", 14)}<span>${t("mfa.regenerate")}</span></button>
        <button class="btn sm ghost danger-text" type="button" id="mfa-disable-open">${icon("ban", 14)}<span>${t("mfa.disable")}</span></button>
      </div>
    </div>
    <form class="mfa-step hidden" id="mfa-regen">
      <label class="form-field">
        <span class="form-label">${t("mfa.currentCode")}</span>
        ${codeInput("mfa-regen-code")}
      </label>
      <div><button class="btn primary" type="submit">${icon("refresh-cw")}<span>${t("mfa.regenerate")}</span></button></div>
    </form>
    <form class="mfa-step hidden" id="mfa-disable">
      <div class="create-grid">
        <label class="form-field">
          <span class="form-label">${t("mfa.confirmPassword")}</span>
          <input class="input" type="password" id="mfa-disable-pass" autocomplete="current-password" required />
        </label>
        <label class="form-field">
          <span class="form-label">${t("mfa.currentCode")}</span>
          ${codeInput("mfa-disable-code")}
        </label>
      </div>
      <div><button class="btn danger" type="submit">${icon("ban")}<span>${t("mfa.disable")}</span></button></div>
    </form>`;
}

function bindEnabled(body) {
  const regen = body.querySelector("#mfa-regen");
  const disable = body.querySelector("#mfa-disable");
  const toggle = (show, hide) => {
    hide.classList.add("hidden");
    show.classList.toggle("hidden");
    show.querySelector("input")?.focus();
  };
  body.querySelector("#mfa-regen-open").addEventListener("click", () => toggle(regen, disable));
  body.querySelector("#mfa-disable-open").addEventListener("click", () => toggle(disable, regen));

  regen.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const { recovery_codes } = await api.post("/account/2fa/recovery-codes", { code: body.querySelector("#mfa-regen-code").value });
      await renderSecurityPage();
      showRecoveryCodes(recovery_codes, t("mfa.regenerated"));
    } catch (err) {
      await uiError(t("mfa.invalid"), err);
    }
  });
  disable.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (
      !(await uiConfirm({
        title: t("mfa.disableConfirmTitle"),
        message: t("mfa.disableConfirmMsg"),
        confirmLabel: t("mfa.disable"),
        tone: "danger",
      }))
    )
      return;
    try {
      await api.post("/account/2fa/disable", {
        password: body.querySelector("#mfa-disable-pass").value,
        code: body.querySelector("#mfa-disable-code").value,
      });
      await renderSecurityPage();
    } catch (err) {
      await uiError(t("mfa.disableFailed"), err);
    }
  });
}

/* ---------------------------------------------------------------- recovery codes (mostrados uma vez) */

function showRecoveryCodes(codes, headline) {
  const box = document.getElementById("mfa-reveal");
  box.innerHTML = `
    <div class="key-reveal">
      <div class="key-reveal-head">
        ${icon("check")}<strong>${headline}</strong>
        <span class="muted">${t("mfa.saveCodes")}</span>
      </div>
      <div class="mfa-codes">${codes.map((c) => `<code class="mono">${escHtml(c)}</code>`).join("")}</div>
      <div><button class="btn sm" type="button" id="mfa-copy-codes">${icon("copy")}<span>${t("common.copy")}</span></button></div>
    </div>`;
  loadIcons(box);
  box.querySelector("#mfa-copy-codes").addEventListener("click", (e) => copy(e.currentTarget, codes.join("\n")));
}

async function copy(btn, text) {
  await navigator.clipboard.writeText(text);
  btn.innerHTML = `${icon("check")}<span>${t("common.copied")}</span>`;
  loadIcons(btn);
}

export { renderSecurityPage };
