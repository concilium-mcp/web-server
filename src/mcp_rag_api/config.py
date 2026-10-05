from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Dimensão fixa do vetor no schema (migrations/001_init.sql). Todos os provedores são configurados para ela.
EMBEDDING_DIM = 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://user:password@localhost:5432/kb"
    public_url: str = "http://localhost:8000"
    migrations_dir: Path = PROJECT_ROOT / "migrations"

    embedding_provider: Literal["voyage", "openai", "local", "fake"] = "voyage"
    embedding_model: str = ""
    voyage_api_key: str = ""
    openai_api_key: str = ""

    chunk_words: int = 450
    chunk_overlap_words: int = 60

    duplicate_threshold: float = 0.92
    memory_duplicate_threshold: float = 0.90
    load_agent_memory_limit: int = 15

    kb_api_key: str = ""
    kb_auth_disabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
