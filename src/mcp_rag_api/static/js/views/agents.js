/* Tela de agents — perfis de agentes com system prompt gerado, escopos e autonomia. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtDate, fmtAgo, modalShell, bindModal, uiConfirm, uiAlert, uiError } from "../ui.js";
import { state, colorFor } from "../state.js";

/* ---------------------------------------------------------------- tela: agents */

const AGENT_SCOPES = [
  ["read", "scope.read"],
  ["write", "scope.write"],
  ["agents:manage", "scope.agentsManageAgent"],
  ["admin", "scope.admin"],
];

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

function agentCreateForm(collections) {
  return `
    ${modalShell({
      id: "agent",
      iconName: "bot",
      title: t("agents.new"),
      subtitle: t("agents.newSub", { tool: '<span class="mono">create_agent</span>' }),
      submitLabel: t("agents.create"),
      submitIcon: "bot",
      body: `
      <div class="form-section">
        <div class="form-section-title"><span class="step">1</span>${t("agents.identity")}</div>
        <div class="create-grid three">
          <label class="form-field">
            <span class="form-label">${t("common.name")}</span>
            <input class="input" id="a-name" placeholder="${t("agents.namePh")}" required />
          </label>
          <label class="form-field">
            <span class="form-label">${t("agents.slug")} <span class="muted">${t("agents.slugHint")}</span></span>
            <input class="input mono" id="a-slug" placeholder="${t("agents.slugPh")}" pattern="[a-z0-9][a-z0-9-]{1,62}" required />
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
        <label class="form-field">
          <span class="form-label prompt-label">System prompt
            <span class="muted" id="a-prompt-state">${t("agents.promptAuto")}</span>
            <button class="link-btn hidden" type="button" id="a-prompt-regen">${t("agents.regen")}</button>
          </span>
          <textarea class="input mono prompt-box" id="a-prompt" rows="9" required></textarea>
        </label>
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
        <div class="scope-grid" id="a-scopes">
          ${AGENT_SCOPES.map(
            ([s, desc]) => `
            <label class="scope-option">
              <input type="checkbox" value="${s}" ${s === "read" || s === "write" ? "checked" : ""} />
              <span><span class="mono">${s}</span><span class="muted">${t(desc)}</span></span>
            </label>`,
          ).join("")}
        </div>
        <label class="scope-option toggle-option">
          <input type="checkbox" id="a-auto" />
          <span><span>${t("agents.autonomy")}</span><span class="muted">${t("agents.autonomyDesc")}</span></span>
        </label>
      </div>`,
    })}`;
}

function agentCard(a, isAdmin) {
  const archived = a.status !== "active";
  return `
    <div class="agent-card ${archived ? "revoked" : ""}">
      <div class="agent-head">
        <span class="row-icon">${icon("bot")}</span>
        <div class="grow">
          <div class="row-title">${escHtml(a.name)} <span class="muted mono">${escHtml(a.slug)}</span>
            ${archived ? `<span class="chip danger-chip">${t("agents.archived")}</span>` : ""}
            ${a.proposals ? `<span class="chip accent">${icon("git-pull-request-arrow", 12)} ${t("agents.proposals", { count: a.proposals })}</span>` : ""}
          </div>
          ${a.description ? `<div class="muted">${escHtml(a.description)}</div>` : ""}
        </div>
        ${
          isAdmin && !archived
            ? `<button class="btn sm ${a.auto_apply_updates ? "primary" : "ghost"}" data-autonomy="${escHtml(a.slug)}" data-on="${a.auto_apply_updates}"
                 title="${t("agents.autonomyBtnTitle")}">
                 ${icon("zap", 14)}<span>${a.auto_apply_updates ? t("agents.autonomyOn") : t("agents.autonomyOff")}</span>
               </button>`
            : `<span class="muted">${a.auto_apply_updates ? t("agents.autonomous") : t("agents.underReview")}</span>`
        }
      </div>
      <div class="agent-meta">
        <span>${a.scopes.map((s) => `<span class="chip ${s === "admin" ? "accent" : ""}">${s}</span>`).join(" ")}</span>
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
    const scopes = [...form.querySelectorAll("#a-scopes input:checked")].map((i) => i.value);
    if (!scopes.length) {
      await uiAlert(t("agents.pickScopeTitle"), t("agents.pickScopeMsg"));
      return;
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

function showAgentBanner(created) {
  const box = document.getElementById("agent-banner");
  const copyRow = (id, value) => `
    <div class="key-reveal-row">
      <code class="mono">${escHtml(value)}</code>
      <button class="btn sm" type="button" data-copy="${id}">${icon("copy")}<span>${t("common.copy")}</span></button>
    </div>`;
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
  const values = { key: created.api_key, cmd: created.connect_command };
  box.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    await navigator.clipboard.writeText(values[btn.dataset.copy]);
    btn.innerHTML = `${icon("check")}<span>${t("common.copied")}</span>`;
    loadIcons(btn);
  });
}

export { renderAgentsList };
