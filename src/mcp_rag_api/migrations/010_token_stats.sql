-- Medidor de economia de tokens (plan-web-35): agregado por dia, projeto (coleção), agente e tool.
-- Não guarda consulta nem conteúdo, só contadores. served = tokens estimados entregues ao agente;
-- baseline = tokens estimados dos documentos inteiros de onde a resposta saiu (o que ele leria
-- sem o Concilium). NULLS NOT DISTINCT (PostgreSQL 15+): coleção/agente nulos agregam na mesma linha.
CREATE TABLE IF NOT EXISTS token_stats_daily (
  day           DATE   NOT NULL,
  collection_id UUID   REFERENCES collections(id) ON DELETE CASCADE ON UPDATE CASCADE,
  agent_id      UUID   REFERENCES agents(id) ON DELETE CASCADE,
  tool          TEXT   NOT NULL,
  calls         BIGINT NOT NULL DEFAULT 0,
  served        BIGINT NOT NULL DEFAULT 0,
  baseline      BIGINT NOT NULL DEFAULT 0,
  CONSTRAINT token_stats_daily_key UNIQUE NULLS NOT DISTINCT (day, collection_id, agent_id, tool)
);

CREATE INDEX IF NOT EXISTS token_stats_daily_day_idx ON token_stats_daily (day);
