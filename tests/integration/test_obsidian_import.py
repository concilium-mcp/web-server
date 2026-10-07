"""Import de vault Obsidian pela dash (plan-web-13): fluxo completo com job assíncrono,
permissões (editor/viewer), idempotência do re-import, wikilinks resolvidos e cancelamento.

Coleções com prefixo "oi-" (o banco é compartilhado entre os módulos de integração).
"""

import asyncio
import io
import zipfile

import httpx
import pytest

from mcp_rag_api.core import dash_auth
from mcp_rag_api.main import app

VAULT = {
    "nota-alfa.md": "Alfa aponta para [[Beta Bonito]] e para [[Gama]].\n",
    "clientes/acme/beta.md": "---\ntitle: Beta Bonito\ntags: [cliente, acme]\n---\n# Beta\nconteúdo beta\n",
    "clientes/acme/novos/gama.md": "---\ntags: prospect\n---\nGama faz embed: ![[Beta Bonito]]\n",
    ".obsidian/app.json": "{}",
    "anexos/foto.png": "binário",
}

VAULT_MANY_NOTES = {f"oi-lote/nota-{i:03}.md": f"nota {i} " + "lorem ipsum dolor " * 12 for i in range(300)}


def _zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


async def _client(username: str, role: str) -> httpx.AsyncClient:
    await dash_auth.create_user(username, "senha-segura-123", role)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://dash.test",
        headers={"X-Requested-With": "fetch"},  # exigido em mutações da dash (CSRF)
    )
    resp = await client.post("/dash/api/auth/login", json={"username": username, "password": "senha-segura-123"})
    assert resp.status_code == 200 and resp.json()["role"] == role
    return client


async def _upload(client: httpx.AsyncClient, entries: dict[str, str]) -> httpx.Response:
    return await client.post(
        "/dash/api/import/obsidian",
        files={"file": ("vault.zip", _zip(entries), "application/zip")},
    )


async def _wait_job(client: httpx.AsyncClient, job_id: str, deadline_s: float = 60) -> dict:
    for _ in range(int(deadline_s / 0.1)):
        snap = (await client.get(f"/dash/api/import/{job_id}")).json()
        if snap["status"] != "running":
            return snap
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} não terminou em {deadline_s}s")


@pytest.fixture(scope="module")
async def editor():
    client = await _client("oi-editora", "editor")
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def viewer():
    client = await _client("oi-leitora", "viewer")
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def other_editor():
    client = await _client("oi-editorb", "editor")
    yield client
    await client.aclose()


async def test_viewer_cannot_import(viewer):
    resp = await _upload(viewer, {"oi-x/nota.md": "x"})
    assert resp.status_code == 403


async def test_import_full_flow_counts_collections_tags_and_links(editor):
    resp = await _upload(editor, VAULT)
    assert resp.status_code == 200
    started = resp.json()
    assert started["total"] == 3  # .obsidian e anexo fora; só .md entra

    snap = await _wait_job(editor, started["job_id"])
    assert snap["status"] == "done"
    assert snap["done"] == 3 and snap["notes"] == 3 and snap["errors"] == []
    assert snap["collections"] == 3  # raiz + clientes - acme + clientes - acme - novos

    tree = (await editor.get("/dash/api/notes/tree")).json()
    cols = {c["name"] for c in tree["collections"]}
    assert {"Obsidian Import", "clientes - acme", "clientes - acme - novos"} <= cols
    by_title = {n["title"]: n for n in tree["notes"]}
    assert by_title["Beta Bonito"]["tags"] == ["cliente", "acme"]

    # origem rastreável e frontmatter stripado do conteúdo
    alfa_id = by_title["nota-alfa"]["id"]
    alfa = (await editor.get(f"/dash/api/notes/{alfa_id}")).json()
    assert alfa["source"] == "obsidian-import" and alfa["collection"] == "Obsidian Import"
    assert alfa["content"].startswith("Alfa aponta")

    # wikilinks resolvidos (inclusive o ![[embed]] convertido): alfa → beta e gama
    links = (await editor.get(f"/dash/api/documents/{alfa_id}/links")).json()
    resolved = {ln["target_title"]: ln["document_id"] for ln in links["links"] if not ln["pending"]}
    assert resolved.get("Beta Bonito") == by_title["Beta Bonito"]["id"]
    assert resolved.get("Gama") == by_title["gama"]["id"]


async def test_reimport_is_idempotent_and_bumps_version(editor):
    # mesmo zip de novo: atualiza, não duplica — e, sem mudança de conteúdo,
    # não cria versão nova (update_document devolve no_changes)
    resp = await _upload(editor, VAULT)
    assert resp.status_code == 200
    snap = await _wait_job(editor, resp.json()["job_id"])
    assert snap["status"] == "done" and snap["notes"] == 3

    tree = (await editor.get("/dash/api/notes/tree")).json()
    betas = [n for n in tree["notes"] if n["title"] == "Beta Bonito"]
    assert len(betas) == 1 and betas[0]["version"] == 1

    # re-import com UMA nota alterada: só ela sobe de versão
    vault_v2 = {**VAULT, "clientes/acme/beta.md": "---\ntitle: Beta Bonito\n---\n# Beta\nconteúdo beta v2\n"}
    resp = await _upload(editor, vault_v2)
    assert resp.status_code == 200
    snap = await _wait_job(editor, resp.json()["job_id"])
    assert snap["status"] == "done" and snap["notes"] == 3

    tree = (await editor.get("/dash/api/notes/tree")).json()
    betas = [n for n in tree["notes"] if n["title"] == "Beta Bonito"]
    assert len(betas) == 1  # atualizou de novo, não duplicou
    assert betas[0]["version"] == 2
    alfa = next(n for n in tree["notes"] if n["title"] == "nota-alfa")
    assert alfa["version"] == 1  # sem mudança: versão preservada

    # os links continuam resolvidos depois do re-import (sync de wikilinks no update)
    alfa_id = alfa["id"]
    links = (await editor.get(f"/dash/api/documents/{alfa_id}/links")).json()
    assert all(not ln["pending"] for ln in links["links"])


async def test_job_of_another_user_is_not_visible(editor, other_editor):
    resp = await _upload(editor, {"oi-priv/nota.md": "segredo"})
    job_id = resp.json()["job_id"]
    assert (await other_editor.get(f"/dash/api/import/{job_id}")).status_code == 404
    assert (await other_editor.post(f"/dash/api/import/{job_id}/cancel", json={})).status_code == 404
    await _wait_job(editor, job_id)


async def test_invalid_zip_and_zip_without_notes_are_rejected(editor):
    resp = await editor.post(
        "/dash/api/import/obsidian",
        files={"file": ("vault.zip", b"texto puro, nem zip", "application/zip")},
    )
    assert resp.status_code == 400 and "zip" in resp.json()["detail"]
    resp = await _upload(editor, {"só-texto.txt": "olá"})
    assert resp.status_code == 400 and "Nenhuma nota" in resp.json()["detail"]


async def test_one_active_import_per_user_and_cancel(editor):
    resp = await _upload(editor, VAULT_MANY_NOTES)
    assert resp.status_code == 200
    started = resp.json()
    assert started["total"] == 300

    # segundo import do MESMO usuário enquanto há um ativo: 409
    assert (await _upload(editor, {"oi-lote2/outra.md": "x"})).status_code == 409

    cancel = await editor.post(f"/dash/api/import/{started['job_id']}/cancel", json={})
    assert cancel.status_code == 200

    snap = await _wait_job(editor, started["job_id"])
    assert snap["status"] == "cancelled"
    assert snap["done"] < snap["total"]  # parou no meio
    assert snap["notes"] == snap["done"] and snap["errors"] == []

    # o que já tinha sido importado ficou na base; re-import completa sem duplicar
    tree = (await editor.get("/dash/api/notes/tree")).json()
    lote = [n for n in tree["notes"] if n["collection"] == "oi-lote"]
    assert 0 < len(lote) < 300

    resp = await _upload(editor, VAULT_MANY_NOTES)
    snap = await _wait_job(editor, resp.json()["job_id"])
    assert snap["status"] == "done" and snap["notes"] == 300
    tree = (await editor.get("/dash/api/notes/tree")).json()
    assert len([n for n in tree["notes"] if n["collection"] == "oi-lote"]) == 300
