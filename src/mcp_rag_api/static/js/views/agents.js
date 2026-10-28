/* Tela de agents — perfis de agentes com system prompt gerado, escopos e autonomia. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtDate, fmtAgo, modalShell, bindModal, uiConfirm, uiAlert, uiError, disclosure } from "../ui.js";
import { state, colorFor } from "../state.js";
import { accessChips, accessPreset, accessPresetsHtml, scopesBlockHtml, bindAccessPicker, readScopes } from "../access.js";

/* ---------------------------------------------------------------- tela: agents */

const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,62}$/;

const slugify = (s) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);

const lines = (s) =>
  s
    .split("\n")
    .map((l) => l.replace(/^[-•*]\s*/, "").trim())
    .filter(Boolean);

// mesmo roteiro do prompt design_agent do MCP: objetivo, tom, sempre/nunca, coleções
function buildSystemPrompt(f) {
  const parts = [t("prompt.youAre", { name: f.name || t("prompt.anAgent") }) + (f.goal ? " " + f.goal.trim() : "")];
  if (f.tone.trim()) parts.push(t("prompt.tone", { tone: f.tone.trim() }));
  const always = lines(f.always);
  const never = lines(f.never);
  if (always.length) parts.push(t("prompt.always") + "\n" + always.map((l) => `- ${l}`).join("\n"));
  if (never.length) parts.push(t("prompt.never") + "\n" + never.map((l) => `- ${l}`).join("\n"));
  const scope = f.collections.length ? t("prompt.scopeCollections", { collections: f.collections.join(", ") }) : t("prompt.scopeBase");
  parts.push(t("prompt.search", { scope }) + t("prompt.memory"));
  return parts.join("\n\n");
}

// Formulário em linguagem simples (quem é, como se comporta, o que pode acessar). Identificador, instruções
// completas, escopos da API e autonomia ficam no "Ver mais" — slug e prompt são montados sozinhos.
function agentCreateForm(collections) {
  return `
    ${modalShell({
      id: "agent",
      iconName: "bot",
      title: t("agents.new"),
      subtitle: t("agents.newSub"),
      submitLabel: t("agents.create"),
      submitIcon: "bot",
      body: `
      <div class="form-section">
        <div class="form-section-title"><span class="step">1</span>${t("agents.identity")}</div>
        <div class="create-grid">
          <label class="form-field">
            <span class="form-label">${t("common.name")}</span>
            <input class="input" id="a-name" placeholder="${t("agents.namePh")}" required />
            <span class="form-help">${t("agents.nameHelp")}</span>
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.desc")}</span>
            <input class="input" id="a-desc" placeholder="${t("agents.descPh")}" />
          </label>
        </div>
      </div>

      <div class="form-section">
        <div class="form-section-title"><span class="step">2</span>${t("agents.behavior")}</div>
        <div class="create-grid">
          <label class="form-field">
            <span class="form-label">${t("agents.goal")}</span>
            <textarea class="input" id="a-goal" rows="3" placeholder="${t("agents.goalPh")}"></textarea>
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.tone")}</span>
            <textarea class="input" id="a-tone" rows="3">${t("agents.toneDefault")}</textarea>
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.always")} <span class="muted">${t("agents.onePerLine")}</span></span>
            <textarea class="input" id="a-always" rows="3" placeholder="${t("agents.alwaysPh")}"></textarea>
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.never")} <span class="muted">${t("agents.onePerLine")}</span></span>
            <textarea class="input" id="a-never" rows="3" placeholder="${t("agents.neverPh")}"></textarea>
          </label>
        </div>
      </div>

      <div class="form-section">
        <div class="form-section-title"><span class="step">3</span>${t("agents.access")}</div>
        <span class="form-label">${t("agents.collections")} <span class="muted">${t("agents.collectionsHint")}</span></span>
        <div class="chip-picks" id="a-cols">
          ${
            collections.length
              ? collections
                  .map(
                    (c) => `
              <label class="chip-pick"><input type="checkbox" value="${escHtml(c.name)}" />
                <span class="dot" style="background:${colorFor(c.name)}"></span>${escHtml(c.name)}<span class="muted">${c.documents}</span>
              </label>`,
                  )
                  .join("")
              : `<span class="muted">${t("agents.noCollections")}</span>`
          }
        </div>
        <span class="form-label">${t("agents.permissions")}</span>
        ${accessPresetsHtml("a", "write")}
      </div>

      ${disclosure({
        id: "agent-advanced",
        hint: t("agents.advancedHint"),
        body: `
        <label class="form-field">
          <span class="form-label">${t("agents.slug")} <span class="muted">${t("agents.slugHint")}</span></span>
          <input class="input mono" id="a-slug" placeholder="${t("agents.slugPh")}" />
        </label>
        <label class="form-field">
          <span class="form-label prompt-label">${t("agents.prompt")}
            <span class="muted" id="a-prompt-state">${t("agents.promptAuto")}</span>
            <button class="link-btn hidden" type="button" id="a-prompt-regen">${t("agents.regen")}</button>
          </span>
          <textarea class="input mono prompt-box" id="a-prompt" rows="9"></textarea>
        </label>
        ${scopesBlockHtml("a", ["read", "write"])}
        <label class="scope-option toggle-option">
          <input type="checkbox" id="a-auto" />
          <span><span>${t("agents.autonomy")}</span><span class="muted">${t("agents.autonomyDesc")}</span></span>
        </label>`,
      })}`,
    })}`;
}

function agentCard(a, isAdmin) {
  const archived = a.status !== "active";
  return `
    <div class="agent-card ${archived ? "revoked" : ""}">
      <div class="agent-head">
        <span class="row-icon">${icon("bot")}</span>
        <div class="grow">
          <div class="row-title">${escHtml(a.name)} <span class="chip subtle mono">${escHtml(a.slug)}</span>
            ${archived ? `<span class="chip danger-chip">${t("agents.archived")}</span>` : ""}
            ${a.proposals ? `<span class="chip accent">${icon("git-pull-request-arrow", 12)} ${t("agents.proposals", { count: a.proposals })}</span>` : ""}
          </div>
          ${a.description ? `<div class="agent-desc">${escHtml(a.description)}</div>` : ""}
        </div>
        ${
          isAdmin && !archived
            ? `<div class="agent-actions">
                 <button class="btn sm ghost" data-autonomy="${escHtml(a.slug)}" data-on="${a.auto_apply_updates}" title="${t("agents.autonomyBtnTitle")}">
                   ${icon("zap", 14)}<span>${a.auto_apply_updates ? t("agents.autonomyOn") : t("agents.autonomyOff")}</span>
                 </button>
                 <button class="btn sm" type="button" data-manage="${escHtml(a.slug)}" data-tab="keys" title="${t("agents.keys")}">${icon("key-round", 14)}<span>${t("agents.keys")}</span></button>
                 <button class="btn sm" type="button" data-manage="${escHtml(a.slug)}" data-tab="profile" title="${t("agents.edit")}">${icon("pencil", 14)}<span>${t("agents.edit")}</span></button>
               </div>`
            : `<span class="muted">${a.auto_apply_updates ? t("agents.autonomous") : t("agents.underReview")}</span>`
        }
      </div>
      <div class="agent-meta">
        <span>${accessChips(a.scopes)}</span>
        <span>${
          a.allowed_collections.length
            ? a.allowed_collections.map((c) => `<span class="chip"><span class="dot" style="background:${colorFor(c)}"></span>${escHtml(c)}</span>`).join(" ")
            : `<span class="chip subtle">${t("agents.allCollections")}</span>`
        }</span>
        <span class="grow"></span>
        <span class="muted">${icon("key-round", 12)} ${t("agents.keysCount", { count: a.active_keys })}</span>
        <span class="muted mono">v${a.version}</span>
        <span class="muted" title="${fmtDate(a.updated_at)}">${t("agents.updatedAgo", { ago: fmtAgo(a.updated_at) })}</span>
      </div>
    </div>`;
}

async function renderAgentsList(created) {
  const body = document.getElementById("page-body");
  const isAdmin = state.user.role === "admin";
  let list, collections;
  try {
    [list, collections] = await Promise.all([api.get("/agents"), isAdmin ? api.get("/collections") : []]);
  } catch {
    body.innerHTML = `<div class="results-empty">${t("agents.loadFailed")}</div>`;
    return;
  }
  agentsState.collections = collections;
  const active = list.filter((a) => a.status === "active").length;
  body.innerHTML = `
    <div class="page-col">
      <div class="section-head">
        <span class="muted">${t("agents.counts", { active, archived: list.length - active })}</span>
        ${isAdmin ? `<button class="btn primary" id="agent-new">${icon("plus")}<span>${t("agents.new")}</span></button>` : ""}
      </div>
      ${isAdmin ? agentCreateForm(collections) : ""}
      <div id="agent-banner"></div>
      <div class="agent-list">
        ${
          list.map((a) => agentCard(a, isAdmin)).join("") ||
          `<div class="results-empty">
             <div class="results-empty-title">${t("agents.emptyTitle")}</div>
             ${isAdmin ? t("agents.emptyAdmin", { prompt: '<span class="mono">design_agent</span>' }) : t("agents.emptyViewer")}
           </div>`
        }
      </div>
    </div>`;
  await loadIcons(body);
  if (isAdmin) bindAgentCreate();
  if (created) showAgentBanner(created);
  // #page-body sobrevive aos re-renders da lista: liga o handler uma vez só
  if (body.dataset.agentsBound) return;
  body.dataset.agentsBound = "1";
  body.addEventListener("click", async (e) => {
    const manage = e.target.closest("[data-manage]");
    if (manage) {
      await openAgentManage(manage.dataset.manage, manage.dataset.tab);
      return;
    }
    const btn = e.target.closest("[data-autonomy]");
    if (!btn) return;
    const turnOn = btn.dataset.on !== "true";
    const ok = await uiConfirm({
      title: t(turnOn ? "agents.autonomyConfirmOn" : "agents.autonomyConfirmOff", { slug: btn.dataset.autonomy }),
      message: turnOn ? t("agents.autonomyMsgOn") : t("agents.autonomyMsgOff"),
      confirmLabel: turnOn ? t("agents.autonomyTurnOn") : t("agents.autonomyTurnOff"),
      tone: turnOn ? "danger" : "default",
      iconName: "zap",
    });
    if (!ok) return;
    try {
      await api.post(`/agents/${btn.dataset.autonomy}/autonomy`, { auto_apply_updates: turnOn });
      await renderAgentsList();
    } catch (err) {
      await uiError(t("agents.autonomyFailed"), err);
    }
  });
}

function bindAgentCreate() {
  const form = document.getElementById("agent-form");
  const $ = (id) => document.getElementById(id);
  let slugTouched = false;
  let promptTouched = false;

  const fields = () => ({
    name: $("a-name").value.trim(),
    goal: $("a-goal").value,
    tone: $("a-tone").value,
    always: $("a-always").value,
    never: $("a-never").value,
    collections: [...form.querySelectorAll("#a-cols input:checked")].map((i) => i.value),
  });
  const syncPrompt = () => {
    if (!promptTouched) $("a-prompt").value = buildSystemPrompt(fields());
  };
  const setPromptTouched = (v) => {
    promptTouched = v;
    $("a-prompt-state").textContent = v ? t("agents.promptManual") : t("agents.promptAuto");
    $("a-prompt-regen").classList.toggle("hidden", !v);
  };
  const setOpen = bindModal("agent", { trigger: $("agent-new"), onOpen: syncPrompt });
  bindAccessPicker(form, "a");

  $("a-name").addEventListener("input", () => {
    if (!slugTouched) $("a-slug").value = slugify($("a-name").value);
  });
  $("a-slug").addEventListener("input", () => (slugTouched = true));
  form.addEventListener("input", (e) => {
    if (e.target.id === "a-prompt") setPromptTouched(true);
    else syncPrompt();
  });
  $("a-prompt-regen").addEventListener("click", () => {
    setPromptTouched(false);
    syncPrompt();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const scopes = readScopes(form, "a");
    if (!scopes.length) {
      await uiAlert(t("agents.pickScopeTitle"), t("agents.pickScopeMsg"));
      return;
    }
    // slug e prompt vivem no "Ver mais" (fechado por padrão): validação manual, abrindo o bloco se precisar
    if (!SLUG_RE.test($("a-slug").value.trim())) {
      $("agent-advanced").open = true;
      await uiAlert(t("agents.slugInvalidTitle"), t("agents.slugInvalidMsg"));
      $("a-slug").focus();
      return;
    }
    if (!$("a-prompt").value.trim()) {
      setPromptTouched(false);
      syncPrompt();
    }
    const f = fields();
    try {
      const created = await api.post("/agents", {
        slug: $("a-slug").value.trim(),
        name: f.name,
        description: $("a-desc").value.trim() || null,
        system_prompt: $("a-prompt").value.trim(),
        allowed_collections: f.collections,
        scopes,
        auto_apply_updates: $("a-auto").checked,
      });
      setOpen(false);
      await renderAgentsList(created);
    } catch (err) {
      await uiError(t("agents.createFailed"), err);
    }
  });
}

/* ---------------------------------------------------------------- gerenciar agente (plan-web-16) */

const agentsState = { collections: [] };

// botão "Copiar" das linhas de segredo (chave, comando de conexão)
const copyRow = (id, value) => `
  <div class="key-reveal-row">
    <code class="mono">${escHtml(value)}</code>
    <button class="btn sm" type="button" data-copy="${id}">${icon("copy")}<span>${t("common.copy")}</span></button>
  </div>`;

function bindCopy(box, values) {
  box.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn || !(btn.dataset.copy in values)) return;
    await navigator.clipboard.writeText(values[btn.dataset.copy]);
    btn.innerHTML = `${icon("check")}<span>${t("common.copied")}</span>`;
    loadIcons(btn);
  });
}

function manageProfileHtml(a) {
  const preset = accessPreset(a.scopes)?.id ?? null;
  const cols = agentsState.collections;
  return `
    <div class="create-grid">
      <label class="form-field">
        <span class="form-label">${t("common.name")}</span>
        <input class="input" id="e-name" value="${escHtml(a.name)}" required />
      </label>
      <label class="form-field">
        <span class="form-label">${t("agents.desc")}</span>
        <input class="input" id="e-desc" value="${escHtml(a.description || "")}" placeholder="${t("agents.descPh")}" />
      </label>
    </div>
    <div class="form-field">
      <span class="form-label">${t("agents.collections")} <span class="muted">${t("agents.collectionsHint")}</span></span>
      <div class="chip-picks" id="e-cols">
        ${
          cols.length
            ? cols
                .map(
                  (c) => `
            <label class="chip-pick"><input type="checkbox" value="${escHtml(c.name)}" ${a.allowed_collections.includes(c.name) ? "checked" : ""} />
              <span class="dot" style="background:${colorFor(c.name)}"></span>${escHtml(c.name)}<span class="muted">${c.documents}</span>
            </label>`,
                )
                .join("")
            : `<span class="muted">${t("agents.noCollections")}</span>`
        }
      </div>
    </div>
    <div class="form-field">
      <span class="form-label">${t("agents.permissions")}</span>
      ${accessPresetsHtml("e", preset)}
    </div>
    ${disclosure({
      id: "e-advanced",
      hint: t("agents.advancedHintEdit"),
      body: `
      <label class="form-field">
        <span class="form-label">${t("agents.slug")}</span>
        <input class="input mono" value="${escHtml(a.slug)}" readonly />
        <span class="form-help">${t("agents.slugFixed")}</span>
      </label>
      <label class="form-field">
        <span class="form-label">${t("agents.prompt")}</span>
        <textarea class="input mono prompt-box" id="e-prompt" rows="10">${escHtml(a.system_prompt)}</textarea>
      </label>
      ${scopesBlockHtml("e", a.scopes)}
      <label class="scope-option toggle-option">
        <input type="checkbox" id="e-auto" ${a.auto_apply_updates ? "checked" : ""} />
        <span><span>${t("agents.autonomy")}</span><span class="muted">${t("agents.autonomyDesc")}</span></span>
      </label>`,
    })}
    <div class="danger-zone">
      <div class="grow">
        <strong>${t("agents.archiveZoneTitle")}</strong>
        <span class="muted">${t("agents.archiveZoneDesc")}</span>
      </div>
      <button class="btn sm danger" type="button" id="e-archive">${icon("archive", 14)}<span>${t("agents.archive")}</span></button>
    </div>`;
}

function manageKeysHtml(keys) {
  const active = keys.filter((k) => !k.revoked_at);
  const revoked = keys.filter((k) => k.revoked_at);
  const row = (k) => `
    <div class="trow agent-keys ${k.revoked_at ? "revoked" : ""}">
      <div class="tcell-main">
        <span class="row-icon">${icon("key-round")}</span>
        <div class="grow"><div class="row-title">${escHtml(k.label || "—")}</div><div class="mono muted">${escHtml(k.prefix)}…</div></div>
      </div>
      <div class="tcell" title="${fmtDate(k.created_at)}">${t("agents.keyCreatedAgo", { ago: fmtAgo(k.created_at) })}</div>
      <div class="tcell ${k.last_used_at ? "" : "muted"}" title="${k.last_used_at ? fmtDate(k.last_used_at) : ""}">${
        k.revoked_at ? t("keys.revokedAgo", { ago: fmtAgo(k.revoked_at) }) : k.last_used_at ? t("agents.keyLastUsed", { ago: fmtAgo(k.last_used_at) }) : t("keys.neverUsed")
      }</div>
      <div class="tcell-actions">${
        k.revoked_at
          ? ""
          : `<button class="btn sm ghost" type="button" data-key-renew="${k.id}" title="${t("keys.renewTitle")}">${icon("refresh-cw", 14)}<span>${t("keys.renew")}</span></button>
             <button class="btn sm ghost danger-text" type="button" data-key-revoke="${k.id}" title="${t("keys.revokeTitle")}">${icon("ban", 14)}<span>${t("keys.revoke")}</span></button>`
      }</div>
    </div>`;
  return `
    <div class="section-head">
      <span class="muted">${t("agents.keysIntro")}</span>
      <button class="btn sm primary" type="button" id="e-key-new">${icon("plus", 14)}<span>${t("agents.newKey")}</span></button>
    </div>
    <div id="e-key-reveal"></div>
    ${active.length ? `<div class="data-table">${active.map(row).join("")}</div>` : `<div class="results-empty compact">${t("agents.noKeys")}</div>`}
    ${
      revoked.length
        ? `<details class="revoked-block"><summary>${icon("history", 14)} ${t("keys.revokedBlock", { count: revoked.length })}</summary>
             <div class="data-table">${revoked.map(row).join("")}</div></details>`
        : ""
    }`;
}

function manageHistoryHtml(history) {
  if (!history.length) return `<div class="results-empty compact">${t("agents.historyEmpty")}</div>`;
  return `<div class="data-table">${history
    .map(
      (v) => `
    <div class="trow agent-history">
      <div class="tcell"><span class="chip subtle mono">v${v.version ?? "—"}</span></div>
      <div class="tcell" title="${escHtml(v.change_note || "")}">${escHtml(v.change_note || "—")}</div>
      <div class="tcell muted" title="${fmtDate(v.created_at)}">${escHtml((v.proposed_by || "—").replace(/^dash:/, ""))} · ${fmtAgo(v.created_at)}</div>
      <div class="tcell"><span class="chip ${v.status === "applied" ? "" : "accent"}">${t(`agents.status.${v.status}`)}</span></div>
    </div>`,
    )
    .join("")}</div>`;
}

async function openAgentManage(slug, tab = "profile") {
  let detail;
  try {
    detail = await api.get(`/agents/${slug}`);
  } catch (err) {
    await uiError(t("agents.loadDetailFailed"), err);
    return;
  }
  const a = detail.agent;
  document.getElementById("agent-manage-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "agent-manage",
    iconName: "bot",
    title: escHtml(a.name),
    subtitle: t("agents.manageSub", { slug: `<span class="mono">${escHtml(a.slug)}</span>`, version: a.version }),
    submitLabel: t("agents.save"),
    submitIcon: "check",
    width: 820,
    body: `
      <div class="seg manage-tabs" role="tablist">
        <button type="button" role="tab" data-tab="profile">${t("agents.tabProfile")}</button>
        <button type="button" role="tab" data-tab="keys">${t("agents.tabKeys")} <span class="muted">${detail.api_keys.filter((k) => !k.revoked_at).length}</span></button>
        <button type="button" role="tab" data-tab="history">${t("agents.tabHistory")}</button>
      </div>
      <div class="manage-panel" data-panel="profile">${manageProfileHtml(a)}</div>
      <div class="manage-panel" data-panel="keys">${manageKeysHtml(detail.api_keys)}</div>
      <div class="manage-panel" data-panel="history">${manageHistoryHtml(detail.history)}</div>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("agent-manage-modal");
  const form = modal.querySelector("#agent-manage-form");
  await loadIcons(modal);
  const setOpen = bindModal("agent-manage");
  bindAccessPicker(form, "e");
  let changedSomething = false; // chave criada/revogada ou perfil salvo: a lista precisa ser relida ao fechar

  const submit = form.querySelector("button[type=submit]");
  const cancel = form.querySelector(".modal-foot [data-close-modal]");
  const showTab = (name) => {
    cancel.textContent = name === "profile" ? t("common.cancel") : t("common.close"); // fora do perfil não há o que cancelar
    modal.querySelectorAll(".manage-tabs [data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    modal.querySelectorAll(".manage-panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== name));
    submit.classList.toggle("hidden", name !== "profile"); // salvar só faz sentido no perfil
  };
  showTab(tab);
  modal.querySelector(".manage-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) showTab(b.dataset.tab);
  });

  const reloadKeys = async () => {
    const fresh = await api.get(`/agents/${slug}`);
    modal.querySelector('[data-panel="keys"]').innerHTML = manageKeysHtml(fresh.api_keys);
    modal.querySelector('.manage-tabs [data-tab="keys"] .muted').textContent = fresh.api_keys.filter((k) => !k.revoked_at).length;
    await loadIcons(modal.querySelector('[data-panel="keys"]'));
  };
  const reveal = async (titleKey, apiKey, command) => {
    await reloadKeys();
    const box = modal.querySelector("#e-key-reveal");
    box.innerHTML = `
      <div class="key-reveal">
        <div class="key-reveal-head">${icon("check")}<strong>${t(titleKey)}</strong><span class="muted">${t("agents.copyNow")}</span></div>
        <span class="form-label">${t("agents.apiKey")}</span>
        ${copyRow("key", apiKey)}
        ${command ? `<span class="form-label">${t("agents.connectClaudeCode")}</span>${copyRow("cmd", command)}` : ""}
      </div>`;
    await loadIcons(box);
    bindCopy(box, { key: apiKey, ...(command ? { cmd: command } : {}) });
  };

  modal.querySelector('[data-panel="keys"]').addEventListener("click", async (e) => {
    const create = e.target.closest("#e-key-new");
    const renew = e.target.closest("[data-key-renew]");
    const revoke = e.target.closest("[data-key-revoke]");
    try {
      if (create) {
        const key = await api.post(`/agents/${slug}/keys`, {});
        changedSomething = true;
        await reveal("agents.keyCreated", key.api_key, key.connect_command);
      } else if (renew) {
        const ok = await uiConfirm({ title: t("keys.renewConfirmTitle"), message: t("keys.renewConfirmMsg"), confirmLabel: t("keys.renew"), iconName: "refresh-cw" });
        if (!ok) return;
        const key = await api.post(`/keys/${renew.dataset.keyRenew}/renew`);
        changedSomething = true;
        await reveal("agents.keyRenewed", key.api_key, null);
      } else if (revoke) {
        const ok = await uiConfirm({ title: t("keys.revokeConfirmTitle"), message: t("keys.revokeConfirmMsg"), confirmLabel: t("keys.revoke"), tone: "danger" });
        if (!ok) return;
        await api.post(`/keys/${revoke.dataset.keyRevoke}/revoke`);
        changedSomething = true;
        await reloadKeys();
      }
    } catch (err) {
      await uiError(create ? t("agents.issueFailed") : t("common.opFailed"), err);
    }
  });

  modal.querySelector("#e-archive").addEventListener("click", async () => {
    const ok = await uiConfirm({
      title: t("agents.archiveConfirmTitle", { name: a.name }),
      message: t("agents.archiveConfirmMsg"),
      confirmLabel: t("agents.archive"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api.post(`/agents/${slug}/archive`, {});
      changedSomething = true;
      setOpen(false);
    } catch (err) {
      await uiError(t("agents.archiveFailed"), err);
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const scopes = readScopes(form, "e");
    if (!scopes.length) {
      await uiAlert(t("agents.pickScopeTitle"), t("agents.pickScopeMsg"));
      return;
    }
    const prompt = form.querySelector("#e-prompt").value.trim();
    if (!prompt) {
      form.querySelector("#e-advanced").open = true;
      await uiAlert(t("agents.promptEmptyTitle"), t("agents.promptEmptyMsg"));
      return;
    }
    // só o que mudou: cada PATCH vira uma versão nova do agente
    const next = {
      name: form.querySelector("#e-name").value.trim(),
      description: form.querySelector("#e-desc").value.trim(),
      system_prompt: prompt,
      allowed_collections: [...form.querySelectorAll("#e-cols input:checked")].map((i) => i.value),
      scopes,
      auto_apply_updates: form.querySelector("#e-auto").checked,
    };
    const same = (x, y) => JSON.stringify([...x].sort()) === JSON.stringify([...y].sort());
    const patch = {};
    if (next.name !== a.name) patch.name = next.name;
    if (next.description !== (a.description || "")) patch.description = next.description;
    if (next.system_prompt !== a.system_prompt.trim()) patch.system_prompt = next.system_prompt;
    if (!same(next.allowed_collections, a.allowed_collections)) patch.allowed_collections = next.allowed_collections;
    if (!same(next.scopes, a.scopes)) patch.scopes = next.scopes;
    if (next.auto_apply_updates !== a.auto_apply_updates) patch.auto_apply_updates = next.auto_apply_updates;
    try {
      if (Object.keys(patch).length) {
        await api.patch(`/agents/${slug}`, patch);
        changedSomething = true;
      }
      setOpen(false);
    } catch (err) {
      await uiError(t("agents.saveFailed"), err);
    }
  });

  // qualquer fechamento (salvar, arquivar, ×, Esc, Cancelar) passa por aqui: remove o modal e,
  // se algo mudou (perfil, chaves), relê a lista para os cards refletirem
  const observer = new MutationObserver(() => {
    if (modal.classList.contains("hidden")) {
      observer.disconnect();
      modal.remove();
      if (changedSomething) renderAgentsList();
    }
  });
  observer.observe(modal, { attributes: true, attributeFilter: ["class"] });
  setOpen(true);
}

function showAgentBanner(created) {
  const box = document.getElementById("agent-banner");
  box.innerHTML = `
    <div class="key-reveal">
      <div class="key-reveal-head">
        ${icon("check")}<strong>${t("agents.createdTitle", { name: escHtml(created.agent.name) })}</strong>
        <span class="muted">${t("agents.copyNow")}</span>
      </div>
      <span class="form-label">${t("agents.apiKey")}</span>
      ${copyRow("key", created.api_key)}
      <span class="form-label">${t("agents.connectClaudeCode")}</span>
      ${copyRow("cmd", created.connect_command)}
      <span class="muted">${t("agents.nextStep", { prompt: '<span class="mono">start_as_agent</span>', slug: `<span class="mono">${escHtml(created.agent.slug)}</span>` })}</span>
    </div>`;
  loadIcons(box);
  bindCopy(box, { key: created.api_key, cmd: created.connect_command });
}

export { renderAgentsList };
