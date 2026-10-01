"""Member administration services backed by existing user tables."""

import json

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sensitive_fields import is_masked_value, mask_phone
from app.schemas.matchmaker_member_admin import (
    CertificationDetail,
    CertificationMaterial,
    MatchmakerMemberAdminItem,
    MatchmakerMemberCreate,
    MatchmakerMemberUpdate,
    MemberAuditLogItem,
)


def _mask_phone(phone: str | None) -> str | None:
    # 旧实现用 ``phone[:3] + **** + phone[-4:]``：号码只有 7-8 位时首尾保留位数已达总长，
    # 等于把整串号码原样返回。改为复用 mask_middle 的“不足则全掩码”口径。
    return mask_phone(phone)


async def _member(
    db: AsyncSession, member_id: int, scope: str, scope_params: dict[str, object],
) -> MatchmakerMemberAdminItem:
    """读取单个会员。

    ``scope`` 由调用方通过 `CurrentMatchmakerAdmin.scope_exists_clause` 生成（D-4）；
    ``scope_params`` 必须是生成谓词时用的**同一个**参数字典：谓词里的 ``:scope_*``
    占位符只能靠它绑定。各处早先传一次性空字典，使 SELF / STORE / ORGANIZATION 三档
    普通账号因参数缺失而无法编辑本人范围内会员，因此本参数不接受默认值。
    越权访问统一按 404 「会员不存在」返回，避免通过状态码差异枚举他组织会员 ID。
    """
    row = (await db.execute(text(f"""SELECT u.id, u.nickname, u.phone, u.gender, u.status,
        u.created_at, u.updated_at, a.matchmaker_id,
        v.vip_end_at,
        CASE WHEN v.user_id IS NULL OR (v.vip_end_at IS NOT NULL AND v.vip_end_at <= UTC_TIMESTAMP())
             THEN 0 ELSE 1 END AS is_vip
        FROM users u
        LEFT JOIN (SELECT user_id, MAX(end_at) vip_end_at FROM user_membership
            WHERE status = 1 GROUP BY user_id) v ON v.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(matchmaker_id) AS matchmaker_id FROM resource_assignment
            WHERE status = 1 GROUP BY user_id) a ON a.user_id = u.id
        WHERE u.id = :id AND ({scope})"""), {"id": member_id, **scope_params})).mappings().first()
    if not row:
        raise HTTPException(404, detail="会员不存在")
    return MatchmakerMemberAdminItem(
        id=int(row["id"]),
        nickname=row["nickname"],
        phone_masked=_mask_phone(row["phone"]),
        gender=row["gender"],
        status=int(row["status"]),
        is_vip=bool(row["is_vip"]),
        vip_end_at=row["vip_end_at"],
        matchmaker_id=row["matchmaker_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def create_member(
    db: AsyncSession,
    body: MatchmakerMemberCreate,
    actor_id: int,
    *,
    organization_id: int | None = None,
    matchmaker_user_id: int | None = None,
) -> MatchmakerMemberAdminItem:
    duplicate = await db.execute(text("SELECT id FROM users WHERE phone = :phone"), {"phone": body.phone})
    if duplicate.scalar():
        raise HTTPException(409, detail="手机号已注册")
    result = await db.execute(text("""INSERT INTO users
        (phone, nickname, gender, birthday, is_married, avatar, status)
        VALUES (:phone, :nickname, :gender, :birthday, :is_married, :avatar, 1)"""), {
        **body.model_dump(exclude={"remark"}), "phone": body.phone,
    })
    member_id = int(result.lastrowid)
    # D-4：建档必须同时落归属。否则 SELF / STORE / ORGANIZATION 账号建出的会员
    # 不属于任何组织或红娘，在 fail-closed 的列表里永远查不到，也无法改派。
    if organization_id is not None or matchmaker_user_id is not None:
        await db.execute(text("""INSERT INTO resource_assignment
            (user_id, organization_id, matchmaker_id, source, assigned_by)
            VALUES (:user_id, :organization_id, :matchmaker_id, 'manual', :assigned_by)"""), {
            "user_id": member_id,
            "organization_id": organization_id,
            "matchmaker_id": matchmaker_user_id,
            "assigned_by": actor_id,
        })
    await db.execute(text("""INSERT INTO matchmaker_admin_member_note
        (user_id, note, updated_by) VALUES (:user_id, :note, :updated_by)"""), {
        "user_id": member_id, "note": body.remark, "updated_by": actor_id,
    })
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (:actor, 'member.create', 'user', :resource_id, :reason)"""), {
        "actor": actor_id, "resource_id": member_id, "reason": body.remark,
    })
    await db.commit()
    # 该记录由调用方本人刚创建，回读不叠加作用域，避免归属未即时生效时误判 404。
    return await _member(db, member_id, "1 = 1", {})


async def update_member(
    db: AsyncSession, member_id: int, body: MatchmakerMemberUpdate, actor_id: int, *,
    scope: str, scope_params: dict[str, object],
) -> MatchmakerMemberAdminItem:
    await _member(db, member_id, scope, scope_params)
    values = body.model_dump(exclude_unset=True)
    # 后台表单预填的是掩码值，原样提交会用 ``***`` 覆盖库中真实微信号：掩码形态一律
    # 丢弃该字段，确实要改必须提交新的完整微信号。
    if is_masked_value(values.get("wechat")):
        values.pop("wechat")
    remark = values.pop("remark", None)
    user_values = {key: values.pop(key) for key in ("nickname", "gender", "birthday", "is_married", "avatar") if key in values}
    auth_values = {key: values.pop(key) for key in ("education", "school", "job", "company", "auth_status") if key in values}
    match_status = values.pop("match_status", None) if "match_status" in values else None
    profile_columns: set[str] = set()
    if values:
        profile_columns = {
            str(row[0])
            for row in (
                await db.execute(
                    text("""SELECT COLUMN_NAME FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_profile'""")
                )
            ).all()
        }
        missing_profile_values = set(values) - profile_columns
        if missing_profile_values:
            raise HTTPException(
                status_code=503,
                detail="数据库缺少会员资料字段，请先重启服务完成数据库结构迁移",
            )
    if auth_values:
        auth_columns = {
            str(row[0])
            for row in (
                await db.execute(
                    text("""SELECT COLUMN_NAME FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_auth'""")
                )
            ).all()
        }
        missing_auth_values = set(auth_values) - auth_columns
        if missing_auth_values:
            raise HTTPException(
                status_code=503,
                detail="数据库缺少会员认证字段，请先重启服务完成数据库结构迁移",
            )
    if "tags" in values and isinstance(values["tags"], list):
        # Profile tags are stored as a category map; preserve a simple list as custom tags.
        values["tags"] = {"custom": values["tags"]}
    if "tags" in values:
        values["tags"] = json.dumps(values["tags"], ensure_ascii=False) if values["tags"] is not None else None
    if user_values:
        assignments = ", ".join(f"{key} = :{key}" for key in user_values)
        await db.execute(text(f"UPDATE users SET {assignments}, updated_at = UTC_TIMESTAMP() WHERE id = :id"), {
            **user_values, "id": member_id,
        })
    if values:
        columns = ["user_id", *values]
        updates = ", ".join(f"{key} = VALUES({key})" for key in values)
        await db.execute(text(f"""INSERT INTO user_profile ({', '.join(columns)})
            VALUES ({', '.join(':' + key for key in columns)})
            ON DUPLICATE KEY UPDATE {updates}"""), {"user_id": member_id, **values})
    if auth_values:
        await db.execute(text(f"""INSERT INTO user_auth (user_id, {', '.join(auth_values)})
            VALUES (:user_id, {', '.join(':' + key for key in auth_values)})
            ON DUPLICATE KEY UPDATE {', '.join(f'{key} = VALUES({key})' for key in auth_values)}"""),
            {"user_id": member_id, **auth_values})
    if match_status is not None:
        await db.execute(text("""INSERT INTO user_privacy (user_id, match_status)
            VALUES (:user_id, :match_status)
            ON DUPLICATE KEY UPDATE match_status = VALUES(match_status), updated_at = UTC_TIMESTAMP()"""), {
            "user_id": member_id,
            "match_status": match_status,
        })
    if remark is not None:
        await db.execute(text("""INSERT INTO matchmaker_admin_member_note (user_id, note, updated_by)
            VALUES (:user_id, :note, :updated_by)
            ON DUPLICATE KEY UPDATE note = VALUES(note), updated_by = VALUES(updated_by),
            updated_at = UTC_TIMESTAMP()"""), {"user_id": member_id, "note": remark, "updated_by": actor_id})
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, reason)
        VALUES (:actor, 'member.update', 'user', :resource_id, :reason)"""), {
        "actor": actor_id, "resource_id": member_id, "reason": remark,
    })
    await db.commit()
    return await _member(db, member_id, scope, scope_params)


async def certification_detail(
    db: AsyncSession, member_id: int, kind: str, *,
    scope: str, scope_params: dict[str, object],
) -> CertificationDetail:
    """读取单个认证项。

    作用域守卫必须在取数**之前**：``users`` / ``user_auth`` 本身不带归属列，只有先按
    ``scope`` 命中会员才进入认证材料读取。把 :func:`_member` 放在「查不到」分支里，
    等于让越权请求先完整读到别人的学历、房产与证件图 URL，只在缺行时才想起鉴权。
    """
    await _member(db, member_id, scope, scope_params)
    if kind == "marriage":
        row = (await db.execute(text(
            "SELECT id, is_married, updated_at FROM users WHERE id = :id"
        ), {"id": member_id})).mappings().first()
        if not row:
            raise HTTPException(404, detail="会员不存在")
        return CertificationDetail(
            user_id=member_id,
            kind=kind,
            status=1 if row["is_married"] else 0,
            submitted_at=None,
            reviewed_at=row["updated_at"],
            fail_reason=None,
            value=str(row["is_married"]) if row["is_married"] else None,
            material_urls=[],
            reviewer_id=None,
            audit_history=[],
        )

    fields = {
        "education": ("education_verified", "education", "education_cert", "fail_reason", "created_at", "updated_at"),
        "house": ("house_verified", "house_cert", "house_cert", "fail_reason", "created_at", "updated_at"),
    }
    if kind not in fields:
        raise HTTPException(422, detail="不支持的认证类型")
    status_field, value_field, material_field, fail_field, submitted_field, reviewed_field = fields[kind]
    row = (await db.execute(text(f"""SELECT ua.user_id, ua.{status_field} AS status,
        ua.{value_field} AS value, ua.{material_field} AS material,
        ua.{fail_field} AS fail_reason, ua.{submitted_field} AS submitted_at,
        ua.{reviewed_field} AS reviewed_at
        FROM user_auth ua WHERE ua.user_id = :id"""), {"id": member_id})).mappings().first()
    if not row:
        return CertificationDetail(
            user_id=member_id, kind=kind, status=0, submitted_at=None, reviewed_at=None,
            fail_reason=None, value=None, material_urls=[], reviewer_id=None, audit_history=[],
        )
    material_urls = []
    if row["material"]:
        material_urls = [CertificationMaterial(id=0, url=str(row["material"]), thumbnail_url=None, expires_at=None)]
    return CertificationDetail(
        user_id=member_id, kind=kind, status=int(row["status"] or 0),
        submitted_at=row["submitted_at"], reviewed_at=row["reviewed_at"],
        fail_reason=row["fail_reason"], value=row["value"],
        material_urls=material_urls, reviewer_id=None, audit_history=[],
    )


async def member_audit_logs(
    db: AsyncSession, member_id: int, *, scope: str, scope_params: dict[str, object],
) -> list[MemberAuditLogItem]:
    await _member(db, member_id, scope, scope_params)
    result = await db.execute(text("""SELECT id, action, resource_type, resource_id, reason, created_at
        FROM business_audit_log WHERE resource_type = 'user' AND resource_id = :id
        ORDER BY id DESC LIMIT 200"""), {"id": member_id})
    return [MemberAuditLogItem(**dict(row)) for row in result.mappings().all()]
