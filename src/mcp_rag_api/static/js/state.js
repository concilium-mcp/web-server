/* Estado compartilhado da dashboard — sem side effects, importável por qualquer módulo. */
const PALETTE = ["#e8b26a", "#7fb4ca", "#a9c181", "#d08770", "#b48ead", "#ebcb8b", "#88c0d0", "#a3be8c", "#d3869b", "#81a1c1"];

const state = {
  user: null,
  screen: "overview", // tela inicial ao entrar
  insightsDays: 30, // período do Painel (7 | 30 | 90)
  insights: null,
  level: "documents",
  collection: "",
  minSimilarity: 0.5, // calibrado para bge-m3: docs relacionados ficam ~0.5–0.7
  k: 5,
  colors: new Map(),
  graphInstance: null,
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
};

function colorFor(collection) {
  if (!state.colors.has(collection)) {
    state.colors.set(collection, PALETTE[state.colors.size % PALETTE.length]);
  }
  return state.colors.get(collection);
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

export { state, colorFor, store };
