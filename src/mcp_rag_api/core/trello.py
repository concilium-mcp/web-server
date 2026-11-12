"""Trello (plan-web-37d): conectar a conta, importar um quadro e espelhar nos dois sentidos.

- Importar: listas viram colunas (papel deduzido do nome), cartões vêm com descrição, prazo, etiquetas,
  membros (como texto), checklists e comentários (no log). Reimportar atualiza pelo mapa `external_refs`.
- Espelhar daqui para lá: toda escrita num quadro espelhado entra na `sync_queue` (core/boards.enqueue_mirror);
  o worker relê o estado atual e manda para o Trello (respeitando o limite de ~100 requisições/10 s).
- De lá para cá: webhook do Trello (assinatura HMAC-SHA1 do corpo + URL com o segredo da conta). Ações feitas
  pela própria conta conectada são ignoradas (é o eco do nosso envio). Conflito: vale a mais recente.
Tudo que a integração faz entra no log com o ator `integration:trello`.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..config import get_settings
from ..db import pool
from ..security import KBError, NotFound, Principal
from . import boards, integrations
from .documents import parse_uuid

logger = logging.getLogger(__name__)

API = "https://api.trello.com/1"
ACTOR = Principal(actor="integration:trello", scopes=frozenset({"write"}))
ORIGIN = boards.Origin.of("integration:trello", None)
MAX_ATTEMPTS = 8
# testes trocam o transporte por um Trello falso (httpx.MockTransport)
_transport: httpx.AsyncBaseTransport | None = None


class TrelloError(KBError):
    pass


class Trello:
    def __init__(self, key: str, token: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=get_settings().trello_api_url,
            params={"key": key, "token": token},
            timeout=20,
            transport=_transport,
        )

    async def __aenter__(self) -> "Trello":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def req(self, method: str, path: str, **params: Any) -> Any:
        for tentativa in range(4):
            r = await self._http.request(method, path, params={k: v for k, v in params.items() if v is not None})
            if r.status_code == 429 and tentativa < 3:
                await asyncio.sleep(float(r.headers.get("retry-after", 2)))
                continue
            if r.status_code >= 400:
                detalhe = r.text[:200].strip()
                if r.status_code in (401, 403):
                    raise TrelloError(f"O Trello recusou a credencial ({r.status_code}): confira a chave e o token.")
                raise TrelloError(f"Trello respondeu {r.status_code} em {method} {path}: {detalhe}")
            if _transport is None:
                await asyncio.sleep(0.12)  # ~80 req/10 s, abaixo do limite de 100 por token
            return r.json() if r.content else None
        raise TrelloError("Trello: limite de requisições, tente de novo em instantes.")


def _client(creds: dict) -> Trello:
    return Trello(creds["key"], creds["token"])


# ---------------------------------------------------------------- conexão


async def connect(p: Principal, key: str, token: str, secret: str) -> dict:
    """Confere a credencial no Trello (GET /members/me) e grava cifrada."""
    p.require("admin")
    key, token, secret = key.strip(), token.strip(), secret.strip()
    if not (key and token and secret):
        raise KBError("Informe a API key, o token e o segredo (Secret) do Trello.")
    async with Trello(key, token) as tr:
        me = await tr.req("GET", "/members/me", fields="id,fullName,username")
    account = {"id": me["id"], "name": me.get("fullName") or me.get("username"), "username": me.get("username")}
    return await integrations.save_integration(
        p, "trello", f"Trello · {account['name']}", {"key": key, "token": token, "secret": secret}, account
    )


async def remote_boards(p: Principal, integration_id: str) -> list[dict]:
    p.require("read")
    creds, _ = await integrations.credentials_of(parse_uuid(integration_id, "integration_id"))
    async with _client(creds) as tr:
        rows = await tr.req("GET", "/members/me/boards", filter="open", fields="id,name,url,desc")
    linked = {
        r["external_id"]: str(r["board_id"])
        for r in await pool().fetch(
            "SELECT external_id, board_id FROM board_links WHERE integration_id = $1",
            parse_uuid(integration_id, "integration_id"),
        )
    }
    return [{"id": b["id"], "name": b["name"], "url": b.get("url"), "linked_board": linked.get(b["id"])} for b in rows]


# ---------------------------------------------------------------- mapa de lá para cá


async def _ref(iid: uuid.UUID, kind: str, local_id: str) -> str | None:
    return await pool().fetchval(
        "SELECT external_id FROM external_refs WHERE integration_id = $1 AND kind = $2 AND local_id = $3",
        iid,
        kind,
        local_id,
    )


async def _local(iid: uuid.UUID, kind: str, external_id: str) -> str | None:
    return await pool().fetchval(
        "SELECT local_id FROM external_refs WHERE integration_id = $1 AND kind = $2 AND external_id = $3",
        iid,
        kind,
        external_id,
    )


async def _set_ref(iid: uuid.UUID, kind: str, local_id: str, external_id: str, synced_hash: str | None = None) -> None:
    await pool().execute(
        """
        INSERT INTO external_refs (integration_id, kind, local_id, external_id, synced_hash) VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (integration_id, kind, local_id)
        DO UPDATE SET external_id = EXCLUDED.external_id, synced_hash = EXCLUDED.synced_hash, updated_at = now()
        """,
        iid,
        kind,
        local_id,
        external_id,
        synced_hash,
    )


async def _hash_of(iid: uuid.UUID, kind: str, local_id: str) -> str | None:
    return await pool().fetchval(
        "SELECT synced_hash FROM external_refs WHERE integration_id = $1 AND kind = $2 AND local_id = $3",
        iid,
        kind,
        local_id,
    )


def _ik(card_id: str, item_id: Any) -> str:
    """Chave local do item no mapa: "<cartão>:<item>" (a limpeza de itens apagados fica no cartão certo)."""
    return f"{card_id}:{item_id}"


def _item_id(key: str) -> int:
    return int(key.rsplit(":", 1)[-1])


def _digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _due(value: str | None) -> str | None:
    return value[:10] if value else None


# ---------------------------------------------------------------- importar


async def import_board(
    p: Principal,
    integration_id: str,
    trello_board_id: str,
    collection: str,
    folder_path: str | None = None,
    mirror: bool = False,
) -> dict:
    """Traz o quadro do Trello para a pasta (ou atualiza o já trazido) e, com mirror=true, passa a espelhar."""
    p.require("write")
    iid = parse_uuid(integration_id, "integration_id")
    creds, info = await integrations.credentials_of(iid)
    async with _client(creds) as tr:
        tb = await tr.req("GET", f"/boards/{trello_board_id}", fields="id,name,desc,url")
        lists = await tr.req("GET", f"/boards/{trello_board_id}/lists", filter="open", fields="id,name,pos")
        cards = await tr.req(
            "GET",
            f"/boards/{trello_board_id}/cards",
            filter="open",
            fields="id,name,desc,due,idList,pos,labels,idMembers",
            checklists="all",
            members="true",
            member_fields="fullName,username",
        )
        comments = await tr.req(
            "GET",
            f"/boards/{trello_board_id}/actions",
            filter="commentCard",
            limit=1000,
            memberCreator_fields="fullName",
        )
    existing = await pool().fetchval(
        "SELECT board_id FROM board_links WHERE integration_id = $1 AND external_id = $2", iid, tb["id"]
    )
    lists = sorted(lists, key=lambda x: x.get("pos", 0))
    if existing:
        board_id = str(existing)
    else:
        name = tb["name"]
        for n in range(1, 50):  # nome repetido na pasta ganha " (Trello)", " (Trello 2)"…
            try:
                created = await boards.create_board(
                    p,
                    collection,
                    name,
                    folder_path,
                    tb.get("desc") or None,
                    [x["name"] for x in lists] or None,
                    origin=ORIGIN,
                )
                break
            except KBError as e:
                if "Já existe" not in str(e):
                    raise
                name = f"{tb['name']} (Trello{'' if n == 1 else f' {n}'})"
        board_id = created["id"]
        for col, tl in zip(created["columns"], lists, strict=False):
            await _set_ref(iid, "list", col["id"], tl["id"])
        await pool().execute(
            "INSERT INTO board_links (board_id, integration_id, external_id, external_url, mode) "
            "VALUES ($1, $2, $3, $4, 'import')",
            uuid.UUID(board_id),
            iid,
            tb["id"],
            tb.get("url"),
        )
    # colunas novas de lá
    for tl in lists:
        if not await _local(iid, "list", tl["id"]):
            col = await boards.create_column(
                ACTOR,
                board_id,
                tl["name"],
                boards.guess_role(tl["name"]) if not await _role_taken(board_id, tl["name"]) else None,
                origin=ORIGIN,
            )
            await _set_ref(iid, "list", col["id"], tl["id"])
    by_card: dict[str, list[dict]] = {}
    for a in comments or []:
        cid = (a.get("data") or {}).get("card", {}).get("id")
        if cid:
            by_card.setdefault(cid, []).append(a)
    stats = {"created": 0, "updated": 0}
    for tc in sorted(cards, key=lambda x: (x.get("idList"), x.get("pos", 0))):
        await _upsert_card_from_trello(iid, board_id, tc, by_card.get(tc["id"], []), stats)
    await pool().execute(
        "UPDATE board_links SET last_sync_at = now(), last_error = NULL WHERE board_id = $1", uuid.UUID(board_id)
    )
    await integrations.mark(iid, None)
    if mirror:
        await start_mirror(p, board_id, integration_id, trello_board_id=tb["id"])
    out = await boards.get_board(p, board_id)
    out["import"] = stats | {"source": tb.get("url"), "account": (info.get("account") or {}).get("name")}
    return out


async def _role_taken(board_id: str, name: str) -> bool:
    role = boards.guess_role(name)
    if role is None:
        return False
    return bool(
        await pool().fetchval(
            "SELECT 1 FROM board_columns WHERE board_id = $1 AND role = $2", uuid.UUID(board_id), role
        )
    )


def _description(tc: dict) -> str | None:
    desc = (tc.get("desc") or "").strip()
    nomes = [m.get("fullName") or m.get("username") for m in tc.get("members") or [] if m]
    if nomes:
        desc = (desc + "\n\n" if desc else "") + "Membros no Trello: " + ", ".join(nomes)
    return desc or None


def _items_of(tc: dict) -> list[tuple[str, str, bool]]:
    """(id do item lá, texto, feito) de todas as checklists do cartão; com mais de uma, o nome vira prefixo."""
    out: list[tuple[str, str, bool]] = []
    cls = tc.get("checklists") or []
    for cl in sorted(cls, key=lambda x: x.get("pos", 0)):
        for it in sorted(cl.get("checkItems") or [], key=lambda x: x.get("pos", 0)):
            texto = it["name"] if len(cls) == 1 else f"{cl['name']}: {it['name']}"
            out.append((it["id"], texto[:500], it.get("state") == "complete"))
    return out


async def _upsert_card_from_trello(
    iid: uuid.UUID, board_id: str, tc: dict, comentarios: list[dict], stats: dict
) -> None:
    col = await _local(iid, "list", tc["idList"])
    if col is None:
        return
    local = await _local(iid, "card", tc["id"])
    labels = [lb.get("name") or lb.get("color") for lb in tc.get("labels") or [] if lb.get("name") or lb.get("color")]
    itens = _items_of(tc)
    if local is None:
        card = await boards.create_card(
            ACTOR,
            board_id,
            tc["name"][:300] or "(sem título)",
            _description(tc),
            [t for _, t, _ in itens],
            labels,
            _due(tc.get("due")),
            col,
            origin=ORIGIN,
        )
        local = card["id"]
        await _set_ref(iid, "card", local, tc["id"])
        for (ext, _, feito), item in zip(itens, card["items"], strict=False):
            await _set_ref(iid, "item", _ik(local, item["id"]), ext)
            if feito:
                await boards.check_item(ACTOR, local, item["id"], done=True, origin=ORIGIN)
        for a in sorted(comentarios, key=lambda x: x.get("date", "")):
            quem = (a.get("memberCreator") or {}).get("fullName") or "Trello"
            texto = (a.get("data") or {}).get("text") or ""
            if texto.strip():
                await boards.log_card(
                    ACTOR, local, f"{quem} ({a.get('date', '')[:10]}): {texto}"[:4000], "note", origin=ORIGIN
                )
        stats["created"] += 1
        return
    atual = await boards.get_card(ACTOR, local, events=0)
    await boards.update_card(
        ACTOR,
        local,
        title=tc["name"][:300] or "(sem título)",
        description=_description(tc) or "",
        due=_due(tc.get("due")) or "",
        labels=labels,
        column=col,
        archived=False if atual.get("archived") else None,
        origin=ORIGIN,
    )
    for ext, texto, feito in itens:
        item_local = await _local(iid, "item", ext)
        if item_local is None:
            novo = await boards.add_items(ACTOR, local, [texto], origin=ORIGIN)
            item_local = _ik(local, novo["items"][-1]["id"])
            await _set_ref(iid, "item", item_local, ext)
        await boards.check_item(ACTOR, local, _item_id(item_local), done=feito, origin=ORIGIN)
    stats["updated"] += 1


# ---------------------------------------------------------------- espelhar


async def start_mirror(
    p: Principal, board_id: str, integration_id: str, trello_board_id: str | None = None, create: bool = False
) -> dict:
    """Liga o espelho: usa um quadro de lá (trello_board_id) ou cria um novo (create=true) com o nome daqui.
    Envia tudo (colunas e cartões) pela fila e registra o webhook (precisa de PUBLIC_URL https)."""
    p.require("write")
    iid = parse_uuid(integration_id, "integration_id")
    board = await boards.get_board(p, board_id, include_done=True)
    bid = uuid.UUID(board["id"])
    creds, _ = await integrations.credentials_of(iid)
    link = await integrations.board_link(bid)
    async with _client(creds) as tr:
        if link and link["integration_id"] == str(iid):
            ext = link["external_id"]
            url = link.get("external_url")
        elif create:
            tb = await tr.req(
                "POST", "/boards", name=board["name"], desc=board.get("description") or None, defaultLists="false"
            )
            ext, url = tb["id"], tb.get("url")
        elif trello_board_id:
            tb = await tr.req("GET", f"/boards/{trello_board_id}", fields="id,url")
            ext, url = tb["id"], tb.get("url")
        else:
            raise KBError("Escolha um quadro do Trello ou peça para criar um novo.")
        await pool().execute(
            """
            INSERT INTO board_links (board_id, integration_id, external_id, external_url, mode)
            VALUES ($1, $2, $3, $4, 'mirror')
            ON CONFLICT (board_id) DO UPDATE SET integration_id = EXCLUDED.integration_id,
              external_id = EXCLUDED.external_id, external_url = EXCLUDED.external_url,
              mode = 'mirror', last_error = NULL
            """,
            bid,
            iid,
            ext,
            url,
        )
        webhook_error = await _register_webhook(tr, bid, ext)
    async with pool().acquire() as conn:
        await conn.execute("INSERT INTO sync_queue (board_id, target) VALUES ($1, 'columns')", bid)
        for col in board["columns"]:
            for c in col["cards"]:
                await conn.execute(
                    "INSERT INTO sync_queue (board_id, target, local_id) VALUES ($1, 'card', $2)", bid, c["id"]
                )
    # sem PUBLIC_URL https é o esperado em ambiente local: só avisa (a tela Integrações já explica);
    # falha de verdade no registro do webhook marca o quadro
    if webhook_error and not webhook_error.startswith("Sem PUBLIC_URL"):
        await pool().execute("UPDATE board_links SET last_error = $2 WHERE board_id = $1", bid, webhook_error)
    return {
        "board_id": board_id,
        "mode": "mirror",
        "external_url": url,
        "webhook": webhook_error is None,
        "warning": webhook_error,
    }


def webhook_url(board_id: uuid.UUID) -> str:
    return get_settings().public_url.rstrip("/") + f"/integrations/trello/webhook/{board_id}"


async def _register_webhook(tr: Trello, board_id: uuid.UUID, ext: str) -> str | None:
    url = webhook_url(board_id)
    if not url.startswith("https://"):
        return "Sem PUBLIC_URL https: o que muda no Trello não volta sozinho (o daqui vai para lá normalmente)."
    old = await pool().fetchval("SELECT webhook_id FROM board_links WHERE board_id = $1", board_id)
    if old:
        try:
            await tr.req("DELETE", f"/webhooks/{old}")
        except KBError:
            pass
    try:
        wh = await tr.req("POST", "/webhooks", callbackURL=url, idModel=ext, description=f"Concilium {board_id}")
    except KBError as e:
        return f"Webhook do Trello não registrado: {e}"
    await pool().execute("UPDATE board_links SET webhook_id = $2 WHERE board_id = $1", board_id, wh["id"])
    return None


async def stop_mirror(p: Principal, board_id: str) -> dict:
    p.require("write")
    bid = parse_uuid(board_id, "board_id")
    await boards.get_board(p, board_id)  # alcance
    link = await integrations.board_link(bid)
    if not link:
        raise NotFound("Este quadro não está ligado a uma integração.")
    if link.get("webhook_id"):
        try:
            creds, _ = await integrations.credentials_of(uuid.UUID(link["integration_id"]))
            async with _client(creds) as tr:
                await tr.req("DELETE", f"/webhooks/{link['webhook_id']}")
        except KBError:
            pass
    await pool().execute("UPDATE board_links SET mode = 'import', webhook_id = NULL WHERE board_id = $1", bid)
    await pool().execute(
        "UPDATE sync_queue SET done_at = now(), error = 'espelho desligado' WHERE board_id = $1 AND done_at IS NULL",
        bid,
    )
    return {"board_id": board_id, "mode": "import"}


# ---------------------------------------------------------------- worker: daqui para lá


async def process_queue(limit: int = 25) -> int:
    """Processa a fila de espelho (o que está vencido). Devolve quantos envios fez."""
    feitos = 0
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM sync_queue WHERE done_at IS NULL AND next_at <= now() ORDER BY id LIMIT $1", limit
        )
    for row in rows:
        link = await integrations.board_link(row["board_id"])
        if not link or link["mode"] != "mirror" or link["provider"] != "trello":
            await pool().execute(
                "UPDATE sync_queue SET done_at = now(), error = 'sem espelho' WHERE id = $1", row["id"]
            )
            continue
        iid = uuid.UUID(link["integration_id"])
        try:
            creds, _ = await integrations.credentials_of(iid)
            async with _client(creds) as tr:
                if row["target"] == "columns":
                    await _sync_columns(tr, iid, row["board_id"], link["external_id"])
                elif row["target"] == "card":
                    await _sync_card(tr, iid, row["board_id"], link["external_id"], row["local_id"])
                elif row["target"] == "board":
                    nome = await pool().fetchval("SELECT name FROM boards WHERE id = $1", row["board_id"])
                    await tr.req("PUT", f"/boards/{link['external_id']}", name=nome)
                elif row["target"] == "comment":
                    ext = await _ref(iid, "card", row["local_id"])
                    note = (row["payload"] or {}).get("note")
                    if ext and note:
                        await tr.req("POST", f"/cards/{ext}/actions/comments", text=note[:16000])
            await pool().execute("UPDATE sync_queue SET done_at = now(), error = NULL WHERE id = $1", row["id"])
            await pool().execute(
                "UPDATE board_links SET last_sync_at = now(), last_error = NULL WHERE board_id = $1", row["board_id"]
            )
            await integrations.mark(iid, None)
            feitos += 1
        except Exception as e:  # noqa: BLE001 — falha de um envio não derruba a fila
            tentativas = row["attempts"] + 1
            erro = str(e)[:500]
            logger.warning("trello: envio %s falhou (%s/%s): %s", row["id"], tentativas, MAX_ATTEMPTS, erro)
            espera = timedelta(seconds=min(3600, 10 * 2**tentativas))
            await pool().execute(
                "UPDATE sync_queue SET attempts = $2, error = $3, next_at = $4, "
                "done_at = CASE WHEN $2 >= $5 THEN now() END WHERE id = $1",
                row["id"],
                tentativas,
                erro,
                datetime.now(UTC) + espera,
                MAX_ATTEMPTS,
            )
            await pool().execute(
                "UPDATE board_links SET last_error = $2 WHERE board_id = $1",
                row["board_id"],
                f"Envio ao Trello falhou: {erro}",
            )
    return feitos


async def _sync_columns(tr: Trello, iid: uuid.UUID, board_id: uuid.UUID, ext_board: str) -> None:
    cols = await pool().fetch(
        "SELECT id, name FROM board_columns WHERE board_id = $1 ORDER BY position, created_at", board_id
    )
    vivos = set()
    for i, c in enumerate(cols):
        lid = str(c["id"])
        vivos.add(lid)
        ext = await _ref(iid, "list", lid)
        h = _digest([c["name"], i])
        if ext is None:
            tl = await tr.req("POST", "/lists", name=c["name"], idBoard=ext_board, pos="bottom")
            await _set_ref(iid, "list", lid, tl["id"], h)
        elif await _hash_of(iid, "list", lid) != h:
            await tr.req("PUT", f"/lists/{ext}", name=c["name"], pos=str((i + 1) * 1024))
            await _set_ref(iid, "list", lid, ext, h)
    # colunas apagadas aqui: arquiva a lista lá
    # as listas deste quadro lá (GET) cujo mapa aponta para coluna que não existe mais aqui
    remotas = {x["id"] for x in await tr.req("GET", f"/boards/{ext_board}/lists", filter="open", fields="id")}
    for r in await pool().fetch(
        "SELECT local_id, external_id FROM external_refs r WHERE integration_id = $1 AND kind = 'list' "
        "AND NOT EXISTS (SELECT 1 FROM board_columns c WHERE c.id::text = r.local_id)",
        iid,
    ):
        if r["external_id"] in remotas and r["local_id"] not in vivos:
            try:
                await tr.req("PUT", f"/lists/{r['external_id']}/closed", value="true")
            except KBError:
                pass
            await pool().execute(
                "DELETE FROM external_refs WHERE integration_id = $1 AND kind = 'list' AND local_id = $2",
                iid,
                r["local_id"],
            )


async def _sync_card(tr: Trello, iid: uuid.UUID, board_id: uuid.UUID, ext_board: str, card_id: str | None) -> None:
    if not card_id:
        return
    c = await pool().fetchrow(
        "SELECT k.*, bc.role FROM cards k JOIN board_columns bc ON bc.id = k.column_id WHERE k.id = $1",
        uuid.UUID(card_id),
    )
    if c is None:
        return
    lista = await _ref(iid, "list", str(c["column_id"]))
    if lista is None:
        await _sync_columns(tr, iid, board_id, ext_board)
        lista = await _ref(iid, "list", str(c["column_id"]))
    ext = await _ref(iid, "card", card_id)
    campos = {
        "name": c["title"],
        "desc": c["description"] or "",
        "due": c["due_date"].isoformat() + "T12:00:00.000Z" if c["due_date"] else None,
        "idList": lista,
        "closed": c["archived_at"] is not None,
        "dueComplete": c["role"] == "done",
    }
    h = _digest(campos)
    if ext is None:
        if campos["closed"]:
            return
        tc = await tr.req(
            "POST",
            "/cards",
            idList=lista,
            name=campos["name"],
            desc=campos["desc"] or None,
            due=campos["due"],
            pos="bottom",
        )
        ext = tc["id"]
        await _set_ref(iid, "card", card_id, ext, h)
    elif await _hash_of(iid, "card", card_id) != h:
        await tr.req(
            "PUT",
            f"/cards/{ext}",
            name=campos["name"],
            desc=campos["desc"],
            due=campos["due"] or "null",
            idList=lista,
            closed=str(campos["closed"]).lower(),
            dueComplete=str(campos["dueComplete"]).lower(),
        )
        await _set_ref(iid, "card", card_id, ext, h)
    # checklist: um "Checklist" por cartão espelhando os itens daqui
    itens = await pool().fetch(
        "SELECT id, text, done FROM card_items WHERE card_id = $1 ORDER BY position, id", uuid.UUID(card_id)
    )
    cl = await _ref(iid, "checklist", card_id)
    if itens and cl is None:
        novo = await tr.req("POST", f"/cards/{ext}/checklists", name="Checklist")
        cl = novo["id"]
        await _set_ref(iid, "checklist", card_id, cl)
    vivos = set()
    for it in itens:
        lid = _ik(card_id, it["id"])
        vivos.add(lid)
        ext_item = await _ref(iid, "item", lid)
        hi = _digest([it["text"], it["done"]])
        if ext_item is None:
            ti = await tr.req("POST", f"/checklists/{cl}/checkItems", name=it["text"], checked=str(it["done"]).lower())
            await _set_ref(iid, "item", lid, ti["id"], hi)
        elif await _hash_of(iid, "item", lid) != hi:
            await tr.req(
                "PUT",
                f"/cards/{ext}/checkItem/{ext_item}",
                name=it["text"],
                state="complete" if it["done"] else "incomplete",
            )
            await _set_ref(iid, "item", lid, ext_item, hi)
    if cl:
        orfaos = await pool().fetch(
            "SELECT local_id, external_id FROM external_refs WHERE integration_id = $1 AND kind = 'item' "
            "AND local_id LIKE $2 || ':%'",
            iid,
            card_id,
        )
        for r in [r for r in orfaos if r["local_id"] not in vivos]:
            try:
                await tr.req("DELETE", f"/checklists/{cl}/checkItems/{r['external_id']}")
            except KBError:
                pass
            await pool().execute(
                "DELETE FROM external_refs WHERE integration_id = $1 AND kind = 'item' AND local_id = $2",
                iid,
                r["local_id"],
            )


async def worker() -> None:
    """Laço de fundo (lifespan do app): envia a fila de espelho a cada SYNC_INTERVAL_SECONDS."""
    while True:
        await asyncio.sleep(get_settings().sync_interval_seconds)
        try:
            await process_queue()
        except Exception:
            logger.exception("trello: worker da fila falhou")


# ---------------------------------------------------------------- webhook: de lá para cá


def signature_ok(secret: str, body: bytes, callback_url: str, header: str | None) -> bool:
    if not header:
        return False
    digest = hmac.new(secret.encode(), body + callback_url.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), header.strip())


async def handle_webhook(board_id: str, body: bytes, header: str | None) -> dict:
    bid = parse_uuid(board_id, "board_id")
    link = await integrations.board_link(bid)
    if not link or link["mode"] != "mirror":
        raise NotFound("Quadro sem espelho.")
    iid = uuid.UUID(link["integration_id"])
    creds, info = await integrations.credentials_of(iid)
    if not signature_ok(creds.get("secret", ""), body, webhook_url(bid), header):
        raise KBError("Assinatura do webhook do Trello inválida.")
    data = json.loads(body or b"{}")
    action = data.get("action") or {}
    if action.get("idMemberCreator") and action["idMemberCreator"] == (info.get("account") or {}).get("id"):
        return {"ignored": "eco do nosso envio"}
    try:
        aplicado = await _apply(iid, str(bid), action)
        await pool().execute("UPDATE board_links SET last_sync_at = now(), last_error = NULL WHERE board_id = $1", bid)
        return {"applied": aplicado}
    except KBError as e:
        await pool().execute(
            "UPDATE board_links SET last_error = $2 WHERE board_id = $1", bid, f"Mudança do Trello não aplicada: {e}"
        )
        return {"applied": None, "error": str(e)}


async def _apply(iid: uuid.UUID, board_id: str, action: dict) -> str | None:
    tipo = action.get("type")
    d = action.get("data") or {}
    card_ext = (d.get("card") or {}).get("id")
    card = await _local(iid, "card", card_ext) if card_ext else None
    if tipo == "createList":
        tl = d.get("list") or {}
        if not await _local(iid, "list", tl.get("id", "")):
            nova = await boards.create_column(ACTOR, board_id, tl.get("name") or "Lista", origin=ORIGIN)
            await _set_ref(iid, "list", nova["id"], tl["id"], _digest([nova["name"], -1]))
        return tipo
    if tipo == "updateList":
        tl = d.get("list") or {}
        col = await _local(iid, "list", tl.get("id", ""))
        if col and "name" in (d.get("old") or {}):
            await boards.update_column(ACTOR, col, name=tl.get("name"), origin=ORIGIN)
        return tipo
    if tipo == "createCard":
        col = await _local(iid, "list", (d.get("list") or {}).get("id", ""))
        if col and not card and card_ext:
            novo = await boards.create_card(
                ACTOR, board_id, (d["card"].get("name") or "(sem título)")[:300], column=col, origin=ORIGIN
            )
            await _set_ref(iid, "card", novo["id"], card_ext)
        return tipo
    if not card:
        return None
    if tipo == "updateCard":
        old = d.get("old") or {}
        tc = d.get("card") or {}
        kw: dict[str, Any] = {}
        if "name" in old:
            kw["title"] = tc.get("name")
        if "desc" in old:
            kw["description"] = tc.get("desc") or ""
        if "due" in old:
            kw["due"] = _due(tc.get("due")) or ""
        if "idList" in old:
            col = await _local(iid, "list", d.get("listAfter", {}).get("id") or tc.get("idList", ""))
            if col:
                kw["column"] = col
        if "closed" in old:
            kw["archived"] = bool(tc.get("closed"))
        if kw:
            await boards.update_card(ACTOR, card, origin=ORIGIN, **kw)
        return tipo
    if tipo == "deleteCard":
        await boards.update_card(ACTOR, card, archived=True, origin=ORIGIN)
        return tipo
    if tipo == "commentCard":
        quem = (action.get("memberCreator") or {}).get("fullName") or "Trello"
        await boards.log_card(ACTOR, card, f"{quem}: {d.get('text', '')}"[:4000], "note", origin=ORIGIN)
        return tipo
    item_ext = (d.get("checkItem") or {}).get("id")
    if tipo == "createCheckItem" and item_ext:
        if not await _local(iid, "item", item_ext):
            novo = await boards.add_items(ACTOR, card, [d["checkItem"].get("name") or "item"], origin=ORIGIN)
            await _set_ref(iid, "item", _ik(card, novo["items"][-1]["id"]), item_ext)
        return tipo
    item = await _local(iid, "item", item_ext) if item_ext else None
    if not item:
        return None
    if tipo == "updateCheckItemStateOnCard":
        await boards.check_item(
            ACTOR, card, _item_id(item), done=d["checkItem"].get("state") == "complete", origin=ORIGIN
        )
    elif tipo == "updateCheckItem" and "name" in (d.get("old") or {}):
        await boards.edit_item(ACTOR, card, _item_id(item), d["checkItem"].get("name"), origin=ORIGIN)
    elif tipo == "deleteCheckItem":
        await boards.edit_item(ACTOR, card, _item_id(item), delete=True, origin=ORIGIN)
        await pool().execute(
            "DELETE FROM external_refs WHERE integration_id = $1 AND kind = 'item' AND local_id = $2", iid, item
        )
    else:
        return None
    return tipo
