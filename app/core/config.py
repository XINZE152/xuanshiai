"""Environment-backed application settings."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from ``.env`` and process environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Xuanshi AI API"
    app_version: str = "0.1.0"
    environment: Literal["development", "testing", "staging", "production"] = "development"
    debug: bool = True
    docs_enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    api_prefix: str = "/api/v1"

    database_url: str = "mysql+aiomysql://root:YOUR_MYSQL_PASSWORD@127.0.0.1:3306/xuanshiai"
    redis_url: str = "redis://127.0.0.1:6379/0"

    secret_key: str = "change-me-in-local-env"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    cors_origins_raw: str = "http://localhost:3000,http://localhost:5173"
    upload_dir: str = "storage/uploads"
    log_level: str = "INFO"

    @property
    def cors_origins(self) -> list[str]:
        """Convert the comma-separated environment value into CORS origins."""
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for dependency injection."""
    return Settings()


settings = get_settings()
