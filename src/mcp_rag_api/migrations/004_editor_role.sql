-- Papel "editor" na dashboard (plan-web-02): cria/edita/arquiva notas; admin continua com tudo.
ALTER TABLE dash_users DROP CONSTRAINT IF EXISTS dash_users_role_check;
ALTER TABLE dash_users ADD CONSTRAINT dash_users_role_check CHECK (role IN ('admin', 'editor', 'viewer'));
