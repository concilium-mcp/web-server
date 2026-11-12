/* Integrações (plan-web-37d) — conectar contas de outros Kanbans (Trello agora, Notion na fase E).
   Só admin. As credenciais vão cifradas para o servidor e nunca voltam para a tela. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtAgo, uiConfirm, uiError, helpTip } from "../ui.js";

async function renderIntegrations(main) {
  main.innerHTML = `
    <section class="page integrations-page">
      <header class="page-header"><h1>${icon("zap", 18)} ${t("integrations.title")}</h1><span class="sub">${t("integrations.sub")}</span></header>
      <div class="boards-col" id="int-body"><div class="muted">${t("common.loading")}</div></div>
    </section>`;
  await loadIcons(main);
  await paint();
}

async function paint() {
  const body = document.getElementById("int-body");
  let data;
  try {
    data = await api.get("/integrations");
  } catch (err) {
    body.innerHTML = `<div class="form-error">${escHtml(err.detail || err.message)}</div>`;
    return;
  }
  const trelloAcc = data.items.find((i) => i.provider === "trello");
  body.innerHTML = `
    ${data.encryption ? "" : `<div class="int-warn">${icon("triangle-alert", 16)}<span>${t("integrations.noKey")}</span></div>`}
    ${data.webhooks ? "" : `<div class="int-warn soft">${icon("triangle-alert", 16)}<span>${t("integrations.noWebhooks")}</span></div>`}
    <article class="int-card">
      <header class="int-head">
        <span class="int-logo trello" aria-hidden="true"><span></span><span></span></span>
        <div class="grow">
          <h2>Trello</h2>
          <span class="muted">${t("integrations.trello.desc")}</span>
        </div>
        ${trelloAcc ? `<span class="status ok"><span class="status-dot"></span>${t("integrations.connected")}</span>` : ""}
      </header>
      ${
        trelloAcc
          ? `<div class="int-acc">
              <div><span class="muted">${t("integrations.account")}</span> <b>${escHtml(trelloAcc.account?.name || trelloAcc.label)}</b>
                ${trelloAcc.account?.username ? `<span class="muted">@${escHtml(trelloAcc.account.username)}</span>` : ""}</div>
              <div class="muted">${t("integrations.boardsLinked", { count: trelloAcc.boards })} · ${t("integrations.lastOk", { when: fmtAgo(trelloAcc.last_ok_at) })}</div>
              ${trelloAcc.last_error ? `<div class="form-error">${escHtml(trelloAcc.last_error)}</div>` : ""}
              <div class="int-actions">
                <button class="btn sm" id="int-trello-edit">${icon("key-round", 14)}<span>${t("integrations.replace")}</span></button>
                <button class="btn sm ghost danger-text" id="int-trello-off">${icon("ban", 14)}<span>${t("integrations.disconnect")}</span></button>
              </div>
            </div>`
          : ""
      }
      <form class="int-form ${trelloAcc ? "hidden" : ""}" id="int-trello-form" autocomplete="off">
        <p class="muted">${t("integrations.trello.how")}</p>
        <ol class="int-steps">
          <li>${t("integrations.trello.step1")} <a href="https://trello.com/power-ups/admin" target="_blank" rel="noopener noreferrer">trello.com/power-ups/admin ${icon("external-link", 12)}</a></li>
          <li>${t("integrations.trello.step2")}</li>
          <li>${t("integrations.trello.step3")}</li>
        </ol>
        <div class="form-grid2">
          <div><label class="form-label" for="tr-key">API key</label><input class="input" id="tr-key" required spellcheck="false" /></div>
          <div><label class="form-label" for="tr-secret">Secret ${helpTip(t("integrations.trello.secretHelp"))}</label><input class="input" id="tr-secret" type="password" required spellcheck="false" /></div>
        </div>
        <label class="form-label" for="tr-token">Token ${helpTip(t("integrations.trello.tokenHelp"))}</label>
        <div class="int-token">
          <input class="input" id="tr-token" type="password" required spellcheck="false" />
          <button class="btn sm ghost" type="button" id="tr-token-link">${icon("external-link", 14)}<span>${t("integrations.trello.getToken")}</span></button>
        </div>
        <div class="form-error hidden" id="tr-error"></div>
        <div class="int-actions"><button class="btn primary" type="submit" ${data.encryption ? "" : "disabled"}>${icon("plug", 14)}<span>${t("integrations.connect")}</span></button></div>
      </form>
    </article>
    <article class="int-card soon">
      <header class="int-head">
        <span class="int-logo notion" aria-hidden="true">N</span>
        <div class="grow"><h2>Notion</h2><span class="muted">${t("integrations.notion.desc")}</span></div>
        <span class="chip subtle">${t("integrations.soon")}</span>
      </header>
    </article>`;
  loadIcons(body);
  document.getElementById("int-trello-edit")?.addEventListener("click", () => document.getElementById("int-trello-form").classList.toggle("hidden"));
  document.getElementById("int-trello-off")?.addEventListener("click", async () => {
    const ok = await uiConfirm({ title: t("integrations.disconnectQ"), message: t("integrations.disconnectHelp"), confirmLabel: t("integrations.disconnect"), tone: "danger" });
    if (!ok) return;
    try {
      await api.del(`/integrations/${trelloAcc.id}`);
      paint();
    } catch (err) {
      uiError(t("integrations.failed"), err);
    }
  });
  document.getElementById("tr-token-link").addEventListener("click", () => {
    const key = document.getElementById("tr-key").value.trim();
    if (!key) return document.getElementById("tr-key").focus();
    const url = `https://trello.com/1/authorize?expiration=never&scope=read,write&response_type=token&name=Concilium&key=${encodeURIComponent(key)}`;
    window.open(url, "_blank", "noopener,noreferrer");
  });
  document.getElementById("int-trello-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("tr-error");
    err.classList.add("hidden");
    const btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    try {
      await api.post("/integrations/trello", {
        key: document.getElementById("tr-key").value,
        token: document.getElementById("tr-token").value,
        secret: document.getElementById("tr-secret").value,
      });
      paint();
    } catch (ex) {
      err.textContent = ex.detail || ex.message;
      err.classList.remove("hidden");
      btn.disabled = false;
    }
  });
}

export { renderIntegrations };
