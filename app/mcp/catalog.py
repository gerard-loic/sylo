from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.auth import is_public_route
from app.crud.json_schema import build_entity_read_schema
from app.crud.model import get_all_models

# Une route custom (ex: POST /users/login) n'a pas de nom générique : son verbe
# devient directement le nom du "verbe" ci-dessous (ex: tool `login_user`). Ces alias
# ne couvrent que les 5 opérations CRUD génériques + leur repli QUERY -> POST/query
# (voir `app/crud/routes.py`), pour donner des noms d'outils lisibles.
_VERB_ALIASES = {
    "list_items": "list",
    "get_item": "get",
    "create_item": "create",
    "update_item": "update",
    "delete_item": "delete",
    "query_items_fallback": "search",
}

_LIST_LIKE_ROUTES = {"list_items", "query_items_fallback"}
_SINGLE_ITEM_ROUTES = {"get_item", "create_item", "update_item"}


@dataclass(frozen=True)
class McpOperation:
    """Une opération MCP dynamiquement dérivée d'une route FastAPI réellement
    enregistrée (voir `build_catalog`). `permission_uid` suit exactement la
    convention `app.auth.authenticate()` (`"<path>_<MÉTHODE>"`) ; `None` si la route
    est publique (`mark_public`), auquel cas l'outil est toujours listé.
    """

    tool_name: str
    description: str
    path: str
    method: str
    permission_uid: str | None
    path_params: frozenset[str]
    query_params: frozenset[str]
    body_params: frozenset[str]
    input_schema: dict
    output_schema: dict | None


def _resolve_schema(schema: dict, components: dict) -> dict:
    """Déréférence récursivement les `$ref` OpenAPI (`#/components/schemas/X`) pour
    obtenir un schéma JSON autoportant, exploitable tel quel comme `inputSchema` MCP
    (qui n'a pas accès au document OpenAPI complet)."""
    if "$ref" in schema:
        ref_name = schema["$ref"].rsplit("/", 1)[-1]
        return _resolve_schema(components.get(ref_name, {}), components)

    resolved = dict(schema)
    if "properties" in resolved:
        resolved["properties"] = {
            key: _resolve_schema(value, components) for key, value in resolved["properties"].items()
        }
    if "items" in resolved:
        resolved["items"] = _resolve_schema(resolved["items"], components)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in resolved:
            resolved[key] = [_resolve_schema(sub, components) for sub in resolved[key]]
    return resolved


def _build_input_schema(
    operation: dict, components: dict
) -> tuple[dict, frozenset[str], frozenset[str], frozenset[str]]:
    """Aplati les paramètres path/query et le corps JSON (déjà typés par le
    validateur Pydantic réel de la route) en un unique schéma d'entrée, plus naturel
    pour un appelant MCP qu'une structure imbriquée path/query/body."""
    properties: dict[str, dict] = {}
    required: list[str] = []
    path_params: set[str] = set()
    query_params: set[str] = set()
    body_params: set[str] = set()

    for param in operation.get("parameters", []):
        name = param["name"]
        schema = _resolve_schema(param.get("schema", {}), components)
        if param.get("description") and "description" not in schema:
            schema["description"] = param["description"]
        properties[name] = schema
        if param.get("required"):
            required.append(name)
        if param.get("in") == "path":
            path_params.add(name)
        else:
            query_params.add(name)

    request_body = operation.get("requestBody")
    if request_body:
        body_schema = _resolve_schema(request_body["content"]["application/json"]["schema"], components)
        for name, sub_schema in (body_schema.get("properties") or {}).items():
            properties[name] = sub_schema
            body_params.add(name)
        for name in body_schema.get("required", []):
            if name not in required:
                required.append(name)

    input_schema: dict = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        input_schema["required"] = required
    return input_schema, frozenset(path_params), frozenset(query_params), frozenset(body_params)


def _build_output_schema(route_name: str, entity: str, models: dict) -> dict | None:
    model = models.get(entity)
    if model is None:
        return None
    read_schema = build_entity_read_schema(model)

    if route_name in _LIST_LIKE_ROUTES:
        return {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "data": {"type": "array", "items": read_schema},
                "meta": {"type": "object"},
            },
        }
    if route_name in _SINGLE_ITEM_ROUTES:
        return {"type": "object", "properties": {"success": {"type": "boolean"}, "data": read_schema}}
    if route_name == "delete_item":
        return {"type": "object", "properties": {"success": {"type": "boolean"}, "data": {"type": "null"}}}
    return None


def _normalize_route(route: str) -> str:
    route = route.strip()
    if route and not route.startswith("/"):
        route = "/" + route
    return route


def parse_exclude_file(path: Path) -> tuple[set[str], set[tuple[str, str]], set[str]]:
    """Même syntaxe que `--exclude-file` dans `scripts/sync_permissions.py` /
    `generate_entities.py` (une entrée par ligne, lignes vides et `#...` ignorées),
    plus un joker `:*` propre au catalogue MCP (absent des autres scripts) :

        users               # exclut toutes les méthodes de la route /users
        users/login:POST    # exclut uniquement cette méthode
        roles:*             # exclut /roles et tout ce qui est sous /roles/...
                             # (/roles/{item_id}, /roles/query, une future route
                             # custom...), utile pour retirer une entité entière.

    Réutilisé tel quel pour exclure des routes du catalogue MCP (voir
    `WAKARU_MCP_EXCLUDE_FILE`, `app/config.py`).
    """
    if not path.is_file():
        raise FileNotFoundError(f"WAKARU_MCP_EXCLUDE_FILE : fichier introuvable : {path}")

    routes: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    prefixes: set[str] = set()
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        route, sep, method = line.rpartition(":")
        if sep:
            if not route or not method:
                raise ValueError(
                    f"WAKARU_MCP_EXCLUDE_FILE : ligne {lineno} invalide (attendu ROUTE:METHOD) : {raw_line!r}"
                )
            if method.strip() == "*":
                prefixes.add(_normalize_route(route))
            else:
                pairs.add((_normalize_route(route), method.strip().upper()))
        else:
            routes.add(_normalize_route(line))
    return routes, pairs, prefixes


def build_catalog(
    app: FastAPI,
    *,
    exclude_routes: frozenset[str] = frozenset(),
    exclude_pairs: frozenset[tuple[str, str]] = frozenset(),
    exclude_prefixes: frozenset[str] = frozenset(),
) -> list[McpOperation]:
    """Construit le catalogue d'outils MCP à partir des routes réellement enregistrées
    sur `app` (même principe que `scripts/generate_bruno_collection.py`) : une
    opération par route, schéma d'entrée/sortie dérivé du document OpenAPI généré par
    FastAPI lui-même (`app.openapi()`) — jamais réécrit à la main, toujours en phase
    avec les validateurs Pydantic réels. À appeler une fois `register_entities()`
    exécuté (toutes les routes doivent déjà exister).

    `exclude_routes`/`exclude_pairs`/`exclude_prefixes` (voir `parse_exclude_file`)
    retirent une route entière, une seule méthode, ou tout un préfixe (ex: `/roles` et
    tout ce qui est sous `/roles/...`) du catalogue : aucun outil MCP n'est généré
    pour elles, elles restent par ailleurs inchangées côté REST.
    """
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    models = get_all_models()
    paths = spec.get("paths", {})

    operations: list[McpOperation] = []
    seen_names: set[str] = set()

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = route.methods - {"HEAD", "OPTIONS"}
        if len(methods) != 1:
            continue
        method = next(iter(methods))
        if method == "QUERY":
            # Méthode HTTP non standard (brouillon IETF) : son équivalent fonctionnel
            # `POST /<entité>/query` (query_items_fallback) est déjà couvert.
            continue

        if route.path in exclude_routes or (route.path, method) in exclude_pairs:
            continue
        if any(route.path == prefix or route.path.startswith(prefix + "/") for prefix in exclude_prefixes):
            continue

        operation = paths.get(route.path, {}).get(method.lower())
        if operation is None:
            continue

        entity = route.tags[0] if route.tags else "api"
        verb = _VERB_ALIASES.get(route.name, route.name)
        tool_name = f"{verb}_{entity}"
        suffix = 2
        while tool_name in seen_names:
            tool_name = f"{verb}_{entity}_{suffix}"
            suffix += 1
        seen_names.add(tool_name)

        permission_uid = None if is_public_route(route.path, method) else f"{route.path}_{method}".upper()
        input_schema, path_params, query_params, body_params = _build_input_schema(operation, components)
        output_schema = _build_output_schema(route.name, entity, models)

        summary = operation.get("summary") or route.name.replace("_", " ").title()
        description = f"{summary} ({method} {route.path})."

        operations.append(
            McpOperation(
                tool_name=tool_name,
                description=description,
                path=route.path,
                method=method,
                permission_uid=permission_uid,
                path_params=path_params,
                query_params=query_params,
                body_params=body_params,
                input_schema=input_schema,
                output_schema=output_schema,
            )
        )

    return operations
