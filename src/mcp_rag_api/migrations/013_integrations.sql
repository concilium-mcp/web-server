-- Integrações dos quadros (plan-web-37d): Trello agora, Notion e outros depois pelo mesmo molde.
-- Credenciais sempre cifradas (AES-GCM com INTEGRATIONS_ENCRYPTION_KEY) e nunca devolvidas pela API.
CREATE TABLE IF NOT EXISTS integrations (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider     TEXT NOT NULL CHECK (provider IN ('trello', 'notion')),
  label        TEXT NOT NULL,
  credentials  TEXT NOT NULL,
  account      JSONB,                      -- quem é a conta do lado de lá (id e nome do membro), sem segredo
  created_by   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  disabled_at  TIMESTAMPTZ,
  last_ok_at   TIMESTAMPTZ,
  last_error   TEXT
);

-- Um quadro daqui ligado a um quadro de lá: import (só trouxe) ou mirror (os dois lados se atualizam).
CREATE TABLE IF NOT EXISTS board_links (
  board_id       UUID PRIMARY KEY REFERENCES boards(id) ON DELETE CASCADE,
  integration_id UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
  external_id    TEXT NOT NULL,
  external_url   TEXT,
  mode           TEXT NOT NULL CHECK (mode IN ('import', 'mirror')),
  webhook_id     TEXT,
  last_sync_at   TIMESTAMPTZ,
  last_error     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (integration_id, external_id)
);

-- Mapa de lá para cá: colunas (list), cartões (card), checklist do cartão e itens (item).
CREATE TABLE IF NOT EXISTS external_refs (
  integration_id UUID NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
  kind           TEXT NOT NULL CHECK (kind IN ('list', 'card', 'checklist', 'item')),
  local_id       TEXT NOT NULL,
  external_id    TEXT NOT NULL,
  synced_hash    TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (integration_id, kind, local_id),
  UNIQUE (integration_id, kind, external_id)
);

-- Fila de envio para o lado de lá (espelho): uma linha pendente por alvo; o worker processa em ordem,
-- com tentativas e espera crescente.
CREATE TABLE IF NOT EXISTS sync_queue (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  board_id   UUID NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  target     TEXT NOT NULL CHECK (target IN ('board', 'columns', 'card', 'comment')),
  local_id   TEXT,
  payload    JSONB,
  attempts   INT NOT NULL DEFAULT 0,
  next_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  done_at    TIMESTAMPTZ,
  error      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sync_queue_pending_idx ON sync_queue (next_at) WHERE done_at IS NULL;
