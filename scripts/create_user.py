"""Crée un utilisateur (table `users`) et lui associe optionnellement un rôle (table
`user_role`). Réutilise `UserMethods.create()` (voir `app/entities/user/methods.py`) :
même comportement que `POST /users` — mot de passe haché s'il est fourni, sinon un
email d'initialisation de mot de passe est envoyé.

`level_id` (colonne NOT NULL de `users`) est résolu automatiquement sur le niveau
marqué `is_default` dans la table `levels` : une erreur claire est levée s'il n'y en a
aucun ou plusieurs (voir `scripts/create_role.py` pour créer les rôles au préalable).

Usage:
    python scripts/create_user.py --email a@b.fr --first_name Alice --last_name B --password secret
    python scripts/create_user.py --email a@b.fr --first_name Alice --last_name B --role-uid ADMIN
    python scripts/create_user.py --email a@b.fr --first_name Alice --last_name B --dry-run
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crée un utilisateur, avec rôle optionnel.")
    parser.add_argument("--email", required=True, help="Email de l'utilisateur.")
    parser.add_argument("--first_name", required=True, help="Prénom.")
    parser.add_argument("--last_name", required=True, help="Nom.")
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Mot de passe en clair (haché avant écriture, email de bienvenue envoyé). "
            "Absent : un email d'initialisation de mot de passe est envoyé à la place "
            "(comme POST /users sans password)."
        ),
    )
    parser.add_argument(
        "--role-uid",
        default=None,
        metavar="UID",
        help="uid du rôle à associer à l'utilisateur, ex: ADMIN. Optionnel.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit rien en base et n'envoie aucun email : affiche seulement ce qui serait fait.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Aligné sur UserMethods._normalize_email : l'email (login) est toujours en
    # minuscules, sinon la pré-vérification d'unicité ci-dessous passe à côté d'un
    # doublon écrit en minuscules par la route/API.
    email = args.email.strip().lower()
    first_name = args.first_name.strip()
    last_name = args.last_name.strip()
    role_uid = args.role_uid.strip().upper() if args.role_uid else None

    if not email or not first_name or not last_name:
        raise SystemExit("--email, --first_name et --last_name ne peuvent pas être vides.")

    from app.database import SessionLocal
    from app.main import app  # noqa: F401  (déclenche le mapping ORM)
    from app.exceptions import ApiError
    from app.crud.mapper import get_association_table
    from app.crud.model import get_model
    from app.entities.user.methods import UserMethods

    db = SessionLocal()
    try:
        user_model = get_model("user")
        level_model = get_model("level")
        role_model = get_model("role")

        existing = db.execute(
            user_model.table.select().where(user_model.table.c.email == email)
        ).first()
        if existing is not None:
            raise SystemExit(f"Un utilisateur avec l'email {email!r} existe déjà (id={existing.id}).")

        default_levels = db.execute(
            level_model.table.select().where(level_model.table.c.is_default.is_(True))
        ).fetchall()
        if len(default_levels) != 1:
            raise SystemExit(
                f"Impossible de résoudre le niveau par défaut : {len(default_levels)} niveau(x) "
                "avec is_default=true trouvé(s) (il en faut exactement un)."
            )
        level = default_levels[0]

        role = None
        if role_uid:
            role = db.execute(
                role_model.table.select().where(role_model.table.c.uid == role_uid)
            ).first()
            if role is None:
                raise SystemExit(f"Aucun rôle avec l'uid {role_uid!r} (utilisez scripts/create_role.py).")

        print(f"Utilisateur à créer : email={email!r} first_name={first_name!r} last_name={last_name!r}")
        print(f"  Niveau (défaut) : {level.uid} (id={level.id})")
        print(f"  Mot de passe    : {'fourni' if args.password else 'absent (email d’initialisation envoyé)'}")
        print(f"  Rôle            : {role_uid or '-'}")

        if args.dry_run:
            print("\n--dry-run : aucune écriture effectuée, aucun email envoyé.")
            return

        methods = UserMethods(user_model)
        try:
            user = methods.create(
                db,
                {
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "level_id": level.id,
                    "password": args.password,
                },
            )
        except ApiError as exc:
            # L'écriture en base est déjà commitée avant l'envoi de l'email (voir
            # UserMethods.create) : un échec d'envoi ne signifie pas que l'utilisateur
            # n'a pas été créé.
            print(f"\n! Utilisateur créé mais l'envoi de l'email a échoué : {exc.message}")
            user = db.execute(
                user_model.table.select().where(user_model.table.c.email == email)
            ).first()
            if user is None:
                raise SystemExit("La création a échoué (utilisateur introuvable après l'erreur).")

        if role is not None:
            user_role = get_association_table("user_role")
            db.execute(user_role.insert(), [{"user_id": user.id, "role_id": role.id}])
            db.commit()

        print(f"\nUtilisateur créé (id={user.id}).")
        if role is not None:
            print(f"Rôle {role_uid} associé.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
