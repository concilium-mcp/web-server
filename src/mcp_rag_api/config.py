from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Dimensão fixa do vetor no schema (migrations/001_init.sql, embutida no pacote). Todos os
# provedores são configurados para ela.
EMBEDDING_DIM = 1024


def default_migrations_dir() -> Path:
    """Diretório das migrações embutido no pacote (o wheel inclui src/mcp_rag_api/migrations)."""
    return Path(str(files("mcp_rag_api").joinpath("migrations")))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://user:password@localhost:5432/kb"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
