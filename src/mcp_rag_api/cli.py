"""Linha de comando: servir (HTTP/stdio), migrar, criar chave admin, usuários da dash, sincronizar agentes, limpeza."""

import argparse
import asyncio
import getpass
from pathlib import Path

from . import db


async def _create_key(label: str, scopes: list[str]) -> dict:
    from .security import create_api_key

    await db.run_migrations()
    await db.init_pool()
    try:
        async with db.pool().acquire() as conn:
            return await create_api_key(conn, label=label, scopes=scopes)
    finally:
        await db.close_pool()


async def _create_user(username: str, role: str, password: str) -> dict:
    from .core import dash_auth

    await db.run_migrations()
    await db.init_pool()
    try:
        return await dash_auth.create_user(username, password, role)
    finally:
        await db.close_pool()


async def _sync_agents(out_dir: Path) -> list[Path]:
    """Gera .claude/agents/<slug>.md a partir do banco (fonte da verdade continua sendo o banco)."""
    await db.init_pool()
    try:
        rows = await db.pool().fetch(
            "SELECT slug, name, description, system_prompt FROM agents WHERE status = 'active' ORDER BY slug"
        )
    finally:
        await db.close_pool()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for r in rows:
        description = (r["description"] or r["name"]).replace("\n", " ")
        path = out_dir / f"{r['slug']}.md"
        path.write_text(
            f"---\nname: {r['slug']}\ndescription: {description}\n---\n\n"
            f"<!-- Gerado por `mcp-rag-api sync-agents`. Edite pelo MCP (update_agent), não aqui. -->\n\n"
            f'No início, chame a tool load_agent com agent_slug="{r["slug"]}" para carregar memórias, '
            f"último resumo e tarefas. Ao terminar, chame save_session.\n\n{r['system_prompt']}\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


async def _cleanup() -> dict:
    from .core.memory import cleanup_memories

    await db.init_pool()
    try:
        return await cleanup_memories()
    finally:
        await db.close_pool()


async def _reindex() -> dict:
    from .core.reindex import reindex_all

    await db.init_pool()
    try:
        return await reindex_all(progress=print)
    finally:
        await db.close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp-rag-api")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="API REST + MCP (streamable HTTP em /mcp)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    sub.add_parser("stdio", help="MCP via stdio (usa KB_API_KEY)")
    sub.add_parser("migrate", help="Aplica migrações pendentes")

    key = sub.add_parser("create-key", help="Cria uma chave humana (padrão: admin)")
    key.add_argument("--label", default="admin")
    key.add_argument("--scopes", nargs="+", default=["admin"])

    user = sub.add_parser("create-user", help="Cria um usuário da dashboard (primeiro admin: --role admin)")
    user.add_argument("--username", required=True)
    user.add_argument("--role", choices=["admin", "viewer"], default="viewer")
    user.add_argument("--password", help="Se omitido, pede interativamente (sem eco)")

    sync = sub.add_parser("sync-agents", help="Gera .claude/agents/<slug>.md a partir do banco")
    sync.add_argument("--out", type=Path, default=Path(".claude/agents"))

    sub.add_parser("cleanup", help="Remove memórias expiradas")
    sub.add_parser("reindex", help="Recalcula todos os embeddings com o provedor atual (após trocar de provedor)")

    args = parser.parse_args()
    match args.cmd:
        case "serve":
            import uvicorn

            uvicorn.run("mcp_rag_api.main:app", host=args.host, port=args.port, reload=args.reload)
        case "stdio":
            from .mcp_server import mcp

            mcp.run("stdio")
        case "migrate":
            applied = asyncio.run(db.run_migrations())
            print("Aplicadas:", ", ".join(applied) if applied else "nenhuma (já atualizado)")
        case "create-key":
            result = asyncio.run(_create_key(args.label, args.scopes))
            print(f"Chave criada ({', '.join(args.scopes)}). Guarde agora, ela não será exibida de novo:\n")
            print(result["api_key"])
        case "create-user":
            password = args.password
            if not password:
                password = getpass.getpass("Senha: ")
                if getpass.getpass("Repita a senha: ") != password:
                    raise SystemExit("Senhas não conferem.")
            result = asyncio.run(_create_user(args.username, args.role, password))
            print(f"Usuário '{result['username']}' criado com role '{result['role']}'.")
        case "sync-agents":
            for path in asyncio.run(_sync_agents(args.out)):
                print("escrito:", path)
        case "cleanup":
            print(asyncio.run(_cleanup()))
        case "reindex":
            result = asyncio.run(_reindex())
            print(f"Reindexado: {result['documents']} documento(s), {result['memories']} memória(s).")


if __name__ == "__main__":
    main()
