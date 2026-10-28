# Deploy do Concilium (web-server)

Cada mudança na `main` publica uma imagem pública, sem login:

```
ghcr.io/concilium-mcp/web-server
```

| | |
|---|---|
| Plataformas | `linux/amd64`, `linux/arm64` |
| Tags | `latest` (`main` atual), `sha-<commit>` (imutável), `X.Y.Z` / `X.Y` (versões) |
| Porta | `8000` (`GET /health` → `{"status":"ok"}`) |
| Banco | PostgreSQL 16 com **pgvector ≥ 0.8**. As tabelas são criadas no start |
| Usuário | roda sem root (uid 1000) |

Em produção, fixe uma tag `sha-<commit>` ou de versão e atualize quando quiser.

## Configuração

A imagem lê variáveis de ambiente. A lista completa, comentada, está no [`.env.example`](../.env.example). As que você precisa:

| Variável | O que é |
|---|---|
| `DATABASE_URL` | `postgresql://usuario:senha@host:5432/banco` (o Compose abaixo já preenche) |
| `EMBEDDING_PROVIDER` | `voyage` (padrão), `openai` ou `huggingface`, com a chave dele: `VOYAGE_API_KEY`, `OPENAI_API_KEY` ou `HF_API_KEY` |
| `PUBLIC_URL` | o endereço público `https://`. O OAuth (conectores do Claude) e os comandos de conexão usam ele |
| `DASH_TOTP_ENCRYPTION_KEY` | opcional: criptografa os segredos do 2FA no banco |
| `OCR_PROVIDER` | opcional: OCR das páginas escaneadas de PDF. `tesseract` (padrão, já vem na imagem, português e inglês), `openai` (modelo de visão em qualquer API compatível com OpenAI, veja o `.env.example`) ou `none`. Rodando fora da imagem? Instale o `tesseract-ocr` |

Escolheu o provedor de embeddings? Fique nele. Trocar de provedor ou modelo depois exige `mcp-rag-api reindex`, porque vetores de modelos diferentes não se comparam.

## 1. Docker Compose (qualquer servidor)

O [`deploy/docker-compose.yml`](../deploy/docker-compose.yml) sobe o app e um banco pgvector com volume persistente.

```bash
mkdir concilium && cd concilium
curl -fsSLO https://raw.githubusercontent.com/concilium-mcp/web-server/main/deploy/docker-compose.yml

# .env ao lado do arquivo do compose
cat > .env <<'EOF'
POSTGRES_PASSWORD=change-me-to-something-long
EMBEDDING_PROVIDER=voyage
VOYAGE_API_KEY=your-key
PUBLIC_URL=https://concilium.example.com
EOF

docker compose up -d
```

Crie o primeiro usuário da dashboard e abra `http://localhost:8000/dashboard`:

```bash
docker compose exec api mcp-rag-api create-user --username you --role admin
```

## 2. Coolify

**Opção A: Docker Compose (app + banco juntos).** *+ New → Docker Compose Empty*, cole o [`deploy/docker-compose.yml`](../deploy/docker-compose.yml), preencha as variáveis em *Environment Variables* e configure o domínio no serviço `api` (porta `8000`). O bloco `ports:` pode sair: o proxy do Coolify roteia o domínio.

**Opção B: Docker Image + banco do Coolify.**

1. *+ New → Database → PostgreSQL* e troque a imagem para `pgvector/pgvector:pg16`. Copie a URL interna.
2. *+ New → Docker Image* com `ghcr.io/concilium-mcp/web-server:latest`. *Ports Exposes* `8000`, configure o domínio.
3. Em *Environment Variables*: `DATABASE_URL` (a URL interna do passo 1) e as variáveis da tabela acima.
4. Deixe o healthcheck do Coolify desligado: a imagem não tem curl/wget. O `HEALTHCHECK` do próprio Docker (embutido na imagem) continua valendo.
5. Deploy. Para atualizar, *Redeploy* (puxa a tag de novo), ou troque a tag por um `sha-<commit>` mais novo.

Primeiro usuário da dashboard: abra o *Terminal* do app no Coolify e rode `mcp-rag-api create-user --username voce --role admin`.

## 3. Outras plataformas

- **Dokploy, EasyPanel, Portainer (stacks), CapRover**: use o Compose acima.
- **Railway, Render, Fly.io, Kubernetes, qualquer host de containers**: rode a imagem, exponha a porta `8000` e aponte o `DATABASE_URL` para um PostgreSQL com a extensão `vector` (imagem pgvector, Supabase, Neon, Postgres do Render, template pgvector do Railway...). Caminho do health check: `/health`.
- **Proxy reverso**: o app já confia nos headers `X-Forwarded-*` (`--proxy-headers`), então HTTPS e IP do cliente chegam certos pelo Traefik, Caddy ou nginx.

## 4. Buildar a imagem você mesmo

A imagem pública sai do [`Dockerfile.coolify`](../Dockerfile.coolify). Para buildar local, opcionalmente com embeddings locais (`bge-m3`, imagem bem maior):

```bash
docker build -f Dockerfile.coolify -t concilium-web-server:local .
docker build -f Dockerfile.coolify --build-arg INSTALL_LOCAL_EMBEDDINGS=true -t concilium-web-server:local-embeddings .
```

## 5. Checklist de produção

- Mantenha **1 réplica**: o app não guarda estado, mas duas réplicas subindo juntas disputam as migrações do banco.
- Faça backup do banco (no Coolify: *Database → Backups*; fora dele, um `pg_dump` agendado).
- Memórias expiradas: agende `mcp-rag-api cleanup` uma vez por dia (Coolify: *Scheduled Tasks*, `0 3 * * *`; ou `docker compose exec api mcp-rag-api cleanup` no cron).
- Atrás do Cloudflare Access? Os clientes MCP não passam pela tela de login: crie uma regra *Bypass* para `/mcp`, `/oauth`, `/.well-known` e `/health`. As chaves do próprio app continuam protegendo tudo.
- Embeddings locais (`INSTALL_LOCAL_EMBEDDINGS=true`) precisam de 2–3 GB de RAM. Em VPS pequena, prefira `voyage`, `openai` ou `huggingface`.
