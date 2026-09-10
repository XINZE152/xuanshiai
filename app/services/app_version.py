from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.app_version import AppVersionResponse

_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")


def _version_tuple(value: str) -> tuple[int, ...]:
    if not _VERSION_RE.fullmatch(value) or len(value) > 32:
        raise HTTPException(400, detail="平台或版本号格式错误")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


async def check_version(db: AsyncSession, platform: str, current_version: str) -> AppVersionResponse:
    if not _PLATFORM_RE.fullmatch(platform) or len(platform) > 32:
        raise HTTPException(400, detail="平台或版本号格式错误")
    current_tuple = _version_tuple(current_version)
    try:
        result = await db.execute(text("""SELECT version, is_force_update, download_url, update_log
            FROM app_release_version WHERE platform = :platform AND is_active = 1"""), {"platform": platform})
        rows = result.mappings().all()
    except SQLAlchemyError as exc:
        raise HTTPException(503, detail="版本服务暂不可用") from exc
    valid = []
    for row in rows:
        try:
            valid.append((_version_tuple(str(row["version"])), row))
        except HTTPException:
            continue
    if not valid:
        raise HTTPException(404, detail="暂无该平台版本信息")
    latest_tuple, latest = max(valid, key=lambda item: item[0])
    latest_version = str(latest["version"])
    has_update = latest_tuple > current_tuple
    update_log = latest.get("update_log") or []
    if isinstance(update_log, str):
        import json
        try:
            update_log = json.loads(update_log)
        except json.JSONDecodeError:
            update_log = []
    if not isinstance(update_log, list):
        update_log = []
    return AppVersionResponse(
        platform=platform, latest_version=latest_version, current_version=current_version,
        has_update=has_update, is_force_update=bool(latest["is_force_update"]) if has_update else False,
        download_url=latest.get("download_url") if has_update else None,
        update_log=[str(item) for item in update_log] if has_update else [],
    )
