/* 2ª etapa do login com 2FA — código do app autenticador ou um recovery code.
   Usada pelo login da dashboard (main.js) e pela tela "Autorizar" do OAuth (oauth-consent.js). */
import { t } from "./i18n.js";
import { icon, loadIcons } from "./icons.js";

function mfaStepHtml() {
  return `
    <form id="mfa-form" class="mfa-form">
      <div class="consent-badge">${icon("shield-check", 24)}</div>
      <p class="muted" id="mfa-intro">${t("mfa.loginIntro")}</p>
      <label class="form-label" for="mfa-code" id="mfa-label">${t("mfa.code")}</label>
      <input class="input mfa-code" id="mfa-code" type="text" inputmode="numeric" autocomplete="one-time-code"
        pattern="[0-9 ]*" maxlength="7" placeholder="123456" required />
      <div class="form-error hidden" id="mfa-error"></div>
      <button class="btn primary" type="submit">${icon("log-in")}<span>${t("mfa.verify")}</span></button>
      <div class="mfa-links">
        <button class="btn ghost sm" type="button" id="mfa-recovery">${t("mfa.useRecovery")}</button>
        <button class="btn ghost sm" type="button" id="mfa-back">${t("mfa.back")}</button>
      </div>
    </form>`;
}

/* post(body) faz o POST /dash/api/auth/login/mfa e devolve o usuário; onBack(msg?) volta para a senha. */
function bindMfaStep(root, { mfaToken, post, onDone, onBack }) {
  loadIcons(root);
  const form = root.querySelector("#mfa-form");
  const input = root.querySelector("#mfa-code");
  const error = root.querySelector("#mfa-error");
  let recovery = false;
  input.focus();

  root.querySelector("#mfa-recovery").addEventListener("click", (e) => {
    recovery = !recovery;
    // recovery code é xxxxx-xxxxx (hex): teclado de texto e sem o pattern numérico
    input.value = "";
    input.inputMode = recovery ? "text" : "numeric";
    input.maxLength = recovery ? 11 : 7;
    input.placeholder = recovery ? "a1b2c-3d4e5" : "123456";
    if (recovery) input.removeAttribute("pattern");
    else input.setAttribute("pattern", "[0-9 ]*");
    input.autocomplete = recovery ? "off" : "one-time-code";
    root.querySelector("#mfa-label").textContent = recovery ? t("mfa.recoveryCode") : t("mfa.code");
    root.querySelector("#mfa-intro").textContent = recovery ? t("mfa.recoveryIntro") : t("mfa.loginIntro");
    e.currentTarget.textContent = recovery ? t("mfa.useApp") : t("mfa.useRecovery");
    input.focus();
  });
  root.querySelector("#mfa-back").addEventListener("click", () => onBack());

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.classList.add("hidden");
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const user = await post({ mfa_token: mfaToken, code: input.value });
      await onDone(user);
    } catch (err) {
      button.disabled = false;
      if (err.status === 401) {
        // desafio expirou ou estourou as tentativas: recomeça pela senha
        onBack(t("mfa.expired"));
        return;
      }
      error.textContent = err.status === 429 ? err.detail : t("mfa.invalid");
      error.classList.remove("hidden");
      input.select();
    }
  });
}

export { mfaStepHtml, bindMfaStep };
