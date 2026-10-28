/* Loader da marca — o logo do Concilium animado só com CSS (dashboard.css, seção "loader").

   Mesma coreografia da composição ConciliumLoading do Remotion, num ciclo de 3 s: um sinal sai do
   corpo e percorre um braço por vez (topo → direita → esquerda), o nó pulsa e solta uma onda, o corpo
   "respira" e os olhos piscam (máscara). Geometria do static/brand/logo.svg (viewBox 0 0 224 244).

   O mesmo SVG vai estático no dashboard.html e no oauth/consent.html (boot, antes do JS): gerado deste
   template com __ID__ = "cl-eyes-boot". O teste tests/unit/test_loader_svg.py garante que não divergem.
   Sem imports: o módulo é só o template e um helper. */

const LOADER_SVG = `<svg class="cl-svg" viewBox="-14 -14 252 272" aria-hidden="true" focusable="false"><defs><mask id="__ID__" maskUnits="userSpaceOnUse" x="-14" y="-14" width="252" height="272"><rect x="-14" y="-14" width="252" height="272" fill="#fff"/><path class="cl-eye" style="transform-origin:67.6px 153.5px" fill="#000" d="M74.48 142.8C73.45 137.94 68.66 134.84 63.8 135.88C58.94 136.92 55.84 141.7 56.88 146.56L60.65 164.17C61.68 169.03 66.47 172.13 71.33 171.09C76.19 170.05 79.29 165.26 78.25 160.4L74.48 142.8Z"/><path class="cl-eye" style="transform-origin:104.6px 146.5px" fill="#000" d="M111.48 135.8C110.45 130.94 105.66 127.84 100.8 128.88C95.94 129.92 92.84 134.7 93.88 139.56L97.65 157.17C98.68 162.03 103.47 165.13 108.33 164.09C113.19 163.05 116.29 158.26 115.25 153.4L111.48 135.8Z"/></mask></defs><path class="cl-arm" style="--i:0" d="M78.82 94.2L90.43 22.11L103.27 24.18L91.66 96.26Z"/><path class="cl-arm" style="--i:1" d="M143.92 120.38L207.46 156.36L201.05 167.67L137.52 131.69Z"/><path class="cl-arm" style="--i:2" d="M68.12 174.01L23.12 231.51L12.88 223.49L57.88 165.99Z"/><circle class="cl-ripple" style="--i:0;transform-origin:96.6px 27.9px" cx="96.6" cy="27.9" r="20.5"/><circle class="cl-ripple" style="--i:1;transform-origin:199.9px 160.1px" cx="199.9" cy="160.1" r="20.5"/><circle class="cl-ripple" style="--i:2;transform-origin:20.5px 223.5px" cx="20.5" cy="223.5" r="20.5"/><circle class="cl-node" style="--i:0;transform-origin:96.6px 27.9px" cx="96.6" cy="27.9" r="20.5"/><circle class="cl-node" style="--i:1;transform-origin:199.9px 160.1px" cx="199.9" cy="160.1" r="20.5"/><circle class="cl-node" style="--i:2;transform-origin:20.5px 223.5px" cx="20.5" cy="223.5" r="20.5"/><g class="cl-body"><path mask="url(#__ID__)" d="M101 68C130.82 68 155 92.18 155 122V149C155 178.82 130.82 203 101 203H77C47.18 203 23 178.82 23 149V122C23 92.18 47.18 68 77 68H101Z"/></g><g class="cl-signal" style="--i:0;--dx:10.2px;--dy:-42.1px"><circle cx="86.4" cy="70" r="15" opacity=".35"/><circle cx="86.4" cy="70" r="6.5" fill="#fff"/></g><g class="cl-signal" style="--i:1;--dx:45.9px;--dy:26.7px"><circle cx="154" cy="133.4" r="15" opacity=".35"/><circle cx="154" cy="133.4" r="6.5" fill="#fff"/></g><g class="cl-signal" style="--i:2;--dx:-31px;--dy:38.9px"><circle cx="51.5" cy="184.6" r="15" opacity=".35"/><circle cx="51.5" cy="184.6" r="6.5" fill="#fff"/></g></svg>`;

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// id da máscara único por instância: o grafo e o painel podem mostrar dois loaders ao mesmo tempo
let seq = 0;

// size: "lg" (tela/área cheia) | "sm" (inline, ao lado do texto)
function loaderHtml(label = "", size = "lg") {
  const svg = LOADER_SVG.replaceAll("__ID__", `cl-eyes-${++seq}`);
  const text = label ? `<span class="cl-label">${esc(label)}</span>` : "";
  return `<div class="cl-loader ${size}" role="status" aria-live="polite">${svg}${text}</div>`;
}

export { loaderHtml };
