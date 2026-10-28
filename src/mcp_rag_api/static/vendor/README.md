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

## inter/

- **Arquivos:** `inter-latin-wght-normal.woff2` e `inter-latin-ext-wght-normal.woff2` (fonte variável, pesos 100–900)
- **Versão:** `@fontsource-variable/inter@5.0.16`
- **Origem:** `https://cdn.jsdelivr.net/npm/@fontsource-variable/inter@5.0.16/files/`
- **Licença:** SIL Open Font License 1.1 (ver `LICENSE`)
- **Uso:** fonte da interface (plan-web-15, estética Resend). Local por causa da CSP (`default-src 'self'`, sem fonte externa).

## swagger-ui/

- **Arquivos:** `swagger-ui-bundle.js`, `swagger-ui.css` (+ `LICENSE`)
- **Versão:** `swagger-ui-dist@5.17.14`
- **Origem:** `https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/`
- **Licença:** Apache-2.0 (ver `LICENSE`)
- **Uso:** documentação da API em `/dash/docs`. Local por causa da CSP (`script-src 'self'`): o
  `get_swagger_ui_html` padrão do FastAPI carrega do CDN e inicializa com `<script>` inline, o que
  deixava a página em branco. A inicialização fica em `static/js/swagger-init.js`.

## 3d-force-graph/

- **Arquivo:** `3d-force-graph.min.js` (+ `LICENSE`) — bundle UMD que já embute o three.js
- **Versão:** `3d-force-graph@1.80.1`
- **Origem:** `https://cdn.jsdelivr.net/npm/3d-force-graph@1.80.1/dist/3d-force-graph.min.js`
- **Licença:** MIT (ver `LICENSE`)
- **Uso:** modo 3D do grafo (plan-web-19). Carregado sob demanda (só quando a pessoa escolhe 3D),
  por isso não entra no `dashboard.html`. Usa o motor de física d3 (padrão), igual ao 2D: o bundle
  também traz o `ngraph.forcelayout`, que gera código com `new Function` e seria barrado pela CSP
  (`script-src 'self'`, sem `unsafe-eval`) — **não ligar `forceEngine('ngraph')`**.
