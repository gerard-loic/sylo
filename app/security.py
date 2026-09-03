import secrets
import uuid

import bcrypt

from app.config import get_settings


def hash_password(password: str) -> str:
    settings = get_settings()
    salt = bcrypt.gensalt(rounds=settings.password_hash_rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Hash stocké dans un format inattendu (ex: mot de passe legacy non hashé).
        return False


def generate_token() -> str:
    return secrets.token_urlsafe(48)


def anonymized_value() -> str:
    """Valeur de remplacement d'un attribut anonymisé lors d'une suppression :
    préfixe `***` suivi d'une chaîne unique, pour rester repérable comme donnée
    anonymisée sans divulguer la valeur d'origine."""
    return f"***{uuid.uuid4().hex}"
