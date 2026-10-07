/* Tema claro/escuro — a escolha fica no navegador; o <head> aplica antes do 1º paint. */
/* ---------------------------------------------------------------- tema (escuro/claro) */

// a escolha fica no navegador; sem escolha salva, segue o sistema. O <head> aplica antes do 1º paint.
const THEME_KEY = "concilium:theme";

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, JSON.stringify(theme));
  } catch {
    /* sem armazenamento local: vale só nesta aba */
  }
}

// cores do canvas do grafo vêm dos tokens CSS (lidas uma vez por render, não por frame)
function graphTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return {
    label: v("--graph-label"),
    labelHover: v("--graph-label-hover"),
    labelDim: v("--graph-label-dim"),
    nodeStroke: v("--graph-node-stroke"),
    edgeRgb: v("--graph-edge-rgb"),
  };
}

export { currentTheme, setTheme, graphTheme };
