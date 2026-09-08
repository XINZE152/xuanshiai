"""Back-office management for the 总店红娘 -> 分派配置 page.

Two scopes (`member_crm` / `customer_lead`) × two config types (`assign` / `abandon`)
= 4 rows stored in `matchmaker_apportion_config`. CRUD operations write audit entries
to `business_audit_log` and respect the strategies/ambiguity whitelist defined by the UI.
"""

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.matchmaker_admin import (
    ApportionAbandonUpdate,
    ApportionAssignUpdate,
    ApportionConfig,
    ApportionConfigAuditLog,
    ApportionConfigAuditLogPage,
    ApportionConfigType,
    ApportionScope,
    ApportionToggleUpdate,
)


_SELECT_COLUMNS = """SELECT id, scope, config_type, strategy, target_matchmaker_id,
    auto_abandon_days, daily_pickup_limit,
    show_admin_abandoned_in_pool, show_store_abandoned_in_pool,
    is_enabled, updated_by, remark, created_at, updated_at
    FROM matchmaker_apportion_config"""


_SELECT_JOIN_USER = """SELECT c.id, c.scope, c.config_type, c.strategy, c.target_matchmaker_id,
    u.display_name AS target_matchmaker_name,
    c.auto_abandon_days, c.daily_pickup_limit,
    c.show_admin_abandoned_in_pool, c.show_store_abandoned_in_pool,
    c.is_enabled, c.updated_by, c.remark, c.created_at, c.updated_at
    FROM matchmaker_apportion_config c
    LEFT JOIN users u ON u.id = c.target_matchmaker_id"""


def _coerce(row: dict[str, Any]) -> ApportionConfig:
    payload = dict(row)
    payload.setdefault("target_matchmaker_name", None)
    payload.setdefault("target_matchmaker_id", None)
    payload.setdefault("auto_abandon_days", None)
    payload.setdefault("daily_pickup_limit", None)
    payload.setdefault("updated_by", None)
    payload.setdefault("remark", None)
    return ApportionConfig.model_validate(payload)


async def list_configs(db: AsyncSession) -> list[ApportionConfig]:
    rows = (await db.execute(text(f"{_SELECT_JOIN_USER} ORDER BY id"))).mappings().all()
    return [_coerce(dict(row)) for row in rows]


async def get_config(db: AsyncSession, scope: ApportionScope, config_type: ApportionConfigType) -> ApportionConfig:
    row = (
        await db.execute(
            text(f"{_SELECT_JOIN_USER} WHERE scope = :scope AND config_type = :config_type"),
            {"scope": scope, "config_type": config_type},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="分派配置不存在")
    return _coerce(dict(row))


async def _ensure_matchmaker_exists(db: AsyncSession, matchmaker_id: int) -> None:
    row = (
        await db.execute(
            text(
                """SELECT u.id FROM users u
                     JOIN user_role r ON r.user_id = u.id AND r.status = 1
                    WHERE u.id = :id AND r.role_code = 'service_matchmaker'
                    LIMIT 1"""
            ),
            {"id": matchmaker_id},
        )
    ).first()
    if not row:
        raise HTTPException(400, detail="指定的服务红娘不存在或未启用服务红娘角色")


async def upsert_assign(
    db: AsyncSession,
    admin_id: int,
    scope: ApportionScope,
    request: ApportionAssignUpdate,
) -> ApportionConfig:
    if scope == "customer_lead" and request.strategy in {"by_region", "by_promoter"}:
        # 客源线索维度暂无地区/推广概念，约束在 UI 之外
        raise HTTPException(
            400,
            detail=f"客源线索不支持 strategy={request.strategy}",
        )
    if request.strategy == "designated":
        await _ensure_matchmaker_exists(db, request.target_matchmaker_id or 0)

    payload = {
        "scope": scope,
        "config_type": "assign",
        "strategy": request.strategy,
        "target_matchmaker_id": request.target_matchmaker_id,
        "is_enabled": 1 if request.is_enabled else 0,
        "remark": request.remark,
        "updated_by": admin_id,
    }
    await db.execute(
        text(
            """INSERT INTO matchmaker_apportion_config
                 (scope, config_type, strategy, target_matchmaker_id,
                  is_enabled, remark, updated_by, updated_at)
               VALUES
                 (:scope, :config_type, :strategy, :target_matchmaker_id,
                  :is_enabled, :remark, :updated_by, UTC_TIMESTAMP())
               ON DUPLICATE KEY UPDATE
                 strategy = VALUES(strategy),
                 target_matchmaker_id = VALUES(target_matchmaker_id),
                 is_enabled = VALUES(is_enabled),
                 remark = VALUES(remark),
                 updated_by = VALUES(updated_by),
                 updated_at = UTC_TIMESTAMP()"""
        ),
        payload,
    )
    after_json = json.dumps(request.model_dump(), ensure_ascii=False)
    await db.execute(
        text(
            """INSERT INTO business_audit_log
                 (actor_user_id, action, resource_type, resource_id, after_json)
               SELECT :actor, 'apportion_config.assign.upsert',
                      'matchmaker_apportion_config', id, :after_json
                 FROM matchmaker_apportion_config
                WHERE scope = :scope AND config_type = :config_type"""
        ),
        {"actor": admin_id, "scope": scope, "config_type": "assign", "after_json": after_json},
    )
    await db.commit()
    return await get_config(db, scope, "assign")


async def upsert_abandon(
    db: AsyncSession,
    admin_id: int,
    scope: ApportionScope,
    request: ApportionAbandonUpdate,
) -> ApportionConfig:
    payload = {
        "scope": scope,
        "config_type": "abandon",
        "auto_abandon_days": request.auto_abandon_days,
        "daily_pickup_limit": request.daily_pickup_limit,
        "show_admin_abandoned_in_pool": 1 if request.show_admin_abandoned_in_pool else 0,
        "show_store_abandoned_in_pool": 1 if request.show_store_abandoned_in_pool else 0,
        "is_enabled": 1 if request.is_enabled else 0,
        "remark": request.remark,
        "updated_by": admin_id,
    }
    await db.execute(
        text(
            """INSERT INTO matchmaker_apportion_config
                 (scope, config_type, auto_abandon_days, daily_pickup_limit,
                  show_admin_abandoned_in_pool, show_store_abandoned_in_pool,
                  is_enabled, remark, updated_by, updated_at)
               VALUES
                 (:scope, :config_type, :auto_abandon_days, :daily_pickup_limit,
                  :show_admin_abandoned_in_pool, :show_store_abandoned_in_pool,
                  :is_enabled, :remark, :updated_by, UTC_TIMESTAMP())
               ON DUPLICATE KEY UPDATE
                 auto_abandon_days = VALUES(auto_abandon_days),
                 daily_pickup_limit = VALUES(daily_pickup_limit),
                 show_admin_abandoned_in_pool = VALUES(show_admin_abandoned_in_pool),
                 show_store_abandoned_in_pool = VALUES(show_store_abandoned_in_pool),
                 is_enabled = VALUES(is_enabled),
                 remark = VALUES(remark),
                 updated_by = VALUES(updated_by),
                 updated_at = UTC_TIMESTAMP()"""
        ),
        payload,
    )
    after_json = json.dumps(request.model_dump(), ensure_ascii=False)
    await db.execute(
        text(
            """INSERT INTO business_audit_log
                 (actor_user_id, action, resource_type, resource_id, after_json)
               SELECT :actor, 'apportion_config.abandon.upsert',
                      'matchmaker_apportion_config', id, :after_json
                 FROM matchmaker_apportion_config
                WHERE scope = :scope AND config_type = :config_type"""
        ),
        {"actor": admin_id, "scope": scope, "config_type": "abandon", "after_json": after_json},
    )
    await db.commit()
    return await get_config(db, scope, "abandon")


async def toggle_config(
    db: AsyncSession,
    admin_id: int,
    scope: ApportionScope,
    config_type: ApportionConfigType,
    request: ApportionToggleUpdate,
) -> ApportionConfig:
    existing = await get_config(db, scope, config_type)
    await db.execute(
        text(
            """UPDATE matchmaker_apportion_config
                  SET is_enabled = :is_enabled,
                      remark = COALESCE(:remark, remark),
                      updated_by = :updated_by,
                      updated_at = UTC_TIMESTAMP()
                WHERE scope = :scope AND config_type = :config_type"""
        ),
        {
            "scope": scope,
            "config_type": config_type,
            "is_enabled": 1 if request.is_enabled else 0,
            "remark": request.remark,
            "updated_by": admin_id,
        },
    )
    after_json = json.dumps(
        {"is_enabled": request.is_enabled, "remark": request.remark},
        ensure_ascii=False,
    )
    await db.execute(
        text(
            """INSERT INTO business_audit_log
                 (actor_user_id, action, resource_type, resource_id, after_json)
               SELECT :actor, 'apportion_config.toggle',
                      'matchmaker_apportion_config', id, :after_json
                 FROM matchmaker_apportion_config
                WHERE scope = :scope AND config_type = :config_type"""
        ),
        {
            "actor": admin_id,
            "scope": scope,
            "config_type": config_type,
            "after_json": after_json,
        },
    )
    await db.commit()
    _ = existing  # keep mypy happy
    return await get_config(db, scope, config_type)


async def list_audit_logs(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
) -> ApportionConfigAuditLogPage:
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size
    total_row = (
        await db.execute(
            text(
                """SELECT COUNT(*) FROM business_audit_log
                    WHERE resource_type = 'matchmaker_apportion_config'"""
            )
        )
    ).scalar_one()
    rows = (
        await db.execute(
            text(
                """SELECT id, actor_user_id, action, resource_type, resource_id,
                          before_json, after_json, reason, created_at
                     FROM business_audit_log
                    WHERE resource_type = 'matchmaker_apportion_config'
                    ORDER BY id DESC
                    LIMIT :limit OFFSET :offset"""
            ),
            {"limit": page_size, "offset": offset},
        )
    ).mappings().all()
    items = [ApportionConfigAuditLog(**dict(row)) for row in rows]
    return ApportionConfigAuditLogPage(
        items=items,
        page=page,
        page_size=page_size,
        total=int(total_row or 0),
        has_more=offset + len(items) < int(total_row or 0),
    )