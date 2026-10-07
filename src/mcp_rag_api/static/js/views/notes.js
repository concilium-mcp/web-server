/* Tela de notas — espaço de notas do time: árvore, editor, rascunhos, modelos, histórico. */
import { api } from "../api.js";
import { t, locale } from "../i18n.js";
import { currentTheme } from "../theme.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, sanitizeNoteHtml, fmtDate, fmtAgo, modalShell, bindModal, uiDialog, uiConfirm, uiAlert, uiError } from "../ui.js";
import { state, colorFor, store } from "../state.js";
import { on } from "../events.js";
import { openConnectModal } from "./docpanel.js";
import { openImportModal } from "./import.js";

// o import de vault (import.js) avisa quando a árvore precisa ser relida
on("notes:changed", () => {
  if (notes.tree) void loadNotesTree();
});

// Modelos v1 estáticos: não poluem a busca RAG com documentos-modelo. Textos no dicionário (tpl.<id>.*).
const NOTE_TEMPLATE_IDS = ["blank", "client", "meeting", "proposal", "objections"];
const noteTemplates = () =>
  NOTE_TEMPLATE_IDS.map((id) => ({
    id,
    label: t(`tpl.${id}.label`),
    desc: t(`tpl.${id}.desc`),
    content: id === "blank" ? "" : t(`tpl.${id}.content`),
  }));

const IDLE_SAVE_MS = 20000; // salva sozinho depois de ~20 s sem digitar
const DRAFT_PREFIX = "concilium:draft:";

const notes = {
  tree: null, // { collections, notes } — sem conteúdo
  current: null, // nota aberta, como está salva no banco
  editor: null, // instância Toast UI (editor ou viewer)
  tags: [], // tags em edição
  dirty: false,
  saving: false,
  idleTimer: null,
  draftTimer: null,
  filter: "",
  side: null, // "related" | "history" | null
};

const canEdit = () => ["admin", "editor"].includes(state.user?.role);

function notesTemplate() {
  return `
    <div class="notes-layout">
      <aside class="notes-tree">
        <div class="notes-tree-head">
          <div class="field notes-filter">${icon("search")}<input type="text" id="notes-filter" placeholder="${t("notes.filterPh")}" autocomplete="off" /></div>
          ${
            canEdit()
              ? `<div class="notes-tree-actions">
                  <button class="btn sm primary" id="note-new" title="${t("notes.newNoteTitle")}">${icon("plus", 14)}<span>${t("notes.newNoteBtn")}</span></button>
                  <button class="btn sm ghost" id="folder-new" title="${t("notes.newFolderTitle")}">${icon("folder", 14)}<span>${t("notes.newFolderBtn")}</span></button>
                  <button class="btn sm ghost" id="note-import" title="${t("import.toolbarTitle")}">${icon("folder-input", 14)}<span>${t("import.toolbarBtn")}</span></button>
                </div>`
              : ""
          }
        </div>
        <div class="notes-tree-list" id="notes-tree-list"></div>
      </aside>
      <section class="note-main" id="note-main"></section>
      <aside class="note-side hidden" id="note-side"></aside>
    </div>`;
}

async function renderNotesScreen(main, openId) {
  main.innerHTML = notesTemplate();
  await loadIcons(main);
  main.querySelector("#notes-filter").addEventListener("input", (e) => {
    notes.filter = e.target.value;
    renderNotesTree();
  });
  main.querySelector("#note-new")?.addEventListener("click", () => openNewNoteModal());
  main.querySelector("#folder-new")?.addEventListener("click", () => openNewFolderModal());
  main.querySelector("#note-import")?.addEventListener("click", () => openImportModal());
  main.querySelector("#notes-tree-list").addEventListener("click", async (e) => {
    const folder = e.target.closest("[data-toggle-folder]");
    const note = e.target.closest("[data-note]");
    if (folder) {
      const collapsed = new Set(store.get("concilium:collapsed") || []);
      const name = folder.dataset.toggleFolder;
      collapsed.has(name) ? collapsed.delete(name) : collapsed.add(name);
      store.set("concilium:collapsed", [...collapsed]);
      renderNotesTree();
    } else if (note) {
      await openNote(note.dataset.note);
    }
  });
  document.addEventListener("keydown", notesKeys);
  await loadNotesTree();
  const target = openId || store.get("concilium:last-note");
  const exists = notes.tree.notes.some((n) => n.id === target);
  if (exists) await openNote(target);
  else if (notes.tree.notes.length) await openNote(notes.tree.notes[0].id);
  else renderNotesEmpty();
}

// sai da tela de notas: salva o que estiver pendente e desmonta o editor
async function notesLeave() {
  document.removeEventListener("keydown", notesKeys);
  if (notes.current && notes.dirty) await saveNote();
  clearTimeout(notes.idleTimer);
  notes.editor?.destroy();
  notes.editor = null;
  notes.current = null;
  notes.dirty = false;
}

function notesKeys(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    saveNote();
  } else if (e.altKey && e.key.toLowerCase() === "n" && canEdit()) {
    e.preventDefault(); // Ctrl+N é do navegador (nova janela) e não pode ser capturado
    openNewNoteModal();
  }
}

async function loadNotesTree() {
  notes.tree = await api.get("/notes/tree");
  renderNotesTree();
}

function renderNotesTree() {
  const box = document.getElementById("notes-tree-list");
  if (!box || !notes.tree) return;
  const q = titleKey(notes.filter);
  const collapsed = new Set(store.get("concilium:collapsed") || []);
  const byCol = new Map(notes.tree.collections.map((c) => [c.name, []]));
  for (const n of notes.tree.notes) {
    if (!q || titleKey(n.title).includes(q)) byCol.get(n.collection)?.push(n);
  }
  box.innerHTML =
    [...byCol.entries()]
      .filter(([, items]) => !q || items.length)
      .map(([name, items]) => {
        const closed = collapsed.has(name) && !q;
        return `
      <div class="tree-folder ${closed ? "collapsed" : ""}">
        <button class="tree-folder-head" type="button" data-toggle-folder="${escHtml(name)}">
          <span class="tree-chevron">${icon("chevron-right", 13)}</span>
          <span class="dot" style="background:${colorFor(name)}"></span>
          <span class="grow">${escHtml(name)}</span><span class="count">${items.length}</span>
        </button>
        <div class="tree-notes">
          ${
            items
              .map(
                (n) => `<button class="tree-note ${notes.current?.id === n.id ? "active" : ""}" type="button" data-note="${n.id}" title="${escHtml(n.title)}">
                  <span class="grow">${escHtml(n.title)}</span>${notes.current?.id === n.id && notes.dirty ? `<span class="dirty-dot" title="${t("notes.unsaved")}"></span>` : ""}</button>`,
              )
              .join("") || `<div class="tree-empty">${t("notes.emptyFolder")}</div>`
          }
        </div>
      </div>`;
      })
      .join("") || `<div class="tree-empty">${t("notes.nothingFound", { query: escHtml(notes.filter) })}</div>`;
  loadIcons(box);
}

// mesma regra do kb_title_key do banco: minúsculas, sem acento
const titleKey = (s) =>
  String(s || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();

function renderNotesEmpty() {
  const main = document.getElementById("note-main");
  main.innerHTML = `
    <div class="results-empty notes-empty">
      <div class="results-empty-title">${canEdit() ? t("notes.firstNote") : t("notes.noneYet")}</div>
      ${
        canEdit()
          ? `${t("notes.emptyBody")}
             <div class="template-pick">${noteTemplates().map((tpl) => `<button class="btn" type="button" data-template="${tpl.id}">${escHtml(tpl.label)}</button>`).join("")}</div>`
          : t("notes.askEditor")
      }
    </div>`;
  main.querySelectorAll("[data-template]").forEach((b) => b.addEventListener("click", () => openNewNoteModal(b.dataset.template)));
}

/* ---------- abrir / editar / salvar */

async function openNote(id) {
  if (notes.current?.id === id) return;
  if (notes.current && notes.dirty && !(await saveNote())) {
    const leave = await uiConfirm({
      title: t("notes.saveFailedTitle"),
      message: t("notes.switchAnywayMsg"),
      confirmLabel: t("notes.switchAnyway"),
    });
    if (!leave) return;
  }
  let note;
  try {
    note = await api.get(`/notes/${id}`);
  } catch (err) {
    await uiError(t("notes.openFailed"), err);
    return;
  }
  notes.current = note;
  notes.tags = [...(note.tags || [])];
  notes.dirty = false;
  store.set("concilium:last-note", id);
  if (location.hash !== `#/notes/${id}`) history.replaceState(null, "", `#/notes/${id}`);
  renderNoteMain();
  renderNotesTree();
  if (notes.side) renderNoteSide(notes.side);
  await offerDraftRecovery();
}

function renderNoteMain() {
  const n = notes.current;
  const main = document.getElementById("note-main");
  const editable = canEdit();
  main.innerHTML = `
    <div class="note-head">
      <input class="note-title" id="note-title" value="${escHtml(n.title)}" placeholder="${t("notes.untitled")}" ${editable ? "" : "readonly"} />
      <div class="note-actions">
        <span class="save-state" id="save-state"></span>
        <button class="btn sm ghost ${notes.side === "related" ? "active" : ""}" type="button" data-side="related">${icon("link", 14)}<span>${t("notes.connections")}</span></button>
        <button class="btn sm ghost ${notes.side === "history" ? "active" : ""}" type="button" data-side="history">${icon("history", 14)}<span>${t("notes.history")}</span></button>
        ${
          editable
            ? `<button class="icon-btn" type="button" id="note-move" title="${t("notes.moveTitle")}">${icon("folder-input", 16)}</button>
               <button class="icon-btn danger" type="button" id="note-archive" title="${t("notes.archiveTitle")}">${icon("archive", 16)}</button>
               <button class="btn sm primary" type="button" id="note-save" title="${t("notes.saveTitle")}">${icon("save", 14)}<span>${t("notes.save")}</span></button>`
            : ""
        }
      </div>
    </div>
    <div class="note-meta">
      <span class="chip"><span class="dot" style="background:${colorFor(n.collection)}"></span>${escHtml(n.collection)}</span>
      <span class="muted" id="note-version"></span>
      <div class="tag-input" id="note-tags"></div>
    </div>
    <div class="note-editor" id="note-editor"></div>`;
  loadIcons(main);
  renderVersionLine();
  renderTags();

  notes.editor?.destroy();
  const el = main.querySelector("#note-editor");
  const common = { el, initialValue: n.content, theme: currentTheme(), usageStatistics: false, customHTMLSanitizer: sanitizeNoteHtml };
  notes.editor = editable
    ? new toastui.Editor({
        ...common,
        height: "100%",
        initialEditType: "wysiwyg",
        previewStyle: "vertical",
        language: locale(),
        placeholder: t("notes.editorPh"),
      })
    : toastui.Editor.factory({ ...common, viewer: true });
  if (editable) {
    notes.editor.on("change", markDirty);
    main.querySelector("#note-title").addEventListener("input", markDirty);
    main.querySelector("#note-save").addEventListener("click", () => saveNote());
    main.querySelector("#note-move").addEventListener("click", openMoveNoteModal);
    main.querySelector("#note-archive").addEventListener("click", archiveCurrentNote);
  }
  main.querySelectorAll("[data-side]").forEach((b) =>
    b.addEventListener("click", () => {
      notes.side = notes.side === b.dataset.side ? null : b.dataset.side;
      main.querySelectorAll("[data-side]").forEach((x) => x.classList.toggle("active", x.dataset.side === notes.side));
      renderNoteSide(notes.side);
    }),
  );
  setSaveState(notes.dirty ? "dirty" : "saved");
}

function renderVersionLine() {
  const n = notes.current;
  const box = document.getElementById("note-version");
  if (box) box.textContent = t("notes.versionLine", { version: n.version, ago: fmtAgo(n.updated_at) }) + (n.updated_by ? t("notes.versionBy", { name: n.updated_by.replace(/^dash:/, "") }) : "");
}

function renderTags() {
  const box = document.getElementById("note-tags");
  if (!box) return;
  const editable = canEdit();
  box.innerHTML =
    notes.tags
      .map((tag, i) => `<span class="chip tag-chip">#${escHtml(tag)}${editable ? `<button type="button" data-tag-del="${i}" title="${t("notes.removeTag")}">×</button>` : ""}</span>`)
      .join("") + (editable ? `<input type="text" id="tag-add" placeholder="${notes.tags.length ? "+ tag" : t("notes.addTag")}" />` : "");
  if (!editable) return;
  const input = box.querySelector("#tag-add");
  input.addEventListener("keydown", (e) => {
    const value = input.value.trim().replace(/^#/, "").replace(/,$/, "");
    if ((e.key === "Enter" || e.key === ",") && value) {
      e.preventDefault();
      if (!notes.tags.includes(value)) notes.tags.push(value);
      renderTags();
      document.getElementById("tag-add").focus();
      markDirty();
    } else if (e.key === "Backspace" && !input.value && notes.tags.length) {
      notes.tags.pop();
      renderTags();
      document.getElementById("tag-add").focus();
      markDirty();
    }
  });
  box.querySelectorAll("[data-tag-del]").forEach((b) =>
    b.addEventListener("click", () => {
      notes.tags.splice(Number(b.dataset.tagDel), 1);
      renderTags();
      markDirty();
    }),
  );
}

function editedNote() {
  return {
    title: document.getElementById("note-title")?.value.trim() || notes.current.title,
    content: notes.editor?.getMarkdown() ?? notes.current.content,
    tags: [...notes.tags],
  };
}

function hasChanges() {
  const n = notes.current;
  const e = editedNote();
  return e.title !== n.title || e.content.trim() !== (n.content || "").trim() || JSON.stringify(e.tags) !== JSON.stringify(n.tags || []);
}

function markDirty() {
  if (!notes.current) return;
  const was = notes.dirty;
  notes.dirty = hasChanges();
  if (was !== notes.dirty) renderNotesTree();
  setSaveState(notes.dirty ? "dirty" : "saved");
  // rascunho local contínuo (não perde nada se a aba fechar) + salvamento automático depois de parado
  clearTimeout(notes.draftTimer);
  clearTimeout(notes.idleTimer);
  if (!notes.dirty) {
    store.del(DRAFT_PREFIX + notes.current.id);
    return;
  }
  notes.draftTimer = setTimeout(() => {
    store.set(DRAFT_PREFIX + notes.current.id, { base_version: notes.current.version, at: new Date().toISOString(), ...editedNote() });
  }, 400);
  notes.idleTimer = setTimeout(() => saveNote(), IDLE_SAVE_MS);
}

function setSaveState(kind) {
  const box = document.getElementById("save-state");
  if (!box) return;
  const labels = {
    saved: t("save.saved", { version: notes.current?.version }),
    dirty: t("save.dirty"),
    saving: t("save.saving"),
    error: t("save.error"),
  };
  box.className = `save-state ${kind}`;
  box.textContent = canEdit() ? labels[kind] : t("save.readOnly");
}

// Salva no banco (gera versão). Devolve true se não sobrou nada pendente.
async function saveNote(force = false) {
  if (!notes.current || !canEdit()) return true;
  if (!notes.dirty || notes.saving) return !notes.dirty;
  clearTimeout(notes.idleTimer);
  notes.saving = true;
  setSaveState("saving");
  const n = notes.current;
  const edited = editedNote();
  const content = edited.content.trim() ? edited.content : n.content; // conteúdo vazio não é aceito pela base
  try {
    const result = await api.patch(`/notes/${n.id}`, {
      base_version: force ? force : n.version,
      title: edited.title,
      content,
      tags: edited.tags,
    });
    Object.assign(n, { ...edited, content, version: result.version ?? n.version, updated_at: new Date().toISOString(), updated_by: `dash:${state.user.username}` });
    notes.dirty = hasChanges(); // o usuário pode ter digitado durante o salvamento
    if (!notes.dirty) store.del(DRAFT_PREFIX + n.id);
    const item = notes.tree?.notes.find((x) => x.id === n.id);
    if (item) Object.assign(item, { title: n.title, tags: n.tags, version: n.version });
    renderNotesTree();
    renderVersionLine();
    setSaveState(notes.dirty ? "dirty" : "saved");
    if (notes.side === "history") renderNoteSide("history");
    return !notes.dirty;
  } catch (err) {
    setSaveState("error");
    if (err.status === 409 && err.detail?.current_version) {
      notes.saving = false;
      const overwrite = await uiDialog({
        title: t("notes.conflictTitle"),
        message: t("notes.conflictMsg", { message: err.detail.message }),
        confirmLabel: t("notes.conflictOverwrite"),
        cancelLabel: t("notes.conflictReload"),
        tone: "danger",
      });
      if (overwrite) return saveNote(err.detail.current_version);
      store.del(DRAFT_PREFIX + n.id);
      notes.current = null;
      await openNote(n.id);
      return true;
    }
    await uiError(t("notes.saveNoteFailed"), err);
    return false;
  } finally {
    notes.saving = false;
  }
}

async function offerDraftRecovery() {
  const n = notes.current;
  const draft = store.get(DRAFT_PREFIX + n.id);
  if (!draft || !canEdit()) return;
  const same = draft.title === n.title && draft.content.trim() === (n.content || "").trim() && JSON.stringify(draft.tags) === JSON.stringify(n.tags || []);
  if (same) {
    store.del(DRAFT_PREFIX + n.id);
    return;
  }
  const stale = draft.base_version !== n.version;
  const recover = await uiConfirm({
    title: t("draft.title"),
    message: t("draft.msg", { ago: fmtAgo(draft.at) }) + (stale ? t("draft.stale", { version: n.version }) : ""),
    confirmLabel: t("draft.recover"),
  });
  if (!recover) {
    store.del(DRAFT_PREFIX + n.id);
    return;
  }
  document.getElementById("note-title").value = draft.title;
  notes.tags = [...draft.tags];
  renderTags();
  notes.editor.setMarkdown(draft.content, false);
  markDirty();
}

/* ---------- criar / mover / arquivar / pastas */

function collectionOptions(selected) {
  return notes.tree.collections
    .map((c) => `<option value="${escHtml(c.name)}" ${c.name === selected ? "selected" : ""}>${escHtml(c.name)}</option>`)
    .join("");
}

async function openNewNoteModal(templateId = "blank") {
  if (!notes.tree.collections.length) {
    await uiAlert(t("notes.needFolderTitle"), t("notes.needFolderMsg"));
    return;
  }
  document.getElementById("note-new-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "note-new",
    iconName: "notebook-pen",
    title: t("notes.new"),
    subtitle: t("notes.newSub"),
    submitLabel: t("notes.create"),
    width: 640,
    body: `
      <div class="create-grid">
        <label class="form-field">
          <span class="form-label">${t("notes.titleLabel")}</span>
          <input class="input" id="nn-title" placeholder="${t("notes.titlePh")}" required />
        </label>
        <label class="form-field">
          <span class="form-label">${t("notes.folder")}</span>
          <select class="input" id="nn-collection">${collectionOptions(notes.current?.collection || notes.tree.collections[0].name)}</select>
        </label>
      </div>
      <div class="form-field">
        <span class="form-label">${t("notes.template")}</span>
        <div class="scope-grid two">
          ${noteTemplates().map(
            (tpl) => `
            <label class="scope-option">
              <input type="radio" name="nn-template" value="${tpl.id}" ${tpl.id === templateId ? "checked" : ""} />
              <span><span>${escHtml(tpl.label)}</span><span class="muted">${escHtml(tpl.desc)}</span></span>
            </label>`,
          ).join("")}
        </div>
      </div>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("note-new-modal");
  await loadIcons(modal);
  const setOpen = bindModal("note-new");
  modal.querySelector("#note-new-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = modal.querySelector("#nn-title").value.trim();
    const template = noteTemplates().find((tpl) => tpl.id === modal.querySelector("input[name=nn-template]:checked").value);
    const body = {
      collection: modal.querySelector("#nn-collection").value,
      title,
      content: template.content || `# ${title}\n`,
    };
    try {
      let created = await api.post("/notes", body);
      if (!created.created && created.reason === "duplicate_suspected") {
        const sim = created.similar_document;
        const go = await uiConfirm({
          title: t("notes.duplicateTitle"),
          message: t("notes.duplicateMsg", { title: sim.title, pct: Math.round(sim.similarity * 100) }),
          confirmLabel: t("notes.createAnyway"),
        });
        if (!go) return;
        created = await api.post("/notes", { ...body, force: true });
      }
      setOpen(false);
      modal.remove();
      await loadNotesTree();
      notes.current = notes.current?.id === created.document_id ? null : notes.current;
      await openNote(created.document_id);
      notes.editor?.focus?.();
    } catch (err) {
      await uiError(t("notes.createFailed"), err);
    }
  });
  setOpen(true);
}

async function openNewFolderModal() {
  document.getElementById("folder-new-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "folder-new",
    iconName: "folder",
    title: t("folders.new"),
    subtitle: t("folders.newSub"),
    submitLabel: t("folders.create"),
    width: 520,
    body: `
      <label class="form-field">
        <span class="form-label">${t("common.name")}</span>
        <input class="input" id="nf-name" placeholder="${t("folders.namePh")}" required />
      </label>
      <label class="form-field">
        <span class="form-label">${t("folders.desc")} <span class="muted">${t("common.optional")}</span></span>
        <input class="input" id="nf-desc" placeholder="${t("folders.descPh")}" />
      </label>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("folder-new-modal");
  await loadIcons(modal);
  const setOpen = bindModal("folder-new");
  modal.querySelector("#folder-new-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.post("/collections", {
        name: modal.querySelector("#nf-name").value.trim(),
        description: modal.querySelector("#nf-desc").value.trim() || null,
      });
      setOpen(false);
      modal.remove();
      await loadNotesTree();
    } catch (err) {
      await uiError(t("folders.createFailed"), err);
    }
  });
  setOpen(true);
}

async function openMoveNoteModal() {
  const n = notes.current;
  if (!(await saveNote())) return;
  document.getElementById("note-move-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "note-move",
    iconName: "folder-input",
    title: t("move.title"),
    subtitle: t("move.sub", { title: escHtml(n.title), folder: escHtml(n.collection) }),
    submitLabel: t("move.submit"),
    submitIcon: "folder-input",
    width: 480,
    body: `
      <label class="form-field">
        <span class="form-label">${t("move.to")}</span>
        <select class="input" id="nm-collection">${collectionOptions(n.collection)}</select>
      </label>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("note-move-modal");
  await loadIcons(modal);
  const setOpen = bindModal("note-move");
  modal.querySelector("#note-move-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const destination = modal.querySelector("#nm-collection").value;
    try {
      await api.post(`/notes/${n.id}/move`, { collection: destination });
      setOpen(false);
      modal.remove();
      n.collection = destination;
      await loadNotesTree();
      renderNoteMain();
    } catch (err) {
      await uiError(t("move.failed"), err);
    }
  });
  setOpen(true);
}

async function archiveCurrentNote() {
  const n = notes.current;
  const ok = await uiConfirm({
    title: t("archive.title"),
    message: t("archive.msg", { title: n.title }),
    confirmLabel: t("archive.confirm"),
    tone: "danger",
  });
  if (!ok) return;
  try {
    await api.post(`/notes/${n.id}/archive`, {});
    store.del(DRAFT_PREFIX + n.id);
    notes.dirty = false;
    notes.editor?.destroy();
    notes.editor = null;
    notes.current = null;
    history.replaceState(null, "", "#/notes");
    await loadNotesTree();
    if (notes.tree.notes.length) await openNote(notes.tree.notes[0].id);
    else renderNotesEmpty();
  } catch (err) {
    await uiError(t("archive.failed"), err);
  }
}

/* ---------- painel lateral: conexões e histórico */

async function renderNoteSide(kind) {
  const side = document.getElementById("note-side");
  if (!side) return;
  side.classList.toggle("hidden", !kind);
  if (!kind || !notes.current) return;
  const n = notes.current;
  side.innerHTML = `<div class="muted">${t("common.loading")}</div>`;
  if (kind === "related") {
    let rel;
    try {
      rel = await api.get(`/notes/${n.id}/related`);
    } catch {
      side.innerHTML = `<div class="muted">${t("side.relatedFailed")}</div>`;
      return;
    }
    const item = (d, extra = "") =>
      `<button class="side-item" type="button" data-open-note="${d.document_id}"><span class="dot" style="background:${colorFor(d.collection)}"></span><span class="grow">${escHtml(d.title)}</span>${extra}</button>`;
    side.innerHTML = `
      <div class="side-head"><h3>${icon("link", 15)} ${t("notes.connections")}</h3>
        ${state.user?.role === "admin" ? `<button class="btn sm ghost" type="button" id="side-connect">${icon("plus", 13)}<span>${t("side.connectTo")}</span></button>` : ""}
      </div>
      <div class="links-label">${t("side.links", { count: rel.links.length })}</div>
      ${
        rel.links
          .map((l) => (l.pending ? `<div class="side-item pending">${escHtml(l.target_title)} <span class="muted">${t("side.notYet")}</span></div>` : item(l)))
          .join("") || `<div class="muted links-empty">${t("side.linksEmpty")}</div>`
      }
      <div class="links-label">${t("side.backlinks", { count: rel.backlinks.length })}</div>
      ${rel.backlinks.map((b) => item(b)).join("") || `<div class="muted links-empty">${t("side.backlinksEmpty")}</div>`}
      <div class="links-label">${t("side.similar", { count: rel.semantic.length })}</div>
      ${rel.semantic.map((s) => item(s, `<span class="muted">${Math.round(s.similarity * 100)}%</span>`)).join("") || `<div class="muted links-empty">${t("side.similarEmpty")}</div>`}`;
    side.querySelector("#side-connect")?.addEventListener("click", () =>
      openConnectModal({ document_id: n.id, title: n.title }, null, () => renderNoteSide("related")),
    );
  } else {
    let versions;
    try {
      versions = await api.get(`/notes/${n.id}/versions`);
    } catch {
      side.innerHTML = `<div class="muted">${t("side.historyFailed")}</div>`;
      return;
    }
    side.innerHTML = `
      <div class="side-head"><h3>${icon("history", 15)} ${t("notes.history")}</h3></div>
      ${versions
        .map(
          (v) => `
        <button class="side-version ${v.version === n.version ? "current" : ""}" type="button" data-version="${v.version}">
          <span class="idx">v${v.version}</span>
          <span class="grow">${escHtml(v.change_note || "")}<span class="muted">${escHtml((v.changed_by || "—").replace(/^dash:/, ""))} · ${fmtAgo(v.created_at)}</span></span>
          ${v.version === n.version ? `<span class="chip subtle">${t("side.current")}</span>` : ""}
        </button>`,
        )
        .join("")}`;
  }
  await loadIcons(side);
  side.querySelectorAll("[data-open-note]").forEach((b) => b.addEventListener("click", () => openNote(b.dataset.openNote)));
  side.querySelectorAll("[data-version]").forEach((b) => b.addEventListener("click", () => openVersionPreview(Number(b.dataset.version))));
}

async function openVersionPreview(version) {
  const n = notes.current;
  let v;
  try {
    v = await api.get(`/notes/${n.id}/versions/${version}`);
  } catch (err) {
    await uiError(t("version.openFailed"), err);
    return;
  }
  const isCurrent = version === n.version;
  document.getElementById("version-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "version",
    iconName: "history",
    title: `${escHtml(v.title)} — v${version}`,
    subtitle: `${escHtml((v.changed_by || "—").replace(/^dash:/, ""))} · ${fmtDate(v.created_at)}${v.change_note ? ` · “${escHtml(v.change_note)}”` : ""}`,
    submitLabel: isCurrent ? t("common.close") : t("version.restore"),
    submitIcon: isCurrent ? "check" : "refresh-cw",
    width: 820,
    body: `<div class="version-viewer" id="version-viewer"></div>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("version-modal");
  await loadIcons(modal);
  const viewer = toastui.Editor.factory({ el: modal.querySelector("#version-viewer"), viewer: true, initialValue: v.content, theme: currentTheme(), usageStatistics: false, customHTMLSanitizer: sanitizeNoteHtml });
  const setOpen = bindModal("version");
  if (!canEdit() && !isCurrent) modal.querySelector("button[type=submit]").remove();
  modal.querySelector("#version-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (isCurrent) {
      setOpen(false);
      return;
    }
    if (notes.dirty) {
      const discard = await uiConfirm({
        title: t("version.discardTitle"),
        message: t("version.discardMsg", { version }),
        confirmLabel: t("version.restoreAnyway"),
        tone: "danger",
      });
      if (!discard) return;
    }
    try {
      await api.post(`/notes/${n.id}/restore/${version}`);
      viewer.destroy();
      setOpen(false);
      modal.remove();
      store.del(DRAFT_PREFIX + n.id);
      notes.dirty = false;
      notes.current = null;
      await loadNotesTree();
      await openNote(n.id);
    } catch (err) {
      await uiError(t("version.restoreFailed"), err);
    }
  });
  setOpen(true);
}

export { NOTE_TEMPLATE_IDS, noteTemplates, DRAFT_PREFIX, notes, canEdit, renderNotesScreen, notesLeave, openNote, editedNote };
