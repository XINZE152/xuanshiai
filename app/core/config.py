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
    refresh_token_expire_days: int = 30
    max_sessions_per_user: int = 5
    sms_code_expire_seconds: int = 300
    sms_send_interval_seconds: int = 60
    sms_daily_limit: int = 10
    wechat_app_id: str | None = None
    wechat_app_secret: str | None = None
    sms_provider: str = "disabled"
    agreement_versions_raw: str = (
        "user_service:v1,privacy_policy:v1,safety_pledge:v1,community_rules:v1"
    )

    cors_origins_raw: str = "http://localhost:3000,http://localhost:5173"
    upload_dir: str = "storage/uploads"
    log_level: str = "INFO"

    @property
    def cors_origins(self) -> list[str]:
        """Convert the comma-separated environment value into CORS origins."""
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def agreement_versions(self) -> dict[str, str]:
        """Return the currently published agreement version for each type."""
        versions: dict[str, str] = {}
        for item in self.agreement_versions_raw.split(","):
            if ":" in item:
                agreement_type, version = item.split(":", 1)
                versions[agreement_type.strip()] = version.strip()
        return versions


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for dependency injection."""
    return Settings()


settings = get_settings()
