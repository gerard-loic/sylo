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
    python scripts/sync_permissions.py --exclude-file except.txt

Fichier --exclude-file (une entrée par ligne, lignes vides et '#...' ignorées) :
    users               # toutes les méthodes de la route users
    users/login:POST    # uniquement la méthode POST de users/login
"""

import argparse
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
        "--exclude-file",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Fichier listant les routes/méthodes à exclure, une entrée par ligne "
            "(lignes vides et commentaires '#...' ignorés). Même syntaxe que "
            "--exclude-route / --exclude : 'users' exclut toutes les méthodes, "
            "'users/login:POST' exclut uniquement cette méthode."
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


def _parse_exclude_file(path: Path) -> tuple[set[str], set[tuple[str, str]]]:
    if not path.is_file():
        raise SystemExit(f"--exclude-file : fichier introuvable : {path}")

    routes: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        route, sep, method = line.rpartition(":")
        if sep:
            if not route or not method:
                raise SystemExit(
                    f"--exclude-file : ligne {lineno} invalide (attendu ROUTE:METHOD) : {raw_line!r}"
                )
            pairs.add((route.strip(), method.strip().upper()))
        else:
            routes.add(line)
    return routes, pairs


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

    if args.exclude_file:
        file_routes, file_pairs = _parse_exclude_file(args.exclude_file)
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
        print(f"Déjà existantes          : {len(candidates) - len(to_create)}")
        print(f"À créer                  : {len(to_create)}")

        if not to_create:
            return

        for uid, (route, method) in sorted(to_create.items()):
            print(f"  + {uid}  ({method} {route})")

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
