-- OAuth 2.1 do MCP (plan-web-17): clientes (registro dinâmico), conexões autorizadas na tela
-- "Autorizar", pedidos pendentes, códigos e tokens. Segredos de usuário (códigos/tokens) só em hash.

-- Cliente registrado via DCR (RFC 7591). metadata = OAuthClientInformationFull completo (inclui o
-- client_secret quando o cliente é confidencial: o SDK compara em texto, e o PKCE segue obrigatório).
CREATE TABLE oauth_clients (
  client_id     TEXT PRIMARY KEY,
  client_name   TEXT,
  redirect_uris TEXT[] NOT NULL,
  metadata      JSONB NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Uma "conexão": o que a pessoa autorizou para um cliente (aparece em Conexões, com Revogar).
CREATE TABLE oauth_grants (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id           TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  user_id             UUID NOT NULL REFERENCES dash_users(id) ON DELETE CASCADE,
  agent_id            UUID REFERENCES agents(id) ON DELETE CASCADE,  -- NULL = age como o usuário
  scopes              TEXT[] NOT NULL,
  allowed_collections TEXT[] NOT NULL DEFAULT '{}',                  -- vazio = todas
  resource            TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at        TIMESTAMPTZ,
  revoked_at          TIMESTAMPTZ
);
CREATE INDEX oauth_grants_user_idx ON oauth_grants (user_id);
CREATE INDEX oauth_grants_client_idx ON oauth_grants (client_id);

-- Pedido do /authorize esperando a decisão na tela de consentimento (vida curta).
CREATE TABLE oauth_auth_requests (
  id                          TEXT PRIMARY KEY,               -- aleatório, vai na URL da tela
  client_id                   TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
  redirect_uri                TEXT NOT NULL,
  redirect_uri_provided       BOOLEAN NOT NULL,
  code_challenge              TEXT NOT NULL,
  state                       TEXT,
  scopes                      TEXT[] NOT NULL DEFAULT '{}',
  resource                    TEXT,
  expires_at                  TIMESTAMPTZ NOT NULL,
  created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE oauth_codes (
  code_hash             TEXT PRIMARY KEY,
  grant_id              UUID NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
  redirect_uri          TEXT NOT NULL,
  redirect_uri_provided BOOLEAN NOT NULL,
  code_challenge        TEXT NOT NULL,
  expires_at            TIMESTAMPTZ NOT NULL,
  used_at               TIMESTAMPTZ
);
CREATE INDEX oauth_codes_grant_idx ON oauth_codes (grant_id);

CREATE TABLE oauth_tokens (
  token_hash TEXT PRIMARY KEY,
  grant_id   UUID NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
  scopes     TEXT[] NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX oauth_tokens_grant_idx ON oauth_tokens (grant_id);
