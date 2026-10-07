/* Concilium Dashboard — bootstrap de tema/idioma antes do 1º paint.
   Fica em arquivo separado (não inline no HTML) para a CSP poder manter script-src 'self'. */
(function () {
  var t = null;
  try {
    t = JSON.parse(localStorage.getItem("concilium:theme"));
  } catch (e) {}
  if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.dataset.theme = t;
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
