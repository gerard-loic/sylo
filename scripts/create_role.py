"""Crée un rôle dans la table `roles`.

Avec --config-file, associe aussi au rôle les permissions listées dans la clé
`roles.<uid>` du JSON. Formats supportés pour chaque entrée :
    "*"            -> toutes les permissions
    "route:GET"    -> la permission de cette route et méthode
    "route:*"      -> toutes les permissions de cette route
    "PERMISSION"   -> une permission par son uid exact (ex: "KNOWLEDGE_UNLIMITED")

Usage:
    python scripts/create_role.py --name "Administrateur" --uid ADMIN
    python scripts/create_role.py --name "Gestionnaire" --uid MANAGER --dry-run
    python scripts/create_role.py --name "Admin" --uid ADMIN --config-file generate-config.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crée un rôle (name + uid).")
    parser.add_argument("--name", required=True, help="Nom du rôle, ex: 'Administrateur'.")
    parser.add_argument("--uid", required=True, help="Identifiant unique du rôle, ex: ADMIN.")
    parser.add_argument(
        "--config-file",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Fichier de configuration JSON. Les entrées de `roles.<uid>` (avec "
            "<uid> = l'uid du rôle créé) sont associées au rôle. Formats : '*' "
            "(toutes), 'route:GET', 'route:*', ou 'PERMISSION' (uid exact)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit rien en base : affiche seulement ce qui serait créé.",
    )
    return parser.parse_args()


def _normalize_route(route: str) -> str:
    route = route.strip()
    if route and not route.startswith("/"):
        route = "/" + route
    return route


def _load_role_specs(path: Path, role_uid: str) -> list[str]:
    """Retourne la liste des specs de permissions déclarées sous `roles.<role_uid>`
    dans le JSON (liste vide si la clé est absente)."""
    if not path.is_file():
        raise SystemExit(f"--config-file : fichier introuvable : {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--config-file : JSON invalide ({path}) : {exc}")

    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        raise SystemExit(f"--config-file : `roles` doit être un objet ({path})")

    specs = roles.get(role_uid, [])
    if not isinstance(specs, list):
        raise SystemExit(f"--config-file : `roles.{role_uid}` doit être une liste ({path})")
    return [str(spec).strip() for spec in specs if str(spec).strip()]


def _resolve_specs(specs: list[str], permissions: list) -> tuple[set[int], list[str]]:
    """Fait correspondre chaque spec aux lignes `permissions` (id, uid, route,
    method). Retourne (ids retenus, avertissements pour les specs sans résultat)."""
    selected: set[int] = set()
    warnings: list[str] = []

    for spec in specs:
        if spec == "*":
            selected.update(perm.id for perm in permissions)
            continue

        route, sep, method = spec.rpartition(":")
        if sep:
            route = _normalize_route(route)
            method = method.strip().upper()
            if method == "*":
                matched = [perm for perm in permissions if perm.route == route]
            else:
                matched = [
                    perm for perm in permissions
                    if perm.route == route and perm.method == method
                ]
        else:
            uid = spec.upper()
            matched = [perm for perm in permissions if perm.uid == uid]

        if not matched:
            warnings.append(f"spec '{spec}' : aucune permission correspondante")
            continue
        selected.update(perm.id for perm in matched)

    return selected, warnings


def main() -> None:
    args = parse_args()
    name = args.name.strip()
    uid = args.uid.strip().upper()

    if not name or not uid:
        raise SystemExit("name et uid ne peuvent pas être vides.")

    specs = _load_role_specs(args.config_file, uid) if args.config_file else []

    from app.database import SessionLocal
    from app.main import app  # noqa: F401  (déclenche le mapping ORM)
    from app.crud.mapper import get_association_table
    from app.entities.permission.model import PermissionModel
    from app.entities.role.model import RoleModel

    db = SessionLocal()
    try:
        existing = db.execute(
            RoleModel.table.select().where(RoleModel.table.c.uid == uid)
        ).first()
        if existing is not None:
            raise SystemExit(f"Un rôle avec l'uid {uid!r} existe déjà (id={existing.id}).")

        print(f"Rôle à créer : name={name!r} uid={uid!r}")

        to_link_ids: set[int] = set()
        if specs:
            permissions = db.execute(
                PermissionModel.table.select().with_only_columns(
                    PermissionModel.table.c.id,
                    PermissionModel.table.c.uid,
                    PermissionModel.table.c.route,
                    PermissionModel.table.c.method,
                )
            ).fetchall()
            to_link_ids, warnings = _resolve_specs(specs, permissions)
            print(f"Permissions à associer : {len(to_link_ids)} (specs : {', '.join(specs)})")
            for warning in warnings:
                print(f"  ! {warning}")

        if args.dry_run:
            print("\n--dry-run : aucune écriture effectuée.")
            return

        result = db.execute(
            RoleModel.table.insert().returning(RoleModel.table.c.id),
            {"name": name, "uid": uid},
        )
        role_id = result.scalar_one()

        if to_link_ids:
            role_permission = get_association_table("role_permission")
            db.execute(
                role_permission.insert(),
                [{"role_id": role_id, "permission_id": pid} for pid in sorted(to_link_ids)],
            )

        db.commit()
        print("\nRôle créé.")
        if to_link_ids:
            print(f"{len(to_link_ids)} permission(s) associée(s) au rôle {uid}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
