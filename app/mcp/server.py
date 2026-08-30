import json
from pathlib import Path

import mcp.types as types
from fastapi import FastAPI
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request

from app.auth import resolve_user_id, user_has_permission
from app.config import get_settings
from app.database import SessionLocal
from app.exceptions import UnauthorizedError
from app.mcp.catalog import McpOperation, build_catalog, parse_exclude_file
from app.mcp.execution import call_operation


def _bearer_token(request: Request | None) -> str | None:
    """Le token Bearer est porté par l'en-tête `Authorization` de la requête HTTP MCP
    en cours (chaque appel d'outil est une requête HTTP indépendante en mode
    `stateless`) — mêmes règles que côté REST, aucun état de session à maintenir côté
    serveur MCP."""
    if request is None:
        return None
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def _resolve_user_id_or_none(token: str | None) -> int | None:
    if token is None:
        return None
    db = SessionLocal()
    try:
        return resolve_user_id(db, token)
    except UnauthorizedError:
        return None
    finally:
        db.close()


def _raise_on_error(status: int, payload: dict) -> None:
    if status < 400:
        return
    message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
    raise RuntimeError(message or json.dumps(payload, ensure_ascii=False))


def build_session_manager(app: FastAPI) -> StreamableHTTPSessionManager:
    """Construit le serveur MCP bas-niveau et son gestionnaire de sessions HTTP. À
    appeler une fois `register_entities()` exécuté (le catalogue est dérivé des
    routes réellement enregistrées, voir `catalog.build_catalog`)."""
    exclude_routes: frozenset[str] = frozenset()
    exclude_pairs: frozenset[tuple[str, str]] = frozenset()
    exclude_prefixes: frozenset[str] = frozenset()
    exclude_file = get_settings().mcp_exclude_file
    if exclude_file:
        routes, pairs, prefixes = parse_exclude_file(Path(exclude_file))
        exclude_routes, exclude_pairs, exclude_prefixes = (
            frozenset(routes),
            frozenset(pairs),
            frozenset(prefixes),
        )

    catalog = build_catalog(
        app,
        exclude_routes=exclude_routes,
        exclude_pairs=exclude_pairs,
        exclude_prefixes=exclude_prefixes,
    )
    operations_by_name: dict[str, McpOperation] = {op.tool_name: op for op in catalog}

    server = Server(
        "sylo-api",
        instructions=(
            "Passez votre token Bearer (obtenu via POST /users/login) dans l'en-tête "
            "Authorization de la connexion à ce serveur MCP. La liste d'outils "
            "disponibles reflète les permissions réelles de cet utilisateur ; sans "
            "token, seuls les outils publics (dont 'login_user') sont visibles."
        ),
    )

    def _tool_from_operation(op: McpOperation) -> types.Tool:
        return types.Tool(
            name=op.tool_name,
            description=op.description,
            inputSchema=op.input_schema,
            outputSchema=op.output_schema,
        )

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        token = _bearer_token(server.request_context.request)
        user_id = _resolve_user_id_or_none(token)

        tools = []
        db = SessionLocal()
        try:
            for op in catalog:
                if op.permission_uid is None:
                    tools.append(_tool_from_operation(op))
                elif user_id is not None and user_has_permission(db, user_id, op.permission_uid):
                    tools.append(_tool_from_operation(op))
        finally:
            db.close()
        return tools

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict):
        operation = operations_by_name.get(name)
        if operation is None:
            raise ValueError(f"Outil inconnu : {name}")

        token = _bearer_token(server.request_context.request)
        if operation.permission_uid is not None and token is None:
            raise RuntimeError(
                "Authentification requise : passez un token Bearer (POST /users/login) "
                "dans l'en-tête Authorization de la connexion MCP."
            )

        status, payload = await call_operation(app, operation, arguments, token=token)
        _raise_on_error(status, payload)
        return payload

    return StreamableHTTPSessionManager(app=server, stateless=True)
