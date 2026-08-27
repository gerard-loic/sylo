import httpx
from fastapi import FastAPI

from app.mcp.catalog import McpOperation


async def call_operation(
    app: FastAPI, operation: McpOperation, arguments: dict, *, token: str | None
) -> tuple[int, dict]:
    """Rejoue une opération en interne sur `app` via un client ASGI (pas de vrai
    aller-retour réseau) : auth (`enforce_auth`), permissions, soft-delete, erreurs...
    tout repasse par le code réel de la route REST, rien n'est dupliqué côté MCP."""
    arguments = dict(arguments)
    path = operation.path
    for name in operation.path_params:
        if name in arguments:
            path = path.replace(f"{{{name}}}", str(arguments.pop(name)))

    query = {name: arguments.pop(name) for name in list(operation.query_params) if name in arguments}
    body = {name: arguments[name] for name in operation.body_params if name in arguments}

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp-internal") as client:
        response = await client.request(
            operation.method,
            path,
            params=query,
            json=body if operation.body_params else None,
            headers=headers,
        )

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    return response.status_code, payload
