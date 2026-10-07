-- Dashboard web: centroide dos documentos (grafo) + usuários/sessões da dash

-- Centroide = média dos embeddings dos chunks (unitário), mantido pelo ingest/update.
-- Alimenta o grafo da dashboard: vizinhança por cosseno via índice HNSW.
ALTER TABLE documents ADD COLUMN centroid vector(1024);

UPDATE documents SET centroid = (
    SELECT avg(c.embedding)
    FROM chunks c
    WHERE c.document_id = documents.id
)
WHERE EXISTS (SELECT 1 FROM chunks c WHERE c.document_id = documents.id);

CREATE INDEX documents_centroid_idx ON documents USING hnsw (centroid vector_cosine_ops);

-- ---------------------------------------------------------------- dashboard: usuários humanos

CREATE TABLE dash_users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,               -- PBKDF2-HMAC-SHA256 (stdlib): pbkdf2$<iter>$<salt>$<hash>
  role          TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin', 'viewer')),
  disabled_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sessão = token opaco; só o hash vai para o banco.
CREATE TABLE dash_sessions (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES dash_users(id) ON DELETE CASCADE,
  token_hash TEXT UNIQUE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ
);
CREATE INDEX dash_sessions_user_idx ON dash_sessions (user_id);
