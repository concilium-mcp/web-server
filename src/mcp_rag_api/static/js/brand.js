/* Personagem da marca (static/brand/logo.svg) desenhado em SVG inline, na cor de destaque do tema.

   O arquivo logo.svg segue sendo a marca oficial (coral) e o favicon inicial; aqui a mesma geometria
   pinta com currentColor (logo da sidebar/login) ou com a cor do tema (favicon). Sem imports. */

const VIEWBOX = "0 0 224 244";
const PATHS = [
  // corpo com os olhos recortados (subcaminhos internos em sentido oposto)
  "M101 68C130.823 68 155 92.1766 155 122V149C155 178.823 130.823 203 101 203H77C47.1766 203 23 178.823 23 149V122C23 92.1766 47.1766 68 77 68H101ZM74.4844 142.801C73.445 137.94 68.6614 134.843 63.8008 135.882C58.9403 136.921 55.8427 141.704 56.8818 146.564L60.6455 164.167C61.6849 169.028 66.4675 172.125 71.3281 171.086C76.1888 170.047 79.2863 165.264 78.2471 160.403L74.4844 142.801ZM111.484 135.801C110.445 130.94 105.661 127.843 100.801 128.882C95.9403 129.921 92.8427 134.704 93.8818 139.564L97.6455 157.167C98.6849 162.028 103.468 165.125 108.328 164.086C113.189 163.047 116.286 158.264 115.247 153.403L111.484 135.801Z",
  // braços
  "M68.1191 174.006L23.1191 231.506L12.8809 223.494L57.8809 165.994L68.1191 174.006Z",
  "M143.923 120.375L207.458 156.355L201.053 167.668L137.518 131.688L143.923 120.375Z",
  "M78.8222 94.1962L90.4348 22.1102L103.27 24.1771L91.6573 96.2632L78.8222 94.1962Z",
];
// nós (círculos r=20.5)
const NODES = [
  [20.5, 223.5],
  [199.929, 160.132],
  [96.5965, 27.8537],
];

function brandSvg(fill = "currentColor", attrs = "") {
  const shapes =
    PATHS.map((d) => `<path d="${d}"/>`).join("") + NODES.map(([cx, cy]) => `<circle cx="${cx}" cy="${cy}" r="20.5"/>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${VIEWBOX}" fill="${fill}" ${attrs}>${shapes}</svg>`;
}

// favicon na cor de destaque do tema atual (data: URL — a CSP permite img-src data:)
function syncFavicon() {
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  const link = document.querySelector('link[rel="icon"]');
  if (!link || !/^#[0-9a-f]{3,8}$/i.test(accent)) return;
  link.href = "data:image/svg+xml," + encodeURIComponent(brandSvg(accent));
}

export { brandSvg, syncFavicon };
