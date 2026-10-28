"""Pastas aninhadas dentro das coleções (plan-web-14) — organização da tela Notas, só no web-server.

A coleção é a pasta principal (o "projeto"); dentro dela, pastas sem limite de profundidade
(`parent_id` NULL = filha direta da coleção). Escopo de acesso, MCP e busca seguem por coleção.
Integridade fica no banco (migração 006): FK composta garante pasta-mãe e nota na mesma coleção,
e mudar a coleção de uma pasta leva a subárvore (pastas e notas) junto via ON UPDATE CASCADE.
"""

import uuid
from typing import Any

import asyncpg

from ..db import audit, pool, records
from ..security import KBError, NotFound, Principal
from . import documents
from .documents import parse_uuid, validate_collection_name

FOLDER_NAME_MAX = 120
PATH_SEP = "/"  # caminho de pastas na API/MCP: "Secretaria de Educação/Reuniões/2026"


def validate_folder_name(name: str) -> str:
    """Nome livre (é só rótulo, sempre exibido escapado), sem quebra de linha, 1–120 chars e sem "/"
    (reservado como separador de caminho na API/MCP)."""
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > FOLDER_NAME_MAX:
        raise KBError(f"Nome de pasta inválido: use de 1 a {FOLDER_NAME_MAX} caracteres.")
    if PATH_SEP in cleaned:
        raise KBError('Nome de pasta não pode conter "/" (é o separador de caminho de pastas).')
    return cleaned


def split_folder_path(path: str) -> list[str]:
    """ "A / B/C " -> ["A", "B", "C"]; segmentos vazios são ignorados; caminho vazio é erro."""
    parts = [validate_folder_name(part) for part in path.split(PATH_SEP) if part.strip()]
    if not parts:
        raise KBError('Caminho de pasta vazio: use algo como "Clientes/Reuniões".')
    return parts


async def _lock_collection(conn: asyncpg.Connection, collection_id: uuid.UUID) -> None:
    # serializa mudanças de árvore por coleção (a checagem de ciclo lê a árvore antes de gravar)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('folders:' || $1::text))", str(collection_id))


async def _collection(conn: asyncpg.Connection, p: Principal, name: str) -> uuid.UUID:
    p.require_collection(name)
    cid = await conn.fetchval("SELECT id FROM collections WHERE name = $1", name)
    if cid is None:
        raise NotFound(f"Pasta principal '{name}' não existe.")
    return cid


async def _load_folder(conn: asyncpg.Connection, p: Principal, folder_id: str) -> dict:
    row = await conn.fetchrow(
        """
        SELECT f.id, f.collection_id, c.name AS collection, f.parent_id, f.name
        FROM folders f JOIN collections c ON c.id = f.collection_id WHERE f.id = $1
        """,
        parse_uuid(folder_id, "folder_id"),
    )
    if row is None:
        raise NotFound(f"Pasta {folder_id} não encontrada.")
    p.require_collection(row["collection"])
    return dict(row)  # UUIDs nativos: são comparados e reusados nas queries


async def _parent_in(
    conn: asyncpg.Connection, p: Principal, parent_id: str | None, collection_id: uuid.UUID
) -> uuid.UUID | None:
    if parent_id is None:
        return None
    parent = await _load_folder(conn, p, parent_id)
    if parent["collection_id"] != collection_id:
        raise KBError("A pasta de destino é de outra pasta principal.")
    return parent["id"]


async def ensure_folder_in(p: Principal, folder_id: str, collection: str) -> None:
    """Valida (sem gravar) que a pasta existe e é da coleção — antes de criar/mover a nota."""
    async with pool().acquire() as conn:
        folder = await _load_folder(conn, p, folder_id)
    if folder["collection"] != collection:
        raise KBError("A pasta de destino é de outra pasta principal.")


async def list_folders(p: Principal, collection: str | None = None) -> list[dict]:
    """Pastas que a chave pode ver, com o caminho completo ("A/B/C") dentro da coleção."""
    p.require("read")
    if collection is not None:
        p.require_collection(collection)
    rows = await pool().fetch(
        """
        SELECT f.id, c.name AS collection, f.parent_id, f.name
        FROM folders f JOIN collections c ON c.id = f.collection_id
        WHERE $1::text IS NULL OR c.name = $1
        ORDER BY c.name, lower(f.name)
        """,
        collection,
    )
    items = [r for r in records(rows) if p.can_access_collection(r["collection"])]
    by_id = {f["id"]: f for f in items}

    def path_of(f: dict) -> str:
        names: list[str] = []
        node: dict | None = f
        while node is not None and len(names) < 1000:
            names.append(node["name"])
            node = by_id.get(node["parent_id"]) if node["parent_id"] else None
        return PATH_SEP.join(reversed(names))

    for f in items:
        f["path"] = path_of(f)
    return items


async def folder_subtree_ids(conn: asyncpg.Connection, p: Principal, folder_id: str) -> list[uuid.UUID]:
    """A pasta e todas as subpastas (qualquer profundidade) — filtro "dentro desta pasta" do grafo e da busca."""
    await _load_folder(conn, p, folder_id)  # existe e está no alcance do principal
    rows = await conn.fetch(
        """
        WITH RECURSIVE sub AS (
          SELECT id FROM folders WHERE id = $1
          UNION ALL SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id
        )
        SELECT id FROM sub
        """,
        parse_uuid(folder_id, "folder_id"),
    )
    return [r["id"] for r in rows]


async def location_tree(p: Principal) -> dict:
    """Coleções e pastas com nº de notas ativas (diretas e com subpastas) — seletor de local da dash."""
    p.require("read")
    items = await list_folders(p)
    direct = {
        str(r["folder_id"]): r["n"]
        for r in await pool().fetch(
            "SELECT folder_id, count(*) AS n FROM documents WHERE status = 'active' AND folder_id IS NOT NULL "
            "GROUP BY folder_id"
        )
    }
    children: dict[str | None, list[dict]] = {}
    for f in items:
        children.setdefault(f["parent_id"], []).append(f)
    totals: dict[str, int] = {}

    def total(f: dict) -> int:  # pós-ordem com memo: cada pasta somada uma vez
        if f["id"] not in totals:
            totals[f["id"]] = direct.get(f["id"], 0) + sum(total(c) for c in children.get(f["id"], []))
        return totals[f["id"]]

    for f in items:
        f["documents"] = direct.get(f["id"], 0)
        f["total"] = total(f)
    collections = [{"name": c["name"], "documents": c["documents"]} for c in await documents.list_collections(p)]
    return {"collections": collections, "folders": items}


async def create_folder(p: Principal, collection: str, name: str, parent_id: str | None = None) -> dict:
    p.require("write")
    name = validate_folder_name(name)
    async with pool().acquire() as conn, conn.transaction():
        cid = await _collection(conn, p, collection)
        await _lock_collection(conn, cid)
        parent = await _parent_in(conn, p, parent_id, cid)
        fid = await conn.fetchval(
            "INSERT INTO folders (collection_id, parent_id, name, created_by) VALUES ($1, $2, $3, $4) RETURNING id",
            cid,
            parent,
            name,
            p.actor,
        )
        await audit(conn, p.actor, "folder.create", str(fid), collection=collection, parent_id=parent_id, name=name)
    return {"id": str(fid), "collection": collection, "parent_id": parent_id, "name": name}


async def rename_folder(p: Principal, folder_id: str, name: str) -> dict:
    p.require("write")
    name = validate_folder_name(name)
    async with pool().acquire() as conn, conn.transaction():
        folder = await _load_folder(conn, p, folder_id)
        await conn.execute("UPDATE folders SET name = $2, updated_at = now() WHERE id = $1", folder["id"], name)
        await audit(conn, p.actor, "folder.rename", folder_id, old=folder["name"], new=name)
    return {"id": folder_id, "name": name}


async def move_folder(p: Principal, folder_id: str, collection: str, parent_id: str | None = None) -> dict:
    """Move a pasta (com tudo o que tem dentro) para outra pasta-mãe — inclusive de outra coleção."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        folder = await _load_folder(conn, p, folder_id)
        dest_cid = await _collection(conn, p, collection)
        for cid in sorted({folder["collection_id"], dest_cid}, key=str):  # ordem fixa: sem deadlock
            await _lock_collection(conn, cid)
        parent = await _parent_in(conn, p, parent_id, dest_cid)
        if parent is not None:
            # destino não pode ser a própria pasta nem algo dentro dela
            inside = await conn.fetchval(
                """
                WITH RECURSIVE sub AS (
                  SELECT id FROM folders WHERE id = $1
                  UNION ALL SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id
                )
                SELECT EXISTS (SELECT 1 FROM sub WHERE id = $2)
                """,
                folder["id"],
                parent,
            )
            if inside:
                raise KBError("Não dá para mover uma pasta para dentro dela mesma.")
        try:
            await conn.execute(
                "UPDATE folders SET collection_id = $2, parent_id = $3, updated_at = now() WHERE id = $1",
                folder["id"],
                dest_cid,
                parent,
            )
        except asyncpg.UniqueViolationError as e:
            # ON UPDATE CASCADE leva as notas junto; colide se o destino já tem o mesmo external_id
            raise KBError(f"Uma nota desta pasta já existe em '{collection}' (mesmo external_id).") from e
        await audit(
            conn,
            p.actor,
            "folder.move",
            folder_id,
            source=folder["collection"],
            destination=collection,
            parent_id=parent_id,
        )
    return {"id": folder_id, "collection": collection, "parent_id": parent_id}


async def delete_folder(p: Principal, folder_id: str) -> dict:
    """Remove a pasta sem perder nada: subpastas e notas sobem para a pasta-mãe (ou raiz da coleção)."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        folder = await _load_folder(conn, p, folder_id)
        await _lock_collection(conn, folder["collection_id"])
        fid, parent = folder["id"], folder["parent_id"]
        moved_folders = await conn.execute("UPDATE folders SET parent_id = $2 WHERE parent_id = $1", fid, parent)
        moved_notes = await conn.execute("UPDATE documents SET folder_id = $2 WHERE folder_id = $1", fid, parent)
        await conn.execute("DELETE FROM folders WHERE id = $1", fid)
        await audit(conn, p.actor, "folder.delete", folder_id, collection=folder["collection"], name=folder["name"])
    return {
        "deleted": True,
        "id": folder_id,
        "moved_folders": int(moved_folders.split()[-1]),
        "moved_notes": int(moved_notes.split()[-1]),
    }


# ---------------------------------------------------------------- pasta principal (plan-web-18)
# Agentes e acessos OAuth guardam as coleções permitidas por NOME (lista vazia = todas): renomear
# atualiza essas listas na mesma transação, e excluir é recusado enquanto alguém estiver restrito à
# coleção — tirar o nome da lista transformaria "só esta coleção" em "todas".


async def rename_collection(p: Principal, name: str, new_name: str) -> dict:
    """Renomeia a pasta principal (coleção) e leva o nome novo para quem tem acesso restrito a ela."""
    p.require("admin")
    new_name = validate_collection_name(new_name)
    async with pool().acquire() as conn, conn.transaction():
        cid = await _collection(conn, p, name)
        if new_name == name:
            return {"name": name, "renamed": False}
        if await conn.fetchval("SELECT 1 FROM collections WHERE name = $1 AND id <> $2", new_name, cid):
            raise KBError(f"Já existe uma pasta principal chamada '{new_name}'.")
        await conn.execute("UPDATE collections SET name = $2 WHERE id = $1", cid, new_name)
        agents_updated = await conn.execute(
            "UPDATE agents SET allowed_collections = array_replace(allowed_collections, $1, $2) "
            "WHERE $1 = ANY(allowed_collections)",
            name,
            new_name,
        )
        grants_updated = await conn.execute(
            "UPDATE oauth_grants SET allowed_collections = array_replace(allowed_collections, $1, $2) "
            "WHERE $1 = ANY(allowed_collections)",
            name,
            new_name,
        )
        await audit(conn, p.actor, "collection.rename", str(cid), old=name, new=new_name)
    return {
        "name": new_name,
        "renamed": True,
        "agents_updated": int(agents_updated.split()[-1]),
        "grants_updated": int(grants_updated.split()[-1]),
    }


async def delete_collection(p: Principal, name: str) -> dict:
    """Exclui a pasta principal (coleção) — irreversível.

    Só sem notas ativas (o resto — notas arquivadas, versões, pastas — vai junto, em cascata no banco)
    e só quando nenhum agente ativo ou acesso OAuth vigente está restrito a ela.
    """
    p.require("admin")
    async with pool().acquire() as conn, conn.transaction():
        cid = await _collection(conn, p, name)
        await _lock_collection(conn, cid)
        active = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE collection_id = $1 AND status = 'active'", cid
        )
        if active:
            raise KBError(
                f"A pasta principal '{name}' ainda tem {active} nota(s) ativa(s). Arquive ou mova antes de excluir."
            )
        agents_using = [
            r["slug"]
            for r in await conn.fetch(
                "SELECT slug FROM agents WHERE status = 'active' AND $1 = ANY(allowed_collections) ORDER BY slug", name
            )
        ]
        grants_using = await conn.fetchval(
            "SELECT count(*) FROM oauth_grants WHERE revoked_at IS NULL AND $1 = ANY(allowed_collections)", name
        )
        if agents_using or grants_using:
            who = []
            if agents_using:
                who.append("agentes " + ", ".join(agents_using))
            if grants_using:
                who.append(f"{grants_using} acesso(s) OAuth")
            raise KBError(
                f"A pasta principal '{name}' é a área permitida de {' e '.join(who)}. "
                "Tire-a do acesso deles antes de excluir."
            )
        archived = await conn.fetchval("SELECT count(*) FROM documents WHERE collection_id = $1", cid)
        folder_count = await conn.fetchval("SELECT count(*) FROM folders WHERE collection_id = $1", cid)
        await conn.execute("DELETE FROM collections WHERE id = $1", cid)
        await audit(
            conn, p.actor, "collection.delete", str(cid), name=name, archived_notes=archived, folders=folder_count
        )
    return {"deleted": True, "name": name, "archived_notes_deleted": archived, "folders_deleted": folder_count}


async def place_document(p: Principal, document_id: str, folder_id: str | None) -> dict:
    """Põe a nota numa pasta da própria coleção (None = raiz da coleção)."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        doc = await conn.fetchrow(
            """
            SELECT d.id, d.collection_id, c.name AS collection, d.status
            FROM documents d JOIN collections c ON c.id = d.collection_id WHERE d.id = $1
            """,
            parse_uuid(document_id, "document_id"),
        )
        if doc is None:
            raise NotFound(f"Documento {document_id} não encontrado.")
        p.require_collection(doc["collection"])
        if doc["status"] != "active":
            raise KBError("Documento arquivado não pode ser movido.")
        target = await _parent_in(conn, p, folder_id, doc["collection_id"])
        await conn.execute("UPDATE documents SET folder_id = $2 WHERE id = $1", doc["id"], target)
        await audit(conn, p.actor, "document.place", document_id, collection=doc["collection"], folder_id=folder_id)
    return {"document_id": document_id, "collection": doc["collection"], "folder_id": folder_id}


# ---------------------------------------------------------------- caminhos (API/MCP, plan-web-16)
# Agentes e integrações endereçam pastas por caminho dentro da coleção ("Clientes/Acme/Reuniões"),
# comparando nomes sem diferenciar maiúsculas. Nome repetido no mesmo nível: vale a pasta mais antiga.


async def _child(
    conn: asyncpg.Connection, cid: uuid.UUID, parent: uuid.UUID | None, name: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, name FROM folders
        WHERE collection_id = $1 AND parent_id IS NOT DISTINCT FROM $2 AND lower(name) = lower($3)
        ORDER BY created_at, id LIMIT 1
        """,
        cid,
        parent,
        name,
    )


async def _resolve_path(
    conn: asyncpg.Connection, p: Principal, cid: uuid.UUID, collection: str, path: str, create: bool
) -> tuple[uuid.UUID, str, list[str]]:
    """Pasta do caminho (criando os níveis que faltam se create=True). Devolve (id, caminho canônico,
    caminhos criados) — canônico = com os nomes como estão gravados (a busca ignora maiúsculas)."""
    parent: uuid.UUID | None = None
    created: list[str] = []
    walked: list[str] = []
    for name in split_folder_path(path):
        existing = await _child(conn, cid, parent, name)
        if existing is not None:
            walked.append(existing["name"])
            parent = existing["id"]
            continue
        walked.append(name)
        if not create:
            raise NotFound(f"Pasta '{PATH_SEP.join(walked)}' não existe em '{collection}'.")
        parent = await conn.fetchval(
            "INSERT INTO folders (collection_id, parent_id, name, created_by) VALUES ($1, $2, $3, $4) RETURNING id",
            cid,
            parent,
            name,
            p.actor,
        )
        created.append(PATH_SEP.join(walked))
        await audit(conn, p.actor, "folder.create", str(parent), collection=collection, path=PATH_SEP.join(walked))
    assert parent is not None  # split_folder_path nunca devolve lista vazia
    return parent, PATH_SEP.join(walked), created


async def ensure_folder_path(p: Principal, collection: str, path: str) -> dict:
    """Garante que o caminho exista na coleção (idempotente: só cria os níveis que faltam)."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        cid = await _collection(conn, p, collection)
        await _lock_collection(conn, cid)
        fid, canonical, created = await _resolve_path(conn, p, cid, collection, path, create=True)
    return {
        "id": str(fid),
        "collection": collection,
        "path": canonical,
        "created": created,
    }


async def _folder_path(conn: asyncpg.Connection, folder_id: uuid.UUID | None) -> str | None:
    if folder_id is None:
        return None
    names = await conn.fetchval(
        """
        WITH RECURSIVE up AS (
          SELECT id, parent_id, name, 0 AS depth FROM folders WHERE id = $1
          UNION ALL SELECT f.id, f.parent_id, f.name, up.depth + 1 FROM folders f JOIN up ON f.id = up.parent_id
        )
        SELECT array_agg(name ORDER BY depth DESC) FROM up
        """,
        folder_id,
    )
    return PATH_SEP.join(names) if names else None


async def place_document_at_path(p: Principal, document_id: str, folder_path: str | None) -> dict:
    """Põe a nota no caminho dentro da própria coleção (criando as pastas que faltam); None = raiz."""
    p.require("write")
    async with pool().acquire() as conn, conn.transaction():
        doc = await conn.fetchrow(
            """
            SELECT d.id, d.collection_id, c.name AS collection, d.status
            FROM documents d JOIN collections c ON c.id = d.collection_id WHERE d.id = $1
            """,
            parse_uuid(document_id, "document_id"),
        )
        if doc is None:
            raise NotFound(f"Documento {document_id} não encontrado.")
        p.require_collection(doc["collection"])
        if doc["status"] != "active":
            raise KBError("Documento arquivado não pode ser movido.")
        target: uuid.UUID | None = None
        if folder_path is not None and folder_path.strip():
            await _lock_collection(conn, doc["collection_id"])
            target, _, _ = await _resolve_path(
                conn, p, doc["collection_id"], doc["collection"], folder_path, create=True
            )
        await conn.execute("UPDATE documents SET folder_id = $2 WHERE id = $1", doc["id"], target)
        path = await _folder_path(conn, target)
        await audit(conn, p.actor, "document.place", document_id, collection=doc["collection"], folder_path=path)
    return {"document_id": document_id, "collection": doc["collection"], "folder_path": path}


async def get_document_with_folder(p: Principal, document_id: str) -> dict:
    """get_document do core + onde a nota está (folder_id / folder_path; None = raiz da coleção)."""
    doc = await documents.get_document(p, document_id)
    async with pool().acquire() as conn:
        fid = await conn.fetchval(
            "SELECT folder_id FROM documents WHERE id = $1", parse_uuid(document_id, "document_id")
        )
        doc["folder_id"] = str(fid) if fid else None
        doc["folder_path"] = await _folder_path(conn, fid)
    return doc


async def add_document_in_folder(p: Principal, folder_path: str | None, **kwargs: Any) -> dict:
    """add_document do core e, se criou e veio caminho, põe a nota na pasta (criando os níveis)."""
    if folder_path:
        split_folder_path(folder_path)  # valida o caminho antes de gravar qualquer coisa
    result = await documents.add_document(p, **kwargs)
    if folder_path and result.get("created"):
        placed = await place_document_at_path(p, result["document_id"], folder_path)
        result["folder_path"] = placed["folder_path"]
    return result


async def upsert_document_in_folder(p: Principal, folder_path: str | None, **kwargs: Any) -> dict:
    """upsert_document do core; com caminho, a nota fica (ou passa a ficar) nessa pasta."""
    if folder_path:
        split_folder_path(folder_path)
    result = await documents.upsert_document(p, **kwargs)
    if folder_path:
        placed = await place_document_at_path(p, result["document_id"], folder_path)
        result["folder_path"] = placed["folder_path"]
    return result


async def move_document_to(p: Principal, document_id: str, collection: str, folder_path: str | None = None) -> dict:
    """Move a nota para a coleção e, opcionalmente, para um caminho de pastas dentro dela."""
    if folder_path:
        split_folder_path(folder_path)
    moved = await documents.move_document(p, document_id, collection)
    placed = await place_document_at_path(p, document_id, folder_path)
    return {**moved, "moved": True, "folder_path": placed["folder_path"]}
