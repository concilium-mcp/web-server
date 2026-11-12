/* Tela do Painel — KPIs e gráficos SVG à mão (atividade, pastas, contribuidores, saúde). */
import { api } from "../api.js";
import { t, locale } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtAgo, debounce, uiError, helpTip } from "../ui.js";
import { state, colorFor } from "../state.js";
import { emit } from "../events.js";
import { openRangePicker, closeRangePicker } from "../range-picker.js";

/* ---------------------------------------------------------------- tela: painel (indicadores e gráficos) */

// Gráficos em SVG feito à mão (sem lib): uma série por gráfico, um tom só (--viz-accent, validado
// contra a superfície escura), barras <= 24px com ponta arredondada de 4px, grade hairline sólida,
// tooltip por marca e alternância "Tabela" em todo gráfico (o tooltip nunca é o único caminho).

const PERIODS = [7, 30, 90];

const isoDaysAgo = (n) => {
  const d = new Date();
  return new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate() - n)).toISOString().slice(0, 10);
};

// rótulo do botão personalizado: "Personalizado" ou "1 out – 15 out"
function rangeLabel() {
  const r = state.insightsRange;
  if (!r) return t("overview.custom");
  return `${fmtDay(r.start)} – ${fmtDay(r.end)}`;
}
// formatadores seguem o idioma atual (criados na hora: a troca de idioma vale sem recarregar)
const nf = { format: (v) => new Intl.NumberFormat(locale()).format(v) };
const nfCompact = { format: (v) => new Intl.NumberFormat(locale(), { notation: "compact", maximumFractionDigits: 1 }).format(v) };
const fmtDay = (iso, opts = { day: "numeric", month: "short" }) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(locale(), opts).replace(".", "");
const actorName = (a) => (a || "—").replace(/^dash:/, "");
const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

// eixo "limpo": 0 / 2 / 4… até cobrir o máximo, com no máximo ~4 divisões
function niceTicks(max) {
  if (max <= 0) return [0, 1];
  const raw = max / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw);
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Math.round(v * 1000) / 1000);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

// coluna com ponta superior arredondada (4px) e base reta
function columnPath(x, y, w, h, r = 4) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

// barra horizontal com ponta direita arredondada (4px) e base reta à esquerda
function barPath(x, y, w, h, r = 4) {
  r = Math.min(r, h / 2, w);
  return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`;
}

/* ---------- tooltip único (conteúdo sempre via textContent: rótulos vêm do banco) */

function vizTip() {
  let tip = document.getElementById("viz-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "viz-tip";
    tip.className = "viz-tip hidden";
    tip.setAttribute("role", "status");
    document.body.appendChild(tip);
  }
  return tip;
}

function showTip(evt, value, label) {
  const tip = vizTip();
  tip.replaceChildren();
  const strong = document.createElement("strong");
  strong.textContent = value;
  const small = document.createElement("span");
  small.textContent = label;
  tip.append(strong, small);
  tip.classList.remove("hidden");
  const r = evt.target.getBoundingClientRect?.() || { left: evt.clientX, top: evt.clientY, width: 0 };
  const x = evt.clientX ?? r.left + r.width / 2;
  const y = evt.clientY ?? r.top;
  tip.style.left = `${Math.min(window.innerWidth - tip.offsetWidth - 8, Math.max(8, x - tip.offsetWidth / 2))}px`;
  tip.style.top = `${Math.max(8, y - tip.offsetHeight - 12)}px`;
}

function hideTip() {
  document.getElementById("viz-tip")?.classList.add("hidden");
}

function bindTip(el, value, label) {
  el.setAttribute("tabindex", "0");
  el.setAttribute("aria-label", `${label}: ${value}`);
  el.addEventListener("pointermove", (e) => showTip(e, value, label));
  el.addEventListener("pointerleave", hideTip);
  el.addEventListener("focus", (e) => {
    const r = e.target.getBoundingClientRect();
    showTip({ target: e.target, clientX: r.left + r.width / 2, clientY: r.top }, value, label);
  });
  el.addEventListener("blur", hideTip);
}

/* ---------- gráficos */

// Área fina (série única): linha de 1,5px + degradê suave, um ponto no pico e alvo de hover por dia.
// points: [{label, value, tip}]
let areaSeq = 0;
function drawArea(box, points, { valueName }) {
  box.replaceChildren();
  const W = Math.max(280, box.clientWidth);
  const H = 150;
  const pad = { top: 22, right: 6, bottom: 24, left: 6 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;
  const max = Math.max(...points.map((p) => p.value), 0);
  const top = max || 1;
  const step = points.length > 1 ? plotW / (points.length - 1) : plotW;
  const xy = points.map((p, i) => [pad.left + i * step, pad.top + plotH - (p.value / top) * plotH]);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", class: "viz-area" });
  const id = `area-grad-${++areaSeq}`;
  const defs = svgEl("defs");
  const grad = svgEl("linearGradient", { id, x1: 0, x2: 0, y1: 0, y2: 1 });
  grad.append(svgEl("stop", { offset: "0", class: "area-stop-top" }), svgEl("stop", { offset: "1", class: "area-stop-bottom" }));
  defs.append(grad);
  svg.append(defs);
  svg.append(svgEl("line", { x1: pad.left, x2: W - pad.right, y1: pad.top + plotH, y2: pad.top + plotH, class: "viz-axis" }));
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  svg.append(svgEl("path", { d: `${line} L${xy[xy.length - 1][0].toFixed(1)},${pad.top + plotH} L${pad.left},${pad.top + plotH} Z`, fill: `url(#${id})` }));
  svg.append(svgEl("path", { d: line, class: "area-line" }));
  const maxIdx = points.findIndex((p) => p.value === max && max > 0);
  points.forEach((p, i) => {
    const [x, y] = xy[i];
    const g = svgEl("g", { class: "viz-col" });
    g.append(svgEl("circle", { cx: x, cy: y, r: i === maxIdx ? 3 : 2.5, class: i === maxIdx ? "area-dot peak" : "area-dot" }));
    g.append(svgEl("rect", { x: x - step / 2, y: pad.top, width: Math.max(step, 4), height: plotH, class: "viz-hit" }));
    bindTip(g, `${nf.format(p.value)} ${valueName(p.value)}`, p.tip);
    svg.append(g);
  });
  if (maxIdx >= 0) {
    const lv = svgEl("text", { x: xy[maxIdx][0], y: xy[maxIdx][1] - 9, class: "viz-value", "text-anchor": "middle" });
    lv.textContent = nfCompact.format(max);
    svg.append(lv);
  }
  const edge = [0, Math.floor((points.length - 1) / 2), points.length - 1];
  edge.forEach((i, k) => {
    if (!points[i]) return;
    const lx = svgEl("text", { x: xy[i][0], y: H - 6, class: "viz-tick", "text-anchor": k === 0 ? "start" : k === 2 ? "end" : "middle" });
    lx.textContent = points[i].label;
    svg.append(lx);
  });
  box.append(svg);
}

// Lista em linhas com filete: rótulo, barra de 3px e valor. rows: [{label, value, dot?, tip?}]
function drawRows(box, rows, { empty, format = (v) => nf.format(v) }) {
  if (!rows.length || rows.every((r) => !r.value)) {
    box.innerHTML = `<div class="viz-empty">${escHtml(empty)}</div>`;
    return;
  }
  const max = Math.max(...rows.map((r) => r.value));
  box.innerHTML = `<div class="rowlist">${rows
    .map(
      (r) => `
      <div class="rowlist-row" title="${escHtml(r.tip || r.label)}">
        <span class="rowlist-label">${r.dot ? `<span class="dot" style="background:${r.dot}"></span>` : ""}<span>${escHtml(r.label)}</span></span>
        <span class="rowlist-bar"><i style="width:${max ? Math.max(r.value ? 2 : 0, (r.value / max) * 100) : 0}%"></i></span>
        <span class="rowlist-value">${format(r.value)}</span>
      </div>`,
    )
    .join("")}</div>`;
}

function meter(label, value, total, hint) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  return `
    <div class="meter">
      <div class="meter-head"><span>${escHtml(label)}</span><strong>${pct}%</strong></div>
      <div class="meter-track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}" aria-label="${escHtml(label)}">
        <span style="width:${pct}%"></span>
      </div>
      <div class="meter-foot muted">${t("meter.foot", { value: nf.format(value), total: nf.format(total), hint: escHtml(hint) })}</div>
    </div>`;
}

/* ---------- tela */

// "Notas por pasta": as 10 maiores; o resto atrás do "Ver mais" (a tabela continua com todas)
const FOLDERS_PREVIEW = 10;

function chartCard(id, title, subtitle, { wide = false, more = false } = {}) {
  return `
    <section class="viz-card ${wide ? "wide" : ""}" id="${id}">
      <header class="viz-card-head">
        <div class="grow"><h2>${title}</h2>${subtitle ? `<span class="muted">${subtitle}</span>` : ""}</div>
        <button class="btn sm" type="button" data-table-toggle="${id}" title="${t("chart.tableTitle")}">${icon("table-2", 13)}<span>${t("chart.table")}</span></button>
      </header>
      <div class="viz-body"></div>
      ${more ? `<div class="viz-more hidden"></div>` : ""}
      <div class="viz-table hidden"></div>
    </section>`;
}

function dataTable(head, rows) {
  return `<table class="dt"><thead><tr>${head.map((h, i) => `<th class="${i ? "num" : ""}">${escHtml(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i ? "num" : ""}">${escHtml(String(c))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function overviewTemplate() {
  const days = state.insightsDays;
  return `
    <section class="page overview-page">
      <header class="page-header">
        <h1>${icon("layout-dashboard", 18)} ${t("overview.title")}</h1>
        <span class="sub">${t("overview.sub")}</span>
      </header>
      <div class="overview-col" id="overview">
        <div class="overview-filters">
          <span class="filter-label">${icon("calendar", 14)}${t("overview.period")}</span>
          <div class="period-wrap">
            <div class="period-seg" id="period-seg" role="radiogroup" aria-label="${t("overview.period")}">
              ${PERIODS.map((d) => `<button type="button" role="radio" aria-checked="${!state.insightsRange && d === days}" data-days="${d}" class="${!state.insightsRange && d === days ? "active" : ""}">${t("overview.periodDays", { count: d })}</button>`).join("")}
              <button type="button" role="radio" aria-checked="${Boolean(state.insightsRange)}" data-custom class="${state.insightsRange ? "active" : ""}">${rangeLabel()}</button>
            </div>
          </div>
          <span class="grow"></span>
          <span class="muted overview-updated" id="overview-updated"></span>
          <button class="btn sm overview-refresh" type="button" id="overview-refresh" title="${t("overview.refresh")}">${icon("refresh-cw", 14)}<span>${t("overview.refreshBtn")}</span></button>
        </div>
        <div class="kpis" id="kpis"></div>
        <div class="viz-grid">
          ${chartCard("viz-tokens", t("overview.tokens"), t("overview.tokensSub", { days }), { wide: true })}
          ${chartCard("viz-tokens-projects", t("overview.tokensByProject"), t("overview.tokensByProjectSub"))}
          ${chartCard("viz-tokens-agents", t("overview.tokensByAgent"), t("overview.tokensByAgentSub"))}
          ${chartCard("viz-usage", t("usage.title"), t("usage.sub", { days }), { wide: true })}
          ${chartCard("viz-usage-projects", t("usage.byProject"), t("usage.byProjectSub"))}
          ${chartCard("viz-activity", t("overview.activity"), t("overview.activitySub", { days }), { wide: true })}
          ${chartCard("viz-collections", t("overview.byFolder"), t("overview.byFolderSub"), { more: true })}
          ${chartCard("viz-contributors", t("overview.contributors"), t("overview.contributorsSub", { days }))}
          <section class="viz-card" id="viz-health">
            <header class="viz-card-head"><div class="grow"><h2>${t("overview.health")}</h2><span class="muted">${t("overview.healthSub")}</span></div></header>
            <div class="viz-body"></div>
          </section>
          <section class="viz-card" id="viz-recent">
            <header class="viz-card-head"><div class="grow"><h2>${t("overview.recent")}</h2><span class="muted">${t("overview.recentSub")}</span></div></header>
            <div class="viz-body"></div>
          </section>
        </div>
      </div>
    </section>`;
}

async function renderOverview(main) {
  main.innerHTML = overviewTemplate();
  await loadIcons(main);
  const markActive = (b) =>
    main.querySelectorAll("#period-seg button").forEach((x) => {
      x.classList.toggle("active", x === b);
      x.setAttribute("aria-checked", String(x === b));
    });
  main.querySelector("#period-seg").addEventListener("click", (e) => {
    if (e.target.closest("[data-custom]")) {
      // período personalizado (plan-web-36): calendário de dois meses com atalhos
      const range = state.insightsRange || { start: isoDaysAgo(state.insightsDays - 1), end: isoDaysAgo(0) };
      openRangePicker(main.querySelector(".period-wrap"), range, (picked) => {
        state.insightsRange = picked;
        const custom = main.querySelector("[data-custom]");
        custom.textContent = rangeLabel();
        markActive(custom);
        loadInsights();
      });
      return;
    }
    const b = e.target.closest("[data-days]");
    if (!b) return;
    closeRangePicker();
    if (Number(b.dataset.days) === state.insightsDays && !state.insightsRange) return;
    state.insightsDays = Number(b.dataset.days);
    state.insightsRange = null;
    main.querySelector("[data-custom]").textContent = rangeLabel();
    markActive(b);
    loadInsights();
  });
  main.querySelector("#overview-refresh").addEventListener("click", () => loadInsights());
  main.querySelector("#overview").addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-table-toggle]");
    if (toggle) {
      const card = document.getElementById(toggle.dataset.tableToggle);
      const showTable = card.querySelector(".viz-table").classList.toggle("hidden") === false;
      card.querySelector(".viz-body").classList.toggle("hidden", showTable);
      toggle.classList.toggle("active", showTable);
      toggle.querySelector("span").textContent = showTable ? t("chart.chart") : t("chart.table");
      return;
    }
    if (e.target.closest("[data-folders-more]")) {
      state.foldersExpanded = !state.foldersExpanded;
      renderOverviewCharts();
      return;
    }
    const open = e.target.closest("[data-open-note]");
    if (open) emit("navigate", { screen: "notes", openId: open.dataset.openNote });
    const go = e.target.closest("[data-goto]");
    if (go) emit("navigate", { screen: go.dataset.goto });
  });
  await loadInsights();
}

async function loadInsights() {
  const root = document.getElementById("overview");
  if (!root) return;
  root.classList.add("loading"); // recarga mantém o quadro anterior, só esmaecido
  const spinStart = performance.now();
  // o ícone completa ao menos a volta em andamento (0,7 s), mesmo com resposta instantânea
  const stopSpin = () =>
    setTimeout(() => document.getElementById("overview-refresh")?.classList.remove("spinning"), 700 - ((performance.now() - spinStart) % 700));
  document.getElementById("overview-refresh")?.classList.add("spinning");
  let data;
  try {
    data = await api.get("/insights", state.insightsRange ? { ...state.insightsRange } : { days: state.insightsDays });
  } catch (err) {
    root.classList.remove("loading");
    stopSpin();
    await uiError(t("overview.loadFailed"), err);
    return;
  }
  if (!document.getElementById("overview")) return;
  state.insights = data;
  root.classList.remove("loading");
  stopSpin();
  document.getElementById("overview-updated").textContent = t("overview.updatedAt", {
    time: new Date().toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }),
  });
  root.querySelector("#viz-activity .muted").textContent = t("overview.activitySub", { days: data.days });
  root.querySelector("#viz-contributors .muted").textContent = t("overview.contributorsSub", { days: data.days });
  root.querySelector("#viz-tokens .muted").textContent = t("overview.tokensSub", { days: data.days });
  root.querySelector("#viz-usage .muted").textContent = t("usage.sub", { days: data.days });
  renderKpis(data);
  renderOverviewCharts();
  renderHealth(data);
  renderRecent(data);
}

function renderKpis(d) {
  const totals = d.totals;
  const edits = d.activity.reduce((s, p) => s + p.edits, 0);
  const delta = d.created.current - d.created.previous;
  const newCount = d.created.current;
  const notesSub = newCount ? t("kpi.notesNew", { count: nf.format(newCount), days: d.days }) : t("kpi.notesNone", { days: d.days });
  const notesTip = t("kpi.notesTip", { count: nf.format(newCount), previous: nf.format(d.created.previous), days: d.days });
  // goto = tela que o cartão abre ao clicar (vira <button>); sem goto é só leitura
  const tiles = [
    { label: t("kpi.notes"), iconName: "notebook-pen", value: totals.documents, sub: notesSub, subTip: notesTip, trend: delta > 0 ? "up" : delta < 0 ? "down" : "", goto: "notes" },
    { label: t("kpi.edits", { days: d.days }), iconName: "activity", value: edits, spark: d.activity.map((p) => p.edits) },
    { label: t("kpi.folders"), iconName: "folder", value: totals.collections, goto: "notes" },
    {
      label: t("kpi.links"),
      iconName: "link",
      value: totals.links,
      sub: totals.pending_links ? t("kpi.pending", { count: nf.format(totals.pending_links) }) : t("kpi.linksSub"),
      goto: "graph",
    },
    { label: t("kpi.agents"), iconName: "bot", value: totals.agents, sub: t("kpi.memories", { count: nf.format(totals.memories) }), goto: "agents" },
    { label: t("kpi.chunks"), iconName: "file-text", value: totals.chunks, sub: t("kpi.chunksSub"), tech: true, goto: "search" },
  ];
  // a descrição fica FORA do cartão (legenda abaixo): assim todos os cartões têm a mesma altura
  document.getElementById("kpis").innerHTML = tiles
    .map((k) => {
      const tag = k.goto ? "button" : "div";
      return `
      <div class="kpi-cell ${k.tech ? "tech" : ""}">
        <${tag} class="kpi"${k.goto ? ` type="button" data-goto="${k.goto}"` : ""}>
          <span class="kpi-value" title="${nf.format(k.value)}">${k.value >= 10000 ? nfCompact.format(k.value) : nf.format(k.value)}</span>
          <span class="kpi-label">${escHtml(k.label)}</span>
          ${k.goto ? `<span class="kpi-go">${icon("chevron-right", 14)}</span>` : ""}
        </${tag}>
        ${k.sub ? `<span class="kpi-sub ${k.trend || ""}" ${k.subTip ? `title="${escHtml(k.subTip)}"` : ""}>${k.trend === "up" ? "▲ " : k.trend === "down" ? "▼ " : ""}${escHtml(k.sub)}</span>` : ""}
      </div>`;
    })
    .join("");
  loadIcons(document.getElementById("kpis"));
}

// redesenha só os gráficos (usado também no resize)
function renderOverviewCharts() {
  const d = state.insights;
  if (!d || !document.getElementById("overview")) return;
  const activity = d.activity.map((p) => ({
    label: fmtDay(p.day),
    value: p.edits,
    tip: fmtDay(p.day, { weekday: "short", day: "numeric", month: "short" }) + (p.created ? t("chart.newNotes", { count: p.created }) : ""),
  }));
  const card = (id) => document.querySelector(`#${id}`);
  drawArea(card("viz-activity").querySelector(".viz-body"), activity, { valueName: (v) => t("unit.edit", { count: v }) });
  card("viz-activity").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.day"), t("col.edits"), t("col.newNotes")],
    d.activity.map((p) => [fmtDay(p.day, { day: "2-digit", month: "2-digit", year: "numeric" }), p.edits, p.created]),
  );

  const folders = d.by_collection;
  const hiddenCount = folders.length - FOLDERS_PREVIEW;
  const shown = state.foldersExpanded || hiddenCount <= 0 ? folders : folders.slice(0, FOLDERS_PREVIEW);
  drawRows(
    card("viz-collections").querySelector(".viz-body"),
    shown.map((c) => ({ label: c.name, value: c.documents, dot: colorFor(c.name) })),
    { empty: t("chart.noFolders") },
  );
  const more = card("viz-collections").querySelector(".viz-more");
  more.classList.toggle("hidden", hiddenCount <= 0);
  if (hiddenCount > 0) {
    more.innerHTML = `<button class="btn sm ghost" type="button" data-folders-more aria-expanded="${Boolean(state.foldersExpanded)}">
      <span>${state.foldersExpanded ? t("chart.showLess") : t("chart.showMore", { count: hiddenCount })}</span>${icon("chevron-right", 14)}</button>`;
    loadIcons(more);
  }
  card("viz-collections").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.folder"), t("col.notes")],
    d.by_collection.map((c) => [c.name, c.documents]),
  );

  renderTokens(d);
  renderUsage(d);

  drawRows(
    card("viz-contributors").querySelector(".viz-body"),
    d.contributors.map((c) => ({ label: actorName(c.actor), value: c.edits })),
    { empty: t("chart.noEdits", { days: d.days }) },
  );
  card("viz-contributors").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.who"), t("col.edits")],
    d.contributors.map((c) => [actorName(c.actor), c.edits]),
  );
}

// Medidor de economia de tokens (plan-web-35): estimativa, sempre rotulada como tal
function renderTokens(d) {
  const tk = d.tokens;
  const card = (id) => document.querySelector(`#${id}`);
  if (!tk) return;
  const saved = (r) => Math.max(r.baseline - r.served, 0);
  const body = card("viz-tokens").querySelector(".viz-body");
  if (!tk.calls) {
    body.innerHTML = `<div class="viz-empty">${escHtml(t("tokens.empty"))}</div>`;
  } else {
    const pct = tk.baseline ? Math.round((saved(tk) / tk.baseline) * 100) : 0;
    body.innerHTML = `
      <div class="tokens-summary">
        <div class="tokens-hero">
          <span class="tokens-big">${nfCompact.format(saved(tk))}</span>
          <span class="tokens-ratio" title="${escHtml(t("tokens.estimate"))}"><i style="width:${pct}%"></i></span>
        </div>
        <span class="muted">${escHtml(t("tokens.detail", { served: nf.format(tk.served), baseline: nf.format(tk.baseline), pct, calls: nf.format(tk.calls) }))}</span>
      </div>
      <div class="tokens-chart"></div>`;
    drawArea(
      body.querySelector(".tokens-chart"),
      tk.daily.map((p) => ({ label: fmtDay(p.day), value: saved(p), tip: fmtDay(p.day, { weekday: "short", day: "numeric", month: "short" }) })),
      { valueName: (v) => t("unit.token", { count: v }) },
    );
  }
  card("viz-tokens").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.day"), t("col.served"), t("col.baseline"), t("col.saved")],
    tk.daily.map((p) => [fmtDay(p.day, { day: "2-digit", month: "2-digit", year: "numeric" }), nf.format(p.served), nf.format(p.baseline), nf.format(saved(p))]),
  );

  drawRows(
    card("viz-tokens-projects").querySelector(".viz-body"),
    tk.by_collection.map((c) => ({ label: c.name, value: saved(c), dot: colorFor(c.name) })),
    { empty: t("tokens.empty"), format: (v) => nfCompact.format(v) },
  );
  card("viz-tokens-projects").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.project"), t("col.reads"), t("col.served"), t("col.baseline"), t("col.saved")],
    tk.by_collection.map((c) => [c.name, nf.format(c.calls), nf.format(c.served), nf.format(c.baseline), nf.format(saved(c))]),
  );

  drawRows(
    card("viz-tokens-agents").querySelector(".viz-body"),
    tk.by_agent.map((a) => ({ label: a.name || a.slug, value: saved(a), tip: a.slug })),
    { empty: t("tokens.noAgents"), format: (v) => nfCompact.format(v) },
  );
  card("viz-tokens-agents").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.agent"), t("col.reads"), t("col.served"), t("col.baseline"), t("col.saved")],
    tk.by_agent.map((a) => [a.name || a.slug, nf.format(a.calls), nf.format(a.served), nf.format(a.baseline), nf.format(saved(a))]),
  );
}

// Gasto real das sessões (plan-web-38): o que a API cobrou, lido do transcript pelo plugin do Claude Code
function renderUsage(d) {
  const u = d.usage;
  const card = (id) => document.querySelector(`#${id}`);
  if (!u || !card("viz-usage")) return;
  const body = card("viz-usage").querySelector(".viz-body");
  if (!u.totals.sessions) {
    body.innerHTML = `<div class="viz-empty">${escHtml(t("usage.empty"))}</div>`;
  } else {
    const w = u.with_concilium;
    const wo = u.without_concilium;
    // com poucas sessões de cada lado a comparação engana (sessão longa custa mais por chamada: o contexto
    // relido cresce); abaixo de MIN_SAMPLE mostra o aviso no lugar do número
    const MIN_SAMPLE = 5;
    const enough = w.sessions >= MIN_SAMPLE && wo.sessions >= MIN_SAMPLE;
    const cmp = enough ? Math.round((1 - w.median_per_request / Math.max(1, wo.median_per_request)) * 100) : null;
    body.innerHTML = `
      <div class="tokens-summary">
        <div class="tokens-hero"><span class="tokens-big">${nfCompact.format(u.totals.cost)}</span></div>
        <span class="muted">${escHtml(t("usage.detail", { sessions: nf.format(u.totals.sessions), requests: nf.format(u.totals.requests), output: nfCompact.format(u.totals.output_tokens) }))} ${helpTip(t("usage.costHelp"))}</span>
      </div>
      <div class="usage-facts">
        <div><b>${nfCompact.format(w.median_per_session)}</b><span>${escHtml(t("usage.withC", { count: w.sessions }))}</span></div>
        <div><b>${nfCompact.format(wo.median_per_session)}</b><span>${escHtml(t("usage.withoutC", { count: wo.sessions }))}</span></div>
        <div><b>${cmp === null ? "—" : `${cmp > 0 ? "−" : "+"}${Math.abs(cmp)}%`}</b><span>${escHtml(enough ? t("usage.perRequest") : t("usage.fewSessions", { min: MIN_SAMPLE }))} ${helpTip(t("usage.perRequestHelp"))}</span></div>
        <div><b>${(u.concilium_share * 100).toLocaleString(locale(), { maximumFractionDigits: 1 })}%</b><span>${escHtml(t("usage.share"))} ${helpTip(t("usage.shareHelp"))}</span></div>
      </div>`;
  }
  card("viz-usage").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.project"), t("usage.col.sessions"), t("usage.col.requests"), t("usage.col.avg"), t("usage.col.calls")],
    u.projects.map((p) => [p.name, nf.format(p.sessions), nf.format(p.requests), nf.format(p.avg_per_session), nf.format(p.concilium_calls)]),
  );
  drawRows(
    card("viz-usage-projects").querySelector(".viz-body"),
    u.projects.map((p) => ({ label: p.name, value: p.cost, dot: colorFor(p.name) })),
    { empty: t("usage.empty"), format: (v) => nfCompact.format(v) },
  );
  card("viz-usage-projects").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.project"), t("usage.col.sessions"), t("usage.col.avg")],
    u.projects.map((p) => [p.name, nf.format(p.sessions), nf.format(p.avg_per_session)]),
  );
}

function renderHealth(d) {
  const h = d.health;
  const totals = d.totals;
  document.querySelector("#viz-health .viz-body").innerHTML = `
    <div class="meters">
      ${meter(t("health.tags"), h.with_tags, h.documents, t("health.tagsHint"))}
      ${meter(t("health.linked"), h.with_links, h.documents, t("health.linkedHint"))}
      ${meter(t("health.fresh"), h.documents - h.stale, h.documents, t("health.freshHint"))}
    </div>
    <div class="health-facts">
      <span>${icon("archive", 13)} ${t("health.archived", { count: nf.format(totals.archived) })}</span>
      <span>${icon("link", 13)} ${t("health.pending", { count: nf.format(totals.pending_links) })}</span>
      <span>${icon("users", 13)} ${t("health.people", { count: nf.format(totals.users) })}</span>
    </div>`;
  loadIcons(document.querySelector("#viz-health"));
}

function renderRecent(d) {
  const box = document.querySelector("#viz-recent .viz-body");
  box.innerHTML =
    d.recent
      .map(
        (n) => `
      <button class="recent-row" type="button" data-open-note="${n.id}">
        <span class="dot" style="background:${colorFor(n.collection)}"></span>
        <span class="grow"><span class="recent-title">${escHtml(n.title)}</span>
          <span class="muted">${escHtml(n.collection)} · v${n.version} · ${escHtml(actorName(n.updated_by))}</span></span>
        <span class="muted recent-when">${fmtAgo(n.updated_at)}</span>
        <span class="recent-go">${icon("chevron-right", 14)}</span>
      </button>`,
      )
      .join("") || `<div class="viz-empty">${t("overview.recentEmpty")}</div>`;
  loadIcons(box);
}

const onOverviewResize = debounce(() => state.screen === "overview" && renderOverviewCharts(), 150);
window.addEventListener("resize", onOverviewResize);

export { renderOverview, hideTip };
