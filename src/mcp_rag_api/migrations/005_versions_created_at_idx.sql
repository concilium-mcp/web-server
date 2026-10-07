-- Regra de migrações: TODA migração nova usa IF NOT EXISTS / IF EXISTS onde aplicável.
-- Migrações JÁ APLICADAS não se editam (a tabela schema_migrations registra por nome de arquivo);
-- para corrigir algo de uma migração antiga, criar um arquivo novo. O advisory lock em run_migrations
-- (db.py) cobre a corrida de startups simultâneas.

-- Índice em document_versions.created_at: o /dash/api/insights agrega por dia nesta tabela,
-- que cresce um snapshot completo por edição (sem o índice, seq scan por subconsulta/dia).
CREATE INDEX IF NOT EXISTS idx_document_versions_created_at ON document_versions (created_at);
