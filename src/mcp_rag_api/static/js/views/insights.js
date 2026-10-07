/* Tela do Painel — KPIs e gráficos SVG à mão (atividade, pastas, contribuidores, saúde). */
import { api } from "../api.js";
import { t, locale } from "../i18n.js";
import { icon, loadIcons } from "../icons.js";
import { escHtml, fmtAgo, debounce, uiError } from "../ui.js";
import { state, colorFor } from "../state.js";
import { emit } from "../events.js";

/* ---------------------------------------------------------------- tela: painel (indicadores e gráficos) */

// Gráficos em SVG feito à mão (sem lib): uma série por gráfico, um tom só (--viz-accent, validado
// contra a superfície escura), barras <= 24px com ponta arredondada de 4px, grade hairline sólida,
// tooltip por marca e alternância "Tabela" em todo gráfico (o tooltip nunca é o único caminho).

const PERIODS = [7, 30, 90];
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

// Colunas por dia (série única). points: [{label, value, tip}]
function drawColumns(box, points, { valueName }) {
  box.replaceChildren();
  const W = Math.max(280, box.clientWidth);
  const H = 200;
  const pad = { top: 18, right: 8, bottom: 26, left: 34 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;
  const max = Math.max(...points.map((p) => p.value), 0);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const slot = plotW / points.length;
  const barW = Math.max(2, Math.min(24, slot - 2)); // 2px de superfície entre colunas vizinhas
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img" });

  for (const tick of ticks) {
    const y = pad.top + plotH - (tick / top) * plotH;
    svg.append(svgEl("line", { x1: pad.left, x2: W - pad.right, y1: y, y2: y, class: tick === 0 ? "viz-axis" : "viz-gridline" }));
    const label = svgEl("text", { x: pad.left - 8, y: y + 4, class: "viz-tick", "text-anchor": "end" });
    label.textContent = nf.format(tick);
    svg.append(label);
  }
  // rótulos do eixo X esparsos: ~6 ao longo do período
  const every = Math.max(1, Math.ceil(points.length / 6));
  const maxIdx = points.findIndex((p) => p.value === max && max > 0);
  points.forEach((p, i) => {
    const x = pad.left + i * slot + (slot - barW) / 2;
    const h = top ? (p.value / top) * plotH : 0;
    const y = pad.top + plotH - h;
    // área de hover = a fatia inteira do dia (maior que a marca)
    const hit = svgEl("rect", { x: pad.left + i * slot, y: pad.top, width: slot, height: plotH, class: "viz-hit" });
    const g = svgEl("g", { class: "viz-col" });
    if (p.value > 0) g.append(svgEl("path", { d: columnPath(x, y, barW, h), class: "viz-mark" }));
    g.append(hit);
    bindTip(g, `${nf.format(p.value)} ${valueName(p.value)}`, p.tip);
    svg.append(g);
    if (i % every === 0 || i === points.length - 1) {
      const lx = svgEl("text", { x: x + barW / 2, y: H - 8, class: "viz-tick", "text-anchor": "middle" });
      lx.textContent = p.label;
      svg.append(lx);
    }
    if (i === maxIdx) {
      // rótulo direto só no pico (nunca um número em cada coluna)
      const lv = svgEl("text", { x: x + barW / 2, y: y - 6, class: "viz-value", "text-anchor": "middle" });
      lv.textContent = nf.format(p.value);
      svg.append(lv);
    }
  });
  box.append(svg);
}

// Barras horizontais (série única). rows: [{label, value, dot?, tip?}]
function drawBars(box, rows, { valueName, empty }) {
  box.replaceChildren();
  if (!rows.length || rows.every((r) => !r.value)) {
    box.innerHTML = `<div class="viz-empty">${escHtml(empty)}</div>`;
    return;
  }
  const W = Math.max(260, box.clientWidth);
  const rowH = 30;
  const barH = 12;
  const labelW = Math.min(150, Math.round(W * 0.38));
  const valueW = 44;
  const plotW = W - labelW - valueW - 8;
  const max = Math.max(...rows.map((r) => r.value));
  const H = rows.length * rowH;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img" });
  svg.append(svgEl("line", { x1: labelW, x2: labelW, y1: 0, y2: H, class: "viz-axis" }));
  rows.forEach((r, i) => {
    const cy = i * rowH + rowH / 2;
    const w = max ? Math.max(r.value ? 3 : 0, (r.value / max) * plotW) : 0;
    let tx = 0;
    if (r.dot) {
      svg.append(svgEl("circle", { cx: 5, cy, r: 4, fill: r.dot }));
      tx = 16;
    }
    const label = svgEl("text", { x: tx, y: cy + 4, class: "viz-label" });
    const maxChars = Math.floor((labelW - tx - 8) / 7);
    label.textContent = r.label.length > maxChars ? `${r.label.slice(0, maxChars - 1)}…` : r.label;
    svg.append(label);
    const g = svgEl("g", { class: "viz-col" });
    if (w > 0) g.append(svgEl("path", { d: barPath(labelW + 1, cy - barH / 2, w, barH), class: "viz-mark" }));
    g.append(svgEl("rect", { x: 0, y: i * rowH, width: W, height: rowH, class: "viz-hit" }));
    bindTip(g, `${nf.format(r.value)} ${valueName(r.value)}`, r.tip || r.label);
    svg.append(g);
    const v = svgEl("text", { x: labelW + 1 + w + 6, y: cy + 4, class: "viz-value" });
    v.textContent = nf.format(r.value);
    svg.append(v);
  });
  box.append(svg);
}

function sparkline(values) {
  const W = 96;
  const H = 28;
  const max = Math.max(...values, 1);
  const step = values.length > 1 ? W / (values.length - 1) : W;
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(H - 3 - (v / max) * (H - 6)).toFixed(1)}`);
  const last = pts[pts.length - 1].split(",");
  return `<svg class="sparkline" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true">
    <polyline points="${pts.join(" ")}" />
    <circle cx="${last[0]}" cy="${last[1]}" r="3" /></svg>`;
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

function chartCard(id, title, subtitle, { wide = false } = {}) {
  return `
    <section class="viz-card ${wide ? "wide" : ""}" id="${id}">
      <header class="viz-card-head">
        <div class="grow"><h2>${title}</h2>${subtitle ? `<span class="muted">${subtitle}</span>` : ""}</div>
        <button class="btn sm ghost" type="button" data-table-toggle="${id}" title="${t("chart.tableTitle")}">${icon("table-2", 13)}<span>${t("chart.table")}</span></button>
      </header>
      <div class="viz-body"></div>
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
          <div class="period-seg" id="period-seg" role="radiogroup" aria-label="${t("overview.period")}">
            ${PERIODS.map((d) => `<button type="button" role="radio" aria-checked="${d === days}" data-days="${d}" class="${d === days ? "active" : ""}">${t("overview.periodDays", { count: d })}</button>`).join("")}
          </div>
          <span class="grow"></span>
          <span class="muted overview-updated" id="overview-updated"></span>
          <button class="icon-btn overview-refresh" type="button" id="overview-refresh" title="${t("overview.refresh")}">${icon("refresh-cw", 15)}</button>
        </div>
        <div class="kpis" id="kpis"></div>
        <div class="viz-grid">
          ${chartCard("viz-activity", t("overview.activity"), t("overview.activitySub", { days }), { wide: true })}
          ${chartCard("viz-collections", t("overview.byFolder"), t("overview.byFolderSub"))}
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
  main.querySelector("#period-seg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-days]");
    if (!b || Number(b.dataset.days) === state.insightsDays) return;
    state.insightsDays = Number(b.dataset.days);
    main.querySelectorAll("#period-seg button").forEach((x) => {
      x.classList.toggle("active", x === b);
      x.setAttribute("aria-checked", String(x === b));
    });
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
    const open = e.target.closest("[data-open-note]");
    if (open) emit("navigate", { screen: "notes", openId: open.dataset.openNote });
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
    data = await api.get("/insights", { days: state.insightsDays });
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
  const tiles = [
    { label: t("kpi.notes"), iconName: "notebook-pen", value: totals.documents, sub: notesSub, subTip: notesTip, trend: delta > 0 ? "up" : delta < 0 ? "down" : "" },
    { label: t("kpi.edits", { days: d.days }), iconName: "activity", value: edits, spark: d.activity.map((p) => p.edits) },
    { label: t("kpi.folders"), iconName: "folder", value: totals.collections },
    {
      label: t("kpi.links"),
      iconName: "link",
      value: totals.links,
      sub: totals.pending_links ? t("kpi.pending", { count: nf.format(totals.pending_links) }) : t("kpi.linksSub"),
    },
    { label: t("kpi.agents"), iconName: "bot", value: totals.agents, sub: t("kpi.memories", { count: nf.format(totals.memories) }) },
    { label: t("kpi.chunks"), iconName: "file-text", value: totals.chunks, sub: t("kpi.chunksSub"), tech: true },
  ];
  document.getElementById("kpis").innerHTML = tiles
    .map(
      (k) => `
      <div class="kpi ${k.tech ? "tech" : ""}">
        <span class="kpi-label"><span class="kpi-icon">${icon(k.iconName, 14)}</span>${escHtml(k.label)}</span>
        <div class="kpi-row">
          <span class="kpi-value" title="${nf.format(k.value)}">${k.value >= 10000 ? nfCompact.format(k.value) : nf.format(k.value)}</span>
          ${k.spark ? sparkline(k.spark) : ""}
        </div>
        ${k.sub ? `<span class="kpi-sub ${k.trend || ""}" ${k.subTip ? `title="${escHtml(k.subTip)}"` : ""}>${k.trend === "up" ? "▲ " : k.trend === "down" ? "▼ " : ""}${escHtml(k.sub)}</span>` : ""}
      </div>`,
    )
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
  drawColumns(card("viz-activity").querySelector(".viz-body"), activity, { valueName: (v) => t("unit.edit", { count: v }) });
  card("viz-activity").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.day"), t("col.edits"), t("col.newNotes")],
    d.activity.map((p) => [fmtDay(p.day, { day: "2-digit", month: "2-digit", year: "numeric" }), p.edits, p.created]),
  );

  drawBars(
    card("viz-collections").querySelector(".viz-body"),
    d.by_collection.map((c) => ({ label: c.name, value: c.documents, dot: colorFor(c.name) })),
    { valueName: (v) => t("unit.note", { count: v }), empty: t("chart.noFolders") },
  );
  card("viz-collections").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.folder"), t("col.notes")],
    d.by_collection.map((c) => [c.name, c.documents]),
  );

  drawBars(
    card("viz-contributors").querySelector(".viz-body"),
    d.contributors.map((c) => ({ label: actorName(c.actor), value: c.edits })),
    { valueName: (v) => t("unit.edit", { count: v }), empty: t("chart.noEdits", { days: d.days }) },
  );
  card("viz-contributors").querySelector(".viz-table").innerHTML = dataTable(
    [t("col.who"), t("col.edits")],
    d.contributors.map((c) => [actorName(c.actor), c.edits]),
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
      </button>`,
      )
      .join("") || `<div class="viz-empty">${t("overview.recentEmpty")}</div>`;
}

const onOverviewResize = debounce(() => state.screen === "overview" && renderOverviewCharts(), 150);
window.addEventListener("resize", onOverviewResize);

export { renderOverview, hideTip };
