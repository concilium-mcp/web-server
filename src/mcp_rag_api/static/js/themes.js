/* Catálogo de temas (plan-web-21) — nome, modo e miniatura de cada um.

   Um tema = modo + paleta:
   - modo ("dark" | "light") vai em <html data-theme>: tudo o que já depende de claro/escuro (editor
     Toast UI, gráficos do Painel, regras [data-theme="light"]) segue funcionando sem saber do tema;
   - paleta vai em <html data-palette> e só troca os tokens de cor (dashboard.css, seção "temas").
   Os temas "concilium-*" são os originais: sem data-palette.

   swatch = [moldura, painel, destaque, texto] — só para a miniatura do seletor; as cores reais
   ficam no CSS. O boot.js tem a lista de ids por modo (aplica antes do 1º paint) — manter em sincronia;
   o teste tests/unit/test_themes.py confere. */

const THEMES = [
  { id: "concilium-dark", mode: "dark", swatch: ["#0a0a0a", "#111111", "#d97757", "#ededed"] },
  { id: "one-dark", name: "One Dark Pro", mode: "dark", swatch: ["#21252b", "#282c34", "#61afef", "#d7dae0"] },
  { id: "dracula", name: "Dracula", mode: "dark", swatch: ["#191a21", "#282a36", "#bd93f9", "#f8f8f2"] },
  { id: "github-dark", name: "GitHub Dark", mode: "dark", swatch: ["#010409", "#0d1117", "#238636", "#f0f6fc"] },
  { id: "ayu-dark", name: "Ayu Dark", mode: "dark", swatch: ["#0b0e14", "#0d1017", "#e6b450", "#e6e1cf"] },
  { id: "catppuccin-mocha", name: "Catppuccin Mocha", mode: "dark", swatch: ["#11111b", "#1e1e2e", "#cba6f7", "#cdd6f4"] },
  { id: "shades-of-purple", name: "Shades of Purple", mode: "dark", swatch: ["#1e1e3f", "#2d2b55", "#fad000", "#ffffff"] },
  { id: "mono-dark", mode: "dark", swatch: ["#000000", "#0a0a0a", "#ffffff", "#ffffff"] },
  { id: "concilium-light", mode: "light", swatch: ["#f5f5f5", "#ffffff", "#c96442", "#0a0a0a"] },
  { id: "github-light", name: "GitHub Light", mode: "light", swatch: ["#f6f8fa", "#ffffff", "#1f883d", "#1f2328"] },
  { id: "ayu-light", name: "Ayu Light", mode: "light", swatch: ["#f3f4f5", "#fcfcfc", "#b35f00", "#333a40"] },
  { id: "catppuccin-latte", name: "Catppuccin Latte", mode: "light", swatch: ["#dce0e8", "#eff1f5", "#8839ef", "#4c4f69"] },
  { id: "mono-light", mode: "light", swatch: ["#ffffff", "#ffffff", "#000000", "#000000"] },
];

// sem nome próprio (Concilium, preto e branco): o rótulo vem do i18n (theme.<id>)
const themeById = (id) => THEMES.find((th) => th.id === id);

export { THEMES, themeById };
