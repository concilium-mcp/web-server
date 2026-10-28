/* Estado compartilhado da dashboard — sem side effects, importável por qualquer módulo. */
const PALETTE = ["#e8b26a", "#7fb4ca", "#a9c181", "#d08770", "#b48ead", "#ebcb8b", "#88c0d0", "#a3be8c", "#d3869b", "#81a1c1"];

const state = {
  user: null,
  screen: "overview", // tela inicial ao entrar
  insightsDays: 30, // período do Painel (7 | 30 | 90)
  insights: null,
  foldersExpanded: false, // Painel: "Notas por pasta" mostrando todas as pastas (Ver mais)
  level: "documents",
  collection: "",
  folderId: null, // grafo: pasta escolhida no seletor de local (inclui subpastas)
  graphPicker: null,
  searchScope: { collection: "", folderId: null }, // busca: onde procurar
  minSimilarity: 0.5, // calibrado para bge-m3: docs relacionados ficam ~0.5–0.7
  k: 5,
  colors: new Map(),
  graphInstance: null,
  graphInstanceMode: null, // modo ("2d" | "3d") da instância viva — troca de modo recria
  graphObserver: null,
  graphNeedsFit: false,
  hoverNode: null,
  legendHover: null,
  docPanelId: null,
  highlightDocs: null, // Set de document_id vindos do testador de busca
  lastSearchResults: [],
  lastSearch: null,
  lastSearchMs: null,
  searchK: 5,
  showRevoked: false,
  connectMode: false, // grafo: modo "clique na origem e no destino"
  connectFrom: null, // nó de origem escolhido no modo conectar
  showSemantic: true, // grafo: mostrar arestas de similaridade além dos links explícitos
  graphMode: "2d", // grafo: "2d" (canvas) | "3d" (WebGL, carregado sob demanda)
  graphView: "auto", // grafo: "auto" (grupos se a base for grande) | "groups" | "all"
  graphGroup: null, // grafo: grupo aberto ({ id, label, size }) ou null
  linkScale: 1, // grafo: multiplicador da espessura das arestas (0.3–3)
};

// paleta das coleções: o tema pode trocar (--collection-palette, ex.: preto e branco em tons de cinza);
// lida sob demanda e zerada a cada troca de tema (resetCollectionColors)
let collectionPalette = null;

function currentCollectionPalette() {
  if (!collectionPalette) {
    const custom = getComputedStyle(document.documentElement).getPropertyValue("--collection-palette").trim();
    collectionPalette = custom ? custom.split(/\s*,\s*/) : PALETTE;
  }
  return collectionPalette;
}

function colorFor(collection) {
  if (!state.colors.has(collection)) {
    const palette = currentCollectionPalette();
    state.colors.set(collection, palette[state.colors.size % palette.length]);
  }
  return state.colors.get(collection);
}

// cor de um grupo de assunto do grafo: mesma paleta das coleções, por índice (não ocupa cor de coleção)
function groupColor(i) {
  const palette = currentCollectionPalette();
  return palette[i % palette.length];
}

function resetCollectionColors() {
  collectionPalette = null;
  state.colors.clear();
}

// localStorage pode falhar (aba privada, bloqueio): rascunho é conveniência, nunca obrigatório
const store = {
  get(key) {
    try {
      return JSON.parse(localStorage.getItem(key));
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* sem armazenamento local: segue sem rascunho */
    }
  },
  del(key) {
    try {
      localStorage.removeItem(key);
    } catch {
      /* idem */
    }
  },
};

export { state, colorFor, groupColor, resetCollectionColors, store };
