# Deploying Concilium (web-server)

Every change to `main` publishes a public image, no login needed:

```
ghcr.io/concilium-mcp/web-server
```

| | |
|---|---|
| Platforms | `linux/amd64`, `linux/arm64` |
| Tags | `latest` (current `main`), `sha-<commit>` (immutable), `X.Y.Z` / `X.Y` (releases) |
| Port | `8000` (`GET /health` → `{"status":"ok"}`) |
| Database | PostgreSQL 16 with **pgvector ≥ 0.8**. Tables are created on startup |
| User | runs as non-root (uid 1000) |

In production, pin a `sha-<commit>` or version tag and update on purpose.

## Configuration

The image reads environment variables. The full list, with comments, is in [`.env.example`](../.env.example). The ones you need:

| Variable | What it is |
|---|---|
| `DATABASE_URL` | `postgresql://user:password@host:5432/db` (the Compose file below sets it for you) |
| `EMBEDDING_PROVIDER` | `voyage` (default), `openai` or `huggingface`, plus its key: `VOYAGE_API_KEY`, `OPENAI_API_KEY` or `HF_API_KEY` |
| `PUBLIC_URL` | the public `https://` address. OAuth (Claude connectors) and the connection commands use it |
| `DASH_TOTP_ENCRYPTION_KEY` | optional: encrypts the 2FA secrets in the database |

Picked an embeddings provider? Stay with it. Changing provider or model later requires `mcp-rag-api reindex`, because vectors from different models can't be compared.

## 1. Docker Compose (any server)

[`deploy/docker-compose.yml`](../deploy/docker-compose.yml) runs the app and a pgvector database with a persistent volume.

```bash
mkdir concilium && cd concilium
curl -fsSLO https://raw.githubusercontent.com/concilium-mcp/web-server/main/deploy/docker-compose.yml

# .env next to the compose file
cat > .env <<'EOF'
POSTGRES_PASSWORD=change-me-to-something-long
EMBEDDING_PROVIDER=voyage
VOYAGE_API_KEY=your-key
PUBLIC_URL=https://concilium.example.com
EOF

docker compose up -d
```

Create the first dashboard user, then open `http://localhost:8000/dashboard`:

```bash
docker compose exec api mcp-rag-api create-user --username you --role admin
```

## 2. Coolify

**Option A: Docker Compose (app + database together).** *+ New → Docker Compose Empty*, paste [`deploy/docker-compose.yml`](../deploy/docker-compose.yml), fill in the variables under *Environment Variables* and set the domain on the `api` service (port `8000`). You can delete the `ports:` block: Coolify's proxy routes the domain.

**Option B: Docker Image + Coolify database.**

1. *+ New → Database → PostgreSQL* and change the image to `pgvector/pgvector:pg16`. Copy its internal URL.
2. *+ New → Docker Image* with `ghcr.io/concilium-mcp/web-server:latest`. *Ports Exposes* `8000`, set your domain.
3. Under *Environment Variables*: `DATABASE_URL` (the internal URL from step 1) and the variables from the table above.
4. Leave Coolify's healthcheck off: the image has no curl/wget. Docker's own `HEALTHCHECK` (built into the image) still runs.
5. Deploy. To update, click *Redeploy* (it pulls the tag again), or change the tag to a newer `sha-<commit>`.

First dashboard user: open the app's *Terminal* in Coolify and run `mcp-rag-api create-user --username you --role admin`.

## 3. Other platforms

- **Dokploy, EasyPanel, Portainer (stacks), CapRover**: use the Compose file above.
- **Railway, Render, Fly.io, Kubernetes, any container host**: run the image, expose port `8000`, point `DATABASE_URL` to a PostgreSQL with the `vector` extension (pgvector image, Supabase, Neon, Render Postgres, Railway's pgvector template...). Health check path: `/health`.
- **Reverse proxy**: the app already trusts `X-Forwarded-*` headers (`--proxy-headers`), so HTTPS and the client IP come through from Traefik, Caddy or nginx.

## 4. Building the image yourself

The public image comes from [`Dockerfile.coolify`](../Dockerfile.coolify). To build it locally, optionally with local embeddings (`bge-m3`, much bigger image):

```bash
docker build -f Dockerfile.coolify -t concilium-web-server:local .
docker build -f Dockerfile.coolify --build-arg INSTALL_LOCAL_EMBEDDINGS=true -t concilium-web-server:local-embeddings .
```

## 5. Production checklist

- Keep **1 replica**: the app is stateless, but two replicas starting together would race the database migrations.
- Back up the database (in Coolify: *Database → Backups*; elsewhere, a scheduled `pg_dump`).
- Expired memories: schedule `mcp-rag-api cleanup` daily (Coolify: *Scheduled Tasks*, `0 3 * * *`; or `docker compose exec api mcp-rag-api cleanup` from cron).
- Behind Cloudflare Access? MCP clients can't pass the login screen: add a *Bypass* rule for `/mcp`, `/oauth`, `/.well-known` and `/health`. The app's own keys still protect everything.
- Local embeddings (`INSTALL_LOCAL_EMBEDDINGS=true`) need 2–3 GB of RAM. On a small VPS, prefer `voyage`, `openai` or `huggingface`.
