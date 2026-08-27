"""Crée dans la table `permissions` une permission par route/méthode existante.

Parcourt les routes réellement enregistrées sur l'app FastAPI (donc toutes les
entités CRUD + routes custom), et insère en base celles qui n'ont pas encore de
permission (en différentiel, sur la base de `uid`). N'écrit jamais sur une
permission déjà existante.

Pour chaque route/méthode :
    uid    = "<ROUTE>_<METHODE>" en majuscules (ex: "/USERS/{ITEM_ID}_DELETE")
    route  = le chemin de la route tel que défini par FastAPI
    method = la méthode HTTP en majuscules

Usage:
    python scripts/sync_permissions.py
    python scripts/sync_permissions.py --dry-run
    python scripts/sync_permissions.py --exclude-route /health
    python scripts/sync_permissions.py --exclude-route /users --exclude-route /roles
    python scripts/sync_permissions.py --exclude "/permissions/{item_id}:DELETE"
    python scripts/sync_permissions.py --config-file generate-config.json

Fichier --config-file (JSON) :
    - `exclude.permissions` : routes/méthodes à ne pas créer. Même syntaxe que
      --exclude-route / --exclude : "users" exclut toutes les méthodes,
      "users/login:POST" exclut uniquement cette méthode.
    - `additionnal.permissions` : permissions "libres" (non liées à une route) à
      créer en plus, une par uid, ex: "KNOWLEDGE_UNLIMITED".

    {
        "exclude": {"permissions": ["users", "users/login:POST"]},
        "additionnal": {"permissions": ["KNOWLEDGE_UNLIMITED"]}
    }
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.routing import APIRoute  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crée les permissions manquantes pour chaque route/méthode enregistrée."
    )
    parser.add_argument(
        "--exclude-route",
        action="append",
        default=[],
        metavar="ROUTE",
        help=(
            "Exclut une route entière (toutes méthodes confondues). Chemin tel que "
            "défini par FastAPI, ex: /users ou /users/{item_id}. Répétable, ou "
            "plusieurs routes séparées par des virgules."
        ),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="ROUTE:METHOD",
        help=(
            "Exclut une route/méthode précise, ex: '/permissions/{item_id}:DELETE'. "
            "Répétable, ou plusieurs paires séparées par des virgules."
        ),
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Fichier de configuration JSON. `exclude.permissions` liste les "
            "routes/méthodes à ne pas créer (même syntaxe que --exclude-route / "
            "--exclude : 'users' ou 'users/login:POST'). `additionnal.permissions` "
            "liste des permissions libres à créer en plus (un uid par entrée, ex: "
            "'KNOWLEDGE_UNLIMITED'). Exemple : generate-config.json."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit rien en base : affiche seulement ce qui serait créé.",
    )
    return parser.parse_args()


def _split_multi(values: list[str]) -> list[str]:
    items = []
    for value in values:
        items.extend(part.strip() for part in value.split(",") if part.strip())
    return items


def _normalize_route(route: str) -> str:
    route = route.strip()
    if route and not route.startswith("/"):
        route = "/" + route
    return route


def _parse_config_file(path: Path) -> tuple[set[str], set[tuple[str, str]], list[str]]:
    """Retourne (routes exclues, paires (route, méthode) exclues, uids additionnels)
    lus dans `exclude.permissions` et `additionnal.permissions` du JSON."""
    if not path.is_file():
        raise SystemExit(f"--config-file : fichier introuvable : {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--config-file : JSON invalide ({path}) : {exc}")

    raw_exclude = config.get("exclude", {}).get("permissions", [])
    if not isinstance(raw_exclude, list):
        raise SystemExit(f"--config-file : `exclude.permissions` doit être une liste ({path})")

    routes: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for entry in raw_exclude:
        item = str(entry).strip()
        if not item:
            continue
        route, sep, method = item.rpartition(":")
        if sep:
            if not route or not method:
                raise SystemExit(
                    f"--config-file : entrée `exclude.permissions` invalide "
                    f"(attendu ROUTE:METHOD) : {entry!r}"
                )
            pairs.add((route.strip(), method.strip().upper()))
        else:
            routes.add(item)

    raw_additional = config.get("additionnal", {}).get("permissions", [])
    if not isinstance(raw_additional, list):
        raise SystemExit(f"--config-file : `additionnal.permissions` doit être une liste ({path})")
    additional_uids = [str(uid).strip().upper() for uid in raw_additional if str(uid).strip()]

    return routes, pairs, additional_uids


def collect_route_methods(app) -> list[tuple[str, str]]:
    """Retourne la liste (route, method) de toutes les routes API réellement
    enregistrées (routes internes FastAPI comme /docs ou /openapi.json exclues, ce
    ne sont pas des APIRoute)."""
    pairs: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods):
            pairs.append((route.path, method.upper()))
    return pairs


def build_uid(route: str, method: str) -> str:
    return f"{route}_{method}".upper()


def main() -> None:
    args = parse_args()
    excluded_routes = set(_split_multi(args.exclude_route))
    excluded_pairs: set[tuple[str, str]] = set()
    for item in _split_multi(args.exclude):
        route, _, method = item.rpartition(":")
        if not route or not method:
            raise SystemExit(f"--exclude invalide (attendu ROUTE:METHOD) : {item!r}")
        excluded_pairs.add((route, method.upper()))

    additional_uids: list[str] = []
    if args.config_file:
        file_routes, file_pairs, additional_uids = _parse_config_file(args.config_file)
        excluded_routes |= file_routes
        excluded_pairs |= file_pairs

    excluded_routes = {_normalize_route(route) for route in excluded_routes}
    excluded_pairs = {(_normalize_route(route), method) for route, method in excluded_pairs}

    from app.database import SessionLocal
    from app.main import app
    from app.entities.permission.model import PermissionModel

    all_pairs = collect_route_methods(app)

    candidates: dict[str, tuple[str, str]] = {}
    skipped_excluded = 0
    for route, method in all_pairs:
        if route in excluded_routes or (route, method) in excluded_pairs:
            skipped_excluded += 1
            continue
        uid = build_uid(route, method)
        candidates[uid] = (route, method)

    # Permissions "libres" (additionnal.permissions) : non liées à une route, on
    # enregistre uniquement l'uid (route/method vides).
    for uid in additional_uids:
        candidates.setdefault(uid, ("", ""))

    db = SessionLocal()
    try:
        existing_uids = {row[0] for row in db.execute(PermissionModel.table.select().with_only_columns(PermissionModel.table.c.uid))}

        to_create = {
            uid: (route, method)
            for uid, (route, method) in candidates.items()
            if uid not in existing_uids
        }

        print(f"Routes/méthodes trouvées : {len(all_pairs)}")
        print(f"Exclues                  : {skipped_excluded}")
        print(f"Permissions additionnelles : {len(additional_uids)}")
        print(f"Déjà existantes          : {len(candidates) - len(to_create)}")
        print(f"À créer                  : {len(to_create)}")

        if not to_create:
            return

        for uid, (route, method) in sorted(to_create.items()):
            print(f"  + {uid}  ({method} {route})" if route or method else f"  + {uid}  (permission libre)")

        if args.dry_run:
            print("\n--dry-run : aucune écriture effectuée.")
            return

        db.execute(
            PermissionModel.table.insert(),
            [
                {"uid": uid, "route": route, "method": method}
                for uid, (route, method) in to_create.items()
            ],
        )
        db.commit()
        print(f"\n{len(to_create)} permission(s) créée(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
