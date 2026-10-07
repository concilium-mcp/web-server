/* Painel lateral do documento — detalhes, chunks, versões e links explícitos entre documentos. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtDate, debounce, modalShell, bindModal, uiConfirm, uiAlert, uiError } from "../ui.js";
import { state, colorFor } from "../state.js";
import { emit } from "../events.js";

/* ---------------------------------------------------------------- painel do documento */

async function openDocumentPanel(docId) {
  const panel = document.getElementById("panel");
  panel.classList.remove("hidden");
  panel.innerHTML = `<div class="muted">${t("common.loading")}</div>`;
  let doc, docLinks;
  try {
    [doc, docLinks] = await Promise.all([
      api.get(`/documents/${docId}`),
      api.get(`/documents/${docId}/links`).catch(() => ({ links: [], backlinks: [] })),
    ]);
  } catch {
    panel.innerHTML = `<div class="muted">${t("doc.loadFailed")}</div>`;
    return;
  }
  state.docPanelId = docId;
  panel.innerHTML = `
    <div class="panel-head">
      <h2>${escHtml(doc.title)}</h2>
      <button class="icon-btn" id="panel-close" title="${t("common.close")}">${icon("x", 16)}</button>
    </div>
    <div class="chip"><span class="dot" style="background:${colorFor(doc.collection)}"></span>${escHtml(doc.collection)}</div>
    <dl class="kv">
      <dt>${t("doc.version")}</dt><dd>v${doc.version} · ${doc.status === "active" ? t("doc.active") : t("doc.archived")}</dd>
      <dt>${t("doc.createdBy")}</dt><dd>${escHtml(doc.created_by ?? "—")}</dd>
      <dt>${t("doc.updated")}</dt><dd>${fmtDate(doc.updated_at)}</dd>
      ${doc.source ? `<dt>${t("doc.source")}</dt><dd>${escHtml(doc.source)}</dd>` : ""}
      ${doc.tags?.length ? `<dt>${t("doc.tags")}</dt><dd>${doc.tags.map((tag) => `<span class="chip">${escHtml(tag)}</span>`).join(" ")}</dd>` : ""}
    </dl>
    ${linksSection(doc, docLinks)}
    <div class="section">
      <h3>${icon("file-text")} ${t("doc.content")}</h3>
      <div class="doc-content">${escHtml(doc.content)}</div>
    </div>
    <div class="section">
      <h3>${icon("folder")} ${t("doc.chunks", { count: doc.chunks.length })}</h3>
      ${doc.chunks.map((c) => `<div class="chunk-item"><span class="idx">#${c.chunk_index}</span>${t("doc.words", { count: c.word_count })}<p>${escHtml(c.content.slice(0, 140))}${c.content.length > 140 ? "…" : ""}</p></div>`).join("")}
    </div>
    <div class="section">
      <h3>${icon("history")} ${t("doc.versions", { count: doc.versions.length })}</h3>
      ${doc.versions.map((v) => `<div class="version-item"><span class="idx">v${v.version}</span>${escHtml(v.change_note ?? "")}<p>${escHtml(v.changed_by ?? "—")} · ${fmtDate(v.created_at)}</p></div>`).join("")}
    </div>`;
  await loadIcons(panel);
  panel.querySelector("#panel-close").addEventListener("click", closePanel);
  panel.querySelector("#connect-open")?.addEventListener("click", () =>
    openConnectModal({ document_id: docId, title: doc.title }),
  );
  panel.querySelector(".links-section")?.addEventListener("click", async (e) => {
    const go = e.target.closest("[data-open-doc]");
    const del = e.target.closest("[data-unlink]");
    if (go) {
      emit("graph:focus", { docId: go.dataset.openDoc });
      openDocumentPanel(go.dataset.openDoc);
    } else if (del) {
      const ok = await uiConfirm({
        title: t("unlink.title"),
        message: t("unlink.msg", { title: del.dataset.title }),
        confirmLabel: t("unlink.confirm"),
        tone: "danger",
      });
      if (!ok) return;
      try {
        await api.del(`/links/${del.dataset.unlink}`);
        openDocumentPanel(docId);
        emit("graph:reload");
      } catch (err) {
        await uiError(t("unlink.failed"), err);
      }
    }
  });
}

/* ---------------------------------------------------------------- links explícitos (plan-web-03) */

function linksSection(doc, data) {
  const isAdmin = state.user?.role === "admin";
  const kindChip = (kind) => `<span class="chip subtle">${kind === "wikilink" ? "[[wikilink]]" : t("links.manual")}</span>`;
  const outgoing = data.links || [];
  const backlinks = data.backlinks || [];
  const outItem = (l) =>
    l.pending
      ? `<div class="link-item pending" title="${t("links.pendingTitle")}">
           ${icon("link", 13)}<span class="grow">${escHtml(l.target_title)} <span class="muted">${t("side.notYet")}</span></span>${kindChip(l.kind)}
         </div>`
      : `<div class="link-item">
           ${icon("link", 13)}
           <button class="link-title grow" type="button" data-open-doc="${l.document_id}">${escHtml(l.title)}
             ${l.note ? `<span class="muted link-note">“${escHtml(l.note)}”</span>` : ""}</button>
           ${kindChip(l.kind)}
           ${
             isAdmin && l.kind === "manual"
               ? `<button class="icon-btn danger" type="button" data-unlink="${l.id}" data-title="${escHtml(l.title)}" title="${t("links.removeManual")}">${icon("x", 13)}</button>`
               : ""
           }
         </div>`;
  const backItem = (b) => `
    <div class="link-item">
      ${icon("link", 13)}
      <button class="link-title grow" type="button" data-open-doc="${b.document_id}">${escHtml(b.title)}
        ${b.note ? `<span class="muted link-note">“${escHtml(b.note)}”</span>` : ""}</button>
      ${kindChip(b.kind)}
    </div>`;
  return `
    <div class="section links-section">
      <h3>${icon("link")} ${t("links.connections")}
        ${isAdmin && doc.status === "active" ? `<button class="btn sm ghost connect-btn" type="button" id="connect-open">${icon("plus", 13)}<span>${t("side.connectTo")}</span></button>` : ""}
      </h3>
      <div class="links-group">
        <div class="links-label">${t("side.links", { count: outgoing.length })}</div>
        ${outgoing.map(outItem).join("") || `<div class="muted links-empty">${t("links.noneOutgoing")}</div>`}
      </div>
      <div class="links-group">
        <div class="links-label">${t("side.backlinks", { count: backlinks.length })}</div>
        ${backlinks.map(backItem).join("") || `<div class="muted links-empty">${t("links.noneBacklinks")}</div>`}
      </div>
    </div>`;
}

// "Conectar a…": escolhe o destino por autocomplete (ou já vem do modo conectar) e uma nota opcional
async function openConnectModal(source, preselected = null, onDone = null) {
  document.getElementById("connect-modal")?.remove();
  const holder = document.createElement("div");
  holder.innerHTML = modalShell({
    id: "connect",
    iconName: "link",
    title: t("connect.title"),
    subtitle: t("connect.sub", { title: escHtml(source.title) }),
    submitLabel: t("connect.submit"),
    submitIcon: "link",
    width: 560,
    body: `
      <label class="form-field">
        <span class="form-label">${t("connect.target")}</span>
        <input class="input" type="text" id="connect-q" placeholder="${t("connect.searchPh")}" autocomplete="off" />
      </label>
      <div class="connect-results" id="connect-results" role="listbox"></div>
      <label class="form-field">
        <span class="form-label">${t("connect.why")} <span class="muted">${t("common.optional")}</span></span>
        <input class="input" type="text" id="connect-note" placeholder="${t("connect.notePh")}" />
      </label>`,
  });
  document.body.appendChild(holder.firstElementChild);
  const modal = document.getElementById("connect-modal");
  await loadIcons(modal);
  const setOpen = bindModal("connect");
  let chosen = preselected;
  const results = modal.querySelector("#connect-results");
  const render = (items) => {
    const list = items.filter((d) => d.document_id !== source.document_id);
    results.innerHTML =
      list
        .map(
          (d) => `
        <button type="button" class="connect-option ${chosen?.document_id === d.document_id ? "selected" : ""}" data-pick="${d.document_id}" data-title="${escHtml(d.title)}">
          <span class="dot" style="background:${colorFor(d.collection)}"></span>
          <span class="grow">${escHtml(d.title)}</span><span class="muted">${escHtml(d.collection)}</span>
        </button>`,
        )
        .join("") || `<div class="muted links-empty">${t("connect.noneFound")}</div>`;
  };
  const search = debounce(async (q) => render(await api.get("/documents/titles", { q, limit: 8 }).catch(() => [])), 180);
  modal.querySelector("#connect-q").addEventListener("input", (e) => search(e.target.value));
  results.addEventListener("click", (e) => {
    const opt = e.target.closest("[data-pick]");
    if (!opt) return;
    chosen = { document_id: opt.dataset.pick, title: opt.dataset.title };
    results.querySelectorAll(".connect-option").forEach((b) => b.classList.toggle("selected", b === opt));
  });
  modal.querySelector("#connect-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!chosen) {
      await uiAlert(t("connect.pickTitle"), t("connect.pickMsg"));
      return;
    }
    try {
      await api.post(`/documents/${source.document_id}/links`, {
        target_id: chosen.document_id,
        note: modal.querySelector("#connect-note").value.trim() || null,
      });
      setOpen(false);
      modal.remove();
      if (onDone) return onDone();
      emit("graph:reload");
      openDocumentPanel(source.document_id);
    } catch (err) {
      await uiError(t("connect.failed"), err);
    }
  });
  setOpen(true);
  if (preselected) {
    modal.querySelector("#connect-q").value = preselected.title;
    render([preselected]);
    modal.querySelector("#connect-note").focus();
  } else {
    render(await api.get("/documents/titles", { q: "", limit: 8 }).catch(() => []));
  }
}

function closePanel() {
  const panel = document.getElementById("panel");
  if (panel) panel.classList.add("hidden");
  state.docPanelId = null;
  document.querySelectorAll(".result.selected").forEach((el) => el.classList.remove("selected"));
}

export { openDocumentPanel, linksSection, openConnectModal, closePanel };
