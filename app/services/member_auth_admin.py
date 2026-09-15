"""会员认证（M3-1）管理后台服务层。

覆盖实名/承诺/婚姻/房产/学历/其他六类认证的列表、统计、审核、删除，
以及「其他认证」的认证类型（config_auth_type）增删改查。
写操作统一写入 business_audit_log；金额类字段以字符串返回（Decimal 序列化）。
"""

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.member_auth_admin import (
    AuthTypeCreate,
    AuthTypeItem,
    AuthTypeUpdate,
    CommitmentReviewItem,
    CommitmentReviewPage,
    EducationReviewItem,
    EducationReviewPage,
    HouseReviewItem,
    HouseReviewPage,
    MarriageReviewItem,
    MarriageReviewPage,
    MarriageStats,
    OtherReviewItem,
    OtherReviewPage,
    RealnameReviewItem,
    RealnameReviewPage,
    RealnameStats,
    ReviewActionRequest,
)


# ─── 通用工具 ─────────────────────────────────────────────────────


def _mask_id(raw: Any) -> str | None:
    """身份证号仅掩码展示：保留前 12 位，其余以 ****** 代替。

    注意：user_auth.id_card 在生产环境为加密存储，此处对原文做掩码展示，
    仅用于后台审核界面，不会回显明文。
    """
    if raw is None:
        return None
    s = str(raw)
    if len(s) >= 12:
        return s[:12] + "******"
    if len(s) >= 6:
        return s[:6] + "******"
    return s


def _gender_label(value: Any) -> str | None:
    if value == 1:
        return "男"
    if value == 2:
        return "女"
    return None


def _date_str(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return value.strftime("%Y-%m-%d")
    except AttributeError:
        return str(value)


def _decimal_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _verify_result(value: int) -> tuple[str, str]:
    """0待审 1通过 2未通过 → (枚举, 标签)。"""
    if value == 1:
        return "pass", "通过"
    if value == 2:
        return "fail", "未通过"
    return "pending", "待审"


def _qualification_result(value: int) -> tuple[str, str]:
    """用户资质四态：0未提交、1审核中、2通过、3未通过。"""
    if value == 2:
        return "pass", "通过"
    if value == 3:
        return "fail", "未通过"
    return "pending", "待审"


def _realname_result(value: int) -> tuple[str, str]:
    """实名结果：1成功 2失败 0待审。"""
    if value == 1:
        return "success", "认证成功"
    if value == 2:
        return "fail", "认证失败"
    return "pending", "待审"


def _marriage_result(value: Any) -> tuple[str, str]:
    if value == "married":
        return "married", "已婚"
    if value == "no_record":
        return "no_record", "无登记信息"
    if value == "divorced":
        return "divorced", "离异"
    return (str(value) if value is not None else ""), (str(value) if value is not None else "")


def _member_code_sql() -> str:
    return "CONCAT('G', LPAD(u.id, 6, '0'))"


# ─── 实名认证 ─────────────────────────────────────────────────────


async def list_realname_reviews(
    db: AsyncSession,
    page: int,
    page_size: int,
    status: str,
    keyword: str | None,
) -> RealnameReviewPage:
    where = ["1=1"]
    params: dict[str, Any] = {}
    if status == "success":
        where.append("ua.face_verified = 1")
    elif status == "fail":
        where.append("ua.face_verified = 2")
    # status=all（默认）包含 verified 0/1/2，待审行 result 记为 pending
    if keyword:
        where.append(
            "(u.nickname LIKE CONCAT('%', :kw, '%') OR u.phone LIKE CONCAT('%', :kw, '%') "
            "OR ua.real_name LIKE CONCAT('%', :kw, '%'))"
        )
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = "FROM user_auth ua JOIN users u ON u.id = ua.user_id"
    rows = await db.execute(
        text(
            f"SELECT ua.id, ua.user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
            f"ua.real_name, ua.id_card, u.gender, u.birthday, ua.id_card_issued, "
            f"ua.id_card_front, ua.id_card_back, ua.face_method, ua.face_vendor, "
            f"ua.face_score, ua.face_photo, ua.face_verified, ua.created_at "
            f"{base} WHERE {clause} ORDER BY ua.id DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {clause}"), params)
    total = int(count.scalar() or 0)
    items = [_build_realname(r) for r in rows.mappings().all()]
    return RealnameReviewPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


def _build_realname(r: dict[str, Any]) -> RealnameReviewItem:
    v = int(r["face_verified"] or 0)
    result, label = _realname_result(v)
    return RealnameReviewItem(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        member_code=r["member_code"],
        nickname=r["nickname"],
        avatar=r["avatar"],
        real_name=r["real_name"],
        id_card_masked=_mask_id(r["id_card"]),
        file_url=r["face_photo"],
        result=result,
        result_label=label,
        created_at=r["created_at"],
        gender=_gender_label(r["gender"]),
        birthday=_date_str(r["birthday"]),
        id_card_issued=r["id_card_issued"],
        id_card_front=r["id_card_front"],
        id_card_back=r["id_card_back"],
        face_method=r["face_method"],
        face_vendor=r["face_vendor"],
        face_score=_decimal_str(r["face_score"]),
        face_photo=r["face_photo"],
    )


async def realname_stats(db: AsyncSession) -> RealnameStats:
    row = (
        await db.execute(
            text(
                "SELECT "
                "SUM(CASE WHEN face_verified = 1 THEN 1 ELSE 0 END) AS success_count, "
                "SUM(CASE WHEN face_verified = 2 THEN 1 ELSE 0 END) AS fail_count, "
                "SUM(CASE WHEN face_verified IN (1, 2) THEN 1 ELSE 0 END) AS total_consumed "
                "FROM user_auth"
            )
        )
    ).mappings().first()
    # 人脸核验余量：当前无独立额度数据源（充值能力未接入），默认返回 0。
    # 如需对接充值，可改为读取独立额度表或 platform_pay 配置。
    return RealnameStats(
        quota_remaining=0,
        success_count=int(row["success_count"] or 0),
        fail_count=int(row["fail_count"] or 0),
        total_consumed=int(row["total_consumed"] or 0),
    )


# ─── 会员承诺 ─────────────────────────────────────────────────────


async def list_commitment_reviews(
    db: AsyncSession,
    page: int,
    page_size: int,
    status: str,
    keyword: str | None,
) -> CommitmentReviewPage:
    where = ["1=1"]
    params: dict[str, Any] = {}
    if status == "pass":
        where.append("ucs.status = 1")
    elif status == "pending":
        where.append("ucs.status = 0")
    elif status == "fail":
        where.append("ucs.status = 2")
    if keyword:
        where.append(
            "(u.nickname LIKE CONCAT('%', :kw, '%') OR u.phone LIKE CONCAT('%', :kw, '%') "
            "OR ua.real_name LIKE CONCAT('%', :kw, '%'))"
        )
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = "FROM user_commitment_sign ucs JOIN users u ON u.id = ucs.user_id LEFT JOIN user_auth ua ON ua.user_id = u.id"
    rows = await db.execute(
        text(
            f"SELECT ucs.id, ucs.user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
            f"ua.real_name, ua.id_card, ucs.file_url, ucs.status, ucs.sign_times, ucs.title, ucs.created_at "
            f"{base} WHERE {clause} ORDER BY ucs.id DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {clause}"), params)
    total = int(count.scalar() or 0)
    items = [_build_commitment(r) for r in rows.mappings().all()]
    return CommitmentReviewPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


def _build_commitment(r: dict[str, Any]) -> CommitmentReviewItem:
    v = int(r["status"] or 0)
    result, label = _verify_result(v)
    return CommitmentReviewItem(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        member_code=r["member_code"],
        nickname=r["nickname"],
        avatar=r["avatar"],
        real_name=r["real_name"],
        id_card_masked=_mask_id(r["id_card"]),
        file_url=r["file_url"],
        result=result,
        result_label=label,
        created_at=r["created_at"],
        sign_times=int(r["sign_times"] or 1),
        title=r["title"],
    )


# ─── 婚姻状况 ─────────────────────────────────────────────────────


async def list_marriage_reviews(
    db: AsyncSession,
    page: int,
    page_size: int,
    status: str,
    keyword: str | None,
) -> MarriageReviewPage:
    where = ["1=1"]
    params: dict[str, Any] = {}
    if status in ("married", "no_record", "divorced"):
        where.append("mc.result = :st")
        params["st"] = status
    if keyword:
        where.append(
            "(u.nickname LIKE CONCAT('%', :kw, '%') OR u.phone LIKE CONCAT('%', :kw, '%') "
            "OR ua.real_name LIKE CONCAT('%', :kw, '%'))"
        )
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = "FROM user_marriage_check mc JOIN users u ON u.id = mc.user_id LEFT JOIN user_auth ua ON ua.user_id = u.id"
    rows = await db.execute(
        text(
            f"SELECT mc.id, mc.user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
            f"ua.real_name, ua.id_card, mc.check_method, mc.declared_status, mc.result, mc.cost, "
            f"mc.checked_at, mc.created_at "
            f"{base} WHERE {clause} ORDER BY mc.id DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {clause}"), params)
    total = int(count.scalar() or 0)
    items = [_build_marriage(r) for r in rows.mappings().all()]
    return MarriageReviewPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


def _build_marriage(r: dict[str, Any]) -> MarriageReviewItem:
    result, label = _marriage_result(r["result"])
    return MarriageReviewItem(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        member_code=r["member_code"],
        nickname=r["nickname"],
        avatar=r["avatar"],
        real_name=r["real_name"],
        id_card_masked=_mask_id(r["id_card"]),
        file_url=None,
        result=result,
        result_label=label,
        created_at=r["created_at"],
        check_method=r["check_method"],
        declared_status=r["declared_status"],
        cost=_decimal_str(r["cost"]),
        checked_at=r["checked_at"],
    )


async def marriage_stats(db: AsyncSession) -> MarriageStats:
    total = int((await db.execute(text("SELECT COUNT(*) FROM user_marriage_check"))).scalar() or 0)
    # 婚况核验查询余量：当前无独立额度数据源，默认返回 0。
    return MarriageStats(quota_remaining=0, total_consumed=total)


# ─── 房产认证 ─────────────────────────────────────────────────────


async def list_house_reviews(
    db: AsyncSession,
    page: int,
    page_size: int,
    status: str,
    keyword: str | None,
) -> HouseReviewPage:
    where = ["1=1"]
    params: dict[str, Any] = {}
    if status == "pass":
        where.append("ua.house_verified = 1")
    elif status == "pending":
        where.append("ua.house_verified = 0")
    elif status == "fail":
        where.append("ua.house_verified = 2")
    if keyword:
        where.append(
            "(u.nickname LIKE CONCAT('%', :kw, '%') OR u.phone LIKE CONCAT('%', :kw, '%') "
            "OR ua.real_name LIKE CONCAT('%', :kw, '%'))"
        )
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = "FROM user_auth ua JOIN users u ON u.id = ua.user_id"
    rows = await db.execute(
        text(
            f"SELECT ua.id, ua.user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
            f"ua.real_name, ua.id_card, ua.house_cert, ua.house_verified, ua.created_at "
            f"{base} WHERE {clause} ORDER BY ua.id DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {clause}"), params)
    total = int(count.scalar() or 0)
    items = [_build_house(r) for r in rows.mappings().all()]
    return HouseReviewPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


def _build_house(r: dict[str, Any]) -> HouseReviewItem:
    v = int(r["house_verified"] or 0)
    result, label = _verify_result(v)
    return HouseReviewItem(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        member_code=r["member_code"],
        nickname=r["nickname"],
        avatar=r["avatar"],
        real_name=r["real_name"],
        id_card_masked=_mask_id(r["id_card"]),
        file_url=r["house_cert"],
        result=result,
        result_label=label,
        created_at=r["created_at"],
    )


# ─── 学历认证 ─────────────────────────────────────────────────────


async def list_education_reviews(
    db: AsyncSession,
    page: int,
    page_size: int,
    status: str,
    keyword: str | None,
) -> EducationReviewPage:
    where = ["ua.education_cert IS NOT NULL"]
    params: dict[str, Any] = {}
    if status == "pass":
        where.append("ua.education_verified = 2")
    elif status == "pending":
        where.append("ua.education_verified = 1")
    elif status == "fail":
        where.append("ua.education_verified = 3")
    if keyword:
        where.append(
            "(u.nickname LIKE CONCAT('%', :kw, '%') OR u.phone LIKE CONCAT('%', :kw, '%') "
            "OR ua.real_name LIKE CONCAT('%', :kw, '%'))"
        )
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = "FROM user_auth ua JOIN users u ON u.id = ua.user_id"
    rows = await db.execute(
        text(
            f"SELECT ua.id, ua.user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
            f"ua.real_name, ua.id_card, ua.education, ua.school, ua.education_cert, "
            f"ua.education_verified, COALESCE(ua.education_submitted_at, ua.created_at) AS created_at "
            f"{base} WHERE {clause} ORDER BY ua.id DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {clause}"), params)
    total = int(count.scalar() or 0)
    items = [_build_education(r) for r in rows.mappings().all()]
    return EducationReviewPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


def _build_education(r: dict[str, Any]) -> EducationReviewItem:
    v = int(r["education_verified"] or 0)
    result, label = _qualification_result(v)
    return EducationReviewItem(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        member_code=r["member_code"],
        nickname=r["nickname"],
        avatar=r["avatar"],
        real_name=r["real_name"],
        id_card_masked=_mask_id(r["id_card"]),
        file_url=r["education_cert"],
        result=result,
        result_label=label,
        created_at=r["created_at"],
        degree=r["education"],
        school=r["school"],
    )


# ─── 其他认证 ─────────────────────────────────────────────────────


async def list_other_reviews(
    db: AsyncSession,
    page: int,
    page_size: int,
    status: str,
    keyword: str | None,
    auth_type_id: int | None = None,
) -> OtherReviewPage:
    where = ["1=1"]
    params: dict[str, Any] = {}
    if status == "pass":
        where.append("uae.status = 1")
    elif status == "pending":
        where.append("uae.status = 0")
    elif status == "fail":
        where.append("uae.status = 2")
    if auth_type_id:
        where.append("uae.auth_type_id = :tid")
        params["tid"] = auth_type_id
    if keyword:
        where.append(
            "(u.nickname LIKE CONCAT('%', :kw, '%') OR u.phone LIKE CONCAT('%', :kw, '%') "
            "OR ua.real_name LIKE CONCAT('%', :kw, '%'))"
        )
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = "FROM user_auth_extra uae JOIN users u ON u.id = uae.user_id LEFT JOIN user_auth ua ON ua.user_id = u.id"
    rows = await db.execute(
        text(
            f"SELECT uae.id, uae.user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
            f"ua.real_name, ua.id_card, uae.file_url, uae.status, uae.auth_type_id, uae.auth_type_name, "
            f"uae.created_at "
            f"{base} WHERE {clause} ORDER BY uae.id DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {clause}"), params)
    total = int(count.scalar() or 0)
    items = [_build_other(r) for r in rows.mappings().all()]
    return OtherReviewPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


def _build_other(r: dict[str, Any]) -> OtherReviewItem:
    v = int(r["status"] or 0)
    result, label = _verify_result(v)
    return OtherReviewItem(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        member_code=r["member_code"],
        nickname=r["nickname"],
        avatar=r["avatar"],
        real_name=r["real_name"],
        id_card_masked=_mask_id(r["id_card"]),
        file_url=r["file_url"],
        result=result,
        result_label=label,
        created_at=r["created_at"],
        auth_type_id=int(r["auth_type_id"]) if r["auth_type_id"] is not None else None,
        auth_type_name=r["auth_type_name"],
    )


# ─── 审核动作 ─────────────────────────────────────────────────────


async def review_auth(
    db: AsyncSession,
    kind: str,
    review_id: int,
    body: ReviewActionRequest,
    actor_id: int,
) -> dict[str, Any]:
    """对某一类认证记录执行通过(1)/未通过(2)审核，并写入审计日志。

    婚姻核验结果本身为 married/no_record/divorced，不存在「通过/未通过」语义，
    因此 marriage 不支持该审核动作，直接返回 400。
    """
    new_status = body.status  # 1 通过 2 未通过
    if new_status == 2 and kind in ("commitment", "education") and not (body.remark or "").strip():
        raise HTTPException(422, detail="审核未通过时必须填写原因")
    if kind == "realname":
        resource_type = "user_auth"
        exists = await db.execute(text("SELECT id FROM user_auth WHERE id = :id"), {"id": review_id})
        if not exists.scalar():
            raise HTTPException(404, detail="实名认证记录不存在")
        await db.execute(
            text("UPDATE user_auth SET face_verified = :st, reviewed_by = :actor, reviewed_at = UTC_TIMESTAMP() WHERE id = :id"),
            {"st": new_status, "actor": actor_id, "id": review_id},
        )
    elif kind == "commitment":
        resource_type = "user_commitment_sign"
        exists = await db.execute(text("SELECT id, user_id, status FROM user_commitment_sign WHERE id = :id"), {"id": review_id})
        commitment = exists.mappings().first()
        if not commitment:
            raise HTTPException(404, detail="承诺书签署记录不存在")
        if int(commitment["status"] or 0) != 0:
            raise HTTPException(409, detail="当前承诺书不在审核中")
        await db.execute(
            text("UPDATE user_commitment_sign SET status = :st, reviewed_by = :actor, reviewed_at = UTC_TIMESTAMP(), remark = :remark WHERE id = :id"),
            {"st": new_status, "actor": actor_id, "id": review_id, "remark": body.remark},
        )
        await db.execute(
            text("UPDATE users SET is_single_pledge=:passed, updated_at=UTC_TIMESTAMP() WHERE id=:user_id"),
            {"passed": 1 if new_status == 1 else 0, "user_id": int(commitment["user_id"])},
        )
    elif kind == "marriage":
        raise HTTPException(400, detail="婚姻核验记录不支持通过/未通过审核，仅可查看或删除")
    elif kind == "house":
        resource_type = "user_auth"
        exists = await db.execute(text("SELECT id FROM user_auth WHERE id = :id"), {"id": review_id})
        if not exists.scalar():
            raise HTTPException(404, detail="房产认证记录不存在")
        await db.execute(
            text("UPDATE user_auth SET house_verified = :st, reviewed_by = :actor, reviewed_at = UTC_TIMESTAMP() WHERE id = :id"),
            {"st": new_status, "actor": actor_id, "id": review_id},
        )
    elif kind == "education":
        resource_type = "user_auth"
        exists = await db.execute(text("SELECT id, education_verified FROM user_auth WHERE id = :id"), {"id": review_id})
        education_review = exists.mappings().first()
        if not education_review:
            raise HTTPException(404, detail="学历认证记录不存在")
        if int(education_review["education_verified"] or 0) != 1:
            raise HTTPException(409, detail="当前学历认证不在审核中")
        qualification_status = 2 if new_status == 1 else 3
        await db.execute(
            text("""UPDATE user_auth SET education_verified=:st, education_fail_reason=:remark,
                education_reviewed_at=UTC_TIMESTAMP(), reviewed_by=:actor, reviewed_at=UTC_TIMESTAMP(),
                updated_at=UTC_TIMESTAMP()
                WHERE id=:id"""),
            {"st": qualification_status, "remark": body.remark if qualification_status == 3 else None, "actor": actor_id, "id": review_id},
        )
    elif kind == "other":
        resource_type = "user_auth_extra"
        exists = await db.execute(text("SELECT id FROM user_auth_extra WHERE id = :id"), {"id": review_id})
        if not exists.scalar():
            raise HTTPException(404, detail="其他认证记录不存在")
        await db.execute(
            text("UPDATE user_auth_extra SET status = :st, reviewed_by = :actor, reviewed_at = UTC_TIMESTAMP(), remark = :remark WHERE id = :id"),
            {"st": new_status, "actor": actor_id, "id": review_id, "remark": body.remark},
        )
    else:
        raise HTTPException(400, detail="不支持的认证类型")

    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, :action, :rt, :rid, :after)"
        ),
        {
            "actor": actor_id,
            "action": f"member.auth.{kind}.review",
            "rt": resource_type,
            "rid": review_id,
            "after": json.dumps({"status": new_status, "remark": body.remark}, ensure_ascii=False),
        },
    )
    await db.commit()
    return {"id": review_id, "kind": kind, "status": new_status}


# ─── 删除 / 重置 ─────────────────────────────────────────────────


async def delete_auth_review(
    db: AsyncSession,
    kind: str,
    review_id: int,
    actor_id: int,
) -> dict[str, Any]:
    """删除认证记录并写审计。

    注意：实名/房产/学历三类认证复用 user_auth 单行，物理删除整行会误删同行的
    其它认证数据，因此这三类的「删除」改为将该认证项重置为待审并清空凭证文件。
    承诺/婚姻/其他各自独立成表，执行物理删除。
    """
    if kind == "commitment":
        resource_type = "user_commitment_sign"
        exists = await db.execute(text("SELECT id, user_id FROM user_commitment_sign WHERE id = :id"), {"id": review_id})
        commitment = exists.mappings().first()
        if not commitment:
            raise HTTPException(404, detail="承诺书签署记录不存在")
        await db.execute(text("DELETE FROM user_commitment_sign WHERE id = :id"), {"id": review_id})
        passed = await db.execute(text("SELECT 1 FROM user_commitment_sign WHERE user_id=:user_id AND status=1 LIMIT 1"), {"user_id": int(commitment["user_id"])})
        await db.execute(text("UPDATE users SET is_single_pledge=:passed, updated_at=UTC_TIMESTAMP() WHERE id=:user_id"), {
            "passed": 1 if passed.scalar() else 0,
            "user_id": int(commitment["user_id"]),
        })
    elif kind == "marriage":
        resource_type = "user_marriage_check"
        exists = await db.execute(text("SELECT id FROM user_marriage_check WHERE id = :id"), {"id": review_id})
        if not exists.scalar():
            raise HTTPException(404, detail="婚姻核验记录不存在")
        await db.execute(text("DELETE FROM user_marriage_check WHERE id = :id"), {"id": review_id})
    elif kind == "other":
        resource_type = "user_auth_extra"
        exists = await db.execute(text("SELECT id FROM user_auth_extra WHERE id = :id"), {"id": review_id})
        if not exists.scalar():
            raise HTTPException(404, detail="其他认证记录不存在")
        await db.execute(text("DELETE FROM user_auth_extra WHERE id = :id"), {"id": review_id})
    elif kind == "realname":
        resource_type = "user_auth"
        exists = await db.execute(text("SELECT id FROM user_auth WHERE id = :id"), {"id": review_id})
        if not exists.scalar():
            raise HTTPException(404, detail="实名认证记录不存在")
        await db.execute(
            text("UPDATE user_auth SET face_verified = 0, face_photo = NULL, reviewed_by = :actor, reviewed_at = UTC_TIMESTAMP() WHERE id = :id"),
            {"actor": actor_id, "id": review_id},
        )
    elif kind == "house":
        resource_type = "user_auth"
        exists = await db.execute(text("SELECT id FROM user_auth WHERE id = :id"), {"id": review_id})
        if not exists.scalar():
            raise HTTPException(404, detail="房产认证记录不存在")
        await db.execute(
            text("UPDATE user_auth SET house_verified = 0, house_cert = NULL, reviewed_by = :actor, reviewed_at = UTC_TIMESTAMP() WHERE id = :id"),
            {"actor": actor_id, "id": review_id},
        )
    elif kind == "education":
        resource_type = "user_auth"
        exists = await db.execute(text("SELECT id FROM user_auth WHERE id = :id"), {"id": review_id})
        if not exists.scalar():
            raise HTTPException(404, detail="学历认证记录不存在")
        await db.execute(
            text("""UPDATE user_auth SET education_verified=0, education_cert=NULL,
                education_fail_reason=NULL, education_submitted_at=NULL, education_reviewed_at=NULL,
                reviewed_by=:actor, reviewed_at=UTC_TIMESTAMP() WHERE id=:id"""),
            {"actor": actor_id, "id": review_id},
        )
    else:
        raise HTTPException(400, detail="不支持的认证类型")

    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, :action, :rt, :rid, :after)"
        ),
        {
            "actor": actor_id,
            "action": f"member.auth.{kind}.delete",
            "rt": resource_type,
            "rid": review_id,
            "after": json.dumps({"reset": kind in ("realname", "house", "education")}, ensure_ascii=False),
        },
    )
    await db.commit()
    return {"id": review_id, "kind": kind, "deleted": True}


# ─── 认证类型（其他认证） ─────────────────────────────────────────


async def list_auth_types(db: AsyncSession, keyword: str | None = None) -> list[AuthTypeItem]:
    where = []
    params: dict[str, Any] = {}
    if keyword:
        where.append("name LIKE CONCAT('%', :kw, '%')")
        params["kw"] = keyword
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = await db.execute(
        text(
            "SELECT id, name, icon_url, description, require_realname, sort, status, created_at, updated_at "
            f"FROM config_auth_type {clause} ORDER BY status DESC, sort DESC, id ASC"
        ),
        params,
    )
    return [AuthTypeItem(**dict(r)) for r in rows.mappings().all()]


async def create_auth_type(db: AsyncSession, body: AuthTypeCreate, actor_id: int) -> AuthTypeItem:
    exists = await db.execute(text("SELECT id FROM config_auth_type WHERE name = :name"), {"name": body.name})
    if exists.scalar():
        raise HTTPException(409, detail="认证类型名称已存在")
    result = await db.execute(
        text(
            "INSERT INTO config_auth_type (name, icon_url, description, require_realname, sort, status, created_at, updated_at) "
            "VALUES (:name, :icon_url, :description, :require_realname, :sort, :status, UTC_TIMESTAMP(), UTC_TIMESTAMP())"
        ),
        {
            "name": body.name,
            "icon_url": body.icon_url,
            "description": body.description,
            "require_realname": int(body.require_realname),
            "sort": body.sort,
            "status": body.status,
        },
    )
    new_id = int(result.lastrowid)
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, 'member.auth.type.create', 'config_auth_type', :rid, :after)"
        ),
        {"actor": actor_id, "rid": new_id, "after": json.dumps(body.model_dump(), ensure_ascii=False)},
    )
    await db.commit()
    row = (await db.execute(text("SELECT id, name, icon_url, description, require_realname, sort, status, created_at, updated_at FROM config_auth_type WHERE id = :id"), {"id": new_id})).mappings().first()
    return AuthTypeItem(**dict(row))


async def update_auth_type(db: AsyncSession, type_id: int, body: AuthTypeUpdate, actor_id: int) -> AuthTypeItem:
    current = (await db.execute(text("SELECT id, name FROM config_auth_type WHERE id = :id"), {"id": type_id})).mappings().first()
    if not current:
        raise HTTPException(404, detail="认证类型不存在")
    if body.name is not None and body.name != current["name"]:
        dup = await db.execute(text("SELECT id FROM config_auth_type WHERE name = :name AND id <> :id"), {"name": body.name, "id": type_id})
        if dup.scalar():
            raise HTTPException(409, detail="认证类型名称已存在")
    sets: list[str] = []
    params: dict[str, Any] = {"id": type_id}
    if body.name is not None:
        sets.append("name = :name"); params["name"] = body.name
    if body.icon_url is not None:
        sets.append("icon_url = :icon_url"); params["icon_url"] = body.icon_url
    if body.description is not None:
        sets.append("description = :description"); params["description"] = body.description
    if body.require_realname is not None:
        sets.append("require_realname = :require_realname"); params["require_realname"] = int(body.require_realname)
    if body.sort is not None:
        sets.append("sort = :sort"); params["sort"] = body.sort
    if body.status is not None:
        sets.append("status = :status"); params["status"] = body.status
    if not sets:
        # 无字段变更，直接返回当前记录
        row = (await db.execute(text("SELECT id, name, icon_url, description, require_realname, sort, status, created_at, updated_at FROM config_auth_type WHERE id = :id"), {"id": type_id})).mappings().first()
        return AuthTypeItem(**dict(row))
    sets.append("updated_at = UTC_TIMESTAMP()")
    await db.execute(text(f"UPDATE config_auth_type SET {', '.join(sets)} WHERE id = :id"), params)
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, 'member.auth.type.update', 'config_auth_type', :rid, :after)"
        ),
        {"actor": actor_id, "rid": type_id, "after": json.dumps(body.model_dump(exclude_none=True), ensure_ascii=False)},
    )
    await db.commit()
    row = (await db.execute(text("SELECT id, name, icon_url, description, require_realname, sort, status, created_at, updated_at FROM config_auth_type WHERE id = :id"), {"id": type_id})).mappings().first()
    return AuthTypeItem(**dict(row))


async def delete_auth_type(db: AsyncSession, type_id: int, actor_id: int) -> dict[str, Any]:
    ref = await db.execute(text("SELECT id FROM user_auth_extra WHERE auth_type_id = :id LIMIT 1"), {"id": type_id})
    if ref.scalar():
        raise HTTPException(409, detail="该认证类型已被会员提交记录引用，无法删除")
    current = await db.execute(text("SELECT id FROM config_auth_type WHERE id = :id"), {"id": type_id})
    if not current.scalar():
        raise HTTPException(404, detail="认证类型不存在")
    await db.execute(text("DELETE FROM config_auth_type WHERE id = :id"), {"id": type_id})
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, 'member.auth.type.delete', 'config_auth_type', :rid, :after)"
        ),
        {"actor": actor_id, "rid": type_id, "after": json.dumps({"id": type_id}, ensure_ascii=False)},
    )
    await db.commit()
    return {"id": type_id, "deleted": True}
