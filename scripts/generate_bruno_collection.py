"""Génère une collection Bruno (fichier .zip) à partir des routes réellement
enregistrées sur l'app FastAPI : une requête par route (list/get/create/update/delete
+ routes custom comme `/users/login`), rangées par entité (tag de la route, ex:
`user`, `theme`).

`POST /users/login` récupère le token retourné et le stocke dans la variable
d'environnement Bruno `token` (`bru.setEnvVar`) via un script post-réponse. Toutes
les autres routes utilisent ce token en authentification Bearer (`{{token}}`) — y
compris celles techniquement publiques côté serveur (ex: `PUT /users/{id}` gère aussi
en interne la définition du mot de passe initial) : envoyer le header ne gêne jamais
une route qui n'en a pas besoin, alors que l'omettre casserait celles qui en ont
besoin malgré leur statut "public".

Le corps d'exemple des requêtes POST/PUT est déduit du validateur Pydantic réel de la
route (`route.body_field`) : uniquement les champs requis, une valeur d'exemple par
type (chaîne vide, 0, false, ...). La route HTTP `QUERY` (brouillon IETF, non
supportée par Bruno) est omise ; son équivalent `POST /<entité>/query` est conservé.

Usage:
    python scripts/generate_bruno_collection.py
    python scripts/generate_bruno_collection.py --output bruno/wakaru_api.zip
    python scripts/generate_bruno_collection.py --base-url http://localhost:8000
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.routing import APIRoute  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Nom de fonction (routes.py) -> nom de requête convivial + priorité d'affichage.
# La méthode HTTP `QUERY` (brouillon IETF) n'est pas un verbe supporté par Bruno :
# son équivalent fonctionnel `POST /<entité>/query` (query_items_fallback) suffit.
_SKIP_FUNCS = {"query_items"}
_FRIENDLY_NAMES = {
    "list_items": ("List", 0),
    "get_item": ("Get", 1),
    "create_item": ("Create", 2),
    "update_item": ("Update", 3),
    "delete_item": ("Delete", 4),
    "query_items_fallback": ("Query", 5),
}

_LOGIN_POST_SCRIPT = 'let data = res.body;\nbru.setEnvVar("token", data.data.token);'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Génère une collection Bruno (zip) à partir des routes enregistrées."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "wakaru_api_bruno_collection.zip",
        metavar="FILE",
        help="Chemin du zip généré (défaut: wakaru_api_bruno_collection.zip à la racine).",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        metavar="URL",
        help="Valeur par défaut de la variable d'environnement baseUrl (défaut: http://localhost:8000).",
    )
    parser.add_argument(
        "--collection-name",
        default="Wakaru API",
        metavar="NAME",
        help="Nom de la collection Bruno (défaut: 'Wakaru API').",
    )
    return parser.parse_args()


def _friendly_name(func_name: str) -> tuple[str, int]:
    if func_name in _FRIENDLY_NAMES:
        return _FRIENDLY_NAMES[func_name]
    return func_name.replace("_", " ").title(), 6


def _example_value(schema: dict, defs: dict) -> object:
    if "$ref" in schema:
        ref_name = schema["$ref"].rsplit("/", 1)[-1]
        return _example_value(defs.get(ref_name, {}), defs)
    if "anyOf" in schema:
        for sub in schema["anyOf"]:
            if sub.get("type") != "null":
                return _example_value(sub, defs)
        return None
    schema_type = schema.get("type")
    if schema_type == "string":
        return "2024-01-01T00:00:00" if schema.get("format") == "date-time" else ""
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0
    if schema_type == "boolean":
        return False
    if schema_type == "array":
        items_schema = schema.get("items")
        return [_example_value(items_schema, defs)] if items_schema else []
    if schema_type == "object":
        return {}
    return None


def build_example_body(validator_cls) -> dict:
    """Corps d'exemple déduit du validateur Pydantic : uniquement les champs requis
    (les champs optionnels n'ont pas besoin d'être fournis pour que la requête soit
    valide)."""
    schema = validator_cls.model_json_schema()
    defs = schema.get("$defs", {})
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    return {name: _example_value(properties[name], defs) for name in required if name in properties}


def render_request(
    *,
    name: str,
    seq: int,
    method: str,
    url: str,
    body: dict | None,
    public: bool,
    post_script: str | None = None,
) -> str:
    lines = [
        "meta {",
        f"  name: {name}",
        "  type: http",
        f"  seq: {seq}",
        "}",
        "",
        f"{method.lower()} {{",
        f"  url: {url}",
        f"  body: {'json' if body is not None else 'none'}",
        f"  auth: {'none' if public else 'bearer'}",
        "}",
    ]
    if not public:
        lines += ["", "auth:bearer {", "  token: {{token}}", "}"]
    if body is not None:
        lines += ["", "body:json {"]
        lines += [f"  {line}" for line in json.dumps(body, indent=2, ensure_ascii=False).splitlines()]
        lines += ["}"]
    if post_script:
        lines += ["", "script:post-response {"]
        lines += [f"  {line}" for line in post_script.splitlines()]
        lines += ["}"]
    lines.append("")
    return "\n".join(lines)


def collect_requests(app, base_url: str) -> dict[str, list[tuple[int, str, str]]]:
    """Retourne {dossier: [(priorité, nom_fichier, contenu), ...]}."""
    folders: dict[str, list[tuple[int, str, str]]] = {}

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        func_name = route.name
        if func_name in _SKIP_FUNCS:
            continue
        methods = sorted(route.methods - {"HEAD", "OPTIONS"})
        if not methods:
            continue

        folder = route.tags[0] if route.tags else "misc"
        display_name, priority = _friendly_name(func_name)
        # Seule /users/login est sans auth : le token n'existe pas encore avant elle.
        # Toutes les autres routes portent le Bearer, y compris celles techniquement
        # publiques côté serveur (voir docstring du module).
        is_login = func_name == "login"

        body = None
        if route.body_field is not None:
            validator_cls = route.body_field.field_info.annotation
            body = build_example_body(validator_cls)

        url = "{{baseUrl}}" + route.path.replace("{item_id}", "1")

        post_script = _LOGIN_POST_SCRIPT if is_login else None

        for method in methods:
            content = render_request(
                name=display_name,
                seq=0,  # renseigné après tri, voir plus bas
                method=method,
                url=url,
                body=body if method in ("POST", "PUT", "PATCH") else None,
                public=is_login,
                post_script=post_script,
            )
            file_name = display_name.replace(" ", "")
            folders.setdefault(folder, []).append((priority, file_name, content))

    # Numérote les requêtes de chaque dossier dans un ordre stable (List, Get,
    # Create, Update, Delete, Query, puis le reste par ordre alphabétique).
    for folder, entries in folders.items():
        entries.sort(key=lambda e: (e[0], e[1]))
        renumbered = []
        for seq, (_priority, file_name, content) in enumerate(entries, start=1):
            content = content.replace("seq: 0", f"seq: {seq}", 1)
            renumbered.append((seq, file_name, content))
        folders[folder] = renumbered

    return folders


def build_zip(app, args: argparse.Namespace) -> None:
    folders = collect_requests(app, args.base_url)
    root_folder = args.collection_name.lower().replace(" ", "_")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as zf:
        bruno_json = {
            "version": "1",
            "name": args.collection_name,
            "type": "collection",
            "ignore": ["node_modules", ".git"],
        }
        zf.writestr(f"{root_folder}/bruno.json", json.dumps(bruno_json, indent=2) + "\n")
        zf.writestr(
            f"{root_folder}/environments/Local.bru",
            f"vars {{\n  baseUrl: {args.base_url}\n  token: \n}}\n",
        )
        for folder, entries in sorted(folders.items()):
            for seq, file_name, content in entries:
                zf.writestr(f"{root_folder}/{folder}/{file_name}.bru", content)

    total_requests = sum(len(entries) for entries in folders.values())
    print(f"Dossiers (entités) : {len(folders)}")
    for folder in sorted(folders):
        print(f"  - {folder} : {len(folders[folder])} requête(s)")
    print(f"Total requêtes      : {total_requests}")
    print(f"\nCollection écrite : {args.output}")


def main() -> None:
    args = parse_args()

    from app.main import app

    build_zip(app, args)


if __name__ == "__main__":
    main()
