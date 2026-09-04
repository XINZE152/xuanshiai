from __future__ import annotations

from pydantic import BaseModel


class AppVersionResponse(BaseModel):
    platform: str
    latest_version: str
    current_version: str
    has_update: bool
    is_force_update: bool
    download_url: str | None
    update_log: list[str]
