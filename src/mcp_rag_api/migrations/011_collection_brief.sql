-- Resumo do projeto (plan-web-34): uma nota comum da coleção, marcada como o resumo que os agentes
-- recebem no início da sessão. Arquivar ou apagar a nota só desmarca.
ALTER TABLE collections
  ADD COLUMN IF NOT EXISTS brief_document_id UUID REFERENCES documents(id) ON DELETE SET NULL;
