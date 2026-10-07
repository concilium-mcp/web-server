# Vendor — bibliotecas frontend servidas estaticamente

Arquivos minificados servidos pela dashboard em `src/mcp_rag_api/static/vendor/`.
Nada aqui passa por build; cada entrada registra origem e versão exata.

## dompurify/

- **Arquivo:** `purify.min.js` (+ `purify.min.js.map`)
- **Versão:** 3.4.16 (linha 3.x mais recente em 2026-10-07)
- **Origem:** release oficial no jsDelivr — `https://cdn.jsdelivr.net/npm/dompurify@3.4.16/dist/purify.min.js`
  (mesmo conteúdo do tarball npm `dompurify@3.4.16`, pasta `dist/`)
- **Licença:** Apache-2.0 e MPL-2.0 — https://github.com/cure53/DOMPurify/blob/3.4.16/LICENSE
- **Uso:** sanitização do HTML de notas renderizado pelo Toast UI
  (`customHTMLSanitizer` em `dashboard.js`), substituindo o DOMPurify 2.3.3
  embutido no bundle do Toast UI (versão com bypasses públicos corrigidos só em
  releases posteriores). Compatível com a CSP `script-src 'self'` (arquivo local).
  Configuração: `FORBID_TAGS` semelhante ao sanitizer padrão do Toast UI
  (`form`, `button`, `select`, `textarea`, `meta`, `style`, `link`, `title`,
  `object`, `base`), mas sem proibir `input` — task lists do markdown precisam
  de `<input type="checkbox" disabled>`.

## toastui/

- **Arquivo:** `toastui-editor-all.min.js` (+ CSS e locales pt-BR/es)
- **Versão:** 3.2.2 (última release do projeto, fev/2023 — mantido como vendor)
- **Origem:** release oficial do Toast UI Editor — https://github.com/nhn/tui.editor
- **Licença:** MIT (ver `LICENSE`)
- **Observação:** embute DOMPurify 2.3.3; o sink de renderização de HTML é
  coberto pelo `customHTMLSanitizer` configurado na dashboard (ver `dompurify/`).

## force-graph.min.js

- **Arquivo:** `force-graph.min.js`
- **Origem:** release oficial — https://github.com/vasturiano/force-graph
- **Uso:** grafo de conexões das notas na dashboard.
