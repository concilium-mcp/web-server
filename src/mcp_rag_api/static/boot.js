/* Concilium Dashboard — bootstrap de tema/idioma antes do 1º paint.
   Fica em arquivo separado (não inline no HTML) para a CSP poder manter script-src 'self'. */
(function () {
  // tema = modo (data-theme) + paleta (data-palette); ids por modo espelham static/js/themes.js
  // (tests/unit/test_themes.py confere). Valores antigos "dark"/"light" viram os temas Concilium.
  var DARK = ["concilium-dark", "one-dark", "dracula", "github-dark", "ayu-dark", "catppuccin-mocha", "shades-of-purple", "mono-dark"];
  var LIGHT = ["concilium-light", "github-light", "ayu-light", "catppuccin-latte", "mono-light"];
  var t = null;
  try {
    t = JSON.parse(localStorage.getItem("concilium:theme"));
  } catch (e) {}
  if (t === "dark") t = "concilium-dark";
  if (t === "light") t = "concilium-light";
  if (DARK.indexOf(t) < 0 && LIGHT.indexOf(t) < 0) {
    t = matchMedia("(prefers-color-scheme: light)").matches ? "concilium-light" : "concilium-dark";
  }
  document.documentElement.dataset.theme = LIGHT.indexOf(t) >= 0 ? "light" : "dark";
  if (t.indexOf("concilium-") !== 0) document.documentElement.dataset.palette = t;
  // idioma: escolha salva > idioma do navegador (es*/en*) > pt-BR
  var l = null;
  try {
    l = JSON.parse(localStorage.getItem("concilium:lang"));
  } catch (e) {}
  if (l !== "pt-BR" && l !== "en" && l !== "es") {
    var nav = (navigator.language || "").toLowerCase();
    l = nav.indexOf("es") === 0 ? "es" : nav.indexOf("en") === 0 ? "en" : "pt-BR";
  }
  document.documentElement.lang = l;
})();
