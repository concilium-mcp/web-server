-- Pastas aninhadas dentro de uma coleção (plan-web-14): a coleção é a pasta principal (o "projeto");
-- dentro dela, pastas sem limite de profundidade (parent_id NULL = filha direta da coleção).
-- Escopo de acesso, MCP e busca continuam por coleção — pasta é organização da dash.
CREATE TABLE IF NOT EXISTS folders (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  collection_id UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  parent_id     UUID,
  name          TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
  created_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id, collection_id),
  CHECK (parent_id IS NULL OR parent_id <> id),
  -- FK composta: a pasta-mãe é da MESMA coleção. ON UPDATE CASCADE leva a subárvore inteira junto
  -- quando a pasta de cima muda de coleção.
  FOREIGN KEY (parent_id, collection_id) REFERENCES folders (id, collection_id)
    ON UPDATE CASCADE ON DELETE SET NULL (parent_id)
);
CREATE INDEX IF NOT EXISTS folders_collection_idx ON folders (collection_id, parent_id);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS folder_id UUID;
CREATE INDEX IF NOT EXISTS documents_folder_idx ON documents (folder_id) WHERE folder_id IS NOT NULL;
-- Mesmo truque: a nota só pode estar em pasta da própria coleção; pasta que muda de coleção leva as notas.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_folder_fk;
ALTER TABLE documents ADD CONSTRAINT documents_folder_fk FOREIGN KEY (folder_id, collection_id)
  REFERENCES folders (id, collection_id) ON UPDATE CASCADE ON DELETE SET NULL (folder_id);

-- Quem move a nota de coleção sem saber de pastas (move_document do core, MCP, REST) não quebra a FK:
-- se a pasta atual não é da coleção nova, a nota cai na raiz da coleção de destino.
CREATE OR REPLACE FUNCTION documents_reset_foreign_folder() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.folder_id IS NOT NULL AND NEW.collection_id IS DISTINCT FROM OLD.collection_id
     AND NOT EXISTS (SELECT 1 FROM folders f WHERE f.id = NEW.folder_id AND f.collection_id = NEW.collection_id) THEN
    NEW.folder_id := NULL;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS documents_reset_foreign_folder ON documents;
CREATE TRIGGER documents_reset_foreign_folder BEFORE UPDATE OF collection_id ON documents
  FOR EACH ROW EXECUTE FUNCTION documents_reset_foreign_folder();
