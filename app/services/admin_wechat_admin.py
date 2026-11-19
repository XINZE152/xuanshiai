"""微信公众号运营（A2）服务：配置、粉丝、分组、菜单、自动回复、模板消息、发送。

所有需要微信的动作用 ``app/services/wechat_mp`` 适配层；凭据未配置时 503，
且**在写库前**抛出，不会留下"已同步/已发送"的假记录。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.admin_wechat_admin import (
    WechatConfig,
    WechatConfigUpdate,
    WechatFanGroupCreate,
    WechatFanGroupItem,
    WechatFanGroupUpdate,
    WechatFanItem,
    WechatFanPage,
    WechatFanSyncResult,
    WechatMenu,
    WechatMenuButton,
    WechatMenuPublish,
    WechatMessageItem,
    WechatMessagePage,
    WechatQrcodeResult,
    WechatReplyRuleCreate,
    WechatReplyRuleItem,
    WechatReplyRuleUpdate,
    WechatSendRequest,
    WechatSendResult,
    WechatTemplateItem,
    WechatTemplateSyncResult,
)
from app.services import admin_config as config_service
from app.services.wechat_mp.providers import (
    ensure_wechat_mp_configured,
    get_wechat_mp_provider,
    provider_status,
)

_CONFIG_NAMESPACE = "wechat_mp"
_MENU_NAMESPACE = "wechat_mp_menu"
_FAN_SELECT = """SELECT f.id, f.openid, f.nickname, f.avatar_url, f.gender, f.city, f.province,
    f.subscribe_status, f.subscribe_at, f.unsubscribe_at, f.group_id, g.name AS group_name,
    f.user_id, f.remark, f.created_at
    FROM admin_wechat_fan f LEFT JOIN admin_wechat_fan_group g ON g.id = f.group_id"""

_MESSAGE_SELECT = """SELECT m.id, m.batch_no, m.send_type, m.template_id, m.group_id,
    g.name AS group_name, m.title, m.content, m.url, m.target_count, m.sent_count,
    m.failed_count, m.status, m.error, m.provider_msg_id, m.sent_at, m.created_at
    FROM admin_wechat_message m LEFT JOIN admin_wechat_fan_group g ON g.id = m.group_id"""

# 单批发送上限：显式 openids 最多 1000，按分组投递最多 5000（与文档一致）。
_MAX_EXPLICIT_TARGETS = 1000
_MAX_BROADCAST_TARGETS = 5000


# ─────────────────────────── 配置 ───────────────────────────


async def get_config(db: AsyncSession) -> WechatConfig:
    snapshot = await config_service.get_config(db, _CONFIG_NAMESPACE)
    config = snapshot.config if isinstance(snapshot.config, dict) else {}
    status = provider_status()
    return WechatConfig(
        app_id_configured=bool(status["app_id_configured"]),
        app_secret_configured=bool(status["app_secret_configured"]),
        token_configured=bool(status["token_configured"]),
        aes_key_configured=bool(status["aes_key_configured"]),
        configured=bool(status["configured"]),
        wx_no=str(config.get("wx_no") or ""),
        crypto_mode=str(config.get("crypto_mode") or "safe"),
        qrcode_url=config.get("qrcode_url"),
        platform_templates=list(config.get("platform_templates") or []),
        version=snapshot.version,
        updated_at=snapshot.updated_at,
    )


async def update_config(
    db: AsyncSession, admin_id: int, body: WechatConfigUpdate
) -> WechatConfig:
    changes = body.model_dump(exclude_unset=True)
    await config_service.patch_config(
        db, admin_id, _CONFIG_NAMESPACE, changes, summary=f"更新公众号配置：{'/'.join(changes)}"
    )
    return await get_config(db)


async def create_qrcode(db: AsyncSession, admin_id: int) -> WechatQrcodeResult:
    """生成公众号带参二维码，并把图片地址落库到 wechat_mp 配置域（qrcode_url）。"""
    ensure_wechat_mp_configured()
    scene = f"admin-{uuid.uuid4().hex[:12]}"
    try:
        url = await get_wechat_mp_provider().create_qrcode(scene)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, detail="公众号二维码生成失败")
    await config_service.patch_config(
        db, admin_id, _CONFIG_NAMESPACE, {"qrcode_url": url}, summary="生成公众号二维码"
    )
    return WechatQrcodeResult(qrcode_url=url, scene_str=scene)


# ─────────────────────────── 粉丝 ───────────────────────────


def _fan_item(row: Any) -> WechatFanItem:
    return WechatFanItem(**dict(row))


async def list_fans(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None,
    group_id: int | None,
    subscribe_status: int | None,
    subscribe_date: str | None = None,
) -> WechatFanPage:
    where: list[str] = []
    params: dict[str, Any] = {}
    if keyword:
        where.append("(f.nickname LIKE :keyword OR f.openid LIKE :keyword)")
        params["keyword"] = f"%{keyword}%"
    if group_id is not None:
        where.append("f.group_id = :group_id")
        params["group_id"] = group_id
    if subscribe_status in (0, 1):
        where.append("f.subscribe_status = :subscribe_status")
        params["subscribe_status"] = subscribe_status
    if subscribe_date:
        where.append("DATE(f.subscribe_at) = :subscribe_date")
        params["subscribe_date"] = subscribe_date
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM admin_wechat_fan f"
                    " LEFT JOIN admin_wechat_fan_group g ON g.id = f.group_id " + where_sql
                ),
                params,
            )
        ).scalar()
        or 0
    )
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = (
        await db.execute(
            text(f"{_FAN_SELECT} {where_sql} ORDER BY f.id DESC LIMIT :limit OFFSET :offset"),
            params,
        )
    ).mappings().all()
    return WechatFanPage(
        items=[_fan_item(r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def sync_fans(db: AsyncSession, admin_id: int) -> WechatFanSyncResult:
    """从微信拉取粉丝并 upsert。凭据未配置时 503（适配层抛出，不写库）。"""
    provider = get_wechat_mp_provider()
    fans = await provider.fetch_fans()
    now = datetime.utcnow()
    created = updated = 0
    seen: set[str] = set()
    for fan in fans:
        seen.add(fan.openid)
        exists = (
            await db.execute(
                text("SELECT id FROM admin_wechat_fan WHERE openid = :openid"),
                {"openid": fan.openid},
            )
        ).scalar_one_or_none()
        if exists:
            updated += 1
            await db.execute(
                text(
                    "UPDATE admin_wechat_fan SET nickname = :nickname, avatar_url = :avatar_url,"
                    " gender = :gender, city = :city, province = :province,"
                    " subscribe_status = :subscribe_status, synced_at = :now,"
                    " updated_at = UTC_TIMESTAMP() WHERE id = :id"
                ),
                {
                    "nickname": fan.nickname,
                    "avatar_url": fan.avatar_url,
                    "gender": fan.gender,
                    "city": fan.city,
                    "province": fan.province,
                    "subscribe_status": fan.subscribe_status,
                    "now": now,
                    "id": int(exists),
                },
            )
            continue
        created += 1
        await db.execute(
            text(
                "INSERT INTO admin_wechat_fan"
                " (openid, unionid, nickname, avatar_url, gender, city, province,"
                "  subscribe_status, subscribe_at, synced_at)"
                " VALUES (:openid, :unionid, :nickname, :avatar_url, :gender, :city, :province,"
                "  :subscribe_status, :now, :now)"
            ),
            {
                "openid": fan.openid,
                "unionid": fan.unionid,
                "nickname": fan.nickname,
                "avatar_url": fan.avatar_url,
                "gender": fan.gender,
                "city": fan.city,
                "province": fan.province,
                "subscribe_status": fan.subscribe_status,
                "now": now,
            },
        )
    # 本次未返回的粉丝视为已取关（微信 user/get 只返回关注中的 openid）。
    # 先取出本地关注中清单在内存比对，再按 500 一批更新：既避免拼超长 IN 列表，
    # 也不依赖 datetime 精度比较。
    unsubscribed = 0
    if seen:
        local = (
            await db.execute(
                text("SELECT id, openid FROM admin_wechat_fan WHERE subscribe_status = 1")
            )
        ).mappings().all()
        stale_ids = [int(row["id"]) for row in local if row["openid"] not in seen]
        for start in range(0, len(stale_ids), 500):
            chunk = stale_ids[start : start + 500]
            result = await db.execute(
                text(
                    "UPDATE admin_wechat_fan SET subscribe_status = 0, unsubscribe_at = :now"
                    " WHERE id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"now": now, "ids": chunk},
            )
            unsubscribed += int(result.rowcount or 0)
    await _audit(
        db,
        admin_id,
        "wechat_fan.sync",
        "admin_wechat_fan",
        None,
        reason=f"拉取 {len(fans)} / 新增 {created} / 更新 {updated} / 取关 {unsubscribed}",
    )
    await db.commit()
    return WechatFanSyncResult(
        fetched=len(fans), created=created, updated=updated, unsubscribed=unsubscribed, synced_at=now
    )


# ─────────────────────────── 粉丝分组 ───────────────────────────


def _group_item(row: Any) -> WechatFanGroupItem:
    data = dict(row)
    data["fan_count"] = int(data.get("fan_count") or 0)
    return WechatFanGroupItem(**data)


_GROUP_SELECT = """SELECT g.id, g.name, g.provider_group_id, g.sort, g.created_at,
    (SELECT COUNT(*) FROM admin_wechat_fan f WHERE f.group_id = g.id) AS fan_count
    FROM admin_wechat_fan_group g"""


async def list_groups(db: AsyncSession) -> list[WechatFanGroupItem]:
    rows = (
        await db.execute(text(f"{_GROUP_SELECT} ORDER BY g.sort, g.id"))
    ).mappings().all()
    return [_group_item(r) for r in rows]


async def _get_group(db: AsyncSession, group_id: int) -> WechatFanGroupItem:
    row = (
        await db.execute(text(f"{_GROUP_SELECT} WHERE g.id = :id"), {"id": group_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="粉丝分组不存在")
    return _group_item(row)


async def create_group(
    db: AsyncSession, admin_id: int, body: WechatFanGroupCreate
) -> WechatFanGroupItem:
    exists = (
        await db.execute(
            text("SELECT 1 FROM admin_wechat_fan_group WHERE name = :name"), {"name": body.name}
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, detail="分组名称已存在")
    result = await db.execute(
        text("INSERT INTO admin_wechat_fan_group (name, sort) VALUES (:name, :sort)"),
        body.model_dump(),
    )
    item = await _get_group(db, int(result.lastrowid))
    await _audit(db, admin_id, "wechat_fan_group.create", "admin_wechat_fan_group", item.id)
    await db.commit()
    return item


async def update_group(
    db: AsyncSession, admin_id: int, group_id: int, body: WechatFanGroupUpdate
) -> WechatFanGroupItem:
    await _get_group(db, group_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("name"):
        dup = (
            await db.execute(
                text("SELECT 1 FROM admin_wechat_fan_group WHERE name = :name AND id <> :id"),
                {"name": values["name"], "id": group_id},
            )
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(409, detail="分组名称已存在")
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        await db.execute(
            text(
                f"UPDATE admin_wechat_fan_group SET {assignments}, updated_at = UTC_TIMESTAMP()"
                " WHERE id = :id"
            ),
            {**values, "id": group_id},
        )
    await _audit(db, admin_id, "wechat_fan_group.update", "admin_wechat_fan_group", group_id)
    await db.commit()
    return await _get_group(db, group_id)


async def delete_group(db: AsyncSession, admin_id: int, group_id: int) -> None:
    item = await _get_group(db, group_id)
    if item.fan_count:
        raise HTTPException(409, detail="分组下仍有粉丝，请先迁移后再删除")
    await db.execute(text("DELETE FROM admin_wechat_fan_group WHERE id = :id"), {"id": group_id})
    await _audit(db, admin_id, "wechat_fan_group.delete", "admin_wechat_fan_group", group_id)
    await db.commit()


# ─────────────────────────── 菜单 ───────────────────────────


def _menu_payload(buttons: list[Any]) -> dict[str, Any]:
    """把后台菜单结构转成微信 create API 需要的 ``{"button": [...]}`` 结构。"""

    def convert(button: Any) -> dict[str, Any]:
        data = button.model_dump(exclude_none=True) if hasattr(button, "model_dump") else dict(button)
        subs = data.pop("sub_button", None) or []
        if subs:
            data["sub_button"] = [convert(item) for item in subs]
        else:
            data.pop("sub_button", None)
        return data

    return {"button": [convert(item) for item in buttons]}


async def get_menu(db: AsyncSession) -> WechatMenu:
    snapshot = await config_service.get_config(db, _MENU_NAMESPACE)
    config = snapshot.config if isinstance(snapshot.config, dict) else {}
    raw_top = config.get("top_menus") or []
    return WechatMenu(
        top_menus=[_menu_button(item) for item in raw_top],
        published_at=config.get("published_at"),
        version=snapshot.version,
    )


def _menu_button(item: Any) -> dict[str, Any]:
    if isinstance(item, WechatMenuButton):
        return item.model_dump()
    data = dict(item or {})
    data["sub_button"] = [_menu_button(child) for child in data.get("sub_button") or []]
    return WechatMenuButton(**data).model_dump()


async def publish_menu(db: AsyncSession, admin_id: int, body: WechatMenuPublish) -> WechatMenu:
    """发布菜单：先调微信，成功后才写本地配置，失败不落半份菜单。"""
    ensure_wechat_mp_configured()
    provider = get_wechat_mp_provider()
    payload = _menu_payload(body.top_menus)
    await provider.publish_menu(payload)
    now = datetime.utcnow()
    await config_service.patch_config(
        db,
        admin_id,
        _MENU_NAMESPACE,
        {
            "top_menus": [button.model_dump() for button in body.top_menus],
            "published_at": now.isoformat(),
        },
        summary=f"发布公众号菜单（{len(body.top_menus)} 个一级菜单）",
    )
    return await get_menu(db)


# ─────────────────────────── 自动回复 ───────────────────────────


_RULE_SELECT = """SELECT id, name, match_type, keyword, match_mode, reply_type, content, status, sort,
    created_at, updated_at FROM admin_wechat_autoreply_rule"""

_rule_columns_ready = False


async def _ensure_rule_columns(db: AsyncSession) -> None:
    """幂等补列：早期建的表没有 match_mode（contain 包含 / equal 等于）。"""
    global _rule_columns_ready
    if _rule_columns_ready:
        return
    try:
        exists = (
            await db.execute(
                text("SHOW COLUMNS FROM admin_wechat_autoreply_rule LIKE 'match_mode'")
            )
        ).first()
        if not exists:
            await db.execute(
                text(
                    "ALTER TABLE admin_wechat_autoreply_rule ADD COLUMN match_mode"
                    " varchar(16) NOT NULL DEFAULT 'contain' COMMENT 'contain 包含 / equal 等于'"
                )
            )
            await db.commit()
        _rule_columns_ready = True
    except Exception:
        # 表不存在（尚未建表）等场景静默跳过，建表 DDL 已带完整列
        return


async def _get_rule(db: AsyncSession, rule_id: int) -> WechatReplyRuleItem:
    await _ensure_rule_columns(db)
    row = (
        await db.execute(text(f"{_RULE_SELECT} WHERE id = :id"), {"id": rule_id})
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="自动回复规则不存在")
    return WechatReplyRuleItem(**dict(row))


async def list_rules(
    db: AsyncSession, match_type: str | None, status: int | None
) -> list[WechatReplyRuleItem]:
    await _ensure_rule_columns(db)
    where: list[str] = []
    params: dict[str, Any] = {}
    if match_type in {"keyword", "subscribe", "default"}:
        where.append("match_type = :match_type")
        params["match_type"] = match_type
    if status in (0, 1):
        where.append("status = :status")
        params["status"] = status
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = (
        await db.execute(text(f"{_RULE_SELECT} {where_sql} ORDER BY sort, id"), params)
    ).mappings().all()
    return [WechatReplyRuleItem(**dict(r)) for r in rows]


async def create_rule(
    db: AsyncSession, admin_id: int, body: WechatReplyRuleCreate
) -> WechatReplyRuleItem:
    result = await db.execute(
        text(
            "INSERT INTO admin_wechat_autoreply_rule"
            " (name, match_type, keyword, match_mode, reply_type, content, status, sort, created_by)"
            " VALUES (:name, :match_type, :keyword, :match_mode, :reply_type, :content, :status, :sort, :actor)"
        ),
        {**body.model_dump(), "actor": admin_id},
    )
    item = await _get_rule(db, int(result.lastrowid))
    await _audit(db, admin_id, "wechat_autoreply.create", "admin_wechat_autoreply_rule", item.id)
    await db.commit()
    return item


async def update_rule(
    db: AsyncSession, admin_id: int, rule_id: int, body: WechatReplyRuleUpdate
) -> WechatReplyRuleItem:
    current = await _get_rule(db, rule_id)
    values = body.model_dump(exclude_unset=True)
    merged_type = values.get("match_type", current.match_type)
    merged_keyword = values.get("keyword", current.keyword)
    if merged_type == "keyword" and not (merged_keyword or "").strip():
        raise HTTPException(422, detail="关键词规则必须填写关键词")
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        await db.execute(
            text(
                f"UPDATE admin_wechat_autoreply_rule SET {assignments},"
                " updated_at = UTC_TIMESTAMP() WHERE id = :id"
            ),
            {**values, "id": rule_id},
        )
    await _audit(db, admin_id, "wechat_autoreply.update", "admin_wechat_autoreply_rule", rule_id)
    await db.commit()
    return await _get_rule(db, rule_id)


async def delete_rule(db: AsyncSession, admin_id: int, rule_id: int) -> None:
    await _get_rule(db, rule_id)
    await db.execute(text("DELETE FROM admin_wechat_autoreply_rule WHERE id = :id"), {"id": rule_id})
    await _audit(db, admin_id, "wechat_autoreply.delete", "admin_wechat_autoreply_rule", rule_id)
    await db.commit()


# ─────────────────────────── 模板消息 ───────────────────────────


_TEMPLATE_SELECT = """SELECT id, template_id, title, primary_industry, deputy_industry, content,
    status, synced_at FROM admin_wechat_template"""


async def list_templates(db: AsyncSession, keyword: str | None) -> list[WechatTemplateItem]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if keyword:
        where.append("(title LIKE :keyword OR template_id LIKE :keyword)")
        params["keyword"] = f"%{keyword}%"
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = (
        await db.execute(text(f"{_TEMPLATE_SELECT} {where_sql} ORDER BY id DESC"), params)
    ).mappings().all()
    return [WechatTemplateItem(**dict(r)) for r in rows]


async def sync_templates(db: AsyncSession, admin_id: int) -> WechatTemplateSyncResult:
    """从微信拉取模板消息并 upsert；凭据未配置时适配层直接 503。"""
    provider = get_wechat_mp_provider()
    templates = await provider.fetch_templates()
    now = datetime.utcnow()
    created = updated = 0
    for template in templates:
        exists = (
            await db.execute(
                text("SELECT id FROM admin_wechat_template WHERE template_id = :tid"),
                {"tid": template.template_id},
            )
        ).scalar_one_or_none()
        if exists:
            updated += 1
            await db.execute(
                text(
                    "UPDATE admin_wechat_template SET title = :title,"
                    " primary_industry = :primary_industry, deputy_industry = :deputy_industry,"
                    " content = :content, synced_at = :now WHERE id = :id"
                ),
                {
                    "title": template.title,
                    "primary_industry": template.primary_industry,
                    "deputy_industry": template.deputy_industry,
                    "content": template.content,
                    "now": now,
                    "id": int(exists),
                },
            )
            continue
        created += 1
        await db.execute(
            text(
                "INSERT INTO admin_wechat_template"
                " (template_id, title, primary_industry, deputy_industry, content, synced_at)"
                " VALUES (:tid, :title, :primary_industry, :deputy_industry, :content, :now)"
            ),
            {
                "tid": template.template_id,
                "title": template.title,
                "primary_industry": template.primary_industry,
                "deputy_industry": template.deputy_industry,
                "content": template.content,
                "now": now,
            },
        )
    await _audit(
        db,
        admin_id,
        "wechat_template.sync",
        "admin_wechat_template",
        None,
        reason=f"拉取 {len(templates)} / 新增 {created} / 更新 {updated}",
    )
    await db.commit()
    return WechatTemplateSyncResult(
        fetched=len(templates), created=created, updated=updated, synced_at=now
    )


# ─────────────────────────── 群发 / 模板发送 ───────────────────────────


async def _resolve_targets(
    db: AsyncSession, group_id: int | None, openids: list[str]
) -> list[str]:
    """确定投递目标 openid 列表：显式 openids 优先，否则按分组/全部关注粉丝。"""
    if openids:
        targets = list(dict.fromkeys(openid.strip() for openid in openids if openid.strip()))
        if len(targets) > _MAX_EXPLICIT_TARGETS:
            raise HTTPException(422, detail=f"单次最多向 {_MAX_EXPLICIT_TARGETS} 位粉丝发送")
        return targets
    where = "subscribe_status = 1"
    params: dict[str, Any] = {}
    if group_id is not None:
        await _get_group(db, group_id)
        where += " AND group_id = :group_id"
        params["group_id"] = group_id
    rows = (
        await db.execute(
            text(f"SELECT openid FROM admin_wechat_fan WHERE {where} LIMIT :limit"),
            {**params, "limit": _MAX_BROADCAST_TARGETS + 1},
        )
    ).scalars().all()
    if len(rows) > _MAX_BROADCAST_TARGETS:
        raise HTTPException(422, detail=f"单次最多向 {_MAX_BROADCAST_TARGETS} 位粉丝发送")
    return [str(value) for value in rows]


async def send_message(db: AsyncSession, admin_id: int, body: WechatSendRequest) -> WechatSendResult:
    """群发或模板消息。适配层在发送失败时抛 503，记录按失败落库并保留原因。"""
    ensure_wechat_mp_configured()
    provider = get_wechat_mp_provider()
    targets = await _resolve_targets(db, body.group_id, body.openids)
    if not targets:
        raise HTTPException(422, detail="没有匹配到可发送的粉丝")

    template_external_id: str | None = None
    if body.send_type == "template":
        row = (
            await db.execute(
                text(
                    "SELECT template_id, status FROM admin_wechat_template WHERE id = :id"
                ),
                {"id": body.template_id},
            )
        ).mappings().first()
        if not row:
            raise HTTPException(404, detail="模板消息不存在")
        if int(row["status"]) != 1:
            raise HTTPException(409, detail="模板已停用，不能发送")
        template_external_id = str(row["template_id"])

    batch_no = uuid.uuid4().hex[:24]
    sent = failed = 0
    error_text: str | None = None
    provider_msg_id: str | None = None
    # 适配层负责包成微信要求的 {"value": ...}，这里保持 key -> 原始字符串。
    for openid in targets:
        try:
            if body.send_type == "template":
                msg_id = await provider.send_template(
                    openid, template_external_id or "", dict(body.data), body.url
                )
                provider_msg_id = msg_id or provider_msg_id
            else:
                provider_msg_id = (
                    await provider.broadcast([openid], body.content or "", body.url)
                    or provider_msg_id
                )
        except HTTPException as exc:
            failed += 1
            error_text = str(exc.detail)[:500]
            continue
        except Exception as exc:  # 单条失败不阻断整批
            failed += 1
            error_text = str(exc)[:500]
            continue
        sent += 1

    status = "sent" if sent else "failed"
    now = datetime.utcnow()
    result = await db.execute(
        text(
            "INSERT INTO admin_wechat_message"
            " (batch_no, send_type, template_id, group_id, title, content, url,"
            "  target_count, sent_count, failed_count, status, error, provider_msg_id,"
            "  created_by, sent_at)"
            " VALUES (:batch_no, :send_type, :template_id, :group_id, :title, :content, :url,"
            "  :target_count, :sent_count, :failed_count, :status, :error, :provider_msg_id,"
            "  :actor, :now)"
        ),
        {
            "batch_no": batch_no,
            "send_type": body.send_type,
            "template_id": body.template_id,
            "group_id": body.group_id,
            "title": body.title,
            "content": body.content,
            "url": body.url,
            "target_count": len(targets),
            "sent_count": sent,
            "failed_count": failed,
            "status": status,
            "error": error_text,
            "provider_msg_id": provider_msg_id,
            "actor": admin_id,
            "now": now,
        },
    )
    await _audit(
        db,
        admin_id,
        "wechat_message.send",
        "admin_wechat_message",
        int(result.lastrowid),
        reason=f"批次 {batch_no} 成功 {sent} / 失败 {failed}",
    )
    await db.commit()
    return WechatSendResult(
        batch_no=batch_no,
        send_type=body.send_type,
        target_count=len(targets),
        sent_count=sent,
        failed_count=failed,
        provider_msg_id=provider_msg_id,
    )


# ─────────────────────────── 发送记录 ───────────────────────────


async def list_messages(
    db: AsyncSession,
    page: int,
    page_size: int,
    send_type: str | None,
    status: str | None,
    start_date: str | None,
    end_date: str | None,
) -> WechatMessagePage:
    where: list[str] = []
    params: dict[str, Any] = {}
    if send_type in {"broadcast", "template"}:
        where.append("m.send_type = :send_type")
        params["send_type"] = send_type
    if status in {"queued", "sent", "failed"}:
        where.append("m.status = :status")
        params["status"] = status
    if start_date:
        where.append("m.created_at >= :start_date")
        params["start_date"] = start_date
    if end_date:
        where.append("m.created_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")
        params["end_date"] = end_date
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM admin_wechat_message m"
                    " LEFT JOIN admin_wechat_fan_group g ON g.id = m.group_id " + where_sql
                ),
                params,
            )
        ).scalar()
        or 0
    )
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = (
        await db.execute(
            text(f"{_MESSAGE_SELECT} {where_sql} ORDER BY m.id DESC LIMIT :limit OFFSET :offset"),
            params,
        )
    ).mappings().all()
    return WechatMessagePage(
        items=[WechatMessageItem(**dict(r)) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def _audit(
    db: AsyncSession,
    actor_id: int,
    action: str,
    resource_type: str,
    resource_id: int | None,
    reason: str | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO business_audit_log"
            " (actor_user_id, action, resource_type, resource_id, reason)"
            " VALUES (:actor, :action, :resource_type, :resource_id, :reason)"
        ),
        {
            "actor": actor_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "reason": reason,
        },
    )
