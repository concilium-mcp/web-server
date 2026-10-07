-- Schema inicial: base de conhecimento + registro de agentes + chaves + auditoria
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------- base de conhecimento

CREATE TABLE collections (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT UNIQUE NOT NULL,
  description TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  collection_id UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  external_id   TEXT,
  title         TEXT NOT NULL,
  source        TEXT,
  content       TEXT NOT NULL,
  content_hash  TEXT NOT NULL,
  metadata      JSONB NOT NULL DEFAULT '{}',
  tags          TEXT[] NOT NULL DEFAULT '{}',
  version       INT NOT NULL DEFAULT 1,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_by    TEXT,
  updated_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (collection_id, external_id)
);
CREATE INDEX documents_metadata_idx ON documents USING gin (metadata);
CREATE INDEX documents_tags_idx ON documents USING gin (tags);
CREATE INDEX documents_collection_idx ON documents (collection_id, status);

CREATE TABLE document_versions (
  id          BIGSERIAL PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version     INT NOT NULL,
  title       TEXT NOT NULL,
  content     TEXT NOT NULL,
  metadata    JSONB,
  tags        TEXT[],
  changed_by  TEXT,
  change_note TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, version)
);

CREATE TABLE chunks (
  id          BIGSERIAL PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  content     TEXT NOT NULL,
  word_count  INT NOT NULL,
  embedding   vector(1024) NOT NULL,
  tsv         tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', content)) STORED
);
CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_document_idx ON chunks (document_id);

-- ---------------------------------------------------------------- agentes

CREATE TABLE agents (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug                TEXT UNIQUE NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name                TEXT NOT NULL,
  description         TEXT,
  system_prompt       TEXT NOT NULL,
  config              JSONB NOT NULL DEFAULT '{}',
  allowed_collections TEXT[] NOT NULL DEFAULT '{}',   -- vazio = todas
  scopes              TEXT[] NOT NULL DEFAULT '{read}',
  version             INT NOT NULL DEFAULT 1,
  status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_by          TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Histórico do perfil (applied) + propostas do próprio agente (proposed/rejected)
CREATE TABLE agent_versions (
  id                  BIGSERIAL PRIMARY KEY,
  agent_id            UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  version             INT,                              -- preenchido quando aplicada
  name                TEXT,
  description         TEXT,
  system_prompt       TEXT,
  config              JSONB,
  allowed_collections TEXT[],
  scopes              TEXT[],
  change_note         TEXT,
  proposed_by         TEXT,
  reviewed_by         TEXT,
  review_note         TEXT,
  status              TEXT NOT NULL DEFAULT 'applied' CHECK (status IN ('proposed', 'applied', 'rejected')),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at         TIMESTAMPTZ
);
CREATE INDEX agent_versions_agent_idx ON agent_versions (agent_id, status);

CREATE TABLE agent_memories (
  id           BIGSERIAL PRIMARY KEY,
  agent_id     UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('fact', 'preference', 'procedure', 'decision', 'lesson')),
  content      TEXT NOT NULL,
  embedding    vector(1024) NOT NULL,
  importance   SMALLINT NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
  shared       BOOLEAN NOT NULL DEFAULT false,
  source       TEXT,
  expires_at   TIMESTAMPTZ,
  last_used_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX agent_memories_embedding_idx ON agent_memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX agent_memories_agent_idx ON agent_memories (agent_id);

CREATE TABLE agent_sessions (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id   UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  started_at TIMESTAMPTZ,
  ended_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  summary    TEXT NOT NULL,
  next_steps TEXT,
  metadata   JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX agent_sessions_agent_idx ON agent_sessions (agent_id, ended_at DESC);

CREATE TABLE agent_tasks (
  id         BIGSERIAL PRIMARY KEY,
  agent_id   UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  title      TEXT NOT NULL,
  details    TEXT,
  status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'done', 'cancelled')),
  due_at     TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX agent_tasks_agent_idx ON agent_tasks (agent_id, status);

-- ---------------------------------------------------------------- acesso e auditoria

CREATE TABLE api_keys (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key_hash   TEXT UNIQUE NOT NULL,
  prefix     TEXT NOT NULL,                   -- primeiros caracteres, para identificar a chave
  label      TEXT,
  agent_id   UUID REFERENCES agents(id) ON DELETE CASCADE,  -- NULL = chave humana/admin
  scopes     TEXT[] NOT NULL DEFAULT '{read}', -- ignorado em chaves de agente (vale agents.scopes)
  revoked_at TIMESTAMPTZ,
  last_used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id         BIGSERIAL PRIMARY KEY,
  actor      TEXT NOT NULL,
  action     TEXT NOT NULL,
  target     TEXT,
  details    JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_created_idx ON audit_log (created_at DESC);
