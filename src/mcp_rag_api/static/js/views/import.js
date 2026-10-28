/* Importação para a base — modal com dois modos e progresso em tempo real.

- Arquivos (plan-web-28): PDF, Word (.docx), Markdown e texto soltos → notas numa coleção/pasta;
- Vault Obsidian (plan-web-13): .zip do vault, pastas viram coleções.

O upload inicia um job no servidor; o poll de 1 s alimenta a barra de progresso.
Fechar o modal durante o import pergunta: continua em segundo plano (o poll segue
silencioso e a árvore de notas é atualizada ao final) ou cancela o job.
Nomes de arquivo são dados não confiáveis: só saem com escHtml/textContent. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, modalShell, uiConfirm, uiAlert, uiError } from "../ui.js";
import { emit } from "../events.js";

const POLL_MS = 1000;
const MAX_POLL_FAILURES = 5;
const FILE_EXTENSIONS = [".pdf", ".docx", ".md", ".markdown", ".txt"];

// job ativo no momento (um por vez na UI): { jobId, phase, timer, fails, doneEmitted, modal }
let active = null;
// destino do modo arquivos: <option>s e parser vêm da tela de notas (mesmos locais do "Nova nota")
let target = null;

/* opts: { locations: html dos <option> "coleção|pasta" (vazio = sem coleção), selected, parseLoc } */
async function openImportModal(opts = {}) {
  target = opts.locations ? opts : null;
  document.getElementById("obsidian-import-modal")?.remove();
  const holder = document.createElement("div");
  const mode = (value, titleKey, descKey, checked, disabled = false) => `
    <label class="scope-option">
      <input type="radio" name="oi-mode" value="${value}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
      <span><span>${t(titleKey)}</span><span class="muted">${t(descKey)}</span></span>
    </label>`;
  holder.innerHTML = modalShell({
    id: "obsidian-import",
    iconName: "folder-input",
    title: t("import.title"),
    subtitle: t("import.subtitle"),
    submitLabel: t("import.start"),
    submitIcon: "folder-input",
    width: 640,
    body: `
      <div class="scope-grid two">
        ${mode("files", "import.modeFiles", target ? "import.modeFilesDesc" : "import.modeFilesNoFolder", Boolean(target), !target)}
        ${mode("vault", "import.modeVault", "import.modeVaultDesc", !target)}
      </div>
      <div data-import-mode="files" class="${target ? "" : "hidden"}">
        <p class="muted">${t("import.filesHelp")}</p>
        <div class="create-grid">
          <label class="form-field">
            <span class="form-label">${t("import.filesLabel")}</span>
            <input class="input" id="oi-files" type="file" multiple accept="${FILE_EXTENSIONS.join(",")}" />
          </label>
          <label class="form-field">
            <span class="form-label">${t("import.destination")}</span>
            <select class="input" id="oi-location">${target?.locations || ""}</select>
          </label>
        </div>
      </div>
      <div data-import-mode="vault" class="${target ? "hidden" : ""}">
        <p class="muted">${t("import.help")}</p>
        <label class="form-field">
          <span class="form-label">${t("import.fileLabel")}</span>
          <input class="input" id="oi-file" type="file" accept=".zip,application/zip" />
        </label>
      </div>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("obsidian-import-modal");
  await loadIcons(modal);
  modal.querySelector("#obsidian-import-form").addEventListener("submit", onStart);
  modal.querySelectorAll("input[name=oi-mode]").forEach((r) =>
    r.addEventListener("change", () => {
      const current = selectedMode(modal);
      modal.querySelectorAll("[data-import-mode]").forEach((el) => el.classList.toggle("hidden", el.dataset.importMode !== current));
    }),
  );
  // fechamento manual (bindModal não tem gancho de "antes de fechar"): durante o import
  // o ×/Esc/clique fora pergunta se continua em segundo plano ou cancela
  modal.querySelectorAll("[data-close-modal]").forEach((b) => b.addEventListener("click", () => void closeRequested()));
  modal.addEventListener("mousedown", (e) => {
    if (e.target === modal) void closeRequested();
  });
  document.addEventListener("keydown", onEsc);
  modal.classList.remove("hidden");
}

const selectedMode = (modal) => modal.querySelector("input[name=oi-mode]:checked")?.value || "vault";

function onEsc(e) {
  if (e.key === "Escape" && !document.querySelector(".dialog-backdrop")) void closeRequested();
}

function closeModal() {
  document.removeEventListener("keydown", onEsc);
  const modal = document.getElementById("obsidian-import-modal");
  modal?.remove();
  if (active?.phase !== "running") active = null;
}

async function closeRequested() {
  if (!active || active.phase !== "running") {
    closeModal();
    return;
  }
  const keep = await uiConfirm({
    title: t("import.closeTitle"),
    message: t("import.closeMsg"),
    confirmLabel: t("import.keepInBackground"),
    cancelLabel: t("import.cancelImport"),
  });
  if (keep) {
    closeModal(); // o poll continua e a árvore de notas é atualizada ao final
  } else {
    await cancelJob();
    closeModal();
  }
}

// monta o FormData do modo escolhido; null = validação falhou (o aviso já foi mostrado)
async function buildUpload(modal) {
  const form = new FormData();
  if (selectedMode(modal) === "files") {
    const files = [...modal.querySelector("#oi-files").files];
    if (!files.length) {
      await uiAlert(t("import.noFilesTitle"), t("import.noFilesMsg"));
      return null;
    }
    const unsupported = files.filter((f) => !FILE_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)));
    if (unsupported.length) {
      await uiAlert(t("import.unsupportedTitle"), t("import.unsupportedMsg", { files: unsupported.map((f) => f.name).join(", ") }));
      return null;
    }
    const { collection, folderId } = target.parseLoc(modal.querySelector("#oi-location").value);
    files.forEach((f) => form.append("files", f));
    form.append("collection", collection);
    if (folderId) form.append("folder_id", folderId);
    return { path: "/import/files", form };
  }
  const file = modal.querySelector("#oi-file").files[0];
  if (!file || !file.name.toLowerCase().endsWith(".zip")) {
    await uiAlert(t("import.notAZipTitle"), t("import.notAZipMsg"));
    return null;
  }
  form.append("file", file);
  return { path: "/import/obsidian", form };
}

async function onStart(e) {
  e.preventDefault();
  const modal = document.getElementById("obsidian-import-modal");
  const upload = await buildUpload(modal);
  if (!upload) return;
  const submit = modal.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    const started = await api.upload(upload.path, upload.form);
    active = { jobId: started.job_id, phase: "running", timer: null, fails: 0, doneEmitted: false };
    renderProgressShell(modal);
    schedulePoll(150); // primeiro poll quase imediato (o job já existe no servidor)
  } catch (err) {
    if (err.status === 409) await uiAlert(t("import.alreadyRunningTitle"), err.detail || t("import.alreadyRunningMsg"));
    else await uiError(t("import.startFailed"), err);
  } finally {
    submit.disabled = false;
  }
}

/* ---------- progresso */

function renderProgressShell(modal) {
  modal.querySelector(".modal-foot").classList.add("hidden");
  modal.querySelector(".modal-body").innerHTML = `
    <div class="import-progress-row">
      <div class="import-progress"><div class="import-progress-bar" id="oi-bar"></div></div>
      <span class="muted" id="oi-count"></span>
    </div>
    <div class="muted import-current" id="oi-current"></div>
    <div class="import-counters">
      <span class="chip" id="oi-notes"></span>
      <span class="chip" id="oi-collections"></span>
      <span class="chip" id="oi-errors-count"></span>
    </div>
    <div id="oi-errors"></div>
    <div class="import-cancel-row">
      <button class="btn sm danger" type="button" id="oi-cancel">${icon("ban", 14)}<span>${t("import.cancelImport")}</span></button>
    </div>`;
  loadIcons(modal.querySelector(".modal-body"));
  modal.querySelector("#oi-cancel").addEventListener("click", () => void cancelJob());
  updateProgress({ status: "running", total: 0, done: 0, current_file: null, notes: 0, collections: 0, errors: [] });
}

function schedulePoll(ms = POLL_MS) {
  if (!active) return;
  clearTimeout(active.timer);
  active.timer = setTimeout(() => void pollOnce(), ms);
}

async function pollOnce() {
  if (!active) return;
  try {
    const snap = await api.get(`/import/${active.jobId}`);
    active.fails = 0;
    updateProgress(snap);
    if (snap.status === "running") {
      schedulePoll();
      return;
    }
    active.phase = "finished";
    notifyDone();
    renderFinished(snap);
  } catch (err) {
    if (err.status === 404) {
      active.phase = "finished";
      notifyDone();
      renderFinished(null); // job expirou em memória (~1h) ou o servidor reiniciou
      return;
    }
    active.fails += 1;
    if (active.fails > MAX_POLL_FAILURES) {
      active.phase = "finished";
      const modal = document.getElementById("obsidian-import-modal");
      if (modal) renderFinished(null, err);
      else await uiError(t("import.progressLostTitle"), err);
      return;
    }
    schedulePoll();
  }
}

// a árvore de notas é atualizada ao final do import, mesmo se o modal já estiver fechado
function notifyDone() {
  if (active && !active.doneEmitted) {
    active.doneEmitted = true;
    emit("notes:changed");
  }
}

function updateProgress(snap) {
  const modal = document.getElementById("obsidian-import-modal");
  if (!modal || !active) return;
  const pct = snap.total ? Math.round((snap.done / snap.total) * 100) : 0;
  modal.querySelector("#oi-bar").style.width = `${pct}%`;
  modal.querySelector("#oi-count").textContent = t("import.progressCount", { done: snap.done, total: snap.total });
  const current = modal.querySelector("#oi-current");
  current.textContent = snap.current_file ? t("import.currentFile", { file: snap.current_file }) : "";
  modal.querySelector("#oi-notes").textContent = t("import.counterNotes", { count: snap.notes });
  modal.querySelector("#oi-collections").textContent = t("import.counterCollections", { count: snap.collections });
  modal.querySelector("#oi-errors-count").textContent = t("import.counterErrors", { count: snap.errors.length });
  renderErrors(modal, snap.errors);
}

function renderErrors(modal, errors) {
  modal.querySelector("#oi-errors").innerHTML = renderErrorsHtml(errors);
}

function renderFinished(snap, pollError = null) {
  const modal = document.getElementById("obsidian-import-modal");
  if (!modal) return; // import terminou em segundo plano: a árvore já foi atualizada pelo evento
  const status = snap?.status || "error";
  const titleKey = status === "done" ? "import.doneTitle" : status === "cancelled" ? "import.cancelledTitle" : "import.errorTitle";
  const body = [];
  if (snap) {
    body.push(
      `<p class="muted">${t("import.doneSummary", {
        notes: snap.notes,
        collections: snap.collections,
        errors: snap.errors.length,
      })}</p>`,
    );
  } else {
    body.push(`<p class="muted">${pollError ? t("import.progressLostMsg") : t("import.goneMsg")}</p>`);
  }
  modal.querySelector(".modal-body").innerHTML = `
    <div class="import-finished ${status}">
      <span class="dialog-icon ${status === "done" ? "" : "danger"}">${icon(status === "done" ? "check" : "triangle-alert", 18)}</span>
      <div class="grow">
        <h3>${t(titleKey)}</h3>
        ${body.join("")}
      </div>
    </div>
    ${snap ? `<div id="oi-errors">${renderErrorsHtml(snap.errors)}</div>` : ""}`;
  const foot = modal.querySelector(".modal-foot");
  foot.classList.remove("hidden");
  foot.innerHTML = `
    <button class="btn primary" type="button" id="oi-close">${icon("check", 14)}<span>${t("import.closeSummary")}</span></button>`;
  loadIcons(modal);
  foot.querySelector("#oi-close").addEventListener("click", () => closeModal());
}

function renderErrorsHtml(errors) {
  if (!errors.length) return "";
  return `
    <div class="import-errors data-table">
      <div class="trow thead"><span>${t("import.errorFile")}</span><span>${t("import.errorDetail")}</span></div>
      ${errors
        .map(
          (e) => `
        <div class="trow">
          <span class="tcell-main">${escHtml(e.file || "—")}</span>
          <span class="muted">${escHtml(e.error)}</span>
        </div>`,
        )
        .join("")}
    </div>`;
}

async function cancelJob() {
  if (!active) return;
  try {
    await api.post(`/import/${active.jobId}/cancel`, {});
  } catch (err) {
    await uiError(t("import.cancelFailed"), err);
  }
}

export { openImportModal };
