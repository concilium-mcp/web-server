-- Links explícitos entre documentos (plan-web-03): [[wikilinks]] derivados do conteúdo + links manuais
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Chave de comparação de títulos: minúsculas, sem acento e com espaços colapsados
-- ("Autenticação" = "autenticacao"). unaccent() puro não é IMMUTABLE; com o dicionário explícito
-- o wrapper pode ser, o que permite indexar.
CREATE OR REPLACE FUNCTION kb_title_key(t text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
    AS $$ SELECT regexp_replace(lower(public.unaccent('public.unaccent'::regdictionary, btrim(t))), '\s+', ' ', 'g') $$;

CREATE INDEX documents_title_key_idx ON documents (kb_title_key(title));

-- Dirigido (source -> target): backlink = links que chegam. No grafo vira uma aresta só.
CREATE TABLE document_links (
  id           BIGSERIAL PRIMARY KEY,
  source_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  target_id    UUID REFERENCES documents(id) ON DELETE CASCADE,  -- NULL = [[...]] sem destino (pendente)
  target_title TEXT,                                             -- texto do [[...]] (resolução tardia)
  kind         TEXT NOT NULL CHECK (kind IN ('wikilink', 'manual')),
  note         TEXT,                                             -- por que estão ligados (manual)
  created_by   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (source_id <> target_id),
  CHECK (target_id IS NOT NULL OR target_title IS NOT NULL)
);
CREATE UNIQUE INDEX document_links_uniq ON document_links (source_id, target_id, kind) WHERE target_id IS NOT NULL;
CREATE UNIQUE INDEX document_links_pending_uniq ON document_links (source_id, kb_title_key(target_title))
    WHERE target_id IS NULL;
CREATE INDEX document_links_target_idx ON document_links (target_id);
CREATE INDEX document_links_pending_idx ON document_links (kb_title_key(target_title)) WHERE target_id IS NULL;
