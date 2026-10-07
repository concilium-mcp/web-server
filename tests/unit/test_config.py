import pytest
from pydantic import ValidationError

from mcp_rag_api.config import EMBEDDING_DIM, Settings, check_config, get_settings


def test_embedding_dim_positiva():
    assert EMBEDDING_DIM > 0


def test_defaults_sao_validos():
    s = Settings()
    assert s.chunk_overlap_words < s.chunk_words
    assert 0.0 <= s.duplicate_threshold <= 1.0
    assert 0.0 <= s.memory_duplicate_threshold <= 1.0
    assert s.kb_log_level == "INFO"
    assert s.dash_session_ttl_hours == 168


@pytest.mark.parametrize("overlap", [450, 500])
def test_overlap_maior_ou_igual_ao_chunk_rejeitado(overlap):
    with pytest.raises(ValidationError):
        Settings(chunk_words=450, chunk_overlap_words=overlap)


@pytest.mark.parametrize("campo", ["duplicate_threshold", "memory_duplicate_threshold"])
@pytest.mark.parametrize("valor", [-0.1, 1.01])
def test_threshold_fora_de_0_a_1_rejeitado(campo, valor):
    with pytest.raises(ValidationError):
        Settings(**{campo: valor})


@pytest.mark.parametrize("valor", [0, -1])
def test_ttl_de_sessao_deve_ser_positivo(valor):
    with pytest.raises(ValidationError):
        Settings(dash_session_ttl_hours=valor)


@pytest.fixture
def settings_env(monkeypatch):
    """Troca o settings lendo variáveis de ambiente, limpando o lru_cache ao final."""
    get_settings.cache_clear()
    try:
        yield monkeypatch
    finally:
        get_settings.cache_clear()


def test_check_config_falha_rapido_sem_chave_do_provider(monkeypatch, settings_env):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY"):
        check_config()


def test_check_config_ok_sem_chave_quando_provider_fake(monkeypatch, settings_env):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    warnings = check_config()
    assert not any("exige" in w for w in warnings)


def test_check_config_avisa_public_url_padrao_e_auth_desligada(monkeypatch, settings_env):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("KB_AUTH_DISABLED", "true")
    warnings = check_config()
    assert any("PUBLIC_URL" in w for w in warnings)
    assert any("KB_AUTH_DISABLED" in w for w in warnings)
