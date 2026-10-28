# Concilium — guia completo (pt-BR)

> Visão geral do projeto: [README](../README.pt-BR.md) · [English](../README.md) · [Español](../README.es.md)

Servidor **MCP** e **API REST** em Python, com **PostgreSQL + pgvector**. Ele oferece duas coisas aos seus agentes de IA:

- **Base de conhecimento (RAG):** documentos que qualquer agente consulta, insere e atualiza, com busca semântica e por palavra-chave, versionamento e aviso de duplicata.
- **Registro de agentes:** cada agente tem perfil (instruções), memória, resumos de sessão e tarefas guardados no banco. Um chat novo carrega tudo isso e continua de onde parou.

Você gerencia tudo **conversando com o Claude**: cadastrar agentes, ajustar regras, aprovar mudanças e liberar autonomia.

> Arquitetura e decisões: [PLANO.md](../PLANO.md)

---

## Sumário

1. [Pré-requisitos](#1-pré-requisitos)
2. [Instalação](#2-instalação)
3. [Configuração (.env)](#3-configuração-env)
4. [Criar sua chave de administrador](#4-criar-sua-chave-de-administrador)
5. [Rodar o servidor](#5-rodar-o-servidor)
6. [Conectar ao Claude](#6-conectar-ao-claude)
7. [Tutorial: primeiro uso](#7-tutorial-primeiro-uso)
8. [Tutorial: cadastrar e usar um agente](#8-tutorial-cadastrar-e-usar-um-agente)
9. [Tutorial: autonomia e propostas](#9-tutorial-autonomia-e-propostas)
10. [Usar pela API REST](#10-usar-pela-api-rest)
11. [Referência](#11-referência)
12. [Manutenção](#12-manutenção)
13. [Problemas comuns](#13-problemas-comuns)
14. [Deploy em produção](#14-deploy-em-produção)

---

## 1. Pré-requisitos

| O quê | Para quê | Como conferir |
|---|---|---|
| Postgres do **DB-DOCKER** rodando | banco (container `db-postgres`, imagem pgvector, porta 5432) | `docker ps --filter name=db-postgres` |
| [uv](https://docs.astral.sh/uv/) | instala o Python e as dependências | `uv --version` |
| Docker | só se for rodar a API em container | `docker --version` |
| Claude Code (ou Claude Desktop) | conversar com o MCP | `claude --version` |
| Chave de um provedor de embeddings | transformar texto em vetores | veja o [passo 3](#3-configuração-env) |

Este projeto **não sobe banco próprio**: ele usa um database `kb` dentro do Postgres compartilhado.

## 2. Instalação

```bash
cd /mnt/l/Workspace/mcp-rag-api

# cria o database do projeto no Postgres compartilhado (só na primeira vez)
docker exec db-postgres psql -U user -d defaultdb -c "CREATE DATABASE kb"

# instala as dependências
uv sync
```

As tabelas são criadas sozinhas quando o servidor sobe pela primeira vez.

## 3. Configuração (.env)

```bash
cp .env.example .env
```

Abra o `.env` e defina **pelo menos** o provedor de embeddings:

| `EMBEDDING_PROVIDER` | Precisa de | Observação |
|---|---|---|
| `voyage` (padrão) | `VOYAGE_API_KEY` | chave em voyageai.com |
| `openai` | `OPENAI_API_KEY` | modelo `text-embedding-3-small` |
| `huggingface` | `HF_API_KEY` | Inference API do Hugging Face, modelo BAAI/bge-m3 (bom em português), sem baixar nada; `EMBEDDING_API_URL` opcional |
| `local` | `uv sync --extra local` | roda na sua máquina (modelo BAAI/bge-m3, ~2 GB, bom em português); sem custo, mais lento |
| `fake` | nada | **só para testar a instalação**: a busca não entende significado |

Exemplo:

```env
DATABASE_URL=postgresql://user:change-me@localhost:5432/kb
PUBLIC_URL=http://localhost:8000
EMBEDDING_PROVIDER=voyage
VOYAGE_API_KEY=pa-xxxxxxxx
```

> ⚠️ Vetores de provedores/modelos diferentes não são compatíveis. Trocou o provedor depois de já ter documentos? Rode `uv run mcp-rag-api reindex` para recalcular todos os embeddings (documentos, grafo e memórias).

Outras opções do `.env` (os padrões costumam servir):

| Variável | Padrão | Para quê |
|---|---|---|
| `PUBLIC_URL` | `http://localhost:8000` | URL usada no comando de conexão que o `create_agent` devolve |
| `CHUNK_WORDS` / `CHUNK_OVERLAP_WORDS` | 450 / 60 | tamanho dos pedaços em que os documentos são quebrados |
| `DUPLICATE_THRESHOLD` | 0.92 | similaridade a partir da qual o `add_document` avisa que o conteúdo é duplicado |
| `LOAD_AGENT_MEMORY_LIMIT` | 15 | quantas memórias o agente recebe ao iniciar um chat |

## 4. Criar sua chave de administrador

```bash
uv run mcp-rag-api create-key --label adriano --scopes admin
```

A saída é algo como `kb_sk_Xy12...`. **Guarde agora**: a chave não é exibida de novo, porque o banco só guarda o hash. É com ela que você conecta o Claude como administrador.

Perdeu a chave? Crie outra com o mesmo comando.

## 5. Rodar o servidor

**Opção A: local (desenvolvimento)**

```bash
uv run mcp-rag-api serve            # adicione --reload para reiniciar ao editar o código
```

**Opção B: Docker (só a API; o banco continua sendo o `db-postgres`; usa o mesmo `Dockerfile.coolify` da produção)**

```bash
docker compose up -d --build
docker compose logs -f api
```

Para conferir se está no ar:

```bash
curl http://localhost:8000/health        # {"status":"ok"}
```

- Documentação interativa da API REST: http://localhost:8000/dash/docs (atrás do login da dashboard)
- Endpoint MCP: http://localhost:8000/mcp

### Dashboard do time

Com o servidor no ar, abra `http://localhost:8000/dashboard`. O primeiro acesso é criado pela linha de comando:

```bash
uv run mcp-rag-api create-user --username voce --role admin
```

| Papel | O que faz |
|---|---|
| `viewer` (Leitor) | lê notas, busca, grafo, chaves e agents |
| `editor` (Editor) | + cria, edita, move e arquiva notas e pastas |
| `admin` (Administrador) | + chaves de API, usuários, links manuais e autonomia dos agents |

A tela inicial é **Notas**: pastas (coleções) à esquerda, editor visual (Toast UI, com aba Markdown) no centro e, sob demanda, as conexões e o histórico da nota. Tudo o que o time escreve vira, na hora, conhecimento dos agentes. O salvamento cria uma versão (Ctrl+S, botão, ~20 s parado ou ao trocar de nota); enquanto isso, um rascunho fica guardado no navegador. Atalhos: **Ctrl+S** salva, **Alt+N** cria nota.

**Pastas dentro de pastas:** cada pasta principal (coleção) é um projeto, cliente ou área — é ela que define o que cada agente pode acessar. Dentro dela dá para criar **subpastas sem limite de profundidade**, como no Notion: passe o mouse na pasta → **+** cria uma nota ali, **…** abre o menu (nova subpasta, renomear, mover, excluir). Mover uma pasta leva tudo o que tem dentro, inclusive para outra pasta principal. Excluir uma pasta não apaga nada: as notas e subpastas sobem um nível. Agentes e integrações também usam as subpastas, por **caminho** dentro da pasta principal (`"Clientes/Acme/Reuniões"`, sem diferenciar maiúsculas; por isso nomes de pasta não podem ter `/`):

| MCP | REST | O que faz |
|---|---|---|
| `list_folders` | `GET /folders?collection=` | lista as pastas com o caminho completo |
| `create_folder` | `POST /folders` `{collection, path}` | cria o caminho, só os níveis que faltam (idempotente) |
| `add_document` / `upsert_document` com `folder_path` | `POST /documents`, `PUT /documents/upsert` | cria a nota já na pasta (criando o que faltar) |
| `move_document` | `POST /documents/{id}/move` `{collection, folder_path}` | move para outra pasta principal e/ou pasta; sem `folder_path`, vai para a raiz |
| `get_document` | `GET /documents/{id}` | devolve também `folder_path` (`null` = raiz) |

O acesso continua sendo por pasta principal: a chave que só enxerga `Clientes` enxerga todas as pastas dentro dela.

### Importar um vault Obsidian

Quem tem um vault no Obsidian e quer migrar para a base: na tela Notas, clique em **Importar** (papel de editor ou admin), selecione o `.zip` do vault e acompanhe o progresso na tela — a vetorização (quebra em trechos + embeddings) roda em background e pode levar minutos em vaults grandes. O cancelamento é cooperativo: para entre uma nota e outra.

**Como zipar o vault:** no gerenciador de arquivos, botão direito na pasta do vault → "Compactar"/"Comprimir" (ou `zip -r vault.zip minha-vault/`). Limites da v1: 100 MB de zip e 5.000 notas `.md`.

**O que vira o quê:**

| No vault | Na base |
|---|---|
| Pasta | Coleção (pastas aninhadas viram um nome único com " - ") |
| Nota `.md` | Documento com chunking + vetorização completos |
| `title`/`tags` do frontmatter | Título/tags do documento |
| `[[Wikilink]]` entre notas | Link real (backlinks, grafo) — `![[embed]]` também vira link |
| Caminho do arquivo no vault | `external_id` (a idempotência mora aqui) |

**O que é ignorado:** a pasta `.obsidian/`, arquivos ocultos, anexos (imagens, PDFs) e o frontmatter no corpo do texto. Comentários Obsidian (`%%...%%`) são removidos.

**Re-import é seguro:** importar o mesmo zip de novo atualiza as notas existentes (bump de versão só no que mudou) em vez de duplicar. Se o servidor reiniciar no meio do import, o job some da memória — basta importar de novo para completar.

### Importar arquivos (PDF, Word, Markdown, texto)

Na tela Notas, **Importar** → **Arquivos**: escolha até 50 arquivos (25 MB cada, 100 MB no total) e o destino (pasta principal ou uma pasta dentro dela). Cada arquivo vira uma nota com o nome do arquivo como título, já quebrada em trechos e vetorizada — o progresso aparece na tela como no import do vault.

| Formato | Como é convertido |
|---|---|
| PDF (`.pdf`) | texto de cada página (`pypdf`); o número de páginas vai para o metadata |
| Word (`.docx`) | markdown com títulos, listas, negrito e links (`mammoth`); imagens são descartadas |
| Markdown (`.md`) | como está; `title`/`tags` do frontmatter viram título/tags |
| Texto (`.txt`) | como está |

**Limites:** PDF escaneado (só imagem) não tem texto — aparece como erro daquele arquivo, sem OCR por enquanto. PDF com senha também é recusado. O arquivo original não fica guardado, só o texto. `.doc` antigo, planilhas e apresentações ficam de fora.

**Reenvio é seguro:** o `external_id` é `file:<pasta>/<nome do arquivo>`, então mandar o mesmo arquivo para o mesmo lugar atualiza a nota (versão nova se o texto mudou) em vez de duplicar.

Pela API, um arquivo por chamada (síncrono, devolve o resultado do upsert):

```bash
curl -s -X POST $API/documents/upload -H "Authorization: Bearer $KEY" \
  -F "file=@contrato.pdf" -F "collection=clientes" -F "folder_path=Acme/Contratos"
```

## 6. Conectar ao Claude

### Claude Code

```bash
claude mcp add --transport http kb http://localhost:8000/mcp \
  --header "Authorization: Bearer kb_sk_SUA_CHAVE_ADMIN"
```

Confira com `claude mcp list` (deve aparecer `kb` conectado). Dentro do Claude Code, o comando `/mcp` mostra as tools.

### Claude Desktop (Windows, com o projeto no WSL)

Edite `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kb": {
      "command": "wsl",
      "args": ["-e", "bash", "-lc", "cd /mnt/l/Workspace/mcp-rag-api && KB_API_KEY=kb_sk_SUA_CHAVE uv run mcp-rag-api stdio"]
    }
  }
}
```

Reinicie o Claude Desktop. Nesse modo (stdio) o servidor HTTP não precisa estar rodando: o Desktop inicia o processo sozinho.

### Seus próprios agentes (Claude API / Agent SDK)

Aponte o cliente MCP para `https://SEU_DOMINIO/mcp` com o header `Authorization: Bearer <chave do agente>`. Para produção, veja o [deploy em produção](#14-deploy-em-produção).

## 7. Tutorial: primeiro uso

Com o Claude Code conectado (passo 6), converse normalmente. Os exemplos abaixo são pedidos que você digita no chat, seguidos da tool que o Claude chama.

**1. Criar uma coleção**
> Crie uma coleção chamada "manuais" para os manuais internos.

→ `create_collection`

**2. Inserir conhecimento**
> Adicione na coleção manuais: "Política de reembolso: o cliente pode pedir reembolso em até 7 dias após a compra, pelo portal, informando o número do pedido."

→ `add_document`. Se já existir algo muito parecido, o servidor **não duplica**: ele devolve o documento semelhante, e o Claude sugere atualizar esse documento.

**3. Consultar**
> Qual o prazo de reembolso? Responda citando a fonte.

→ `search_knowledge`. A resposta vem com título e `document_id`. O prompt `/mcp__kb__answer_with_sources` já instrui o Claude a responder assim.

**4. Atualizar**
> O prazo de reembolso mudou para 30 dias, atualize o documento.

→ `update_document` com `change_note`. Isso gera a versão 2, e a anterior fica no histórico (`document_history`).

**5. Ligar documentos**
Ao escrever um documento, cite outro pelo título entre colchetes duplos: `[[Política de reembolso]]` (também vale `[[Título|texto exibido]]`). Isso vira um **link explícito**: o documento citado ganha um *backlink*, e os dois aparecem ligados por uma linha sólida no grafo da dashboard. Maiúsculas e acentos não importam. Se o título ainda não existe, o link fica pendente e se conecta sozinho quando o documento for criado. Para ver tudo o que se relaciona a um documento (links, backlinks e vizinhos semânticos), use `get_related`; para ligar sem mexer no texto, `link_documents`.

> **Quem pode ligar:** pela API REST e pelo MCP, `link_documents`/`unlink_documents` exigem o escopo `write`. Na dashboard, criar e remover links manuais é ação de **admin** (decisão de produto: quem conecta a base pela tela é quem a administra).

**6. Sincronizar de outro sistema**
Use `upsert_document` com um `external_id` (por exemplo, o id no CRM). Se o documento já existir, ele é atualizado; se não, é criado.

## 8. Tutorial: cadastrar e usar um agente

### 8.1 Cadastro, conversando com o Claude

No Claude Code conectado com a **chave admin**, digite:

```
/mcp__kb__design_agent
```

ou simplesmente:

> Cadastre um agente de suporte pós-venda, tom cordial, que só usa a coleção manuais e nunca promete reembolso sem consultar a política.

O Claude vai:
1. perguntar o que faltar (objetivo, tom, regras, coleções, permissões, autonomia);
2. mostrar o perfil montado (slug, system prompt, coleções e escopos) e **pedir sua confirmação**;
3. chamar `create_agent` e entregar:
   - a **chave do agente**, exibida uma única vez;
   - o **comando pronto** para conectá-lo, por exemplo:
     `claude mcp add --transport http kb-suporte http://localhost:8000/mcp --header "Authorization: Bearer kb_sk_..."`

Você pode semear memórias e tarefas no mesmo momento:
> Adicione ao agente suporte a memória "cliente ACME prefere WhatsApp" e a tarefa "revisar FAQ até sexta".

→ `add_agent_memory` e `add_agent_task`

### 8.2 Usar o agente

Rode o `connect_command` recebido. Em um chat conectado **com a chave do agente**:

```
/mcp__kb-suporte__start_as_agent suporte
```

ou peça: *"carregue seu perfil"*. O Claude chama `load_agent` e recebe:
- o perfil (system prompt, versão, regras);
- as memórias mais importantes;
- o **resumo da última sessão** e os próximos passos;
- as tarefas abertas.

Durante o trabalho, o agente usa:

| Situação | Tool |
|---|---|
| consultar a base | `search_knowledge` |
| lembrar algo específico | `recall` |
| aprendeu algo durável | `remember` (se já existir memória parecida, ela é atualizada em vez de duplicada) |
| memória errada | `forget` (apaga ou corrige) |
| pendência nova ou concluída | `upsert_task` |
| fim do chat ou um marco | `save_session` com resumo e próximos passos |

No dia seguinte, em outro chat (ou em outra ferramenta), `load_agent` traz tudo de volta.

> Dica: o prompt `/mcp__kb-suporte__save_learning` pede ao agente que revise a conversa e salve memórias, documentos, tarefas e o resumo de uma vez.

### 8.3 Ajustar o agente depois

Com a chave admin:

| Pedido no chat | Tool |
|---|---|
| "No agente suporte, adicione a regra: sempre confirmar o número do pedido." | `update_agent` (gera nova versão) |
| "Mostre o histórico do agente suporte." | `get_agent` |
| "Volte o suporte para a versão 2." | `restore_agent_version` |
| "Crie um agente suporte-vip a partir do suporte, com tom mais formal." | `clone_agent` |
| "Gere uma nova chave para o suporte" / "revogue a chave kb_sk_AbCd…" | `issue_agent_key` / `revoke_agent_key` |
| "Desative o agente suporte." | `archive_agent` (também revoga as chaves dele) |

### 8.4 Opcional: subagentes do Claude Code

```bash
uv run mcp-rag-api sync-agents
```

Gera `.claude/agents/<slug>.md` a partir do banco, para usar os agentes como subagentes do Claude Code. A fonte da verdade continua sendo o banco: rode de novo depois de alterar um agente.

## 9. Tutorial: autonomia e propostas

Um agente pode sugerir mudanças no próprio perfil com `propose_agent_update`. O que acontece depende da **autonomia**, que só você controla:

| Autonomia | O que acontece com a proposta |
|---|---|
| **desligada** (padrão) | fica pendente até você aprovar |
| **ligada** | é aplicada na hora como nova versão (e continua no histórico) |

Pedidos no chat, com a chave admin:

> Pode deixar o agente suporte se atualizar sozinho.

→ `set_agent_autonomy(auto_apply_updates=true)`

> Desligue a autoatualização do suporte.

> Tem proposta de mudança pendente?

→ `list_agent_proposals`

> Aprove a proposta 3. / Rejeite a proposta 4, motivo: tom informal demais.

→ `review_agent_update`

**Travas de segurança:** o agente **nunca** consegue ligar a própria autonomia nem mudar as próprias permissões ou coleções. Esses campos são ignorados nas propostas dele, e as tools de gestão exigem o escopo `agents:manage`.

## 10. Usar pela API REST

Todas as rotas exigem `Authorization: Bearer <chave>`. A lista completa, com formulário de teste, está em http://localhost:8000/dash/docs (abra logado na dashboard).

```bash
KEY=kb_sk_SUA_CHAVE
API=http://localhost:8000

# buscar
curl -s -X POST $API/search -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"query": "prazo de reembolso", "top_k": 3}'

# inserir documento
curl -s -X POST $API/documents -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"collection": "manuais", "title": "Horário", "content": "Atendimento de 8h às 18h."}'

# criar/atualizar por id externo (sincronização)
curl -s -X PUT $API/documents/upsert -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"collection": "manuais", "external_id": "crm-42", "title": "Contato", "content": "Ramal 200."}'

# enviar um arquivo (PDF, .docx, .md, .txt) — vira nota vetorizada; reenviar atualiza
curl -s -X POST $API/documents/upload -H "Authorization: Bearer $KEY" \
  -F "file=@manual.pdf" -F "collection=manuais"

# atualizar um documento (versão nova no histórico)
curl -s -X PATCH $API/documents/DOC_ID -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"change_note": "horário atualizado", "content": "Atendimento de 9h às 18h."}'

# trava otimista opcional: envie base_version (a versão que você leu);
# se outra pessoa salvou antes, a resposta é 409 {"detail": {"message": ..., "current_version": 2}}
curl -s -X PATCH $API/documents/DOC_ID -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"change_note": "horário atualizado", "content": "Atendimento de 9h às 18h.", "base_version": 1}'

# links, backlinks e vizinhos semânticos de um documento
curl -s "$API/documents/DOC_ID/related?k=5" -H "Authorization: Bearer $KEY"

# ligar dois documentos (link manual, com nota opcional)
curl -s -X POST $API/documents/DOC_ID/links -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"target_id": "OUTRO_DOC_ID", "note": "o pitch usa estes números"}'

# contexto de um agente (mesmo retorno do load_agent)
curl -s $API/agents/suporte/context -H "Authorization: Bearer $KEY"

# ligar a autonomia
curl -s -X PUT $API/agents/suporte/autonomy -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"auto_apply_updates": true}'
```

## 11. Referência

### Escopos

| Escopo | Permite |
|---|---|
| `read` | consultar a base e ler a própria memória, as sessões e as tarefas |
| `write` | inserir e atualizar documentos; gravar memória, sessões e tarefas (inclui `read`) |
| `agents:manage` | cadastrar e configurar agentes, aprovar propostas, ligar a autonomia |
| `admin` | tudo, incluindo dar `agents:manage` ou `admin` a um agente |

A chave de um agente herda os escopos e as coleções permitidas (`allowed_collections`; vazio = todas) do perfil dele. Alterações valem na hora. Tools que gravam memória/sessões/tarefas de um agente (`remember`, `forget`, `save_session`, `upsert_task`, e também `add_agent_memory`/`add_agent_task`) exigem o escopo `write` — `agents:manage` sozinho não basta.

### Tools MCP

| Grupo | Tools |
|---|---|
| Base | `search_knowledge`, `get_document`, `list_documents`, `list_collections`, `create_collection`, `add_document`, `update_document`, `upsert_document`, `archive_document`, `document_history` |
| Links entre documentos | `get_related`, `link_documents`, `unlink_documents` (e `[[Título]]` no conteúdo) |
| Agente (sobre si mesmo) | `load_agent`, `recall`, `remember`, `forget`, `save_session`, `list_sessions`, `list_tasks`, `upsert_task`, `propose_agent_update` |
| Gestão (`agents:manage`) | `create_agent`, `update_agent`, `set_agent_autonomy`, `get_agent`, `list_agents`, `clone_agent`, `archive_agent`, `restore_agent_version`, `issue_agent_key`, `revoke_agent_key`, `list_agent_proposals`, `review_agent_update`, `add_agent_memory`, `add_agent_task` |

**Prompts:** `design_agent`, `start_as_agent`, `answer_with_sources`, `save_learning`.
**Resource:** `agent://{slug}/context`.

### Comandos

| Comando | O que faz |
|---|---|
| `uv run mcp-rag-api serve [--port 8000] [--reload]` | API REST + MCP HTTP |
| `uv run mcp-rag-api stdio` | MCP via stdio (usa `KB_API_KEY`) |
| `uv run mcp-rag-api create-key --label X --scopes admin` | cria uma chave humana |
| `uv run mcp-rag-api migrate` | aplica migrações pendentes (o `serve` já faz isso) |
| `uv run mcp-rag-api sync-agents [--out .claude/agents]` | exporta os agentes para o Claude Code |
| `uv run mcp-rag-api cleanup` | remove memórias expiradas |
| `uv run mcp-rag-api reindex` | recalcula todos os embeddings com o provedor atual (depois de trocar de provedor/modelo) |
| `uv run mcp-rag-api relink` | reprocessa os `[[links]]` de todos os documentos (depois de importar conteúdo) |

## 12. Manutenção

**Limpeza de memórias expiradas.** Agende o comando, por exemplo no `crontab -e`:

```cron
0 3 * * * cd /mnt/l/Workspace/mcp-rag-api && /home/oadri/.local/bin/uv run mcp-rag-api cleanup
```

**Backup** do database `kb`:

```bash
docker exec db-postgres pg_dump -U user -d kb -Fc > kb_$(date +%F).dump
# restaurar:
docker exec -i db-postgres pg_restore -U user -d kb --clean < kb_2026-09-29.dump
```

**Auditoria.** Toda escrita fica na tabela `audit_log` (quem, o quê, quando):

```bash
docker exec db-postgres psql -U user -d kb -c "SELECT created_at, actor, action, target FROM audit_log ORDER BY id DESC LIMIT 20"
```

**Testes:**

```bash
uv sync --extra dev
uv run pytest tests/unit
# integração: o database indicado é APAGADO a cada execução
docker exec db-postgres psql -U user -d defaultdb -c "CREATE DATABASE kb_test"   # uma vez
TEST_DATABASE_URL=postgresql://user:change-me@localhost:5432/kb_test uv run pytest tests/integration
```

## 13. Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| `database "kb" does not exist` | database não criado | `docker exec db-postgres psql -U user -d defaultdb -c "CREATE DATABASE kb"` |
| `connection refused` na porta 5432 | `db-postgres` parado | suba o compose do DB-DOCKER |
| `extension "vector" is not available` | Postgres sem pgvector | use a imagem `pgvector/pgvector` (é a do `db-postgres`) |
| `VOYAGE_API_KEY não configurada` (ou OpenAI) | falta a chave no `.env` | preencha a chave ou troque o `EMBEDDING_PROVIDER` |
| `Chave de API inválida ou revogada` | chave errada, revogada ou de agente arquivado | crie outra com `create-key` ou `issue_agent_key` |
| `Esta chave não tem o escopo 'agents:manage'` | você está conectado com a chave de um agente | use a chave admin para gerenciar agentes |
| `Sem acesso à coleção 'x'` | coleção fora de `allowed_collections` do agente | ajuste com `update_agent` |
| Busca retorna coisas sem relação | `EMBEDDING_PROVIDER=fake` | use um provedor real |
| API em Docker não acessa o banco | container fora da rede `db_network` | confira `docker network ls` e o nome da rede no `docker-compose.yml` |
| Porta 8000 ocupada | outro serviço usando a porta | `uv run mcp-rag-api serve --port 8010` e ajuste `PUBLIC_URL` e o `claude mcp add` |
| Claude não vê as tools | servidor fora do ar ou header errado | `curl localhost:8000/health`, `claude mcp list`, confira o `Bearer` |
| `401` no `/mcp` | chave ausente, inválida ou revogada: o `/mcp` inteiro exige chave, inclusive para listar as tools | confira o header `Authorization: Bearer kb_sk_...` |

## 14. Deploy em produção

Rode a imagem pública `ghcr.io/concilium-mcp/web-server` (linux/amd64 e arm64) com Docker Compose, Coolify, Dokploy, Railway, Kubernetes ou qualquer host de containers: veja o **[guia de deploy](deploy.pt-BR.md)** ([English](deploy.md)). Ele cobre as variáveis, um Compose com pgvector, o Coolify passo a passo, outras plataformas e um checklist de produção.

Depois do primeiro deploy, crie o primeiro usuário da dashboard (`mcp-rag-api create-user --username voce --role admin`, no terminal do container) e, se quiser uma chave para agentes, `mcp-rag-api create-key --label voce --scopes admin`. Para conectar:

```bash
curl https://kb.seudominio.com/health          # {"status":"ok"}
claude mcp add --transport http kb https://kb.seudominio.com/mcp
```
