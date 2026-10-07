"""Testes de integração contra Postgres + pgvector reais.

Rode com: TEST_DATABASE_URL=postgresql://user:password@localhost:5432/kb_test pytest tests/integration
O banco indicado é APAGADO (schema public recriado) a cada execução — por isso o
setup só prossegue se o nome do banco contiver "test" (trava de segurança).
"""

import os
from urllib.parse import urlparse

import asyncpg
import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def _database_name(url: str) -> str:
    """Extrai o nome do banco de um postgres:// URL, sem query params."""
    return (urlparse(url).path or "/").lstrip("/").split("/")[-1]


def pytest_collection_modifyitems(config, items):
    if TEST_DB:
        return
    skip = pytest.mark.skip(reason="defina TEST_DATABASE_URL para rodar os testes de integração")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
async def database():
    if not TEST_DB:
        yield
        return
    db_name = _database_name(TEST_DB)
    if "test" not in db_name.lower():
        raise RuntimeError(
            f"TEST_DATABASE_URL aponta para o banco '{db_name}', que não parece ser de testes "
            "(o nome precisa conter 'test'). Os testes de integração apagam o schema public — "
            "aponte para um banco exclusivo de teste, nunca para o kb de produção."
        )
    os.environ["DATABASE_URL"] = TEST_DB
    os.environ["EMBEDDING_PROVIDER"] = "fake"
    os.environ["KB_AUTH_DISABLED"] = "false"
    from mcp_rag_api import db
    from mcp_rag_api.config import get_settings
    from mcp_rag_api.core.embeddings import get_embedder

    get_settings.cache_clear()
    get_embedder.cache_clear()

    conn = await asyncpg.connect(TEST_DB)
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await conn.close()

    await db.run_migrations()
    await db.init_pool()
    yield
    await db.close_pool()
