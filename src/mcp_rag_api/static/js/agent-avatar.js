/* Avatar dos agentes: variações do logo do Concilium (corpo arredondado, dois olhos e braços que terminam
   em nós), geradas a partir do slug — o mesmo agente tem sempre o mesmo rosto. Muda a cor, o formato do
   corpo, os olhos e a quantidade/ângulo dos braços. Arquivado = sem cor. */

const COLORS = ["#d97757", "#d6b46a", "#7cc3a0", "#8f95ff", "#6fb3e0", "#e48aa8", "#b39ddb", "#c9a27e"];
// ângulos (graus, 0 = direita, sentido horário) — o logo usa três braços: cima, direita e baixo-esquerda
const ARM_SETS = [
  [-80, 30, 125], // o do logo
  [-100, 15, 150],
  [-60, 160],
  [-120, 40],
  [-75, 20, 140],
  [-95, 170],
  [-45, 120, 200],
];

function hash(text) {
  let h = 2166136261;
  for (const ch of text) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 16777619);
  }
  // mistura final (murmur3 fmix32): sem ela, slugs parecidos caem na mesma cor
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35);
  h ^= h >>> 16;
  return h >>> 0;
}

function eyes(kind, cx, cy) {
  const gap = 7;
  const fill = 'fill="var(--avatar-eye, #0e0e10)"';
  switch (kind) {
    case 0: // pílulas inclinadas, como no logo
      return [-1, 1]
        .map((s) => `<rect x="${cx + s * gap - 1.8}" y="${cy - 4}" width="3.6" height="8" rx="1.8" ${fill} transform="rotate(-12 ${cx + s * gap} ${cy})"/>`)
        .join("");
    case 1: // pontos
      return [-1, 1].map((s) => `<circle cx="${cx + s * gap}" cy="${cy}" r="2.4" ${fill}/>`).join("");
    case 2: // traços (sereno)
      return [-1, 1].map((s) => `<rect x="${cx + s * gap - 3}" y="${cy - 0.9}" width="6" height="1.8" rx="0.9" ${fill}/>`).join("");
    default: // um olho só
      return `<circle cx="${cx}" cy="${cy}" r="3.2" ${fill}/>`;
  }
}

function agentAvatar(slug, size = 32, { archived = false } = {}) {
  const h = hash(slug || "agent");
  const color = archived ? "var(--text-dim)" : COLORS[h % COLORS.length];
  const arms = ARM_SETS[(h >>> 3) % ARM_SETS.length];
  const radius = [13, 10, 16][(h >>> 6) % 3];
  const eyeKind = (h >>> 8) % 4;
  const cx = 32;
  const cy = 34;
  const armLines = arms
    .map((deg) => {
      const r = (deg * Math.PI) / 180;
      const x1 = cx + Math.cos(r) * 12;
      const y1 = cy + Math.sin(r) * 12;
      const x2 = cx + Math.cos(r) * 24;
      const y2 = cy + Math.sin(r) * 24;
      return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${color}" stroke-width="3" stroke-linecap="round"/>
        <circle cx="${x2.toFixed(1)}" cy="${y2.toFixed(1)}" r="4.2" fill="${color}"/>`;
    })
    .join("");
  return `<svg class="agent-avatar${archived ? " archived" : ""}" width="${size}" height="${size}" viewBox="0 0 64 64" aria-hidden="true">
    ${armLines}
    <rect x="${cx - 16}" y="${cy - 14}" width="32" height="28" rx="${radius}" fill="${color}"/>
    ${eyes(eyeKind, cx, cy + 1)}
  </svg>`;
}

export { agentAvatar };
