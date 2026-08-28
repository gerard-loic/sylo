"""Associe à un rôle toutes les permissions existantes, à l'exception de celles
listées dans un fichier d'exclusion (optionnel, même syntaxe que --exclude-file
dans sync_permissions.py). N'écrit jamais sur une association déjà existante.

Fichier d'exclusion (une entrée par ligne, lignes vides et '#...' ignorés) :
    users               # toutes les méthodes de la route users
    users/login:POST    # uniquement la méthode POST de users/login

Avec --config-file (JSON), la liste des permissions à associer n'est plus « toutes
les permissions » mais celle décrite par la clé `roles.<role-uid>`. Chaque entrée
de cette liste peut prendre l'une des formes suivantes :
    *                   # toutes les permissions
    users/login:POST    # la méthode POST de la route /users/login
    users/login:*       # toutes les méthodes de la route /users/login
    KNOWLEDGE_UNLIMITED # une permission nommée précise (par uid)

    {
        "roles": {
            "ADMIN": ["*"],
            "EDITOR": ["knowledge:*", "users/{item_id}:GET", "KNOWLEDGE_UNLIMITED"]
        }
    }

Le fichier d'exclusion, s'il est fourni, filtre ensuite cette sélection.

Usage:
    python scripts/assign_permissions.py --role-uid ADMIN
    python scripts/assign_permissions.py --role-uid ADMIN --exclude-file except.txt
    python scripts/assign_permissions.py --role-uid ADMIN --config-file generate-config.json
    python scripts/assign_permissions.py --role-uid ADMIN --dry-run
"""

import argparse
import json
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
        "--config-file",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Fichier de configuration JSON. La clé `roles.<role-uid>` liste les "
            "permissions à associer au rôle : '*' (toutes), 'route:METHOD', "
            "'route:*' (toutes les méthodes de la route) ou un uid de permission "
            "nommée. Exemple : generate-config.json."
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


def _parse_config_roles(path: Path, role_uid: str) -> list[str]:
    """Retourne la liste des spécifications de `roles.<role_uid>` du fichier JSON."""
    if not path.is_file():
        raise SystemExit(f"--config-file : fichier introuvable : {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--config-file : JSON invalide ({path}) : {exc}")

    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        raise SystemExit(f"--config-file : `roles` doit être un objet ({path})")
    if role_uid not in roles:
        raise SystemExit(f"--config-file : aucune entrée `roles.{role_uid}` dans {path}.")

    specs = roles[role_uid]
    if not isinstance(specs, list):
        raise SystemExit(f"--config-file : `roles.{role_uid}` doit être une liste ({path})")
    return [str(entry).strip() for entry in specs if str(entry).strip()]


def _resolve_role_specs(specs: list[str], all_permissions: list) -> tuple[list, list[str]]:
    """Résout chaque spécification (`*`, `route:METHOD`, `route:*`, `PERMISSION`)
    en permissions concrètes. Retourne (permissions sélectionnées, specs sans
    correspondance)."""
    selected_ids: set = set()
    selected: list = []
    unmatched: list[str] = []

    def _add(perm) -> None:
        if perm.id not in selected_ids:
            selected_ids.add(perm.id)
            selected.append(perm)

    for spec in specs:
        if spec == "*":
            for perm in all_permissions:
                _add(perm)
            continue

        route, sep, method = spec.rpartition(":")
        if sep:
            route = _normalize_route(route)
            method = method.strip().upper()
            matched = False
            for perm in all_permissions:
                if perm.route != route:
                    continue
                if method != "*" and perm.method != method:
                    continue
                _add(perm)
                matched = True
            if not matched:
                unmatched.append(spec)
        else:
            uid = spec.upper()
            matched = False
            for perm in all_permissions:
                if perm.uid == uid:
                    _add(perm)
                    matched = True
            if not matched:
                unmatched.append(spec)

    return selected, unmatched


def main() -> None:
    args = parse_args()
    role_uid = args.role_uid.strip().upper()

    excluded_routes: set[str] = set()
    excluded_pairs: set[tuple[str, str]] = set()
    if args.exclude_file:
        excluded_routes, excluded_pairs = _parse_exclude_file(args.exclude_file)

    role_specs: list[str] | None = None
    if args.config_file:
        role_specs = _parse_config_roles(args.config_file, role_uid)

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

        if role_specs is not None:
            base_permissions, unmatched = _resolve_role_specs(role_specs, all_permissions)
            for spec in unmatched:
                print(f"  ! aucune permission ne correspond à : {spec}")
        else:
            base_permissions = list(all_permissions)

        candidates = []
        skipped_excluded = 0
        for perm in base_permissions:
            if perm.route in excluded_routes or (perm.route, perm.method) in excluded_pairs:
                skipped_excluded += 1
                continue
            candidates.append(perm)

        to_link = [perm for perm in candidates if perm.id not in already_linked_ids]

        print(f"Permissions trouvées      : {len(all_permissions)}")
        if role_specs is not None:
            print(f"Sélectionnées (roles.{role_uid}) : {len(base_permissions)}")
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
