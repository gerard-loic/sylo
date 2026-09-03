from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://sylo:sylo@localhost:5432/sylo"
    default_page_size: int = 50
    max_page_size: int = 200

    # Origines autorisées pour CORS (voir app/main.py). "*" = toutes, adapté à une API
    # publique authentifiée au Bearer token (pas de cookie => allow_credentials inutile).
    # Pour restreindre, surcharger via SYLO_CORS_ALLOW_ORIGINS au format JSON, ex :
    # SYLO_CORS_ALLOW_ORIGINS='["http://localhost:8443","https://mon-app.figma.site"]'
    cors_allow_origins: list[str] = ["*"]

    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    mail_from_address: str = "no-reply@sylo.local"
    mail_from_name: str = "Sylo"

    password_hash_rounds: int = 12
    auth_token_ttl_minutes: int = 1440
    password_setup_url_prefix: str = "http://monsite.fr/choixmotdepasse?token="

    # Serveur MCP (app/mcp/) : désactivé par défaut, monté sur /mcp si activé (voir
    # app/main.py). Expose dynamiquement un outil par route réelle de l'API, filtré
    # selon les permissions de l'utilisateur authentifié via l'outil "login_user".
    mcp_enabled: bool = False
    # Fichier optionnel listant les routes à ne pas exposer comme outils MCP, même
    # syntaxe que --exclude-file dans scripts/sync_permissions.py (une entrée par
    # ligne, "route" ou "route:METHODE", lignes vides et '#...' ignorées).
    mcp_exclude_file: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_prefix="SYLO_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
