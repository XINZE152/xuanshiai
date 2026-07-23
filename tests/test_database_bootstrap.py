import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.main import initialize_database_on_startup


def test_production_rejects_database_auto_init() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", auto_init_db=True)


@pytest.mark.asyncio
async def test_database_bootstrap_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auto_init_db", False)
    await initialize_database_on_startup()
