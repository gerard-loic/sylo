"""Crée un rôle dans la table `roles`.

Usage:
    python scripts/create_role.py --name "Administrateur" --uid ADMIN
    python scripts/create_role.py --name "Gestionnaire" --uid MANAGER --dry-run
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crée un rôle (name + uid).")
    parser.add_argument("--name", required=True, help="Nom du rôle, ex: 'Administrateur'.")
    parser.add_argument("--uid", required=True, help="Identifiant unique du rôle, ex: ADMIN.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit rien en base : affiche seulement ce qui serait créé.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    name = args.name.strip()
    uid = args.uid.strip().upper()

    if not name or not uid:
        raise SystemExit("name et uid ne peuvent pas être vides.")

    from app.database import SessionLocal
    from app.main import app  # noqa: F401  (déclenche le mapping ORM)
    from app.entities.role.model import RoleModel

    db = SessionLocal()
    try:
        existing = db.execute(
            RoleModel.table.select().where(RoleModel.table.c.uid == uid)
        ).first()
        if existing is not None:
            raise SystemExit(f"Un rôle avec l'uid {uid!r} existe déjà (id={existing.id}).")

        print(f"Rôle à créer : name={name!r} uid={uid!r}")

        if args.dry_run:
            print("\n--dry-run : aucune écriture effectuée.")
            return

        db.execute(RoleModel.table.insert(), [{"name": name, "uid": uid}])
        db.commit()
        print("\nRôle créé.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
