/* Quadros Kanban (plan-web-37b) — lista dos quadros por pasta, o quadro (colunas livres, arrastar cartões),
   a janela do cartão (checklist, posse, campos de trabalho, notas ligadas, log) e a aba Logs (auditoria + CSV).
   Os dados e as regras ficam no servidor (core/boards.py); aqui é só a tela. Atualiza sozinho a cada 12 s
   enquanto o quadro está aberto, sem atrapalhar quem está arrastando ou com uma janela aberta. */
import { api } from "../api.js";
import { t } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtDate, fmtAgo, modalShell, bindModal, uiConfirm, uiError, helpTip } from "../ui.js";
import { state } from "../state.js";
import { emit } from "../events.js";
import { agentAvatar } from "../agent-avatar.js";

const ROLES = ["backlog", "ready", "doing", "review", "done"];
const POLL_MS = 12000;

const view = {
  boardId: null,
  board: null,
  tab: "board", // board | logs
  loc: "", // filtro da lista: "" = todas | "<coleção>" | "<coleção>/<caminho>"
  timer: null,
  dragging: false,
  log: { card_id: "", actor: "", kind: "", since: "", until: "", events: [], next: null },
};

const canEdit = () => state.user?.role === "editor" || state.user?.role === "admin";

function stopPoll() {
  clearInterval(view.timer);
  view.timer = null;
}

/* ---------------------------------------------------------------- entrada */

async function renderBoardsScreen(main) {
  stopPoll();
  if (view.boardId) await renderBoard(main);
  else await renderBoardList(main);
}

function openBoard(id) {
  view.boardId = id;
  view.tab = "board";
  view.log = { card_id: "", actor: "", kind: "", since: "", until: "", events: [], next: null };
  renderBoardsScreen(document.getElementById("main"));
}

function leaveBoards() {
  stopPoll();
}

/* ---------------------------------------------------------------- lista de quadros */

const locOf = (b) => (b.folder_path ? `${b.collection}/${b.folder_path}` : b.collection);

async function renderBoardList(main) {
  main.innerHTML = `
    <section class="page boards-page">
      <header class="page-header"><h1>${icon("square-kanban", 18)} ${t("boards.title")}</h1>
        <span class="sub">${t("boards.sub")}</span></header>
      <div class="boards-col">
      <div class="boards-toolbar">
        <select class="input boards-loc" id="boards-loc" aria-label="${t("boards.where")}"></select>
        <span class="grow"></span>
        ${canEdit() ? `<button class="btn primary" id="board-new">${icon("plus")}<span>${t("boards.new")}</span></button>` : ""}
      </div>
      <div id="boards-list" class="boards-list"><div class="muted pad">${t("common.loading")}</div></div>
      </div>
    </section>`;
  await loadIcons(main);
  let all;
  try {
    all = await api.get("/boards");
  } catch (err) {
    document.getElementById("boards-list").innerHTML = `<div class="form-error">${escHtml(err.detail || err.message)}</div>`;
    return;
  }
  const tree = await api.get("/folders").catch(() => ({ collections: [], folders: [] }));
  const sel = document.getElementById("boards-loc");
  const opts = [`<option value="">${t("boards.allPlaces")}</option>`];
  for (const c of tree.collections || []) {
    opts.push(`<option value="${escHtml(c.name)}">${escHtml(c.name)}</option>`);
    for (const f of (tree.folders || []).filter((x) => x.collection === c.name).sort((a, b) => a.path.localeCompare(b.path))) {
      opts.push(`<option value="${escHtml(`${c.name}/${f.path}`)}">${escHtml(`${c.name} › ${f.path.replaceAll("/", " › ")}`)}</option>`);
    }
  }
  sel.innerHTML = opts.join("");
  sel.value = view.loc;
  const paint = () => {
    const pre = view.loc;
    const list = all.filter((b) => !pre || locOf(b) === pre || locOf(b).startsWith(`${pre}/`));
    const box = document.getElementById("boards-list");
    if (!list.length) {
      box.innerHTML = `<div class="empty-state">${icon("square-kanban", 28)}<p>${t("boards.empty")}</p></div>`;
      loadIcons(box);
      return;
    }
    const groups = new Map();
    for (const b of list) {
      const k = locOf(b);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(b);
    }
    box.innerHTML = [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(
        ([loc, bs]) => `
        <div class="boards-group">
          <div class="boards-group-head">${icon("folder", 14)}<span>${escHtml(loc.replaceAll("/", " › "))}</span></div>
          ${bs.map(boardRow).join("")}
        </div>`,
      )
      .join("");
    loadIcons(box);
  };
  paint();
  sel.addEventListener("change", () => {
    view.loc = sel.value;
    paint();
  });
  document.getElementById("boards-list").addEventListener("click", (e) => {
    const row = e.target.closest("[data-board]");
    if (row) openBoard(row.dataset.board);
  });
  document.getElementById("board-new")?.addEventListener("click", () => openNewBoard(tree));
}

function boardRow(b) {
  const stat = (n, key, cls = "") => (n ? `<span class="kb-stat ${cls}">${n} ${t(`boards.stat.${key}`, { count: n })}</span>` : "");
  return `
    <button class="board-row" data-board="${b.id}">
      <span class="board-row-icon">${icon("square-kanban", 16)}</span>
      <span class="grow">
        <span class="board-row-name">${escHtml(b.name)}</span>
        ${b.description ? `<span class="board-row-desc">${escHtml(b.description)}</span>` : ""}
      </span>
      <span class="board-row-stats">
        ${stat(b.open, "open")}${stat(b.doing, "doing", "doing")}${stat(b.review, "review")}${stat(b.blocked, "blocked", "danger")}${stat(b.overdue, "overdue", "danger")}
        ${!b.open ? `<span class="kb-stat">${t("boards.stat.none")}</span>` : ""}
      </span>
      <span class="muted board-row-when">${fmtAgo(b.updated_at)}</span>
    </button>`;
}

function openNewBoard(tree) {
  document.getElementById("board-new-modal")?.remove();
  const cols = (tree.collections || []).map((c) => `<option value="${escHtml(c.name)}">${escHtml(c.name)}</option>`).join("");
  const body = `
    <label class="form-label" for="nb-name">${t("boards.name")}</label>
    <input class="input" id="nb-name" maxlength="120" required placeholder="${t("boards.namePh")}" />
    <div class="form-grid2">
      <div><label class="form-label" for="nb-col">${t("boards.collection")}</label><select class="input" id="nb-col">${cols}</select></div>
      <div><label class="form-label" for="nb-path">${t("boards.folder")}</label><select class="input" id="nb-path"></select></div>
    </div>
    <label class="form-label" for="nb-desc">${t("boards.description")}</label>
    <textarea class="input" id="nb-desc" rows="2" placeholder="${t("boards.descPh")}"></textarea>
    <label class="form-label" for="nb-cols">${t("boards.columns")} ${helpTip(t("boards.columnsHelp"))}</label>
    <input class="input" id="nb-cols" placeholder="${t("boards.columnsPh")}" />`;
  document.body.insertAdjacentHTML(
    "beforeend",
    modalShell({ id: "board-new", iconName: "square-kanban", title: t("boards.new"), body, submitLabel: t("boards.create"), width: 560 }),
  );
  const modal = document.getElementById("board-new-modal");
  loadIcons(modal);
  const colSel = document.getElementById("nb-col");
  const pathSel = document.getElementById("nb-path");
  const fillPaths = () => {
    const fs = (tree.folders || []).filter((f) => f.collection === colSel.value).sort((a, b) => a.path.localeCompare(b.path));
    pathSel.innerHTML = `<option value="">${t("boards.root")}</option>` + fs.map((f) => `<option value="${escHtml(f.path)}">${escHtml(f.path.replaceAll("/", " › "))}</option>`).join("");
  };
  colSel.addEventListener("change", fillPaths);
  if (view.loc) {
    const [c, ...rest] = view.loc.split("/");
    colSel.value = c;
    fillPaths();
    pathSel.value = rest.join("/");
  } else fillPaths();
  const setOpen = bindModal("board-new");
  setOpen(true);
  document.getElementById("board-new-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const columns = document.getElementById("nb-cols").value.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      const b = await api.post("/boards", {
        collection: colSel.value,
        name: document.getElementById("nb-name").value,
        folder_path: pathSel.value || null,
        description: document.getElementById("nb-desc").value || null,
        columns: columns.length ? columns : null,
      });
      setOpen(false);
      modal.remove();
      openBoard(b.id);
    } catch (err) {
      uiError(t("boards.createFailed"), err);
    }
  });
}

/* ---------------------------------------------------------------- o quadro */

async function fetchBoard() {
  view.board = await api.get(`/boards/${view.boardId}`);
  return view.board;
}

async function renderBoard(main) {
  main.innerHTML = `<section class="page board-page"><div class="muted pad">${t("common.loading")}</div></section>`;
  try {
    await fetchBoard();
  } catch (err) {
    view.boardId = null;
    uiError(t("boards.openFailed"), err);
    return renderBoardList(main);
  }
  const b = view.board;
  main.innerHTML = `
    <section class="page board-page">
      <button class="board-back" id="board-back">${icon("arrow-left", 14)}<span>${t("boards.title")}</span></button>
      <header class="board-head">
        <div class="board-title grow">
          <h1 id="board-name" ${canEdit() ? `class="editable" title="${t("boards.rename")}"` : ""}>${escHtml(b.name)}</h1>
          <span class="board-path">${icon("folder", 13)}${escHtml([b.collection, ...(b.folder_path ? b.folder_path.split("/") : [])].join(" › "))}</span>
        </div>
        <div class="seg board-tabs" role="tablist">
          <button role="tab" data-tab="board" class="${view.tab === "board" ? "active" : ""}">${t("boards.tab.board")}</button>
          <button role="tab" data-tab="logs" class="${view.tab === "logs" ? "active" : ""}">${t("boards.tab.logs")}</button>
        </div>
      </header>
      <div id="board-body"></div>
    </section>`;
  await loadIcons(main);
  document.getElementById("board-back").addEventListener("click", () => {
    view.boardId = null;
    renderBoardsScreen(main);
  });
  main.querySelector(".board-tabs").addEventListener("click", (e) => {
    const tab = e.target.closest("[data-tab]")?.dataset.tab;
    if (!tab || tab === view.tab) return;
    view.tab = tab;
    main.querySelectorAll(".board-tabs [data-tab]").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    paintTab();
  });
  if (canEdit()) bindRename(document.getElementById("board-name"), (name) => api.patch(`/boards/${b.id}`, { name }));
  paintTab();
}

function paintTab() {
  stopPoll();
  if (view.tab === "logs") return renderLogs();
  paintColumns();
  view.timer = setInterval(poll, POLL_MS);
}

async function poll() {
  if (document.hidden || view.dragging || document.querySelector(".modal-backdrop:not(.hidden)") || view.tab !== "board") return;
  if (!document.getElementById("kb-board")) return stopPoll();
  const before = JSON.stringify(view.board);
  try {
    await fetchBoard();
  } catch {
    return;
  }
  if (JSON.stringify(view.board) !== before) paintColumns();
}

function bindRename(el, save) {
  el.addEventListener("click", () => {
    if (el.querySelector("input")) return;
    const old = el.textContent;
    el.innerHTML = `<input class="input rename-input" value="${escHtml(old)}" maxlength="120" />`;
    const input = el.querySelector("input");
    input.focus();
    input.select();
    let done = false;
    const finish = async (commit) => {
      if (done) return;
      done = true;
      const v = input.value.trim();
      el.textContent = commit && v ? v : old;
      if (commit && v && v !== old) {
        try {
          await save(v);
        } catch (err) {
          el.textContent = old;
          uiError(t("boards.saveFailed"), err);
        }
      }
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") finish(true);
      if (e.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
  });
}

/* ---------- colunas e cartões */

function claimInfo(c) {
  if (!c.claim && !c.worked_by) return "";
  const raw = c.claimed_by || c.worked_by;
  const who = actorView(raw);
  const label = c.claim === "stale" ? t("boards.stale") : c.claim === "active" ? fmtAgo(c.updated_at) : t("boards.workedBy");
  return `<span class="kb-claim ${c.claim || "done"}" title="${escHtml(raw)}">${who.ava}<span>${escHtml(who.name)}</span><span class="muted">· ${label}</span></span>`;
}

function cardHtml(c) {
  const [done, total] = (c.progress || "0/0").split("/").map(Number);
  const overdue = c.due && c.role !== "done" && c.due < new Date().toISOString().slice(0, 10);
  return `
    <article class="kb-card ${c.blocked ? "blocked" : ""} ${c.claim === "active" ? "held" : ""} ${c.claim === "stale" ? "stale" : ""}"
      draggable="${canEdit()}" data-card="${c.id}">
      ${c.labels?.length ? `<div class="kb-labels">${c.labels.map((l) => `<span class="kb-label">${escHtml(l)}</span>`).join("")}</div>` : ""}
      <div class="kb-card-title">${escHtml(c.title)}</div>
      ${c.blocked ? `<div class="kb-blocked">${icon("ban", 12)}<span>${escHtml(c.blocked)}</span></div>` : ""}
      ${total ? `<div class="kb-progress"><i style="width:${(done / total) * 100}%"></i></div>` : ""}
      ${c.branch || c.worktree ? `<div class="kb-work">${icon("git-branch", 12)}<span>${escHtml(c.branch || c.worktree)}</span></div>` : ""}
      ${
        c.claim || c.worked_by || c.mr_url || c.due || total
          ? `<div class="kb-card-foot">
        ${claimInfo(c)}
        <span class="grow"></span>
        ${c.mr_url ? `<span class="kb-meta" title="MR">${icon("git-pull-request-arrow", 12)}</span>` : ""}
        ${c.due ? `<span class="kb-meta ${overdue ? "danger" : ""}">${icon("calendar", 12)}${escHtml(new Date(c.due + "T12:00").toLocaleDateString(undefined, { day: "2-digit", month: "short" }))}</span>` : ""}
        ${total ? `<span class="kb-meta">${icon("list-checks", 12)}${done}/${total}</span>` : ""}
      </div>`
          : ""
      }
    </article>`;
}

function columnHtml(col) {
  const over = col.wip_limit && col.count > col.wip_limit;
  return `
    <section class="kb-col" data-col="${col.id}">
      <header class="kb-col-head" ${canEdit() ? 'draggable="true"' : ""} data-col-drag="${col.id}">
        <span class="kb-col-name ${canEdit() ? "editable" : ""}" data-col-name="${col.id}">${escHtml(col.name)}</span>
        <span class="kb-count ${over ? "danger" : ""}">${col.count}${col.wip_limit ? `/${col.wip_limit}` : ""}</span>
        ${col.role ? `<span class="kb-role" title="${t("boards.roleHelp")}">${t(`boards.role.${col.role}`)}</span>` : ""}
        <span class="grow"></span>
        ${canEdit() ? `<button class="icon-btn sm" data-col-menu="${col.id}" title="${t("boards.columnMenu")}">${icon("ellipsis", 14)}</button>` : ""}
      </header>
      <div class="kb-cards" data-drop="${col.id}">
        ${col.cards.map(cardHtml).join("")}
        ${col.hidden ? `<div class="muted kb-hidden">${t("boards.hiddenDone", { count: col.hidden })}</div>` : ""}
      </div>
      ${canEdit() ? `<button class="kb-add" data-add="${col.id}">${icon("plus", 14)}<span>${t("boards.addCard")}</span></button>` : ""}
    </section>`;
}

function paintColumns() {
  const body = document.getElementById("board-body");
  if (!body) return;
  const scroll = document.getElementById("kb-board")?.scrollLeft || 0;
  const b = view.board;
  body.innerHTML = `
    <div class="kb-board" id="kb-board">
      ${b.columns.map(columnHtml).join("")}
      ${canEdit() ? `<button class="kb-newcol" id="kb-newcol">${icon("plus", 16)}<span>${t("boards.addColumn")}</span></button>` : ""}
    </div>`;
  loadIcons(body);
  document.getElementById("kb-board").scrollLeft = scroll;
  bindBoard();
}

async function refresh() {
  await fetchBoard();
  paintColumns();
}

const act = async (fn) => {
  try {
    await fn();
  } catch (err) {
    uiError(t("boards.saveFailed"), err);
  }
  await refresh().catch(() => {});
};

function bindBoard() {
  const root = document.getElementById("kb-board");
  root.addEventListener("click", (e) => {
    const card = e.target.closest("[data-card]");
    if (card) return openCard(card.dataset.card);
    const add = e.target.closest("[data-add]");
    if (add) return quickAdd(add);
    const menu = e.target.closest("[data-col-menu]");
    if (menu) return columnMenu(menu.dataset.colMenu, menu);
    if (e.target.closest("#kb-newcol")) return newColumn();
  });
  if (!canEdit()) return;
  root.querySelectorAll("[data-col-name]").forEach((el) =>
    bindRename(el, (name) => api.patch(`/columns/${el.dataset.colName}`, { name }).then(refresh)),
  );
  bindDrag(root);
}

/* ---------- arrastar: cartões entre/dentro das colunas e colunas entre si */

function dropIndex(zone, y) {
  const cards = [...zone.querySelectorAll(".kb-card:not(.dragging)")];
  const i = cards.findIndex((c) => {
    const r = c.getBoundingClientRect();
    return y < r.top + r.height / 2;
  });
  return i === -1 ? cards.length : i;
}

function bindDrag(root) {
  let kind = null;
  let id = null;
  const ghost = document.createElement("div");
  ghost.className = "kb-ghost";
  root.addEventListener("dragstart", (e) => {
    const card = e.target.closest?.("[data-card]");
    const col = e.target.closest?.("[data-col-drag]");
    if (card) {
      kind = "card";
      id = card.dataset.card;
      card.classList.add("dragging");
    } else if (col) {
      kind = "col";
      id = col.dataset.colDrag;
      col.closest(".kb-col").classList.add("dragging");
    } else return;
    view.dragging = true;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", id);
  });
  root.addEventListener("dragend", () => {
    view.dragging = false;
    root.querySelectorAll(".dragging").forEach((x) => x.classList.remove("dragging"));
    root.querySelectorAll(".drop-target").forEach((x) => x.classList.remove("drop-target"));
    ghost.remove();
    kind = null;
  });
  root.addEventListener("dragover", (e) => {
    if (!kind) return;
    if (kind === "card") {
      const zone = e.target.closest(".kb-cards");
      if (!zone) return;
      e.preventDefault();
      const i = dropIndex(zone, e.clientY);
      const cards = [...zone.querySelectorAll(".kb-card:not(.dragging)")];
      if (cards[i]) zone.insertBefore(ghost, cards[i]);
      else zone.appendChild(ghost);
    } else {
      const col = e.target.closest(".kb-col");
      if (!col) return;
      e.preventDefault();
      root.querySelectorAll(".drop-target").forEach((x) => x.classList.remove("drop-target"));
      col.classList.add("drop-target");
    }
  });
  root.addEventListener("drop", (e) => {
    if (!kind) return;
    e.preventDefault();
    if (kind === "card") {
      const zone = e.target.closest(".kb-cards") || ghost.parentElement;
      if (!zone) return;
      const cards = [...zone.children].filter((x) => x.classList.contains("kb-card") || x === ghost);
      const position = cards.indexOf(ghost);
      const column = zone.dataset.drop;
      const cardId = id;
      ghost.remove();
      act(() => api.patch(`/cards/${cardId}`, { column, position: Math.max(0, position) }));
    } else {
      const target = e.target.closest(".kb-col");
      if (!target) return;
      const position = view.board.columns.findIndex((c) => c.id === target.dataset.col);
      const colId = id;
      act(() => api.patch(`/columns/${colId}`, { position }));
    }
  });
}

/* ---------- criar cartão / coluna, menu da coluna */

function quickAdd(btn) {
  const colId = btn.dataset.add;
  btn.outerHTML = `<form class="kb-quick" data-quick="${colId}"><textarea class="input" rows="2" maxlength="300" placeholder="${t("boards.cardTitlePh")}"></textarea>
    <div class="kb-quick-actions"><button class="btn primary sm" type="submit">${t("boards.addCard")}</button><button class="btn ghost sm" type="button" data-cancel>${t("common.cancel")}</button></div></form>`;
  const form = document.querySelector(`[data-quick="${colId}"]`);
  const ta = form.querySelector("textarea");
  ta.focus();
  const submit = async () => {
    const title = ta.value.trim();
    if (!title) return paintColumns();
    view.dragging = true; // segura o poll enquanto grava
    try {
      await api.post(`/boards/${view.boardId}/cards`, { title, column: colId });
    } catch (err) {
      uiError(t("boards.saveFailed"), err);
    }
    view.dragging = false;
    await refresh();
    document.querySelector(`[data-add="${colId}"]`)?.click(); // segue criando na mesma coluna
  };
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submit();
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
    if (e.key === "Escape") paintColumns();
  });
  form.querySelector("[data-cancel]").addEventListener("click", () => paintColumns());
}

async function newColumn() {
  const btn = document.getElementById("kb-newcol");
  btn.outerHTML = `<form class="kb-col kb-col-new" id="kb-newcol-form"><input class="input" maxlength="80" placeholder="${t("boards.columnNamePh")}" />
    <div class="kb-quick-actions"><button class="btn primary sm" type="submit">${t("boards.addColumn")}</button><button class="btn ghost sm" type="button" data-cancel>${t("common.cancel")}</button></div></form>`;
  const form = document.getElementById("kb-newcol-form");
  const input = form.querySelector("input");
  input.focus();
  form.querySelector("[data-cancel]").addEventListener("click", () => paintColumns());
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const name = input.value.trim();
    if (!name) return paintColumns();
    act(() => api.post(`/boards/${view.boardId}/columns`, { name }));
  });
}

function columnMenu(colId, anchor) {
  document.querySelector(".kb-menu")?.remove();
  const col = view.board.columns.find((c) => c.id === colId);
  const used = new Set(view.board.columns.filter((c) => c.id !== colId && c.role).map((c) => c.role));
  const others = view.board.columns.filter((c) => c.id !== colId);
  const menu = document.createElement("div");
  menu.className = "kb-menu";
  menu.innerHTML = `
    <label class="form-label">${t("boards.role.label")} ${helpTip(t("boards.roleHelp"))}</label>
    <select class="input" data-role>
      <option value="">${t("boards.role.none")}</option>
      ${ROLES.map((r) => `<option value="${r}" ${col.role === r ? "selected" : ""} ${used.has(r) ? "disabled" : ""}>${t(`boards.role.${r}`)}${used.has(r) ? ` (${t("boards.role.taken")})` : ""}</option>`).join("")}
    </select>
    <label class="form-label">${t("boards.wip")}</label>
    <input class="input" data-wip type="number" min="0" value="${col.wip_limit || ""}" placeholder="—" />
    ${
      others.length
        ? `<hr />${col.count ? `<label class="form-label">${t("boards.moveCardsTo")}</label><select class="input" data-move>${others.map((o) => `<option value="${o.id}">${escHtml(o.name)}</option>`).join("")}</select>` : ""}
           <button class="btn sm danger-text" data-del>${icon("trash-2", 14)}<span>${t("boards.deleteColumn")}</span></button>`
        : ""
    }`;
  document.body.appendChild(menu);
  loadIcons(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.top = `${r.bottom + 6 + window.scrollY}px`;
  menu.style.left = `${Math.min(r.left, window.innerWidth - 280)}px`;
  const close = (e) => {
    if (e && menu.contains(e.target)) return;
    menu.remove();
    document.removeEventListener("mousedown", close);
  };
  setTimeout(() => document.addEventListener("mousedown", close));
  menu.querySelector("[data-role]").addEventListener("change", (e) => {
    close();
    act(() => api.patch(`/columns/${colId}`, { role: e.target.value }));
  });
  menu.querySelector("[data-wip]").addEventListener("change", (e) => {
    close();
    act(() => api.patch(`/columns/${colId}`, { wip_limit: Number(e.target.value) || 0 }));
  });
  menu.querySelector("[data-del]")?.addEventListener("click", async () => {
    const move = menu.querySelector("[data-move]")?.value;
    close();
    const ok = await uiConfirm({ title: t("boards.deleteColumnQ", { name: col.name }), confirmLabel: t("boards.deleteColumn"), tone: "danger" });
    if (ok) act(() => api.del(`/columns/${colId}${move ? `?move_cards_to=${encodeURIComponent(move)}` : ""}`));
  });
}

/* ---------------------------------------------------------------- janela do cartão */

const KIND_ICON = { card: "square-kanban", claim: "lock-open", item: "list-checks", column: "table-2", board: "square-kanban", log: "file-text" };
// campos que aparecem como "antes → depois" na atividade (o resto o próprio título do evento já diz)
const SHOWN_CHANGES = new Set(["title", "column", "due_date", "labels", "assignee", "blocked", "branch", "worktree", "mr_url", "ci_status", "repo", "name", "role", "folder_path", "item", "claimed_by"]);
const agentNames = new Map(); // slug → nome (carregado ao abrir o cartão)

// quem fez, legível: agente pelo nome com o avatar dele, pessoa pelo usuário, integração pelo logo
function actorView(actor) {
  if (!actor || actor === "system") return { name: t("boards.system"), ava: `<span class="avatar xs sys">${icon("clock", 11)}</span>` };
  if (actor.startsWith("agent:")) {
    const slug = actor.slice(6);
    return { name: agentNames.get(slug) || slug, ava: agentAvatar(slug, 20) };
  }
  if (actor.startsWith("integration:")) {
    const prov = actor.slice(12);
    const name = prov.charAt(0).toUpperCase() + prov.slice(1);
    return { name, ava: prov === "trello" ? `<span class="int-logo trello sm" aria-hidden="true"><span></span><span></span></span>` : `<span class="avatar xs">${escHtml(name[0])}</span>` };
  }
  const name = actor.replace(/^(dash|key):/, "");
  return { name, ava: `<span class="avatar xs">${escHtml(name.slice(0, 1).toUpperCase())}</span>` };
}

const fmtVal = (v) => {
  if (v === null || v === undefined || v === "") return "—";
  if (v === true) return t("common.yes");
  if (v === false) return t("common.no");
  if (Array.isArray(v)) return v.join(", ");
  return String(v).replace(/^(dash|agent|integration):/, "");
};

function eventLine(e, cards, detailed = false) {
  const who = actorView(e.actor);
  const changes = e.kind.endsWith(".create")
    ? ""
    : Object.entries(e.changes || {})
        .filter(([k]) => SHOWN_CHANGES.has(k))
        .map(([k, [a, b]]) => `<span class="ev-change"><b>${escHtml(t(`boards.field.${k}`) === `boards.field.${k}` ? k : t(`boards.field.${k}`))}</b> ${escHtml(fmtVal(a))} → ${escHtml(fmtVal(b))}</span>`)
        .join("");
  const card = cards && e.card_id ? cards.get(e.card_id) : null;
  const label = t(`boards.kind.${e.kind}`);
  const client = e.client?.startsWith("integration:") ? actorView(e.client).name : e.client;
  const meta = detailed && (e.client || e.session_id) ? `<div class="ev-meta muted">${client ? escHtml(client) : ""}${e.session_id ? ` · ${t("boards.session")} ${escHtml(e.session_id.slice(0, 12))}` : ""}</div>` : "";
  return `
    <li class="ev">
      <span class="ev-ava">${who.ava}</span>
      <div class="grow">
        <div class="ev-head"><span><b>${escHtml(who.name)}</b> ${escHtml(label === `boards.kind.${e.kind}` ? e.kind : label)}${card ? ` <span class="ev-card">· ${escHtml(card)}</span>` : ""}</span>
          <span class="ev-when" title="${escHtml(fmtDate(e.created_at))}">${fmtAgo(e.created_at)}</span></div>
        ${e.note ? `<div class="ev-note">${escHtml(e.note)}</div>` : ""}
        ${changes ? `<div class="ev-changes">${changes}</div>` : ""}
        ${meta}
      </div>
    </li>`;
}

async function openCard(cardId) {
  let c;
  try {
    c = await api.get(`/cards/${cardId}`);
  } catch (err) {
    return uiError(t("boards.openFailed"), err);
  }
  const agents = await api.get("/agents").catch(() => []);
  for (const a of agents) agentNames.set(a.slug, a.name || a.slug);
  document.getElementById("card-modal")?.remove();
  const ro = !canEdit();
  const dis = ro ? "disabled" : "";
  const prop = (id, label, value, ph = "", type = "text") =>
    `<label class="prop" for="cd-${id}"><span class="prop-k">${label}</span><input class="prop-v" id="cd-${id}" type="${type}" value="${escHtml(value || "")}" placeholder="${escHtml(ph)}" ${dis} /></label>`;
  const [done, total] = (c.progress || "0/0").split("/").map(Number);
  const items = (c.items || [])
    .map(
      (i) => `
      <li class="cl-item ${i.done ? "done" : ""}" data-item="${i.id}">
        <input type="checkbox" ${i.done ? "checked" : ""} ${dis} data-check="${i.id}" aria-label="${escHtml(i.text)}" />
        <span class="cl-text">${escHtml(i.text)}</span>
        ${i.done && i.done_by ? `<span class="cl-by" title="${escHtml(fmtDate(i.done_at))}">${escHtml(actorView(i.done_by).name)}</span>` : ""}
        ${ro ? "" : `<button class="icon-btn sm cl-del" type="button" data-del-item="${i.id}" title="${t("boards.removeItem")}">${icon("x", 12)}</button>`}
      </li>`,
    )
    .join("");
  const assigneeOpts = [`<option value="">${t("boards.nobody")}</option>`]
    .concat(agents.filter((a) => a.status === "active").map((a) => `<option value="agent:${escHtml(a.slug)}" ${c.assignee === `agent:${a.slug}` ? "selected" : ""}>${escHtml(a.name || a.slug)}</option>`))
    .concat([`<option value="${escHtml(state.user.username)}" ${c.assignee === state.user.username ? "selected" : ""}>${escHtml(state.user.username)} (${t("users.you")})</option>`])
    .join("");
  const claim =
    c.claim
      ? `<div class="cd-claim ${c.claim}">
          ${claimInfo(c)}
          <span class="muted">${c.claim === "active" ? t("boards.leaseUntil", { time: fmtDate(c.lease_until) }) : t("boards.staleHelp")}</span>
          ${ro ? "" : `<button class="btn sm" type="button" id="cd-release">${icon("lock-open", 14)}<span>${t("boards.release")}</span></button>`}
        </div>`
      : "";
  const docs = (c.documents || []).map((d) => `<button class="chip link" type="button" data-open-note="${d.id}">${icon("file-text", 12)}<span>${escHtml(d.title)}</span></button>`).join("");
  const codeFilled = [c.repo, c.worktree, c.branch, c.mr_url, c.ci].filter(Boolean).length;
  const creator = c.created_by ? actorView(c.created_by).name : null;
  const body = `
    ${claim}
    <textarea class="cd-title" id="cd-title" rows="1" maxlength="300" ${dis} aria-label="${t("boards.cardTitle")}">${escHtml(c.title)}</textarea>
    <div class="cd-grid">
      <div class="cd-main">
        <textarea class="cd-desc" id="cd-desc" rows="3" ${dis} placeholder="${t("boards.descAddPh")}">${escHtml(c.description || "")}</textarea>
        <section class="cd-sec">
          <h3>${icon("list-checks", 14)}<span>${t("boards.checklist")}</span>${total ? `<span class="muted">${done}/${total}</span>` : ""}</h3>
          ${total ? `<div class="kb-progress cd-progress"><i style="width:${(done / total) * 100}%"></i></div>` : ""}
          <ul class="cl" id="cd-items">${items}</ul>
          ${ro ? "" : `<div class="cl-add">${icon("plus", 14)}<input class="cl-new" id="cd-new-item" maxlength="500" placeholder="${t("boards.addItemPh")}" /></div>`}
        </section>
        ${docs ? `<section class="cd-sec"><h3>${icon("file-text", 14)}<span>${t("boards.linkedNotes")}</span></h3><div class="cd-docs">${docs}</div></section>` : ""}
        <section class="cd-sec">
          <h3>${icon("activity", 14)}<span>${t("boards.activity")}</span></h3>
          <ul class="ev-list compact">${(c.log || []).map((e) => eventLine(e)).join("") || `<li class="muted">—</li>`}</ul>
        </section>
      </div>
      <aside class="cd-side">
        <div class="props">
          <label class="prop" for="cd-assignee"><span class="prop-k">${t("boards.assignee")}</span><select class="prop-v" id="cd-assignee" ${dis}>${assigneeOpts}</select></label>
          ${prop("due", t("boards.due"), c.due, "", "date")}
          ${prop("labels", t("boards.labels"), (c.labels || []).join(", "), t("boards.labelsAddPh"))}
          <label class="prop ${c.blocked ? "danger" : "hidden"}" for="cd-blocked" id="cd-blocked-row"><span class="prop-k">${t("boards.blocked")}</span><input class="prop-v" id="cd-blocked" value="${escHtml(c.blocked || "")}" placeholder="${t("boards.blockedPh")}" ${dis} /></label>
        </div>
        ${ro || c.blocked ? "" : `<button class="btn sm ghost cd-block-btn" type="button" id="cd-block">${icon("ban", 14)}<span>${t("boards.markBlocked")}</span></button>`}
        <details class="cd-code" ${codeFilled ? "open" : ""}>
          <summary>${icon("git-branch", 14)}<span>${t("boards.codeSection")}</span>${codeFilled ? `<span class="muted">${codeFilled}</span>` : ""}</summary>
          <div class="props">
            ${prop("repo", t("boards.repo"), c.repo, "web-server")}
            ${prop("worktree", "Worktree", c.worktree, "worktrees/…")}
            ${prop("branch", "Branch", c.branch, "feat/…")}
            ${prop("mr", "MR", c.mr_url, "https://…")}
            ${prop("ci", "CI", c.ci, t("boards.ciPh"))}
          </div>
          ${c.mr_url ? `<a class="btn sm ghost" href="${escHtml(c.mr_url)}" target="_blank" rel="noopener noreferrer">${icon("external-link", 14)}<span>${t("boards.openMr")}</span></a>` : ""}
        </details>
        ${c.usage?.sessions ? `<div class="cd-usage" title="${escHtml(t("usage.costHelp"))}"><span class="prop-k">${t("boards.realCost")}</span><span>${escHtml(t("boards.realCostDetail", { cost: new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(c.usage.cost), count: c.usage.sessions }))}</span></div>` : ""}
        <div class="cd-foot muted">${creator ? t("boards.createdBy", { who: creator, when: fmtAgo(c.created_at) }) : ""}</div>
        ${ro ? "" : `<button class="btn sm ghost danger-text" type="button" id="cd-archive">${icon("archive", 14)}<span>${t("boards.archiveCard")}</span></button>`}
      </aside>
    </div>`;
  document.body.insertAdjacentHTML(
    "beforeend",
    modalShell({ id: "card", iconName: "square-kanban", title: escHtml(c.board), subtitle: escHtml(c.column), body, submitLabel: t("boards.save"), submitIcon: "save", width: 980 }),
  );
  const modal = document.getElementById("card-modal");
  if (ro) modal.querySelector('button[type="submit"]').remove();
  loadIcons(modal);
  const setOpen = bindModal("card");
  setOpen(true);
  document.activeElement?.blur(); // abre para ler, não com o título em edição
  modal.classList.add("card-modal");
  const grow = (ta) => {
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  };
  for (const ta of modal.querySelectorAll(".cd-title, .cd-desc")) {
    grow(ta);
    ta.addEventListener("input", () => grow(ta));
  }
  modal.querySelector(".cd-title").addEventListener("keydown", (e) => e.key === "Enter" && e.preventDefault());
  document.getElementById("cd-block")?.addEventListener("click", (e) => {
    e.currentTarget.remove();
    const row = document.getElementById("cd-blocked-row");
    row.classList.remove("hidden");
    row.classList.add("danger");
    document.getElementById("cd-blocked").focus();
  });
  const closeAndRefresh = () => {
    setOpen(false);
    modal.remove();
    refresh().catch(() => {});
  };
  modal.querySelectorAll("[data-close-modal]").forEach((b) => b.addEventListener("click", () => setTimeout(() => modal.remove(), 0)));
  modal.addEventListener("click", async (e) => {
    const note = e.target.closest("[data-open-note]");
    if (note) {
      setOpen(false);
      modal.remove();
      stopPoll();
      return emit("navigate", { screen: "notes", openId: note.dataset.openNote });
    }
    const del = e.target.closest("[data-del-item]");
    if (del) {
      try {
        await api.patch(`/cards/${cardId}/items/${del.dataset.delItem}`, { delete: true });
        del.closest("li").remove();
      } catch (err) {
        uiError(t("boards.saveFailed"), err);
      }
    }
  });
  modal.querySelectorAll("[data-check]").forEach((cb) =>
    cb.addEventListener("change", async () => {
      try {
        await api.post(`/cards/${cardId}/check`, { item_id: Number(cb.dataset.check), done: cb.checked });
        cb.closest("li").classList.toggle("done", cb.checked);
      } catch (err) {
        cb.checked = !cb.checked;
        uiError(t("boards.saveFailed"), err);
      }
    }),
  );
  const addItem = async () => {
    const input = document.getElementById("cd-new-item");
    const text = input.value.trim();
    if (!text) return;
    try {
      await api.post(`/cards/${cardId}/items`, { items: [text] });
      setOpen(false);
      modal.remove();
      openCard(cardId);
    } catch (err) {
      uiError(t("boards.saveFailed"), err);
    }
  };
  document.getElementById("cd-add-item")?.addEventListener("click", addItem);
  document.getElementById("cd-new-item")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addItem();
    }
  });
  document.getElementById("cd-release")?.addEventListener("click", async () => {
    const ok = await uiConfirm({ title: t("boards.releaseQ"), message: t("boards.releaseHelp"), confirmLabel: t("boards.release") });
    if (!ok) return;
    try {
      await api.post(`/cards/${cardId}/release`, { next_step: t("boards.releasedByDash", { who: state.user.username }) });
      closeAndRefresh();
    } catch (err) {
      uiError(t("boards.saveFailed"), err);
    }
  });
  document.getElementById("cd-archive")?.addEventListener("click", async () => {
    const ok = await uiConfirm({ title: t("boards.archiveQ"), confirmLabel: t("boards.archiveCard"), tone: "danger" });
    if (!ok) return;
    try {
      await api.patch(`/cards/${cardId}`, { archived: true });
      closeAndRefresh();
    } catch (err) {
      uiError(t("boards.saveFailed"), err);
    }
  });
  document.getElementById("card-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const val = (id) => document.getElementById(`cd-${id}`).value;
    const patch = {
      title: val("title"),
      description: val("desc"),
      due: val("due"),
      labels: val("labels").split(",").map((s) => s.trim()).filter(Boolean),
      assignee: val("assignee"),
      blocked_reason: val("blocked"),
      repo: val("repo"),
      worktree: val("worktree"),
      branch: val("branch"),
      mr_url: val("mr"),
      ci_status: val("ci"),
    };
    // só manda o que mudou (cada campo alterado vira uma linha "antes → depois" no log)
    const before = { title: c.title, description: c.description || "", due: c.due || "", labels: c.labels || [], assignee: c.assignee || "", blocked_reason: c.blocked || "", repo: c.repo || "", worktree: c.worktree || "", branch: c.branch || "", mr_url: c.mr_url || "", ci_status: c.ci || "" };
    const changed = Object.fromEntries(Object.entries(patch).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(before[k])));
    if (!Object.keys(changed).length) return closeAndRefresh();
    try {
      await api.patch(`/cards/${cardId}`, changed);
      closeAndRefresh();
    } catch (err) {
      uiError(t("boards.saveFailed"), err);
    }
  });
}

/* ---------------------------------------------------------------- aba Logs */

function logQuery() {
  const l = view.log;
  return { card_id: l.card_id, actor: l.actor, kind: l.kind, since: l.since, until: l.until };
}

async function renderLogs() {
  const body = document.getElementById("board-body");
  const cards = new Map(view.board.columns.flatMap((c) => c.cards.map((k) => [k.id, k.title])));
  const l = view.log;
  body.innerHTML = `
    <div class="log-filters">
      <select class="input" id="lg-card"><option value="">${t("boards.log.allCards")}</option>${[...cards.entries()].map(([id, title]) => `<option value="${id}" ${l.card_id === id ? "selected" : ""}>${escHtml(title)}</option>`).join("")}</select>
      <input class="input" id="lg-actor" placeholder="${t("boards.log.who")}" value="${escHtml(l.actor)}" />
      <select class="input" id="lg-kind">
        <option value="">${t("boards.log.allKinds")}</option>
        ${["card", "claim", "item", "column", "board", "log"].map((k) => `<option value="${k}" ${l.kind === k ? "selected" : ""}>${t(`boards.log.kind.${k}`)}</option>`).join("")}
      </select>
      <input class="input" type="date" id="lg-since" value="${escHtml(l.since)}" aria-label="${t("boards.log.since")}" />
      <input class="input" type="date" id="lg-until" value="${escHtml(l.until)}" aria-label="${t("boards.log.until")}" />
      <span class="grow"></span>
      <a class="btn sm" id="lg-csv" download>${icon("download", 14)}<span>${t("boards.log.csv")}</span></a>
    </div>
    <ul class="ev-list log-list" id="lg-list"><li class="muted">${t("common.loading")}</li></ul>
    <div class="log-more"><button class="btn sm ghost hidden" id="lg-more">${t("boards.log.more")}</button></div>`;
  loadIcons(body);
  const reload = async (append = false) => {
    const params = { ...logQuery(), limit: 100 };
    if (append && l.next) params.before_id = l.next;
    const page = await api.get(`/boards/${view.boardId}/log`, Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v != null)));
    l.events = append ? l.events.concat(page.events) : page.events;
    l.next = page.next_before_id;
    document.getElementById("lg-list").innerHTML = l.events.map((e) => eventLine(e, cards, true)).join("") || `<li class="muted">${t("boards.log.empty")}</li>`;
    loadIcons(document.getElementById("lg-list"));
    document.getElementById("lg-more").classList.toggle("hidden", !l.next);
    const qs = new URLSearchParams(Object.entries(logQuery()).filter(([, v]) => v));
    document.getElementById("lg-csv").href = `/dash/api/boards/${view.boardId}/log.csv${qs.toString() ? `?${qs}` : ""}`;
  };
  const bind = (id, key, ev = "change") =>
    document.getElementById(id).addEventListener(ev, (e) => {
      l[key] = e.target.value;
      reload().catch((err) => uiError(t("boards.openFailed"), err));
    });
  bind("lg-card", "card_id");
  bind("lg-kind", "kind");
  bind("lg-since", "since");
  bind("lg-until", "until");
  bind("lg-actor", "actor");
  document.getElementById("lg-more").addEventListener("click", () => reload(true));
  await reload().catch((err) => uiError(t("boards.openFailed"), err));
}

export { renderBoardsScreen, leaveBoards };
