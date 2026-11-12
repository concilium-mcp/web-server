-- Gasto real de tokens das sessões (plan-web-38): o plugin do Claude Code lê o transcript da sessão e manda
-- o total acumulado (uma linha por sessão, sobrescrita a cada envio). Só contadores: nada de prompt,
-- código ou conteúdo. concilium_* = o que veio das tools do Concilium (chamadas e tokens estimados).
CREATE TABLE IF NOT EXISTS session_usage (
  session_id              TEXT PRIMARY KEY CHECK (length(session_id) BETWEEN 8 AND 120),
  collection_id           UUID REFERENCES collections(id) ON DELETE SET NULL ON UPDATE CASCADE,
  agent_id                UUID REFERENCES agents(id) ON DELETE SET NULL,
  reporter                TEXT NOT NULL,
  client                  TEXT,
  model                   TEXT,
  repo                    TEXT,
  started_at              TIMESTAMPTZ,
  ended_at                TIMESTAMPTZ,
  requests                INT    NOT NULL DEFAULT 0,
  input_tokens            BIGINT NOT NULL DEFAULT 0,
  output_tokens           BIGINT NOT NULL DEFAULT 0,
  cache_write_tokens      BIGINT NOT NULL DEFAULT 0,
  cache_read_tokens       BIGINT NOT NULL DEFAULT 0,
  concilium_calls         INT    NOT NULL DEFAULT 0,
  concilium_result_tokens BIGINT NOT NULL DEFAULT 0,
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS session_usage_ended_idx ON session_usage (ended_at);
CREATE INDEX IF NOT EXISTS session_usage_collection_idx ON session_usage (collection_id, ended_at);
