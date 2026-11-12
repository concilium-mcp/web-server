"""Integração com o Trello (plan-web-37d) contra um Trello falso em memória (httpx.MockTransport)."""

import base64
import hashlib
import hmac
import itertools
import json
import uuid

import httpx
import pytest

from mcp_rag_api import db
from mcp_rag_api.config import get_settings
from mcp_rag_api.core import boards, documents, integrations, trello
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import KBError

COLLECTION = "trello-37d"
SECRET = "segredo-do-app-trello"
ME = "membro-conectado"


class FakeTrello:
    """O suficiente da API do Trello para importar, espelhar e registrar webhook."""

    def __init__(self) -> None:
        self.ids = (f"t{n}" for n in itertools.count(1))
        self.boards: dict[str, dict] = {}
        self.lists: dict[str, dict] = {}
        self.cards: dict[str, dict] = {}
        self.checklists: dict[str, dict] = {}
        self.comments: list[dict] = []
        self.webhooks: dict[str, dict] = {}
        self.calls: list[str] = []

    def seed(self) -> str:
        b = self._board("Projeto Lá")
        todo = self._list(b, "A fazer", 1)
        doing = self._list(b, "Fazendo", 2)
        self._list(b, "Feito", 3)
        c1 = self._card(
            todo, "Configurar domínio", "desc do trello", due="2027-01-15T12:00:00.000Z", labels=[{"name": "infra"}]
        )
        cl = self._checklist(c1, "Passos")
        self._item(cl, "DNS", "complete")
        self._item(cl, "SSL", "incomplete")
        self._card(doing, "Escrever onboarding", "")
        self.comments.append(
            {
                "type": "commentCard",
                "date": "2026-11-01T10:00:00Z",
                "data": {"card": {"id": c1}, "text": "Lembrar do www"},
                "memberCreator": {"fullName": "Ana"},
            }
        )
        return b

    def _board(self, name: str) -> str:
        i = next(self.ids)
        self.boards[i] = {"id": i, "name": name, "desc": "", "url": f"https://trello.example/b/{i}"}
        return i

    def _list(self, board: str, name: str, pos: float) -> str:
        i = next(self.ids)
        self.lists[i] = {"id": i, "idBoard": board, "name": name, "pos": pos, "closed": False}
        return i

    def _card(self, lst: str, name: str, desc: str, due: str | None = None, labels: list | None = None) -> str:
        i = next(self.ids)
        self.cards[i] = {
            "id": i,
            "idList": lst,
            "idBoard": self.lists[lst]["idBoard"],
            "name": name,
            "desc": desc,
            "due": due,
            "pos": len(self.cards),
            "labels": labels or [],
            "members": [],
            "closed": False,
        }
        return i

    def _checklist(self, card: str, name: str) -> str:
        i = next(self.ids)
        self.checklists[i] = {"id": i, "idCard": card, "name": name, "pos": 1, "checkItems": []}
        return i

    def _item(self, cl: str, name: str, state: str) -> str:
        i = next(self.ids)
        self.checklists[cl]["checkItems"].append(
            {"id": i, "name": name, "state": state, "pos": len(self.checklists[cl]["checkItems"])}
        )
        return i

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        path = request.url.path.removeprefix("/1")
        self.calls.append(f"{request.method} {path}")
        parts = path.strip("/").split("/")
        m = request.method

        def ok(data: object) -> httpx.Response:
            return httpx.Response(200, json=data)

        if path == "/members/me":
            return ok({"id": ME, "fullName": "Conta Concilium", "username": "concilium"})
        if path == "/members/me/boards":
            return ok(list(self.boards.values()))
        if parts[0] == "boards":
            if m == "POST":
                return ok(self.boards[self._board(q["name"])])
            bid = parts[1]
            if len(parts) == 2:
                if m == "PUT":
                    self.boards[bid].update({k: v for k, v in q.items() if k in ("name",)})
                return ok(self.boards[bid])
            if parts[2] == "lists":
                return ok([x for x in self.lists.values() if x["idBoard"] == bid and not x["closed"]])
            if parts[2] == "cards":
                out = []
                for c in self.cards.values():
                    if c["idBoard"] == bid and not c["closed"]:
                        out.append({**c, "checklists": [x for x in self.checklists.values() if x["idCard"] == c["id"]]})
                return ok(out)
            if parts[2] == "actions":
                return ok([a for a in self.comments if self.cards[a["data"]["card"]["id"]]["idBoard"] == bid])
        if parts[0] == "lists":
            if m == "POST":
                return ok(self.lists[self._list(q["idBoard"], q["name"], 99)])
            if len(parts) == 3 and parts[2] == "closed":
                self.lists[parts[1]]["closed"] = True
                return ok(self.lists[parts[1]])
            self.lists[parts[1]].update({"name": q.get("name", self.lists[parts[1]]["name"])})
            return ok(self.lists[parts[1]])
        if parts[0] == "cards":
            if m == "POST" and len(parts) == 1:
                i = self._card(q["idList"], q["name"], q.get("desc", ""), q.get("due"))
                return ok(self.cards[i])
            cid = parts[1]
            if len(parts) == 2 and m == "PUT":
                c = self.cards[cid]
                for k in ("name", "desc", "idList"):
                    if k in q:
                        c[k] = q[k]
                if "closed" in q:
                    c["closed"] = q["closed"] == "true"
                if "due" in q:
                    c["due"] = None if q["due"] == "null" else q["due"]
                c["idBoard"] = self.lists[c["idList"]]["idBoard"]
                return ok(c)
            if parts[2] == "checklists":
                return ok(self.checklists[self._checklist(cid, q["name"])])
            if parts[2] == "checkItem":
                for cl in self.checklists.values():
                    for it in cl["checkItems"]:
                        if it["id"] == parts[3]:
                            it.update({k: q[k] for k in ("name", "state") if k in q})
                            return ok(it)
            if parts[2] == "actions":
                self.comments.append(
                    {
                        "type": "commentCard",
                        "date": "2026-11-12",
                        "data": {"card": {"id": cid}, "text": q["text"]},
                        "memberCreator": {"fullName": "Conta Concilium"},
                    }
                )
                return ok({})
        if parts[0] == "checklists":
            cl = self.checklists[parts[1]]
            if m == "POST":
                i = self._item(parts[1], q["name"], "complete" if q.get("checked") == "true" else "incomplete")
                return ok(next(x for x in cl["checkItems"] if x["id"] == i))
            if m == "DELETE":
                cl["checkItems"] = [x for x in cl["checkItems"] if x["id"] != parts[3]]
                return ok({})
        if parts[0] == "webhooks":
            if m == "POST":
                i = next(self.ids)
                self.webhooks[i] = {"id": i, **q}
                return ok(self.webhooks[i])
            self.webhooks.pop(parts[1], None)
            return ok({})
        return httpx.Response(404, json={"message": f"rota falsa não coberta: {m} {path}"})


@pytest.fixture(scope="module")
async def fake(monkeypatch_module):
    f = FakeTrello()
    trello._transport = httpx.MockTransport(f.handler)
    yield f
    trello._transport = None


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    mp.setenv("INTEGRATIONS_ENCRYPTION_KEY", "chave-de-teste-com-mais-de-32-caracteres-ok")
    mp.setenv("PUBLIC_URL", "https://concilium.example.com")
    get_settings.cache_clear()
    yield mp
    mp.undo()
    get_settings.cache_clear()


@pytest.fixture(scope="module")
async def conta(fake):
    await documents.create_collection(ADMIN, COLLECTION)
    return await trello.connect(ADMIN, "chave", "token-do-usuario", SECRET)


async def test_connect_stores_encrypted_and_never_returns_secret(conta):
    assert conta["account"]["id"] == ME and "credentials" not in conta
    stored = await db.pool().fetchval("SELECT credentials FROM integrations WHERE id = $1", uuid.UUID(conta["id"]))
    assert stored.startswith("enc:v1:") and "token-do-usuario" not in stored and SECRET not in stored
    creds, _ = await integrations.credentials_of(uuid.UUID(conta["id"]))
    assert creds == {"key": "chave", "token": "token-do-usuario", "secret": SECRET}


async def test_connect_requires_encryption_key(fake, monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(KBError, match="INTEGRATIONS_ENCRYPTION_KEY"):
            await trello.connect(ADMIN, "k", "t", "s")
    finally:
        monkeypatch.setenv("INTEGRATIONS_ENCRYPTION_KEY", "chave-de-teste-com-mais-de-32-caracteres-ok")
        get_settings.cache_clear()


async def test_import_and_reimport_without_duplicates(fake, conta):
    tb = fake.seed()
    board = await trello.import_board(ADMIN, conta["id"], tb, COLLECTION, "Clientes/Lá")
    assert board["import"]["created"] == 2 and board["folder_path"] == "Clientes/Lá"
    cols = {c["name"]: c for c in board["columns"]}
    assert cols["Fazendo"]["role"] == "doing" and cols["Feito"]["role"] == "done"
    card = cols["A fazer"]["cards"][0]
    full = await boards.get_card(ADMIN, card["id"], events=20)
    assert full["title"] == "Configurar domínio" and full["due"] == "2027-01-15" and full["labels"] == ["infra"]
    assert full["progress"] == "1/2" and [i["text"] for i in full["items"]] == ["DNS", "SSL"]
    assert any(e["kind"] == "log.note" and "Ana" in e["note"] for e in full["log"])
    # muda lá e reimporta: atualiza, não duplica
    cid = next(c for c in fake.cards.values() if c["name"] == "Configurar domínio")["id"]
    fake.cards[cid]["name"] = "Configurar domínio e e-mail"
    again = await trello.import_board(ADMIN, conta["id"], tb, COLLECTION, "Clientes/Lá")
    assert again["id"] == board["id"] and again["import"] == again["import"] | {"created": 0, "updated": 2}
    titles = [c["title"] for col in again["columns"] for c in col["cards"]]
    assert sorted(titles) == ["Configurar domínio e e-mail", "Escrever onboarding"]


async def test_mirror_sends_local_changes_and_ignores_echo(fake, conta):
    local = await boards.create_board(ADMIN, COLLECTION, "Espelho", "Clientes")
    out = await trello.start_mirror(ADMIN, local["id"], conta["id"], create=True)
    assert out["mode"] == "mirror" and out["webhook"] is True
    assert any(w["callbackURL"].endswith(f"/integrations/trello/webhook/{local['id']}") for w in fake.webhooks.values())
    card = await boards.create_card(ADMIN, local["id"], "Daqui para lá", checklist=["um", "dois"])
    await boards.check_item(ADMIN, card["id"], text="um")
    await boards.log_card(ADMIN, card["id"], "commit abc", "commit")
    await trello.process_queue()
    ext_board = next(b for b in fake.boards.values() if b["name"] == "Espelho")["id"]
    assert [x["name"] for x in fake.lists.values() if x["idBoard"] == ext_board] == [
        c["name"] for c in local["columns"]
    ]
    tc = next(c for c in fake.cards.values() if c["name"] == "Daqui para lá")
    items = [it for cl in fake.checklists.values() if cl["idCard"] == tc["id"] for it in cl["checkItems"]]
    assert [(i["name"], i["state"]) for i in items] == [("um", "complete"), ("dois", "incomplete")]
    assert any(a["data"]["text"] == "commit abc" for a in fake.comments)
    # mover aqui muda a lista lá
    await boards.update_card(ADMIN, card["id"], column="doing")
    await trello.process_queue()
    assert fake.lists[tc["idList"]]["name"] == "Em andamento"
    # sem mudança, não manda de novo
    n = len(fake.calls)
    await boards.update_card(ADMIN, card["id"], title="Daqui para lá")  # mesmo título: nada muda
    await trello.process_queue()
    assert len(fake.calls) == n


def _sign(body: bytes, board_id: str) -> str:
    url = trello.webhook_url(uuid.UUID(board_id))
    return base64.b64encode(hmac.new(SECRET.encode(), body + url.encode(), hashlib.sha1).digest()).decode()


async def test_webhook_applies_trello_changes(fake, conta):
    local = await boards.create_board(ADMIN, COLLECTION, "Volta", "Clientes")
    await trello.start_mirror(ADMIN, local["id"], conta["id"], create=True)
    card = await boards.create_card(ADMIN, local["id"], "Original", checklist=["passo"])
    await trello.process_queue()
    tc = next(c for c in fake.cards.values() if c["name"] == "Original")
    feito = next(x for x in fake.lists.values() if x["idBoard"] == tc["idBoard"] and x["name"] == "Feito")
    acao = {
        "action": {
            "type": "updateCard",
            "idMemberCreator": "outra-pessoa",
            "data": {
                "card": {"id": tc["id"], "name": "Renomeado lá", "idList": feito["id"]},
                "old": {"name": "Original", "idList": tc["idList"]},
                "listAfter": {"id": feito["id"]},
            },
        }
    }
    body = json.dumps(acao).encode()
    # assinatura errada é recusada
    with pytest.raises(KBError, match="Assinatura"):
        await trello.handle_webhook(local["id"], body, "errada")
    res = await trello.handle_webhook(local["id"], body, _sign(body, local["id"]))
    assert res["applied"] == "updateCard"
    got = await boards.get_card(ADMIN, card["id"], events=5)
    assert got["title"] == "Renomeado lá" and got["column"] == "Feito" and got["done_at"]
    assert got["log"][0]["actor"] == "integration:trello" and got["log"][0]["actor_kind"] == "integration"
    # o que veio de lá não volta para a fila (sem eco)
    pend = await db.pool().fetchval(
        "SELECT count(*) FROM sync_queue WHERE board_id = $1 AND done_at IS NULL", uuid.UUID(local["id"])
    )
    assert pend == 0
    # eco do nosso próprio envio é ignorado
    eco = json.dumps({"action": {**acao["action"], "idMemberCreator": ME}}).encode()
    assert (await trello.handle_webhook(local["id"], eco, _sign(eco, local["id"])))["ignored"]
    # checklist e comentário de lá
    item_ext = next(it["id"] for cl in fake.checklists.values() if cl["idCard"] == tc["id"] for it in cl["checkItems"])
    ev = json.dumps(
        {
            "action": {
                "type": "updateCheckItemStateOnCard",
                "idMemberCreator": "x",
                "data": {"card": {"id": tc["id"]}, "checkItem": {"id": item_ext, "state": "complete"}},
            }
        }
    ).encode()
    await trello.handle_webhook(local["id"], ev, _sign(ev, local["id"]))
    com = json.dumps(
        {
            "action": {
                "type": "commentCard",
                "idMemberCreator": "x",
                "memberCreator": {"fullName": "Bia"},
                "data": {"card": {"id": tc["id"]}, "text": "ok!"},
            }
        }
    ).encode()
    await trello.handle_webhook(local["id"], com, _sign(com, local["id"]))
    got = await boards.get_card(ADMIN, card["id"], events=5)
    assert got["progress"] == "1/1" and got["log"][0]["note"] == "Bia: ok!"


async def test_webhook_route_and_stop_mirror(fake, conta):
    from mcp_rag_api.main import app

    local = await boards.create_board(ADMIN, COLLECTION, "Rota", "Clientes")
    await trello.start_mirror(ADMIN, local["id"], conta["id"], create=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.head(f"/integrations/trello/webhook/{local['id']}")).status_code == 200
        assert (
            await c.post(
                f"/integrations/trello/webhook/{local['id']}", content=b"{}", headers={"X-Trello-Webhook": "x"}
            )
        ).status_code == 401
        await trello.stop_mirror(ADMIN, local["id"])
        assert (await c.post(f"/integrations/trello/webhook/{local['id']}", content=b"{}")).status_code == 410
