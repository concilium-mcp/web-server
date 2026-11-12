/* Seletor de período (plan-web-36): calendário de dois meses com o intervalo destacado, atalhos à
   esquerda e o resumo no rodapé, no lugar dos <input type="date"> nativos. Datas como "AAAA-MM-DD"
   e contas em UTC (sem surpresa de fuso). */
import { t, locale } from "./i18n.js";
import { icon, loadIcons } from "./icons.js";

const DAY = 86400000;
const toIso = (ms) => new Date(ms).toISOString().slice(0, 10);
const fromIso = (s) => Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10));
const todayIso = () => {
  const d = new Date();
  return toIso(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
};
const monthStart = (y, m) => Date.UTC(y, m, 1);
const fmt = (iso, opts) => new Date(fromIso(iso) + 12 * 3600000).toLocaleDateString(locale(), { timeZone: "UTC", ...opts });

function presets(today) {
  const t0 = fromIso(today);
  const d = new Date(t0);
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth();
  return [
    ["range.last7", toIso(t0 - 6 * DAY), today],
    ["range.last30", toIso(t0 - 29 * DAY), today],
    ["range.thisMonth", toIso(monthStart(y, m)), today],
    ["range.lastMonth", toIso(monthStart(y, m - 1)), toIso(monthStart(y, m) - DAY)],
    ["range.thisYear", toIso(Date.UTC(y, 0, 1)), today],
  ];
}

let openPicker = null;

function closeRangePicker() {
  openPicker?.cleanup();
  openPicker = null;
}

/* host: elemento posicionado onde o painel abre; range: {start, end}; onApply({start, end}) */
function openRangePicker(host, range, onApply) {
  if (openPicker) {
    closeRangePicker();
    return;
  }
  const today = todayIso();
  let start = range.start;
  let end = range.end;
  let hover = null;
  // mês da esquerda: o do início (ou o anterior ao fim, para os dois meses caberem até hoje)
  const base = new Date(fromIso(end || today));
  let viewY = base.getUTCFullYear();
  let viewM = base.getUTCMonth() - 1;

  const el = document.createElement("div");
  el.className = "rp";
  el.setAttribute("role", "dialog");
  host.appendChild(el);

  const weekdays = [...Array(7)].map((_, i) =>
    new Date(Date.UTC(2023, 0, 1 + i)).toLocaleDateString(locale(), { weekday: "narrow", timeZone: "UTC" }),
  );

  function monthHtml(y, m) {
    const first = monthStart(y, m);
    const days = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    const lead = new Date(first).getUTCDay();
    const lo = start && (end || hover) ? (start < (end || hover) ? start : end || hover) : start;
    const hi = start && (end || hover) ? (start < (end || hover) ? end || hover : start) : start;
    const cells = [...Array(lead)].map(() => `<span class="rp-day blank"></span>`);
    for (let d = 1; d <= days; d++) {
      const iso = toIso(Date.UTC(y, m, d));
      const cls = [
        "rp-day",
        iso === today ? "today" : "",
        iso > today ? "disabled" : "",
        lo && iso === lo ? "start" : "",
        hi && iso === hi ? "end" : "",
        lo && hi && iso > lo && iso < hi ? "in" : "",
      ].join(" ");
      cells.push(`<button type="button" class="${cls}" data-day="${iso}" ${iso > today ? "disabled" : ""}>${d}</button>`);
    }
    const raw = new Date(first + 12 * 3600000).toLocaleDateString(locale(), { month: "long", year: "numeric", timeZone: "UTC" });
    const title = raw.charAt(0).toUpperCase() + raw.slice(1); // só a 1ª letra: "Outubro de 2026"
    return `
      <div class="rp-month">
        <div class="rp-title">${title}</div>
        <div class="rp-grid">${weekdays.map((w) => `<span class="rp-wd">${w}</span>`).join("")}${cells.join("")}</div>
      </div>`;
  }

  function summary() {
    if (!start) return t("range.pickStart");
    if (!end) return t("range.pickEnd", { start: fmt(start, { day: "numeric", month: "short" }) });
    const n = Math.round((fromIso(end) - fromIso(start)) / DAY) + 1;
    return `${fmt(start, { day: "numeric", month: "short" })} – ${fmt(end, { day: "numeric", month: "short", year: "numeric" })} · ${t("overview.periodDays", { count: n })}`;
  }

  function render() {
    const nextY = viewM === 11 ? viewY + 1 : viewY;
    const nextM = (viewM + 1) % 12;
    const lastVisible = toIso(Date.UTC(nextY, nextM + 1, 0));
    el.innerHTML = `
      <div class="rp-presets">
        ${presets(today)
          .map(([key, s, e]) => `<button type="button" class="${s === start && e === end ? "on" : ""}" data-preset="${s}|${e}">${t(key)}</button>`)
          .join("")}
      </div>
      <div class="rp-main">
        <div class="rp-nav">
          <button type="button" class="icon-btn" data-nav="-1" title="${t("range.prev")}">${icon("chevron-left", 16)}</button>
          <button type="button" class="icon-btn" data-nav="1" title="${t("range.next")}" ${lastVisible >= today ? "disabled" : ""}>${icon("chevron-right", 16)}</button>
        </div>
        <div class="rp-months">${monthHtml(viewY, viewM)}${monthHtml(nextY, nextM)}</div>
        <div class="rp-foot">
          <span class="rp-sel">${summary()}</span>
          <button type="button" class="btn sm ghost" data-cancel>${t("common.cancel")}</button>
          <button type="button" class="btn sm primary" data-apply ${start && end ? "" : "disabled"}>${t("overview.apply")}</button>
        </div>
      </div>`;
    loadIcons(el);
  }

  function normalizeView() {
    while (viewM < 0) {
      viewM += 12;
      viewY -= 1;
    }
    while (viewM > 11) {
      viewM -= 12;
      viewY += 1;
    }
  }

  el.addEventListener("click", (e) => {
    e.stopPropagation();
    const day = e.target.closest("[data-day]");
    const preset = e.target.closest("[data-preset]");
    const nav = e.target.closest("[data-nav]");
    if (day) {
      const iso = day.dataset.day;
      if (!start || end) {
        start = iso;
        end = null;
      } else if (iso < start) {
        end = start;
        start = iso;
      } else {
        end = iso;
      }
      hover = null;
      render();
    } else if (preset) {
      [start, end] = preset.dataset.preset.split("|");
      const b = new Date(fromIso(end));
      viewY = b.getUTCFullYear();
      viewM = b.getUTCMonth() - 1;
      normalizeView();
      render();
    } else if (nav) {
      viewM += Number(nav.dataset.nav);
      normalizeView();
      render();
    } else if (e.target.closest("[data-cancel]")) {
      closeRangePicker();
    } else if (e.target.closest("[data-apply]") && start && end) {
      const picked = { start, end };
      closeRangePicker();
      onApply(picked);
    }
  });
  el.addEventListener("mouseover", (e) => {
    const day = e.target.closest("[data-day]");
    if (!start || end || !day || day.dataset.day === hover) return;
    hover = day.dataset.day;
    el.querySelector(".rp-months").innerHTML = monthHtml(viewY, viewM) + monthHtml(viewM === 11 ? viewY + 1 : viewY, (viewM + 1) % 12);
    el.querySelector(".rp-sel").textContent = summary();
  });

  const onDoc = (e) => {
    if (!el.contains(e.target) && !host.contains(e.target)) closeRangePicker();
  };
  const onKey = (e) => e.key === "Escape" && closeRangePicker();
  setTimeout(() => document.addEventListener("click", onDoc), 0);
  document.addEventListener("keydown", onKey);
  openPicker = {
    cleanup() {
      document.removeEventListener("click", onDoc);
      document.removeEventListener("keydown", onKey);
      el.remove();
    },
  };
  normalizeView();
  render();
}

export { openRangePicker, closeRangePicker };
