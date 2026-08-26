from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.exceptions import UnauthorizedError
from app.mail.service import get_mail_service
from app.security import generate_token, hash_password, verify_password
from app.crud.methods import BaseCRUDMethods
from app.crud.model import get_model
from app.entities.usertoken.methods import UsertokenMethods


class UserMethods(BaseCRUDMethods):
    """Exemple de méthode métier surchargée/ajoutée en plus du CRUD générique."""

    def create(self, db: Session, data: dict, with_: Iterable[str] = ()):
        data = self._hash_password(data)

        if data.get("password"):
            user = super().create(db, {**data, "initial_token": None}, with_=with_)
            get_mail_service().send(
                db,
                to=user.email,
                template="welcome",
                context={"name": user.first_name},
            )
            return user

        # Pas de mot de passe fourni : on génère un token à usage unique, stocké sur
        # l'utilisateur, et on invite par mail à choisir son mot de passe via ce lien
        # (la page qui consomme ce token n'existe pas encore côté front).
        token = generate_token()
        user = super().create(db, {**data, "password": None, "initial_token": token}, with_=with_)
        settings = get_settings()
        get_mail_service().send(
            db,
            to=user.email,
            template="set_password",
            context={
                "name": user.first_name,
                "password_url": f"{settings.password_setup_url_prefix}{token}",
            },
        )
        return user

    def update(self, db: Session, item_id, data: dict, with_: Iterable[str] = ()):
        return super().update(db, item_id, self._hash_password(data), with_=with_)

    def get_by_initial_token(self, db: Session, token: str):
        return (
            db.query(self.model.python_class)
            .filter(self.model.python_class.initial_token == token, self._not_deleted())
            .first()
        )

    def _hash_password(self, data: dict) -> dict:
        if not data.get("password"):
            return data
        return {**data, "password": hash_password(data["password"])}

    def login(self, db: Session, email: str, password: str) -> dict:
        user = (
            db.query(self.model.python_class)
            .filter(self.model.python_class.email == email, self._not_deleted())
            .first()
        )
        if user is None or not user.password or not verify_password(password, user.password):
            raise UnauthorizedError("Email ou mot de passe incorrect.")

        settings = get_settings()
        token = generate_token()
        expires_at = datetime.utcnow() + timedelta(minutes=settings.auth_token_ttl_minutes)

        UsertokenMethods(get_model("usertoken")).create(
            db,
            {"user_id": user.id, "token": token, "expires_at": expires_at},
        )

        return {"token": token, "expires_at": expires_at}

