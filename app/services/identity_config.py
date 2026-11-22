"""实名核验厂商配置：加密凭据、版本化存储、回复掩码。

配置域 identity_vendor；secret_id/secret_key 以 Fernet 密文存储，密钥派生自 SECRET_KEY。
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.admin_config import ensure_defaults

NAMESPACE = "identity_vendor"
SECRET_FIELDS = ("secret_id", "secret_key")
ENC_PREFIX = "enc:"

DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "https://ap-beijing.cloudmarket-apigw.com/service-hcgajsa5/idcard/VerifyIdcardv2",
    "secret_id": None,
    "secret_key": None,
    "timeout_seconds": 10,
}


def _fernet():
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt(plain: str) -> str:
    return ENC_PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    if not value.startswith(ENC_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        raise HTTPException(status_code=500, detail="实名核验密钥解密失败，请检查服务器密钥配置")


def _mask(value: Any) -> str | None:
    return "******" if value else None


async def _load_row(db: AsyncSession):
    await ensure_defaults(db)
    row = (
        await db.execute(text("SELECT * FROM admin_config_snapshot WHERE namespace=:ns"), {"ns": NAMESPACE})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="实名核验配置不存在")
    return row


async def get_identity_config(db: AsyncSession, *, reveal: bool = False) -> dict[str, Any]:
    row = await _load_row(db)
    try:
        stored = json.loads(row["config_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        stored = {}
    cfg = {**DEFAULT_CONFIG, **(stored if isinstance(stored, dict) else {})}
    for field in SECRET_FIELDS:
        value = cfg.get(field)
        cfg[field] = (_decrypt(value) if value else None) if reveal else _mask(value)
    cfg["version"] = int(row["version"])
    return cfg


async def update_identity_config(db: AsyncSession, actor_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    row = await _load_row(db)
    version = int(row["version"])
    try:
        current = json.loads(row["config_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    current = {**DEFAULT_CONFIG, **current}

    for key, value in changes.items():
        if key not in DEFAULT_CONFIG:
            continue
        if key in SECRET_FIELDS:
            if not value or value == "******":
                continue
            current[key] = _encrypt(str(value))
        else:
            current[key] = value

    next_version = version + 1
    payload = json.dumps(current, ensure_ascii=False)
    await db.execute(
        text(
            "UPDATE admin_config_snapshot SET version=:v, config_json=:cfg, "
            "updated_by=:actor, updated_at=UTC_TIMESTAMP() WHERE namespace=:ns"
        ),
        {"v": next_version, "cfg": payload, "actor": actor_id, "ns": NAMESPACE},
    )
    await db.execute(
        text(
            "INSERT INTO admin_config_audit_log "
            "(namespace, version, action, actor_user_id, change_summary, before_config_json, after_config_json) "
            "VALUES (:ns, :v, 'patch', :actor, :summary, :before, :after)"
        ),
        {
            "ns": NAMESPACE,
            "v": next_version,
            "actor": actor_id,
            "summary": "更新实名核验厂商配置",
            "before": json.dumps({**current, **{k: "******" for k in SECRET_FIELDS}}, ensure_ascii=False),
            "after": json.dumps({**current, **{k: "******" for k in SECRET_FIELDS}}, ensure_ascii=False),
        },
    )
    await db.commit()
    return await get_identity_config(db)
