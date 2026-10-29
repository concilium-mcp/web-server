import os
import shutil
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Dimensão fixa do vetor no schema (migrations/001_init.sql, embutida no pacote). Todos os
# provedores são configurados para ela.
EMBEDDING_DIM = 1024
if EMBEDDING_DIM <= 0:
    raise ValueError("EMBEDDING_DIM deve ser positiva")


def default_migrations_dir() -> Path:
    """Diretório das migrações embutido no pacote (o wheel inclui src/mcp_rag_api/migrations)."""
    return Path(str(files("mcp_rag_api").joinpath("migrations")))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # sem usuário/senha no default: a credencial vem sempre do .env (DATABASE_URL)
    database_url: str = "postgresql://localhost:5432/kb"
    public_url: str = "http://localhost:8000"
    # None = usar as migrações embutidas no pacote; MIGRATIONS_DIR sobrescreve (deploys antigos).
    migrations_dir: Path | None = None

    embedding_provider: Literal["voyage", "openai", "huggingface", "local", "fake"] = "voyage"
    embedding_model: str = ""
    voyage_api_key: str = ""
    openai_api_key: str = ""
    hf_api_key: str = ""
    # huggingface: URL do endpoint feature-extraction (vazio = derivada do EMBEDDING_MODEL)
    embedding_api_url: str = ""
    # textos por requisição ao provedor (0 = padrão de cada provedor)
    embedding_batch_size: int = 0

    # OCR das páginas de PDF sem texto (plan-web-29): tesseract (local, padrão), openai (API de
    # visão compatível com OpenAI) ou none. OCR_LANGUAGES usa os códigos do tesseract (por+eng).
    ocr_provider: Literal["tesseract", "openai", "none"] = "tesseract"
    ocr_languages: str = "por+eng"
    ocr_max_pages: int = Field(default=200, ge=0)
    # openai: chave própria ou, vazia, a OPENAI_API_KEY; a URL aceita qualquer API compatível
    ocr_api_key: str = ""
    ocr_api_url: str = "https://api.openai.com/v1"
    ocr_model: str = "gpt-4o-mini"

    chunk_words: int = 450
    chunk_overlap_words: int = 60

    duplicate_threshold: float = 0.92
    memory_duplicate_threshold: float = 0.90
    load_agent_memory_limit: int = 15

    kb_api_key: str = ""
    kb_auth_disabled: bool = False

    # Dashboard: falhas de login permitidas por (usuário, IP) antes do backoff exponencial.
    # 0 desliga o rate limit (só para dev/testes controlados).
    dash_login_rate_limit: int = 5
    # IP do cliente no rate limit vem do CF-Connecting-IP. Só é confiável se a origem recebe tráfego
    # apenas do Cloudflare; sem Cloudflare na frente, desligue (qualquer um forjaria o cabeçalho).
    trust_cf_connecting_ip: bool = True
    # Cifra os segredos do 2FA (TOTP) no banco com AES-256-GCM. Opcional: vazio = texto puro.
    # Gere com `openssl rand -base64 32` e guarde fora do banco; perder a chave = resetar o 2FA de todos.
    dash_totp_encryption_key: str = ""

    # OAuth do MCP (plan-web-17): clientes como o Claude conectam pela tela "Autorizar", sem copiar chave.
    # O issuer é o PUBLIC_URL (https em produção; http só em localhost).
    oauth_enabled: bool = True
    oauth_access_ttl_minutes: int = Field(default=60, gt=0)
    oauth_refresh_ttl_days: int = Field(default=30, gt=0)
    # redirect_uri https aceitos no registro dinâmico (plan-web-32): endereço exato, ou caminho fixo
    # terminado em "*" quando o cliente põe um código no fim (ChatGPT: um por conector). Sem subdomínios.
    # Loopback http (Claude Code, Inspector) segue sempre aceito.
    oauth_redirect_uris: str = ",".join(
        [
            "https://claude.ai/api/mcp/auth_callback",
            "https://claude.com/api/mcp/auth_callback",
            "https://chatgpt.com/connector/oauth/*",
            "https://chatgpt.com/connector_platform_oauth_redirect",
            "https://platform.openai.com/oauth/callback",
            "https://vscode.dev/redirect",
            "https://chat.mistral.ai/mcp/callback",
        ]
    )
    # obsoleto (até o plan-web-31): lista de hosts com qualquer subdomínio/caminho. Ignorado; só gera aviso.
    oauth_redirect_allowlist: str = ""
    # esquemas de app nativo aceitos como redirect_uri (ex.: cursor://anysphere.cursor-mcp/oauth/callback)
    oauth_redirect_schemes: str = "cursor,vscode,vscode-insiders"

    kb_log_level: str = "INFO"
    # tempo de vida das sessões da dashboard, em horas (padrão: 7 dias)
    dash_session_ttl_hours: int = Field(default=168, gt=0)

    @field_validator("duplicate_threshold", "memory_duplicate_threshold")
    @classmethod
    def _thresholds_entre_0_e_1(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("threshold de duplicata deve estar em [0, 1]")
        return v

    @model_validator(mode="after")
    def _chunk_sem_conflito(self) -> "Settings":
        # Sem isso, chunk_text explodiria com ValueError no meio de uma requisição (500).
        if self.chunk_overlap_words >= self.chunk_words:
            raise ValueError("CHUNK_OVERLAP_WORDS deve ser menor que CHUNK_WORDS")
        return self


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# senhas de exemplo que já apareceram nos docs/compose do projeto
WEAK_DB_PASSWORDS = {"password", "senha", "change-me", "kb"}


def _csv(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def oauth_redirect_uris(s: Settings) -> list[str]:
    # o caminho diferencia maiúsculas: só tira espaços
    return [item.strip() for item in s.oauth_redirect_uris.split(",") if item.strip()]


def oauth_redirect_schemes(s: Settings) -> set[str]:
    # http/https nunca entram por aqui: seguem as regras de loopback e de host
    return _csv(s.oauth_redirect_schemes) - {"http", "https"}


def oauth_issue() -> str | None:
    """Motivo pelo qual o OAuth não pode subir (None = pode). O SDK exige issuer https,
    exceto em localhost/127.0.0.1."""
    s = get_settings()
    if not s.oauth_enabled:
        return "OAUTH_ENABLED=false"
    url = urlparse(s.public_url)
    if url.scheme == "https" or (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")):
        return None
    return f"PUBLIC_URL={s.public_url} não é https: OAuth do MCP desligado"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def check_config() -> list[str]:
    """Validações de boot: falha rápido se faltar a chave do provedor de embeddings e
    devolve os avisos (PUBLIC_URL padrão, auth desligada) para o lifespan logar.

    Antes desta checagem a chave só era exigida na primeira chamada de embed (erro 500
    em requisição); agora o servidor não sobe com configuração incompleta.
    """
    s = get_settings()
    required_key = {
        "voyage": ("VOYAGE_API_KEY", s.voyage_api_key),
        "openai": ("OPENAI_API_KEY", s.openai_api_key),
        "huggingface": ("HF_API_KEY", s.hf_api_key),
    }
    env_name, key = required_key.get(s.embedding_provider, ("", ""))
    if env_name and not key:
        raise RuntimeError(f"EMBEDDING_PROVIDER={s.embedding_provider} exige {env_name}: defina a variável no .env")
    if s.ocr_provider == "openai" and not (s.ocr_api_key or s.openai_api_key):
        raise RuntimeError("OCR_PROVIDER=openai exige OCR_API_KEY ou OPENAI_API_KEY: defina a variável no .env")
    public = urlparse(s.public_url).hostname not in LOCAL_HOSTS
    # KB_BIND_HOST é gravado pelo `serve` e pelo CMD do Dockerfile: PUBLIC_URL local não garante
    # que o processo só escute em loopback (o padrão é 0.0.0.0, ou seja, a rede inteira)
    bind_host = os.environ.get("KB_BIND_HOST", "").strip()
    if s.kb_auth_disabled and bind_host and bind_host not in LOCAL_HOSTS:
        raise RuntimeError(
            f"KB_AUTH_DISABLED=true só é aceito escutando em loopback; o servidor está em {bind_host}. "
            "Use --host 127.0.0.1 (ou desligue KB_AUTH_DISABLED)."
        )
    if s.kb_auth_disabled and public:
        # auth desligada com URL pública = API e MCP abertos como admin para a internet
        raise RuntimeError(
            f"KB_AUTH_DISABLED=true só é aceito com PUBLIC_URL local; PUBLIC_URL={s.public_url}. "
            "Desligue KB_AUTH_DISABLED em produção."
        )
    if s.dash_totp_encryption_key and len(s.dash_totp_encryption_key) < 32:
        raise RuntimeError(
            "DASH_TOTP_ENCRYPTION_KEY curta demais: use ao menos 32 caracteres (openssl rand -base64 32)"
        )
    warnings: list[str] = []
    if public and urlparse(s.database_url).password in WEAK_DB_PASSWORDS:
        warnings.append("DATABASE_URL usa uma senha de exemplo (ex.: 'password') — troque a senha do banco")
    if s.public_url == "http://localhost:8000":
        warnings.append("PUBLIC_URL é o padrão http://localhost:8000 — defina a URL pública em produção")
    if s.oauth_enabled and (issue := oauth_issue()):
        warnings.append(issue)
    if s.ocr_provider == "tesseract" and not shutil.which("tesseract"):
        warnings.append("OCR_PROVIDER=tesseract, mas o 'tesseract' não está instalado: PDF escaneado não será lido")
    if s.kb_auth_disabled:
        warnings.append("KB_AUTH_DISABLED=true: AUTENTICAÇÃO DESLIGADA — use apenas em desenvolvimento!")
    if s.oauth_redirect_allowlist.strip():
        warnings.append(
            "OAUTH_REDIRECT_ALLOWLIST não é mais usado (aceitava qualquer subdomínio): "
            "liste os endereços exatos em OAUTH_REDIRECT_URIS e apague a variável"
        )
    return warnings
