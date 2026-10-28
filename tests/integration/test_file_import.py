"""Import de arquivos soltos (plan-web-28): PDF/.docx/.md pela dash (job) e pela API REST (síncrono).

Coleções com prefixo "fi-" (o banco é compartilhado entre os módulos de integração).
"""

import asyncio

import httpx
import pytest

from mcp_rag_api import db
from mcp_rag_api.core import dash_auth, documents, folders, search
from mcp_rag_api.main import app
from mcp_rag_api.security import DEV_PRINCIPAL as ADMIN
from mcp_rag_api.security import create_api_key

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _dash_client(username: str, role: str) -> httpx.AsyncClient:
    await dash_auth.create_user(username, "senha-segura-123", role)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://dash.test",
        headers={"X-Requested-With": "fetch"},  # exigido em mutações da dash (CSRF)
    )
    resp = await client.post("/dash/api/auth/login", json={"username": username, "password": "senha-segura-123"})
    assert resp.status_code == 200
    return client


async def _api_client(scopes: list[str]) -> httpx.AsyncClient:
    async with db.pool().acquire() as conn:
        key = (await create_api_key(conn, label=f"fi-{'-'.join(scopes)}", scopes=scopes))["api_key"]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers={"Authorization": f"Bearer {key}"},
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
    client = await _dash_client("fi-editora", "editor")
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def viewer():
    client = await _dash_client("fi-leitora", "viewer")
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def writer():
    client = await _api_client(["read", "write"])
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
async def reader():
    client = await _api_client(["read"])
    yield client
    await client.aclose()


async def test_dash_imports_pdf_docx_md_into_folder_and_reports_bad_file(editor, make_pdf, make_docx, docx_p):
    await documents.create_collection(ADMIN, "fi-contratos")
    folder = await folders.ensure_folder_path(ADMIN, "fi-contratos", "Acme/2026")
    files = [
        ("files", ("Proposta Acme.pdf", make_pdf(["Proposta comercial Acme", "Valor mensal"]), "application/pdf")),
        ("files", ("contrato.docx", make_docx(docx_p("Contrato Acme", style="Heading1")), DOCX_MIME)),
        ("files", ("ata.md", b"---\ntags: [ata]\n---\nAta citando [[Proposta Acme]]\n", "text/markdown")),
        ("files", ("scan.pdf", make_pdf([""]), "application/pdf")),
    ]
    data = {"collection": "fi-contratos", "folder_id": folder["id"]}
    resp = await editor.post("/dash/api/import/files", files=files, data=data)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 4

    snap = await _wait_job(editor, resp.json()["job_id"])
    assert snap["status"] == "done"
    assert snap["notes"] == 3 and snap["collections"] == 1
    assert [e["file"] for e in snap["errors"]] == ["scan.pdf"]
    assert "escaneado" in snap["errors"][0]["error"]

    tree = (await editor.get("/dash/api/notes/tree")).json()
    by_title = {n["title"]: n for n in tree["notes"] if n["collection"] == "fi-contratos"}
    assert set(by_title) == {"Proposta Acme", "contrato", "ata"}
    assert all(n["folder_id"] == folder["id"] for n in by_title.values())

    proposta = (await editor.get(f"/dash/api/notes/{by_title['Proposta Acme']['id']}")).json()
    assert proposta["source"] == "file-import"
    assert proposta["content"] == "Proposta comercial Acme\n\nValor mensal"
    assert proposta["metadata"]["pages"] == 2

    # a nota do .md cita o PDF por [[título]]: link resolvido
    links = (await editor.get(f"/dash/api/documents/{by_title['ata']['id']}/links")).json()
    assert [ln["document_id"] for ln in links["links"] if not ln["pending"]] == [by_title["Proposta Acme"]["id"]]

    # vetorizado: a busca acha o conteúdo do PDF
    hits = await search.search_knowledge(ADMIN, "Proposta comercial Acme", collections=["fi-contratos"])
    assert any(str(h["document_id"]) == by_title["Proposta Acme"]["id"] for h in hits)


async def test_dash_reimport_same_file_updates_instead_of_duplicating(editor, make_pdf):
    await documents.create_collection(ADMIN, "fi-reimport")
    for text in ("versao um", "versao dois"):
        files = [("files", ("relatorio.pdf", make_pdf([text]), "application/pdf"))]
        resp = await editor.post("/dash/api/import/files", files=files, data={"collection": "fi-reimport"})
        snap = await _wait_job(editor, resp.json()["job_id"])
        assert snap["status"] == "done" and snap["notes"] == 1

    docs = await documents.list_documents(ADMIN, "fi-reimport")
    assert len(docs) == 1 and docs[0]["version"] == 2


async def test_dash_rejects_viewer_unsupported_format_and_foreign_folder(editor, viewer):
    await documents.create_collection(ADMIN, "fi-outra")
    other = await folders.ensure_folder_path(ADMIN, "fi-outra", "X")
    txt = [("files", ("a.txt", b"oi", "text/plain"))]

    assert (await viewer.post("/dash/api/import/files", files=txt, data={"collection": "fi-outra"})).status_code == 403

    xlsx = [("files", ("planilha.xlsx", b"x", "application/octet-stream"))]
    resp = await editor.post("/dash/api/import/files", files=xlsx, data={"collection": "fi-outra"})
    assert resp.status_code == 400 and "não suportado" in resp.json()["detail"]

    data = {"collection": "fi-contratos", "folder_id": other["id"]}
    resp = await editor.post("/dash/api/import/files", files=txt, data=data)
    assert resp.status_code == 400


async def test_rest_upload_converts_and_places_in_folder(writer, reader, make_docx, docx_p):
    await documents.create_collection(ADMIN, "fi-rest")
    docx = make_docx(docx_p("Manual", style="Heading1") + docx_p("Passo 1: ligar o equipamento."))
    files = {"file": ("manual.docx", docx, DOCX_MIME)}
    data = {"collection": "fi-rest", "folder_path": "Manuais/Equipamentos"}

    assert (await reader.post("/documents/upload", files=files, data=data)).status_code == 403

    resp = await writer.post("/documents/upload", files=files, data=data)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True and body["folder_path"] == "Manuais/Equipamentos"
    assert body["filename"] == "manual.docx" and body["file_type"] == "docx"

    doc = (await writer.get(f"/documents/{body['document_id']}")).json()
    assert doc["content"] == "# Manual\n\nPasso 1: ligar o equipamento."
    assert doc["metadata"]["original_filename"] == "manual.docx"

    again = (await writer.post("/documents/upload", files=files, data=data)).json()
    assert again["document_id"] == body["document_id"]  # mesmo arquivo, mesmo lugar: atualiza

    bad = await writer.post("/documents/upload", files={"file": ("x.pdf", b"lixo", "application/pdf")}, data=data)
    assert bad.status_code == 400
