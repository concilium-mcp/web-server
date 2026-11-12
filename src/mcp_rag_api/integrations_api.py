"""Rotas públicas das integrações (plan-web-37d): o webhook do Trello chama sem a nossa chave.

A autenticidade vem da assinatura (X-Trello-Webhook = HMAC-SHA1 do corpo + URL com o segredo da conta
conectada). HEAD responde 200: é como o Trello confere a URL ao registrar o webhook.
"""

import logging

from fastapi import APIRouter, Request, Response

from .core import trello
from .security import KBError, NotFound

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrações"])
MAX_BODY = 512 * 1024


@router.head("/trello/webhook/{board_id}")
async def trello_webhook_check(board_id: str) -> Response:
    return Response(status_code=200)


@router.post("/trello/webhook/{board_id}")
async def trello_webhook(board_id: str, request: Request) -> Response:
    body = await request.body()
    if len(body) > MAX_BODY:
        return Response(status_code=413)
    try:
        await trello.handle_webhook(board_id, body, request.headers.get("x-trello-webhook"))
    except NotFound:
        return Response(status_code=410)  # o Trello apaga o webhook ao receber 410
    except KBError as e:
        logger.warning("trello webhook recusado (%s): %s", board_id, e)
        return Response(status_code=401)
    return Response(status_code=200)
