-- Autenticação em dois fatores da dashboard (TOTP, RFC 6238): Google Authenticator, Authy, 1Password...

-- totp_secret só é preenchido depois que a pessoa confirma um código (2FA ligado);
-- até lá o segredo novo fica em totp_pending_secret. totp_last_step barra a reutilização
-- do mesmo código dentro da janela de 30 s. Recovery codes: só o hash SHA-256 de cada um.
ALTER TABLE dash_users
  ADD COLUMN totp_secret          TEXT,
  ADD COLUMN totp_pending_secret  TEXT,
  ADD COLUMN totp_enabled_at      TIMESTAMPTZ,
  ADD COLUMN totp_last_step       BIGINT,
  ADD COLUMN totp_recovery_hashes TEXT[] NOT NULL DEFAULT '{}';

-- Login em duas etapas: senha certa gera um desafio curto; o cookie de sessão só sai
-- quando o código confere. Só o hash do token vai para o banco.
CREATE TABLE dash_mfa_challenges (
  token_hash TEXT PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES dash_users(id) ON DELETE CASCADE,
  attempts   INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX dash_mfa_challenges_user_idx ON dash_mfa_challenges (user_id);
