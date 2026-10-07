"""Logging do servidor: logger 'mcp_rag_api' com nível via KB_LOG_LEVEL (default INFO).

Regras: nunca logar segredos (chaves de API, senhas) nem conteúdo de documentos.
"""

import logging

from .config import get_settings

LOGGER_NAME = "mcp_rag_api"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_logging() -> None:
    """Configura o logger do pacote (idempotente). Chamado no lifespan e no 'cli serve'."""
    level = getattr(logging, get_settings().kb_log_level.upper(), logging.INFO)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
