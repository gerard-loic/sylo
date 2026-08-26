from datetime import datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.exceptions import ForbiddenError, UnauthorizedError
from app.crud.mapper import get_association_table
from app.crud.model import get_model

# Routes publiques (pas de Bearer requis) : déclarées explicitement dans le code de
# l'entité concernée via `mark_public(path, method)`, juste après la définition de la
# route (voir `entities/user/routes.py` pour /users/login). `path` est le template de
# route tel qu'enregistré par FastAPI (ex: "/users/{item_id}"), `method` en majuscules.
_PUBLIC_ROUTES: set[tuple[str, str]] = set()


def mark_public(path: str, method: str) -> None:
    _PUBLIC_ROUTES.add((path, method.upper()))


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def _user_has_permission(db: Session, user_id: int, uid: str) -> bool:
    """`uid` suit la même convention que `scripts/sync_permissions.py`
    (ex: "/USERS/{ITEM_ID}_DELETE") : la permission liée à une route/méthode."""
    user_role = get_association_table("user_role")
    role_permission = get_association_table("role_permission")
    role_table = get_model("role").table
    permission_table = get_model("permission").table

    query = (
        select(permission_table.c.id)
        .select_from(
            user_role.join(role_table, role_table.c.id == user_role.c.role_id)
            .join(role_permission, role_permission.c.role_id == user_role.c.role_id)
            .join(permission_table, permission_table.c.id == role_permission.c.permission_id)
        )
        .where(
            user_role.c.user_id == user_id,
            permission_table.c.uid == uid,
            role_table.c.deleted_at.is_(None),
            permission_table.c.deleted_at.is_(None),
        )
        .limit(1)
    )
    return db.execute(query).first() is not None


def authenticate(request: Request, db: Session) -> int:
    """Exige un Bearer token valide (table `user_tokens`, non expiré) et vérifie que
    l'utilisateur associé a la permission liée à la route/méthode courante
    (`permissions` via `role_permission` / `user_role`). Renvoie l'id de l'utilisateur
    authentifié. Factorisé hors de `enforce_auth` pour être appelable directement par
    une route marquée publique mais qui n'accepte le Bearer que comme une des
    authentifications possibles (voir `entities/user/routes.py`, PUT /users/{item_id}).
    """
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    method = request.method.upper()

    token = _extract_token(request)
    if token is None:
        raise UnauthorizedError("Authentification requise (Bearer token manquant).")

    token_model = get_model("usertoken")
    token_row = (
        db.query(token_model.python_class)
        .filter(
            token_model.python_class.token == token,
            token_model.python_class.deleted_at.is_(None),
        )
        .first()
    )
    if token_row is None:
        raise UnauthorizedError("Token invalide.")
    if token_row.expires_at < datetime.utcnow():
        raise UnauthorizedError("Token expiré.")

    user_model = get_model("user")
    user_row = (
        db.query(user_model.python_class.id)
        .filter(
            user_model.pk_column == token_row.user_id,
            user_model.python_class.deleted_at.is_(None),
        )
        .first()
    )
    if user_row is None:
        raise UnauthorizedError("Token invalide.")

    uid = f"{path}_{method}".upper()
    if not _user_has_permission(db, token_row.user_id, uid):
        raise ForbiddenError("Permission manquante pour cette route.")

    return token_row.user_id


def enforce_auth(request: Request, db: Session = Depends(get_db)) -> None:
    """Dépendance globale (voir `dependencies=` sur l'app dans `app/main.py`), sauf
    routes marquées publiques via `mark_public()`."""
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    method = request.method.upper()

    if (path, method) in _PUBLIC_ROUTES:
        return

    request.state.user_id = authenticate(request, db)
