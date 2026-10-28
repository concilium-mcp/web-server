# Plano — Concilium (MCP RAG API em Python + PostgreSQL + pgvector)

> **Atualizado em 2026-10-28.** As fases (§11), as decisões (§12) e os critérios de pronto (§13) mostram o estado atual. As seções de design (§4 a §10) são as do plano original, com notas onde o projeto mudou. A referência do dia a dia é o [README](README.md), o [guia](docs/guide.pt-BR.md) e o código.

> Servidor MCP em Python que dá aos agentes de IA (Claude e nossos agentes) uma **memória/base de conhecimento compartilhada**: consultar (RAG), inserir e atualizar conhecimento, com PostgreSQL + pgvector como armazenamento.

---

## 1. Objetivo

- Expor uma base de conhecimento via **MCP (Model Context Protocol)** para que qualquer agente compatível (Claude Code, Claude Desktop, Claude API com MCP connector, agentes próprios via Agent SDK) possa:
  1. **Consultar** — busca semântica + busca por palavra-chave (híbrida) com retorno de trechos e fontes.
  2. **Inserir** — adicionar documentos/notas/fatos, que são quebrados em chunks, vetorizados e indexados.
  3. **Atualizar** — editar ou substituir conhecimento existente, com versionamento, para a base "se atualizar" com o que os agentes aprendem.
- Expor também uma **API REST** (FastAPI) com as mesmas operações, para integrações que não falam MCP (painel admin, scripts, webhooks).

## 2. Arquitetura

```
┌──────────────────────┐        ┌──────────────────────────────────────────┐
│  Claude Code/Desktop │  MCP   │              mcp-rag-api                 │
│  Claude API (MCP)    ├───────►│  ┌────────────┐    ┌──────────────────┐  │
│  Nossos agentes      │ (HTTP) │  │ MCP Server │    │ REST API FastAPI │  │
└──────────────────────┘        │  └─────┬──────┘    └────────┬─────────┘  │
                                │        └────────┬───────────┘            │
                                │          ┌──────▼───────┐                │
                                │          │ Core Services│                │
                                │          │ ingest/search│                │
                                │          │ update/embed │                │
                                │          └──┬────────┬──┘                │
                                └─────────────┼────────┼───────────────────┘
                                              │        │
                                  ┌───────────▼──┐  ┌──▼──────────────────┐
                                  │ PostgreSQL 16│  │ Provedor Embeddings │
                                  │ + pgvector   │  │ (API ou local)      │
                                  └──────────────┘  └─────────────────────┘
```

- **Uma única camada de serviços** (`core/`) usada tanto pelo MCP quanto pela REST — nada de lógica duplicada.
- **Transporte MCP**: `streamable-http` (para uso remoto/múltiplos agentes) e `stdio` (para desenvolvimento local).

## 3. Stack

| Camada | Escolha | Motivo |
|---|---|---|
| Linguagem | Python 3.12 | |
| MCP | SDK oficial `mcp` 2.x (`MCPServer`) | Padrão, suporta tools/resources/prompts e streamable HTTP |
| API REST | FastAPI + Uvicorn | Async, OpenAPI automático; MCP montado no mesmo app |
| Banco | PostgreSQL 16 + extensão **pgvector** | Vetores + dados relacionais + full‑text no mesmo lugar |
| Acesso ao banco / migrações | asyncpg + SQL puro; migrações em `migrations/NNN_*.sql` aplicadas no start | Menos camadas; o SQL fica igual ao desenhado aqui |
| Embeddings | Interface plugável: Voyage AI / OpenAI / Hugging Face Inference API (`bge-m3`) **ou** `sentence-transformers` local (`bge-m3`, bom em PT‑BR) | Trocar provedor sem mexer no resto |
| Chunking | Splitter próprio por palavras com sobreposição (450 palavras, overlap 60; `CHUNK_WORDS` e `CHUNK_OVERLAP_WORDS`) | |
| Rerank (opcional) | Cross-encoder local ou API de rerank | Melhora precisão do top‑k |
| Config | pydantic-settings (`.env`) | |
| Infra | Imagem Docker pública (`ghcr.io/concilium-mcp/web-server`, amd64/arm64) + qualquer PostgreSQL 16 com pgvector ≥ 0.8; `deploy/docker-compose.yml` sobe app + banco | Roda em qualquer host de containers ([guia de deploy](docs/deploy.md)) |
| Testes | pytest + pytest-asyncio; integração contra Postgres + pgvector real (`TEST_DATABASE_URL`) | |
| Qualidade | ruff + mypy | |

> Provedores implementados: `voyage` (padrão), `openai`, `huggingface` (`bge-m3` pela Inference API), `local` (`bge-m3` na máquina) e `fake` (testes), sempre em 1024 dimensões. Trocar de provedor ou modelo exige `mcp-rag-api reindex`.

## 4. Modelo de dados

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Agrupamento lógico (ex.: "clientes", "processos", "produto-x")
CREATE TABLE collections (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT UNIQUE NOT NULL,
  description TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- Documento lógico (a "fonte da verdade")
CREATE TABLE documents (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  collection_id UUID REFERENCES collections(id) ON DELETE CASCADE,
  external_id   TEXT,                 -- id vindo de outro sistema (upsert idempotente)
  title         TEXT NOT NULL,
  source        TEXT,                 -- url, arquivo, "agent:<nome>"
  content       TEXT NOT NULL,
  content_hash  TEXT NOT NULL,        -- evita re-embedding se nada mudou
  metadata      JSONB DEFAULT '{}',
  tags          TEXT[] DEFAULT '{}',
  version       INT  NOT NULL DEFAULT 1,
  status        TEXT NOT NULL DEFAULT 'active',  -- active | archived
  created_by    TEXT,                 -- agente/usuário que criou
  updated_by    TEXT,
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (collection_id, external_id)
);

-- Histórico de versões (auditoria e rollback)
CREATE TABLE document_versions (
  id          BIGSERIAL PRIMARY KEY,
  document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
  version     INT NOT NULL,
  content     TEXT NOT NULL,
  metadata    JSONB,
  changed_by  TEXT,
  change_note TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- Chunks indexados
CREATE TABLE chunks (
  id          BIGSERIAL PRIMARY KEY,
  document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  content     TEXT NOT NULL,
  token_count INT,
  embedding   vector(1024),           -- dimensão conforme o modelo escolhido
  tsv         tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', content)) STORED,
  metadata    JSONB DEFAULT '{}'
);

CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON chunks USING gin (tsv);
CREATE INDEX ON documents USING gin (metadata);
CREATE INDEX ON documents USING gin (tags);
```

> Esse é o schema da migração inicial. Depois vieram: usuários e sessões da dashboard (`002`), links entre documentos (`003`), papel editor (`004`), índice de versões (`005`), pastas aninhadas (`006`), OAuth (`007`), layout do grafo (`008`) e 2FA (`009`).

## 5. Pipeline RAG

**Ingestão (inserir/atualizar)**
1. Recebe texto ou arquivo (`.pdf`, `.docx`, `.md`, `.txt` pelo `POST /documents/upload`, convertido para markdown antes do chunking).
2. Normaliza e calcula `content_hash`; se igual ao atual → não faz nada.
3. Faz chunking com sobreposição, mantendo título/seção no metadata do chunk.
4. Gera embeddings em lote.
5. Em **transação**: grava versão anterior em `document_versions`, apaga chunks antigos, insere novos, incrementa `version`.

**Consulta**
1. Embedding da pergunta.
2. **Busca híbrida**: top‑k vetorial (cosine) + top‑k full‑text (`ts_rank`), combinados por **Reciprocal Rank Fusion**.
3. Filtros opcionais: `collection`, `tags`, `metadata`, data.
4. (Opcional) rerank.
5. Retorna chunks com `document_id`, título, fonte, score e versão — para o agente poder citar e depois atualizar o documento certo.

## 6. Tools MCP

| Tool | Descrição | Parâmetros principais |
|---|---|---|
| `search_knowledge` | Busca híbrida na base | `query`, `collection?`, `tags?`, `top_k=5`, `filters?` |
| `get_document` | Retorna documento completo | `document_id` |
| `add_document` | Insere conhecimento novo | `title`, `content`, `collection`, `tags?`, `metadata?`, `source?`, `external_id?` |
| `update_document` | Substitui/edita conteúdo (gera nova versão) | `document_id`, `content?`, `title?`, `tags?`, `metadata?`, `change_note` |
| `upsert_document` | Cria ou atualiza por `external_id` (idempotente) | `collection`, `external_id`, `title`, `content`, ... |
| `archive_document` | Arquivamento (soft delete) | `document_id`, `reason` |
| `list_documents` | Lista/filtra documentos | `collection?`, `tags?`, `limit`, `offset` |
| `list_collections` | Lista coleções | — |
| `document_history` | Histórico de versões | `document_id` |

**Resources MCP** (leitura): `agent://{slug}/context`. (A v2 do SDK não injeta contexto, e portanto autenticação, em resources estáticos; por isso coleções e documentos são lidos pelas tools.)
**Prompts MCP**: `answer_with_sources` (instrui o agente a buscar antes de responder e citar fontes) e `save_learning` (orienta o agente a registrar um aprendizado de forma padronizada).

> Hoje são **42 tools**. Além das acima: `edit_document` (troca só um trecho), coleções (`create_collection`, `rename_collection`, `delete_collection`), pastas (`create_folder`, `list_folders`, `move_document`), links (`link_documents`, `unlink_documents`, `get_related`) e as de agente da §6.1. Cada conexão só vê as tools que o escopo dela permite.

Boas práticas nas tools:
- Descrições claras de **quando** usar cada tool (o modelo decide com base nelas).
- Retornos compactos (trechos + ids), não o banco inteiro — economiza contexto.
- `update_document` exige `change_note` → rastreabilidade do que o agente mudou.
- Buscar antes de inserir: `add_document` avisa se já existe conteúdo muito similar (similaridade > limiar) e sugere `update_document`, evitando duplicatas.

## 6.1 Agentes: configuração, memória e continuidade

**Problema:** a definição do agente (papel, regras, estilo), o que ele aprendeu e o ponto em que parou ficam presos no histórico do chat. Quando o chat acaba ou o contexto enche, isso se perde.

**Solução:** o agente passa a ser um **registro no banco**. Todo chat começa carregando o agente pelo MCP e termina salvando o que mudou. O chat vira descartável e o agente fica persistente.

### Conceitos

| Conceito | O que é | Exemplo |
|---|---|---|
| **Perfil** | Identidade e instruções do agente (versionadas) | "Agente SDR: qualifica leads, tom consultivo, nunca promete prazo" |
| **Memória** | Fatos/aprendizados do próprio agente, com busca semântica | "Cliente X prefere contato por WhatsApp" |
| **Sessão** | Resumo de cada conversa (o que foi feito, decisões, pendências) | "Revisou 3 propostas; falta enviar a do cliente Y" |
| **Tarefas** | Trabalho em aberto que atravessa chats | "Atualizar tabela de preços até sexta" |
| **Base de conhecimento** | Documentos compartilhados (seções 4–6) | Manuais, processos, catálogos |

A diferença entre **memória** e **base de conhecimento**: a memória é do agente (curta, pessoal, muda muito); a base é da empresa (documentos, compartilhada entre agentes).

### Modelo de dados

```sql
CREATE TABLE agents (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug                TEXT UNIQUE NOT NULL,        -- "sdr", "suporte", "financeiro"
  name                TEXT NOT NULL,
  description         TEXT,
  system_prompt       TEXT NOT NULL,               -- instruções principais
  config              JSONB DEFAULT '{}',          -- modelo preferido, temperatura, idioma, tom...
  allowed_collections TEXT[] DEFAULT '{}',         -- quais coleções da base ele lê/escreve
  scopes              TEXT[] DEFAULT '{read}',     -- read | write | admin
  version             INT NOT NULL DEFAULT 1,
  status              TEXT NOT NULL DEFAULT 'active',
  created_at          TIMESTAMPTZ DEFAULT now(),
  updated_at          TIMESTAMPTZ DEFAULT now()
);

-- Histórico do perfil + propostas de mudança feitas pelo próprio agente
CREATE TABLE agent_versions (
  id            BIGSERIAL PRIMARY KEY,
  agent_id      UUID REFERENCES agents(id) ON DELETE CASCADE,
  version       INT NOT NULL,
  system_prompt TEXT NOT NULL,
  config        JSONB,
  change_note   TEXT,
  proposed_by   TEXT,                              -- "agent:sdr" ou "user:adriano"
  status        TEXT NOT NULL DEFAULT 'applied',   -- proposed | applied | rejected
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- Memória de longo prazo do agente
CREATE TABLE agent_memories (
  id          BIGSERIAL PRIMARY KEY,
  agent_id    UUID REFERENCES agents(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,          -- fact | preference | procedure | decision | lesson
  content     TEXT NOT NULL,
  embedding   vector(1024),
  importance  SMALLINT DEFAULT 3,     -- 1..5, usado no ranking e na limpeza
  shared      BOOLEAN DEFAULT false,  -- visível para outros agentes?
  source      TEXT,                   -- sessão/documento de origem
  expires_at  TIMESTAMPTZ,            -- memórias temporárias
  last_used_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON agent_memories USING hnsw (embedding vector_cosine_ops);

-- Resumo de cada conversa, para retomar no próximo chat
CREATE TABLE agent_sessions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id    UUID REFERENCES agents(id) ON DELETE CASCADE,
  started_at  TIMESTAMPTZ DEFAULT now(),
  ended_at    TIMESTAMPTZ,
  summary     TEXT,                   -- o que foi feito / decidido
  next_steps  TEXT,
  metadata    JSONB DEFAULT '{}'      -- cliente, canal, projeto...
);

-- Tarefas que atravessam vários chats
CREATE TABLE agent_tasks (
  id          BIGSERIAL PRIMARY KEY,
  agent_id    UUID REFERENCES agents(id) ON DELETE CASCADE,
  title       TEXT NOT NULL,
  details     TEXT,
  status      TEXT NOT NULL DEFAULT 'open',   -- open | in_progress | done | cancelled
  due_at      TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

-- Cada API key pertence a um agente: a identidade vem da chave, não de um parâmetro
CREATE TABLE api_keys (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key_hash    TEXT UNIQUE NOT NULL,           -- nunca guardar a chave em texto puro
  agent_id    UUID REFERENCES agents(id),     -- NULL = chave humana/admin
  scopes      TEXT[] NOT NULL DEFAULT '{read}',
  revoked_at  TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT now()
);
```

### Tools MCP de agente

| Tool | Quando usar |
|---|---|
| `load_agent` | **Início de todo chat.** Retorna em um pacote só: perfil, top memórias (por importância e recência), último resumo de sessão e tarefas abertas |
| `recall` | Buscar memórias relevantes ao assunto atual (semântica) |
| `remember` | Salvar um fato/preferência/aprendizado novo (detecta duplicata e atualiza em vez de duplicar) |
| `forget` | Remover/corrigir uma memória errada ou obsoleta |
| `save_session` | **Fim do chat** (ou a cada marco): resumo, decisões e próximos passos |
| `list_tasks` / `upsert_task` | Ler e atualizar trabalho pendente |
| `propose_agent_update` | O agente sugere mudança no próprio prompt/config; fica `proposed` até alguém aprovar (ver `review_agent_update`) |

### Cadastro e gestão de agentes pelo próprio MCP

Todo o cadastro é feito **pelo MCP, conversando com o Claude**, sem painel nem script. Basta pedir "cadastre um agente de suporte que..." e o Claude monta o perfil e grava pelo MCP.

| Tool | O que faz |
|---|---|
| `create_agent` | Cria o agente: `slug`, `name`, `description`, `system_prompt`, `config`, `allowed_collections`, `scopes`. Retorna o agente criado + **API key do agente (exibida uma única vez)** + comando pronto para conectá-lo |
| `update_agent` | Altera o perfil/config diretamente (gera nova versão com `change_note`) |
| `get_agent` / `list_agents` | Consultar agentes cadastrados e suas versões |
| `clone_agent` | Cria um agente novo a partir de outro (ex.: "SDR" → "SDR Imobiliário") |
| `archive_agent` | Desativa o agente e revoga suas chaves |
| `issue_agent_key` / `revoke_agent_key` | Gera nova chave (rotação) ou revoga uma chave |
| `review_agent_update` | Aprova ou rejeita uma proposta feita via `propose_agent_update` |
| `add_agent_memory` / `add_agent_task` | Semear memórias e tarefas iniciais no cadastro (ex.: preferências já conhecidas) |

**Prompt MCP `design_agent`**: um roteiro que o Claude segue para entrevistar o usuário antes de cadastrar. Ele pergunta o objetivo, o público, o tom, as regras do que pode e do que não pode fazer, as coleções que o agente usa e as permissões. Depois mostra o perfil montado para confirmação e só então chama `create_agent`. No Claude Code isso aparece como slash command (ex.: `/mcp__kb__design_agent`).

Exemplo de conversa:

```
Você:   Cadastra um agente de suporte pós-venda, tom cordial, só responde
        com base na coleção "manuais" e nunca promete reembolso.
Claude: (monta o perfil, mostra o resumo e pede confirmação)
Você:   Pode criar.
Claude: → create_agent(slug="suporte", ...)
        ✓ Agente "suporte" v1 criado.
          Chave: kb_sk_... (guarde agora, não será exibida de novo)
          Conectar: claude mcp add --transport http suporte https://kb.../mcp \
                    --header "Authorization: Bearer kb_sk_..."
```

Mais tarde, para ajustar o agente, basta pedir "no agente suporte, adiciona a regra X". O Claude chama `update_agent` e o agente passa para a v2, com histórico.

**Quem pode cadastrar:** as tools de gestão exigem uma chave com escopo `agents:manage`. Essa chave é a sua conexão de "administrador" no Claude. Os agentes comuns recebem chaves sem esse escopo, então não conseguem criar nem alterar outros agentes, só fazer `propose_agent_update` do próprio perfil.

### Autonomia do agente (`auto_apply_updates`)

O usuário decide **no chat** se um agente pode atualizar o próprio perfil sem aprovação:

```
Você:   Pode deixar o agente suporte se atualizar sozinho.
Claude: → set_agent_autonomy(slug="suporte", auto_apply_updates=true)
        ✓ Autoatualização ligada para "suporte" (registrado no histórico).
...
Você:   Desliga a autoatualização do suporte.
Claude: → set_agent_autonomy(slug="suporte", auto_apply_updates=false)
```

| Tool | O que faz |
|---|---|
| `set_agent_autonomy` | Liga/desliga `config.auto_apply_updates` de um agente. Exige escopo `agents:manage` |

Comportamento:
- **Desligado (padrão):** o `propose_agent_update` do agente fica `proposed` até alguém aprovar com `review_agent_update`.
- **Ligado:** o `propose_agent_update` é aplicado na hora e vira uma nova versão (`status = applied`, `proposed_by = agent:<slug>`). Nada se perde: dá para ver o histórico e voltar a uma versão anterior com `update_agent`.
- **Trava de segurança:** o agente **nunca** consegue ligar a própria autonomia. `set_agent_autonomy` só funciona com chave `agents:manage`, e o `propose_agent_update` ignora qualquer tentativa de alterar `auto_apply_updates`, `scopes` ou `allowed_collections`. Esses campos só mudam por ação do usuário.
- Toda mudança de autonomia fica registrada em `agent_versions`, com quem ligou ou desligou e quando.

Também como MCP:
- **Resource** `agent://{slug}/context`, com o mesmo conteúdo do `load_agent`.
- **Prompt** `start_as_agent(slug)`, que monta a mensagem inicial já com o contexto do agente. No Claude Code isso aparece como um slash command.

### Ciclo de vida de um chat

```
1. Início     → load_agent("sdr")
                 ↳ perfil v7 + 15 memórias + "na última sessão: ... falta X" + 2 tarefas abertas
2. Trabalho   → search_knowledge / recall conforme o assunto
                 → remember(...) sempre que aprender algo durável
                 → upsert_task(...) ao criar/concluir pendências
3. Fim/marco  → save_session(summary, next_steps)
4. Evolução   → propose_agent_update(...) → você aprova pelo MCP (review_agent_update) → vira v8
```

Assim, um chat novo amanhã (ou em outra ferramenta: Claude Code, Desktop, API) retoma exatamente de onde parou.

### Regras importantes

- **O agente não reescreve o próprio prompt sozinho**: as mudanças que ele mesmo propõe passam por `proposed → applied`, com aprovação feita por uma chave `agents:manage` (pelo MCP, no chat). Isso evita que o agente se degrade com o tempo ou seja manipulado por conteúdo injetado. Se quiser mais autonomia para um agente específico, o usuário liga isso **no próprio chat** (ver "Autonomia do agente" abaixo).
- **Contexto enxuto**: o `load_agent` tem um limite de tokens (ex.: 15–20 memórias + último resumo); o resto vem sob demanda via `recall`.
- **Higiene da memória**: job periódico que consolida memórias parecidas, expira as temporárias e rebaixa as nunca usadas (`last_used_at`).
- **Isolamento**: um agente só vê as próprias memórias + as `shared = true`, e só as coleções em `allowed_collections`.
- **Sincronização opcional com o Claude Code**: um comando `cli sync-agents` pode gerar arquivos `.claude/agents/<slug>.md` a partir do banco. Assim os subagentes do Claude Code usam a mesma definição, e a fonte da verdade continua sendo o banco.

## 7. API REST (espelho)

```
GET    /health
GET    /collections              POST /collections
POST   /search
GET    /documents                POST /documents
GET    /documents/{id}           PATCH /documents/{id}
PUT    /documents/upsert         DELETE /documents/{id}   (archive)
GET    /documents/{id}/versions
GET    /agents                   POST /agents
GET    /agents/{slug}            PATCH /agents/{slug}
GET    /agents/{slug}/context    (mesmo retorno do load_agent)
GET    /agents/{slug}/proposals  POST /agents/{slug}/proposals/{id}/approve|reject
GET    /agents/{slug}/memories   GET /agents/{slug}/sessions   GET /agents/{slug}/tasks
POST   /documents/upload         (PDF, .docx, .md, .txt → markdown → documento vetorizado)
/mcp                             (endpoint MCP streamable HTTP montado no mesmo app)
```

> Além dessas: pastas, links e gestão de coleções na REST; a dashboard usa `/dash/api` e o OAuth usa `/authorize`, `/oauth/*` e `/.well-known/*`. A lista completa está no README.

## 8. Segurança

- **Autenticação por API key / Bearer token** no endpoint MCP e na REST (header `Authorization`); chaves por agente, para saber quem inseriu/alterou (`created_by`/`updated_by`).
- **Escopos por chave**: `read`, `write`, `admin`; e opcionalmente restrição por coleção.
- HTTPS obrigatório fora do localhost (reverse proxy: Caddy/Traefik/Nginx).
- Rate limit básico e limite de tamanho de conteúdo por requisição.
- Logs de auditoria de toda escrita.
- Conteúdo retornado pela base é **dado, não instrução** — documentar isso no prompt dos agentes (mitiga prompt injection via documentos inseridos).
- **OAuth 2.1 no MCP (feito):** conectores como o do claude.ai abrem a tela *Autorizar* do Concilium; a pessoa entra com a conta da dashboard e escolhe escopos e as pastas principais (coleções) que o conector vê. A dashboard tem usuários com papéis (admin, editor, viewer) e 2FA por TOTP.

## 9. Conexão com Claude e nossos agentes

**Claude Code**
```bash
claude mcp add --transport http kb https://kb.nossodominio.com/mcp \
  --header "Authorization: Bearer <API_KEY>"
```

**Claude Desktop / claude.ai** — adicionar como conector MCP remoto (URL `/mcp`; o Concilium abre a tela de autorização OAuth), ou via `stdio` em dev:
```json
{
  "mcpServers": {
    "kb": { "command": "uv", "args": ["run", "mcp-rag-api", "stdio"] }
  }
}
```

**Claude API / nossos agentes** — usar o MCP connector da Messages API (servidor remoto por URL + token) ou o Claude Agent SDK apontando para o mesmo servidor MCP. O passo a passo está no README.

## 10. Estrutura de pastas

```
web-server/
├── PLANO.md / README*.md / docs/        # guia (pt-BR) e guia de deploy
├── pyproject.toml / uv.lock / .env.example
├── Dockerfile.coolify / docker-compose.yml (dev) / deploy/docker-compose.yml (app + pgvector)
├── .github/workflows/docker.yml        # imagem pública no ghcr.io
├── src/mcp_rag_api/
│   ├── main.py          # FastAPI + MCP em /mcp + dashboard em /dashboard
│   ├── mcp_server.py    # 42 tools, prompts, resource agent://{slug}/context
│   ├── api.py           # REST (espelho das tools + upload de arquivos)
│   ├── dashboard_api.py # API da dashboard (/dash/api)
│   ├── oauth_api.py     # OAuth 2.1 do MCP (tela Autorizar)
│   ├── config.py / db.py / security.py / logging_config.py
│   ├── cli.py           # serve, stdio, migrate, create-key, create-user, reset-2fa, sync-agents, cleanup, relink, reindex
│   ├── migrations/      # 001 a 009, aplicadas no start
│   ├── static/          # dashboard (pt-BR, en, es; temas)
│   └── core/
│       ├── chunking.py / embeddings.py / search.py / reindex.py
│       ├── documents.py # CRUD + versionamento + duplicatas
│       ├── folders.py / links.py / wikilinks.py / graph.py / graph_layout.py
│       ├── agents.py / memory.py
│       ├── dash_auth.py / mfa.py / oauth.py
│       └── obsidian_import.py / file_import.py
└── tests/unit, tests/integration
```

## 11. Fases de implementação

**Fase 0 — Setup (0,5 dia)**
- [x] `pyproject.toml` (uv), ruff, mypy, pytest
- [x] `docker-compose.yml` de dev (Postgres externo) e `deploy/docker-compose.yml` (app + pgvector)
- [x] Config via `.env`, healthcheck

**Fase 1 — Banco e núcleo (1–2 dias)**
- [x] Migração SQL inicial (schema das seções 4 e 6.1)
- [x] Interface de embeddings + 1 provedor implementado
- [x] Chunking + ingestão + versionamento
- [x] Busca híbrida com RRF
- [x] Testes de integração com Postgres real

**Fase 2 — MCP server (1 dia)**
- [x] Tools: `search_knowledge`, `get_document`, `add_document`, `update_document`, `upsert_document`, `archive_document`, `list_*`, `document_history`
- [x] Resources e prompts
- [x] Transporte stdio + streamable HTTP
- [x] Teste ponta a ponta com cliente MCP (streamable HTTP)
- [x] Testar com Claude Code usando embeddings reais (Hugging Face `bge-m3`)

**Fase 2.5 — Registro de agentes e memória (1–2 dias)**
- [x] Tabelas `agents`, `agent_versions`, `agent_memories`, `agent_sessions`, `agent_tasks`
- [x] Tools `load_agent`, `recall`, `remember`, `forget`, `save_session`, `list_tasks`, `upsert_task`, `propose_agent_update`
- [x] Tools de gestão via MCP: `create_agent`, `update_agent`, `get_agent`, `list_agents`, `clone_agent`, `archive_agent`, `issue_agent_key`, `revoke_agent_key`, `review_agent_update`, `set_agent_autonomy`, `add_agent_memory`, `add_agent_task`
- [x] Garantir que `propose_agent_update` não altera `auto_apply_updates`, `scopes` nem `allowed_collections` (teste)
- [x] Prompts `design_agent` (entrevista de cadastro) e `start_as_agent`; resource `agent://{slug}/context`
- [x] Escopo `agents:manage` e API key vinculada ao agente (gerada no `create_agent`)
- [x] Cadastrar 1 agente piloto **pelo chat do Claude** e validar o ciclo início → trabalho → fim → novo chat retomando

**Fase 3 — REST + auth (1 dia)**
- [x] Rotas FastAPI espelhando as tools
- [x] API keys por agente com escopos; auditoria
- [x] Upload de arquivos (PDF, .docx, .md, .txt) pelo `POST /documents/upload`; reenviar o mesmo arquivo atualiza o documento
- [x] Na dashboard: *Importar → Arquivos* (até 50 por vez, para uma pasta) e *Vault Obsidian*

**Fase 4 — Qualidade do RAG (contínuo)**
- [x] Detecção de duplicatas no insert
- [ ] Rerank opcional
- [ ] Conjunto de avaliação (perguntas → documentos esperados) e métricas (recall@k, MRR)
- [ ] Ajuste fino de chunk / top_k guiado pelo conjunto de avaliação (hoje: 450/60 palavras, configurável)

**Fase 5 — Deploy e operação**
- [x] Imagem Docker (`Dockerfile.coolify`: multi-stage com uv, non-root, healthcheck, proxy headers)
- [x] CI com testes contra pgvector efêmero, build da imagem e deploy automático
- [x] Imagem pública multi-arch no `ghcr.io` (GitHub Actions) + [guia de deploy](docs/deploy.md): Compose, Coolify e outras plataformas
- [x] Portão de autenticação no `/mcp` inteiro (401 sem chave válida)
- [x] Deploy no Coolify atrás de HTTPS (banco `pgvector/pgvector:pg16`), com instâncias em produção
- [x] Logs estruturados com request id
- [ ] Backup automático do Postgres (o guia de deploy recomenda; no Coolify, *Database → Backups*)
- [ ] Métricas básicas
- [x] Documentação de conexão (README: Claude Code, Desktop, claude.ai, API e agentes próprios)

**Fase 6 — Dashboard e espaço de notas (além do plano original)**
- [x] Dashboard web com login, papéis (admin, editor, viewer) e 2FA por TOTP
- [x] Espaço de notas: editor markdown, `[[wikilinks]]`, backlinks e grafo 2D/3D da base
- [x] Pastas dentro de pastas, mover/renomear/excluir; acesso por coleção (a "pasta principal" da dashboard) para chaves, agentes e conexões OAuth
- [x] Import de vault Obsidian e de arquivos soltos (PDF, .docx, .md, .txt)
- [x] Gestão de agentes, chaves de API e coleções pela dashboard
- [x] OAuth 2.1 no MCP (conector do claude.ai sem chave para copiar)
- [x] Três idiomas (pt-BR, en, es) e 13 temas

## 12. Decisões

**Resolvidas**

1. **Embeddings:** provedor plugável, padrão `voyage`. As instâncias em produção usam `huggingface` com `bge-m3` (bom em PT-BR, sem GPU). Dimensão fixa em 1024; trocar de modelo exige `mcp-rag-api reindex`.
2. **Hospedagem:** qualquer host de containers, com a imagem pública. As instâncias de referência rodam em VPS com Coolify, atrás de HTTPS, cada uma com o próprio Postgres + pgvector.
3. **Coleções:** livres, criadas por quem usa (dashboard ou MCP). Não existe um conjunto inicial fixo.
4. **Escrita dos agentes:** direta, limitada por escopo (`write`) e por coleção, com versionamento, aviso de duplicata e auditoria. Só o **perfil** do agente passa por aprovação (propostas e autonomia, §6.1). O status `pending_review` para documentos não foi necessário.
5. **Multi-tenant:** fora do app por enquanto. Cada empresa tem a própria instância com banco próprio; dentro dela, o isolamento é por coleções, papéis da dashboard e escopo das chaves.
6. **Agentes iniciais:** decisão de cada instalação, com o prompt `design_agent`.

**Em aberto**

- Rerank e conjunto de avaliação com métricas (recall@k, MRR).
- OCR para PDF escaneado (hoje é recusado com mensagem clara) e upload de arquivo pelo MCP.
- Multi-tenant dentro de uma instância só.
- Backup automático e métricas.

## 13. Critérios de pronto (MVP): atingidos

- [x] Claude Code conectado ao MCP consegue: buscar e responder citando fontes; inserir um novo documento; atualizar um documento existente e ver a nova versão refletida na busca seguinte.
- [x] Pelo chat do Claude (via MCP) é possível cadastrar um agente novo, alterar seu perfil, aprovar uma proposta e ligar/desligar a autoatualização, tudo sem sair da conversa.
- [x] Um agente cadastrado no banco é carregado com `load_agent` em um chat novo e retoma memórias, último resumo e tarefas de um chat anterior.
- [x] Testes de integração passando contra Postgres + pgvector reais.
- [x] Sobe com `docker compose up` e um `.env` (`deploy/docker-compose.yml`, com a imagem pública).
