from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://wakaru:wakaru@localhost:5432/wakaru"
    default_page_size: int = 50
    max_page_size: int = 200

    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    mail_from_address: str = "no-reply@wakaru.local"
    mail_from_name: str = "Wakaru"

    password_hash_rounds: int = 12
    auth_token_ttl_minutes: int = 1440
    password_setup_url_prefix: str = "http://monsite.fr/choixmotdepasse?token="

    model_config = SettingsConfigDict(env_file=".env", env_prefix="WAKARU_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
