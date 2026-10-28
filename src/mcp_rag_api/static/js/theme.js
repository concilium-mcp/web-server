/* Tema — modo (claro/escuro) + paleta. A escolha fica no navegador; o <head> (boot.js) aplica antes do 1º paint. */
import { themeById } from "./themes.js";
import { syncFavicon } from "./brand.js";

/* ---------------------------------------------------------------- tema (plan-web-21) */

// Guarda o id do tema (ex.: "dracula") ou "auto" (segue o sistema). Valores antigos "dark"/"light"
// continuam valendo: viram concilium-dark/concilium-light.
const THEME_KEY = "concilium:theme";
const LEGACY = { dark: "concilium-dark", light: "concilium-light" };

// modo do tema aplicado: quem só precisa de claro/escuro (editor, grafo) continua usando isto
function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function systemThemeId() {
  return matchMedia("(prefers-color-scheme: light)").matches ? "concilium-light" : "concilium-dark";
}

// escolha salva: id do catálogo ou "auto"
function savedThemeChoice() {
  let raw = null;
  try {
    raw = JSON.parse(localStorage.getItem(THEME_KEY));
  } catch {
    /* sem armazenamento local */
  }
  raw = LEGACY[raw] || raw;
  return raw === "auto" || themeById(raw) ? raw : "auto";
}

function currentThemeId() {
  const choice = savedThemeChoice();
  return choice === "auto" ? systemThemeId() : choice;
}

// aplica e salva; choice = id do catálogo ou "auto"
function setThemeChoice(choice) {
  const theme = themeById(choice === "auto" ? systemThemeId() : choice) || themeById("concilium-dark");
  const root = document.documentElement;
  root.dataset.theme = theme.mode;
  if (theme.id.startsWith("concilium-")) delete root.dataset.palette;
  else root.dataset.palette = theme.id;
  try {
    localStorage.setItem(THEME_KEY, JSON.stringify(choice));
  } catch {
    /* sem armazenamento local: vale só nesta aba */
  }
  syncFavicon();
}

// compatibilidade: setTheme("dark" | "light") ainda funciona
function setTheme(theme) {
  setThemeChoice(LEGACY[theme] || theme);
}

// cores do canvas do grafo vêm dos tokens CSS (lidas uma vez por render, não por frame)
function graphTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return {
    bg: v("--bg"), // contorno dos rótulos: texto legível por cima das arestas
    label: v("--graph-label"),
    labelHover: v("--graph-label-hover"),
    labelDim: v("--graph-label-dim"),
    nodeStroke: v("--graph-node-stroke"),
    edgeRgb: v("--graph-edge-rgb"),
    accent: v("--accent"), // hex: o grafo aplica alpha com hexAlpha()
    accentRgb: v("--accent-rgb"),
  };
}

export { currentTheme, currentThemeId, savedThemeChoice, setThemeChoice, setTheme, graphTheme };
