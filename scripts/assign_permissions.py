"""Associe à un rôle toutes les permissions existantes, à l'exception de celles
listées dans un fichier d'exclusion (optionnel, même syntaxe que --exclude-file
dans sync_permissions.py). N'écrit jamais sur une association déjà existante.

Fichier d'exclusion (une entrée par ligne, lignes vides et '#...' ignorés) :
    users               # toutes les méthodes de la route users
    users/login:POST    # uniquement la méthode POST de users/login

Usage:
    python scripts/assign_permissions.py --role-uid ADMIN
    python scripts/assign_permissions.py --role-uid ADMIN --exclude-file except.txt
    python scripts/assign_permissions.py --role-uid ADMIN --dry-run
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Associe à un rôle toutes les permissions, sauf celles exclues."
    )
    parser.add_argument("--role-uid", required=True, metavar="UID", help="uid du rôle cible, ex: ADMIN.")
    parser.add_argument(
        "--exclude-file",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Fichier listant les routes/méthodes à exclure, une entrée par ligne "
            "(lignes vides et commentaires '#...' ignorés). 'users' exclut toutes "
            "les méthodes, 'users/login:POST' exclut uniquement cette méthode."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit rien en base : affiche seulement ce qui serait associé.",
    )
    return parser.parse_args()


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
            pairs.add((_normalize_route(route), method.strip().upper()))
        else:
            routes.add(_normalize_route(line))
    return routes, pairs


def main() -> None:
    args = parse_args()
    role_uid = args.role_uid.strip().upper()

    excluded_routes: set[str] = set()
    excluded_pairs: set[tuple[str, str]] = set()
    if args.exclude_file:
        excluded_routes, excluded_pairs = _parse_exclude_file(args.exclude_file)

    from app.database import SessionLocal
    from app.main import app  # noqa: F401  (déclenche le mapping ORM)
    from app.crud.mapper import get_association_table
    from app.entities.permission.model import PermissionModel
    from app.entities.role.model import RoleModel

    db = SessionLocal()
    try:
        role = db.execute(
            RoleModel.table.select().where(RoleModel.table.c.uid == role_uid)
        ).first()
        if role is None:
            raise SystemExit(f"Aucun rôle avec l'uid {role_uid!r} (utilisez scripts/create_role.py).")

        role_permission = get_association_table("role_permission")

        all_permissions = db.execute(
            PermissionModel.table.select().with_only_columns(
                PermissionModel.table.c.id,
                PermissionModel.table.c.uid,
                PermissionModel.table.c.route,
                PermissionModel.table.c.method,
            )
        ).fetchall()

        already_linked_ids = {
            row[0]
            for row in db.execute(
                role_permission.select()
                .with_only_columns(role_permission.c.permission_id)
                .where(role_permission.c.role_id == role.id)
            )
        }

        candidates = []
        skipped_excluded = 0
        for perm in all_permissions:
            if perm.route in excluded_routes or (perm.route, perm.method) in excluded_pairs:
                skipped_excluded += 1
                continue
            candidates.append(perm)

        to_link = [perm for perm in candidates if perm.id not in already_linked_ids]

        print(f"Permissions trouvées      : {len(all_permissions)}")
        print(f"Exclues                   : {skipped_excluded}")
        print(f"Déjà associées à {role_uid} : {len(candidates) - len(to_link)}")
        print(f"À associer                : {len(to_link)}")

        if not to_link:
            return

        for perm in sorted(to_link, key=lambda p: p.uid):
            print(f"  + {perm.uid}  ({perm.method} {perm.route})")

        if args.dry_run:
            print("\n--dry-run : aucune écriture effectuée.")
            return

        db.execute(
            role_permission.insert(),
            [{"role_id": role.id, "permission_id": perm.id} for perm in to_link],
        )
        db.commit()
        print(f"\n{len(to_link)} permission(s) associée(s) au rôle {role_uid}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
