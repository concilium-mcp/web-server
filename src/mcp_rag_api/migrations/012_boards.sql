-- Quadros Kanban nas pastas (plan-web-37a). O quadro é o projeto e mora numa pasta (folder_id NULL =
-- raiz da coleção); uma pasta tem quantos quadros quiser, com nome único entre os ativos do mesmo lugar.
-- Acesso herdado da coleção. As tabelas de integrações (Trello, Notion) entram na fase D.
CREATE TABLE IF NOT EXISTS boards (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  collection_id UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE ON UPDATE CASCADE,
  folder_id     UUID,
  name          TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  description   TEXT,
  owner         TEXT,
  created_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  archived_at   TIMESTAMPTZ,
  -- mesmo truque das notas (migração 006): pasta da própria coleção; a pasta que muda de coleção
  -- leva os quadros junto. Excluir a pasta sobe os quadros um nível (feito no core, antes do DELETE).
  FOREIGN KEY (folder_id, collection_id) REFERENCES folders (id, collection_id)
    ON UPDATE CASCADE ON DELETE SET NULL (folder_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS boards_name_uq ON boards
  (collection_id, COALESCE(folder_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))
  WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS boards_folder_idx ON boards (collection_id, folder_id);

-- Colunas livres: nome digitado por quem usa; papel opcional (o que as tools usam para mover).
CREATE TABLE IF NOT EXISTS board_columns (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  board_id   UUID NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  name       TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),
  role       TEXT CHECK (role IN ('backlog', 'ready', 'doing', 'review', 'done')),
  position   INT  NOT NULL DEFAULT 0,
  wip_limit  INT  CHECK (wip_limit IS NULL OR wip_limit > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS board_columns_board_idx ON board_columns (board_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS board_columns_role_uq ON board_columns (board_id, role) WHERE role IS NOT NULL;

CREATE TABLE IF NOT EXISTS cards (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  board_id          UUID NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
  column_id         UUID NOT NULL REFERENCES board_columns(id),
  title             TEXT NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 300),
  description       TEXT,
  position          DOUBLE PRECISION NOT NULL DEFAULT 0,
  due_date          DATE,
  assignee_user_id  UUID REFERENCES dash_users(id) ON DELETE SET NULL,
  assignee_agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
  labels            TEXT[] NOT NULL DEFAULT '{}',
  -- campos de trabalho, informados pelo agente (sem integração com o GitLab)
  repo              TEXT,
  worktree          TEXT,
  branch            TEXT,
  mr_url            TEXT,
  ci_status         TEXT,
  blocked_reason    TEXT,
  -- posse: vale enquanto lease_until > now(); vencida, o cartão fica "parado" e outra sessão retoma
  claimed_by        TEXT,
  claim_session     TEXT,
  claimed_at        TIMESTAMPTZ,
  lease_until       TIMESTAMPTZ,
  stale_logged_at   TIMESTAMPTZ,
  done_at           TIMESTAMPTZ,
  archived_at       TIMESTAMPTZ,
  created_by        TEXT,
  updated_by        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  tsv               TSVECTOR GENERATED ALWAYS AS (
    to_tsvector('portuguese', coalesce(title, '') || ' ' || coalesce(description, ''))
  ) STORED
);
CREATE INDEX IF NOT EXISTS cards_board_idx ON cards (board_id, column_id, position) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS cards_claim_idx ON cards (claimed_by) WHERE claimed_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS cards_tsv_idx ON cards USING gin (tsv);

CREATE TABLE IF NOT EXISTS card_items (
  id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  card_id  UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  position INT  NOT NULL DEFAULT 0,
  text     TEXT NOT NULL CHECK (length(btrim(text)) BETWEEN 1 AND 500),
  done     BOOLEAN NOT NULL DEFAULT false,
  done_at  TIMESTAMPTZ,
  done_by  TEXT
);
CREATE INDEX IF NOT EXISTS card_items_card_idx ON card_items (card_id, position);

CREATE TABLE IF NOT EXISTS card_documents (
  card_id     UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  created_by  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (card_id, document_id)
);
CREATE INDEX IF NOT EXISTS card_documents_doc_idx ON card_documents (document_id);

-- Log de auditoria do quadro e dos cartões: só de acréscimo. Sem FK de propósito: o histórico
-- sobrevive ao quadro e à coleção. UPDATE, DELETE e TRUNCATE são recusados pelo próprio banco.
CREATE TABLE IF NOT EXISTS board_events (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  board_id   UUID NOT NULL,
  card_id    UUID,
  actor      TEXT NOT NULL,
  actor_kind TEXT NOT NULL,
  client     TEXT,
  session_id TEXT,
  kind       TEXT NOT NULL,
  changes    JSONB,
  note       TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS board_events_board_idx ON board_events (board_id, created_at DESC);
CREATE INDEX IF NOT EXISTS board_events_card_idx ON board_events (card_id, created_at DESC) WHERE card_id IS NOT NULL;

CREATE OR REPLACE FUNCTION board_events_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'board_events é só de acréscimo: % não é permitido', TG_OP;
END $$;

DROP TRIGGER IF EXISTS board_events_no_change ON board_events;
CREATE TRIGGER board_events_no_change BEFORE UPDATE OR DELETE ON board_events
  FOR EACH ROW EXECUTE FUNCTION board_events_append_only();
DROP TRIGGER IF EXISTS board_events_no_truncate ON board_events;
CREATE TRIGGER board_events_no_truncate BEFORE TRUNCATE ON board_events
  FOR EACH STATEMENT EXECUTE FUNCTION board_events_append_only();
