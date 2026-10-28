-- Layout do grafo calculado no servidor (plan-web-26): posições x,y e grupo de cada documento, por nível.
-- Uma linha por nível ('documents'); data = {ids, xy, cluster, labels, full_n, changed}. stamp = assinatura
-- da base quando foi calculado (mudou a base, o layout é atualizado de forma incremental ou recalculado).
CREATE TABLE IF NOT EXISTS graph_layouts (
  level       TEXT PRIMARY KEY,
  stamp       TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  data        JSONB NOT NULL
);
