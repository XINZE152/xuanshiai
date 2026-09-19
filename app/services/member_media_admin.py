"""会员资料媒体验证（M3-2）管理后台服务层。

覆盖：
- 个人介绍（自白内容）列表与编辑；
- 头像/照片/视频媒体的分页查询、审核（通过/未通过/隐藏）、重新上传（归零待审）、软删除；
- 会员头像历史记录（含已软删）。

写操作统一写入 ``business_audit_log``；时间戳由 MySQL 写 ``UTC_TIMESTAMP()``，
Python 端不自行生成。会员编号由 SQL ``CONCAT('G', LPAD(u.id, 6, '0'))`` 生成。
"""

import json
from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.member_media_admin import (
    MemberIntroItem,
    MemberIntroPage,
    MemberMediaItem,
    MemberMediaPage,
    MemberMediaReplace,
    MemberMediaReview,
    MemberPreferenceAdminItem,
    MemberPreferenceAdminUpdate,
    MemberProfileExtItem,
    MemberProfileExtUpdate,
    MemberRecommendItem,
    MemberRecommendPage,
)


# ─── 通用工具 ─────────────────────────────────────────────────────


def _member_code_sql() -> str:
    """会员编号：G + 6 位左补零（与前端 MemberQuickProfileDrawer 同口径）。"""
    return "CONCAT('G', LPAD(u.id, 6, '0'))"


def _review_label(value: int) -> str:
    """0审核中 1已通过 2未通过 3已隐藏 → 中文标签。"""
    return {0: "审核中", 1: "已通过", 2: "未通过", 3: "已隐藏"}.get(int(value or 0), "审核中")


def _build_intro(r: dict[str, Any]) -> MemberIntroItem:
    return MemberIntroItem(
        id=int(r["user_id"]),
        user_id=int(r["user_id"]),
        member_code=r["member_code"],
        nickname=r["nickname"],
        avatar=r["avatar"],
        self_intro=r["self_intro"],
        updated_at=r["updated_at"],
    )


def _build_media(r: dict[str, Any]) -> MemberMediaItem:
    # CONCAT_WS 在全部为 NULL 时返回空串，统一转为 None
    meta = r["meta_text"]
    if meta == "" or meta is None:
        meta = None
    age = int(r["age"]) if r["age"] is not None else None
    duration = int(r["duration_seconds"]) if r["duration_seconds"] is not None else None
    return MemberMediaItem(
        id=int(r["id"]),
        user_id=int(r["user_id"]),
        member_code=r["member_code"],
        nickname=r["nickname"],
        avatar=r["avatar"],
        media_type=r["media_type"],
        file_url=r["file_url"],
        thumbnail_url=r["thumbnail_url"],
        mime_type=r["mime_type"],
        duration_seconds=duration,
        review_status=int(r["review_status"] or 0),
        review_status_label=_review_label(r["review_status"] or 0),
        review_reason=r["review_reason"],
        age=age,
        meta_text=meta,
        created_at=r["created_at"],
    )


# ─── 个人介绍（自白内容） ───────────────────────────────────────


async def list_intros(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None,
    letter_mode: str | None,
    letter_lower: bool,
    digit: bool,
    cn_digit: bool,
) -> MemberIntroPage:
    """分页查询会员个人介绍（自白内容）。

    数据源 ``users u LEFT JOIN user_profile p``；``self_intro`` 取自 ``p.self_intro``。
    过滤：letter_mode=has → 含英文字母；=none → 不含英文字母；letter_lower/digit/cn_digit
    分别对应小写字母/数字/中文数字匹配；keyword 匹配昵称或手机号。
    """
    where = ["1=1"]
    params: dict[str, Any] = {}
    if letter_mode == "has":
        where.append("p.self_intro REGEXP '[A-Za-z]'")
    elif letter_mode == "none":
        # 不含英文字母：NULL（无自白）也视为不含，一并纳入
        where.append("p.self_intro IS NULL OR p.self_intro NOT REGEXP '[A-Za-z]'")
    if letter_lower:
        where.append("p.self_intro REGEXP '[a-z]'")
    if digit:
        where.append("p.self_intro REGEXP '[0-9]'")
    if cn_digit:
        where.append("p.self_intro REGEXP '[零一二三四五六七八九]'")
    if keyword:
        where.append(
            "(u.nickname LIKE CONCAT('%', :kw, '%') OR u.phone LIKE CONCAT('%', :kw, '%'))"
        )
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = "FROM users u LEFT JOIN user_profile p ON p.user_id = u.id"
    rows = await db.execute(
        text(
            f"SELECT u.id, u.id AS user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
            f"p.self_intro, p.updated_at "
            f"{base} WHERE {clause} ORDER BY p.updated_at DESC, u.id DESC LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {clause}"), params)
    total = int(count.scalar() or 0)
    items = [_build_intro(r) for r in rows.mappings().all()]
    return MemberIntroPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def update_intro(db: AsyncSession, user_id: int, body: Any, actor_id: int) -> MemberIntroItem:
    """更新会员个人介绍（自白内容）。

    users 不存在返回 404；user_profile 以 ``uk_user_id`` 唯一键做 upsert，
    写入后回查最新行用于返回；并写审计 ``member.intro.update``。
    """
    exists = await db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id})
    if not exists.scalar():
        raise HTTPException(404, detail="会员不存在")
    await db.execute(
        text(
            "INSERT INTO user_profile (user_id, self_intro) VALUES (:uid, :intro) "
            "ON DUPLICATE KEY UPDATE self_intro = VALUES(self_intro), updated_at = UTC_TIMESTAMP()"
        ),
        {"uid": user_id, "intro": body.self_intro},
    )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, 'member.intro.update', 'user_profile', :rid, :after)"
        ),
        {
            "actor": actor_id,
            "rid": user_id,
            "after": json.dumps({"self_intro": body.self_intro}, ensure_ascii=False),
        },
    )
    await db.commit()
    row = (
        await db.execute(
            text(
                f"SELECT u.id, u.id AS user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
                f"p.self_intro, p.updated_at "
                f"FROM users u LEFT JOIN user_profile p ON p.user_id = u.id WHERE u.id = :id"
            ),
            {"id": user_id},
        )
    ).mappings().first()
    return _build_intro(row)


def _parse_json_tags(value: Any) -> list[str]:
    """personality_tags 为 json 列：驱动可能返回 str / list / None，统一成 list[str]。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(tag) for tag in value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(tag) for tag in parsed] if isinstance(parsed, list) else []


# ─── 资料扩展字段（性格/爱好/MBTI/自我介绍/红娘说） ─────────────


async def get_profile_ext(db: AsyncSession, user_id: int) -> MemberProfileExtItem:
    """查询单个会员的资料扩展字段（性格/爱好/MBTI/自我介绍/红娘说）。

    数据源 ``users u LEFT JOIN user_profile p``；会员不存在返回 404。
    """
    exists = await db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id})
    if not exists.scalar():
        raise HTTPException(404, detail="会员不存在")
    row = (
        await db.execute(
            text(
                f"SELECT u.id, u.id AS user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
                "p.personality_tags, p.hobbies, p.mbti, p.self_intro, p.matchmaker_note, p.updated_at "
                "FROM users u LEFT JOIN user_profile p ON p.user_id = u.id WHERE u.id = :id"
            ),
            {"id": user_id},
        )
    ).mappings().first()
    return MemberProfileExtItem(
        id=int(row["user_id"]),
        user_id=int(row["user_id"]),
        member_code=row["member_code"],
        nickname=row["nickname"],
        avatar=row["avatar"],
        personality_tags=_parse_json_tags(row["personality_tags"]),
        hobbies=row["hobbies"],
        mbti=row["mbti"],
        self_intro=row["self_intro"],
        matchmaker_note=row["matchmaker_note"],
        updated_at=row["updated_at"],
    )


async def update_profile_ext(
    db: AsyncSession, user_id: int, body: MemberProfileExtUpdate, actor_id: int
) -> MemberProfileExtItem:
    """更新会员资料扩展字段，仅更新请求中出现的字段；并写审计 ``member.profile_ext.update``。

    users 不存在返回 404；user_profile 不存在时先 ``INSERT IGNORE`` 建行再动态 UPDATE。
    """
    exists = await db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id})
    if not exists.scalar():
        raise HTTPException(404, detail="会员不存在")

    submitted = body.model_dump(exclude_unset=True)
    sets: list[str] = []
    params: dict[str, Any] = {"uid": user_id}
    if "personality_tags" in submitted:
        sets.append("personality_tags = :ptags")
        params["ptags"] = json.dumps(submitted["personality_tags"] or [], ensure_ascii=False)
    for field in ("hobbies", "mbti", "self_intro", "matchmaker_note"):
        if field in submitted:
            sets.append(f"{field} = :{field}")
            params[field] = submitted[field]

    await db.execute(text("INSERT IGNORE INTO user_profile (user_id) VALUES (:uid)"), {"uid": user_id})
    await db.execute(
        text(
            "UPDATE user_profile SET "
            + ", ".join(sets)
            + ", updated_at = UTC_TIMESTAMP() WHERE user_id = :uid"
        ),
        params,
    )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, 'member.profile_ext.update', 'user_profile', :rid, :after)"
        ),
        {
            "actor": actor_id,
            "rid": user_id,
            "after": json.dumps(submitted, ensure_ascii=False),
        },
    )
    await db.commit()
    return await get_profile_ext(db, user_id)


# ─── 择偶要求（后台管理端） ─────────────────────────────────────

_PREFERENCE_FIELDS = (
    "income_range",
    "education_requirement",
    "preferred_occupation",
    "marriage_requirement",
    "housing_expectation",
    "smoking_expectation",
    "drinking_expectation",
    "marriage_timeline",
    "extra_requirement",
)


async def get_member_preference(db: AsyncSession, user_id: int) -> MemberPreferenceAdminItem:
    """查询单个会员的择偶要求（后台管理口径）。会员不存在返回 404。"""
    exists = await db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id})
    if not exists.scalar():
        raise HTTPException(404, detail="会员不存在")
    row = (
        await db.execute(
            text(
                f"SELECT u.id, u.id AS user_id, {_member_code_sql()} AS member_code, u.nickname, u.avatar, "
                "p.age_min, p.age_max, p.height_min, p.height_max, "
                + ", ".join(f"p.{f}" for f in _PREFERENCE_FIELDS)
                + ", p.updated_at "
                "FROM users u LEFT JOIN user_partner_preference p ON p.user_id = u.id WHERE u.id = :id"
            ),
            {"id": user_id},
        )
    ).mappings().first()
    return MemberPreferenceAdminItem(
        id=int(row["user_id"]),
        user_id=int(row["user_id"]),
        member_code=row["member_code"],
        nickname=row["nickname"],
        avatar=row["avatar"],
        age_min=row["age_min"],
        age_max=row["age_max"],
        height_min=row["height_min"],
        height_max=row["height_max"],
        **{f: row[f] for f in _PREFERENCE_FIELDS},
        updated_at=row["updated_at"],
    )


async def update_member_preference(
    db: AsyncSession, user_id: int, body: MemberPreferenceAdminUpdate, actor_id: int
) -> MemberPreferenceAdminItem:
    """更新会员择偶要求，仅更新请求中出现的字段；并写审计 ``member.preference.update``。

    users 不存在返回 404；user_partner_preference 不存在时先 ``INSERT IGNORE`` 建行再动态 UPDATE。
    """
    exists = await db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id})
    if not exists.scalar():
        raise HTTPException(404, detail="会员不存在")

    submitted = body.model_dump(exclude_unset=True)
    sets: list[str] = []
    params: dict[str, Any] = {"uid": user_id}
    for field in ("age_min", "age_max", "height_min", "height_max", *_PREFERENCE_FIELDS):
        if field in submitted:
            sets.append(f"{field} = :{field}")
            params[field] = submitted[field]

    await db.execute(
        text("INSERT IGNORE INTO user_partner_preference (user_id) VALUES (:uid)"),
        {"uid": user_id},
    )
    await db.execute(
        text(
            "UPDATE user_partner_preference SET "
            + ", ".join(sets)
            + ", updated_at = UTC_TIMESTAMP() WHERE user_id = :uid"
        ),
        params,
    )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, 'member.preference.update', 'user_partner_preference', :rid, :after)"
        ),
        {
            "actor": actor_id,
            "rid": user_id,
            "after": json.dumps(submitted, ensure_ascii=False),
        },
    )
    await db.commit()
    return await get_member_preference(db, user_id)


# ─── 会员推荐（后台按条件筛候选人，强制异性） ───────────────────


_TAG_JSON_COLUMNS = ("p.tags", "p.interest_tags", "p.personality_tags")


async def list_member_recommendations(
    db: AsyncSession,
    user_id: int,
    *,
    page: int,
    page_size: int,
    store_id: int | None = None,
    matchmaker_id: int | None = None,
    smoking: str | None = None,
    drinking: str | None = None,
    house: str | None = None,
    marriage: int | None = None,
    ethnicity: str | None = None,
    constellation: str | None = None,
    hometown: str | None = None,
    residence: str | None = None,
    occupations: list[str] | None = None,
    mbti: str | None = None,
    tags: list[str] | None = None,
) -> MemberRecommendPage:
    """为指定会员按附加条件筛推荐候选人。

    硬规则：候选人必须为异性（``u.gender <>`` 会员自身性别）、状态正常（status=1）、
    排除本人；其余条件全部可选（不传即不限）。候选人无性别信息时返回 422。
    """
    viewer = (
        await db.execute(text("SELECT gender FROM users WHERE id = :id"), {"id": user_id})
    ).mappings().first()
    if not viewer:
        raise HTTPException(404, detail="会员不存在")
    if viewer["gender"] not in (1, 2):
        raise HTTPException(422, detail="该会员未填写性别，无法按异性筛选推荐")

    clauses = [
        "u.id <> :viewer_id",
        "u.status = 1",
        "u.gender <> :opposite_gender",
    ]
    params: dict[str, Any] = {
        "viewer_id": user_id,
        "opposite_gender": viewer["gender"],
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    if matchmaker_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM resource_assignment ram WHERE ram.user_id = u.id"
            " AND ram.status = 1 AND ram.matchmaker_id = :matchmaker_id)"
        )
        params["matchmaker_id"] = matchmaker_id
    if store_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM resource_assignment ras"
            " JOIN organization_member om ON om.user_id = ras.matchmaker_id"
            " AND om.status = 1 AND om.organization_id = :store_id"
            " WHERE ras.user_id = u.id AND ras.status = 1)"
        )
        params["store_id"] = store_id
    for field, column in (
        ("smoking", "p.smoking"),
        ("drinking", "p.drinking"),
        ("house", "p.house"),
        ("ethnicity", "p.ethnicity"),
        ("constellation", "p.constellation"),
        ("hometown", "p.hometown"),
        ("residence", "p.residence"),
        ("mbti", "p.mbti"),
    ):
        value = locals()[field]
        if value:
            clauses.append(f"{column} = :{field}")
            params[field] = value
    if marriage is not None:
        clauses.append("u.is_married = :marriage")
        params["marriage"] = marriage
    if occupations:
        clean_jobs = [job.strip() for job in occupations if job and job.strip()][:10]
        if clean_jobs:
            clauses.append(
                f"p.occupation IN ({', '.join(f':occ_{i}' for i in range(len(clean_jobs)))})"
            )
            for i, job in enumerate(clean_jobs):
                params[f"occ_{i}"] = job
    if tags:
        clean_tags = [tag.strip() for tag in tags if tag and tag.strip()][:12]
        if clean_tags:
            tag_clauses = []
            for i, tag in enumerate(clean_tags):
                for j, column in enumerate(_TAG_JSON_COLUMNS):
                    key = f"tag_{i}_{j}"
                    params[key] = tag
                    tag_clauses.append(f"JSON_CONTAINS({column}, JSON_QUOTE(:{key}))")
            clauses.append("(" + " OR ".join(tag_clauses) + ")")

    where = " AND ".join(clauses)
    base = """FROM users u
        LEFT JOIN user_profile p ON p.user_id = u.id
        LEFT JOIN (SELECT user_id, MAX(id) AS max_assignment_id FROM resource_assignment WHERE status = 1 GROUP BY user_id) ca ON ca.user_id = u.id
        LEFT JOIN resource_assignment cra ON cra.id = ca.max_assignment_id
        LEFT JOIN matchmaker_admin_account maa ON maa.id = cra.matchmaker_id
        LEFT JOIN users mu ON mu.id = cra.matchmaker_id"""
    select_cols = f"""SELECT u.id AS user_id, {_member_code_sql()} AS member_code,
        u.nickname, u.avatar, u.gender, u.birthday, u.is_married,
        p.height, p.income, p.education_level, p.occupation,
        p.constellation, p.mbti, p.hometown, p.residence,
        p.smoking, p.drinking, p.house, p.ethnicity,
        p.tags, p.interest_tags,
        cra.matchmaker_id, COALESCE(mu.nickname, maa.display_name) AS matchmaker_name"""

    rows = await db.execute(
        text(f"{select_cols} {base} WHERE {where} ORDER BY u.id DESC LIMIT :limit OFFSET :offset"),
        params,
    )
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = int(
        await db.scalar(text(f"SELECT COUNT(DISTINCT u.id) {base} WHERE {where}"), count_params) or 0
    )
    items = []
    for row in rows.mappings().all():
        merged_tags: list[str] = []
        for raw in (row.get("tags"), row.get("interest_tags")):
            parsed = _parse_json_tags(raw)
            for tag in parsed:
                if tag not in merged_tags:
                    merged_tags.append(tag)
        birthday = row.get("birthday")
        items.append(
            MemberRecommendItem(
                user_id=int(row["user_id"]),
                member_code=row["member_code"],
                nickname=row["nickname"],
                avatar=row["avatar"],
                gender=row["gender"],
                age=_age_from_birthday(birthday),
                height=row["height"],
                income=float(row["income"]) if row["income"] is not None else None,
                education_level=row["education_level"],
                occupation=row["occupation"],
                constellation=row["constellation"],
                mbti=row["mbti"],
                hometown=row["hometown"],
                residence=row["residence"],
                smoking=row["smoking"],
                drinking=row["drinking"],
                house=row["house"],
                ethnicity=row["ethnicity"],
                tags=merged_tags,
                matchmaker_id=int(row["matchmaker_id"]) if row["matchmaker_id"] is not None else None,
                matchmaker_name=row["matchmaker_name"],
            )
        )
    return MemberRecommendPage(
        items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total
    )


def _age_from_birthday(birthday: Any) -> int | None:
    """按生日算周岁；生日为空返回 None。"""
    if birthday is None:
        return None
    today = date.today()
    age = today.year - birthday.year
    if (today.month, today.day) < (birthday.month, birthday.day):
        age -= 1
    return max(age, 0)


# ─── 媒体（头像/照片/视频） ─────────────────────────────────────


async def list_media(
    db: AsyncSession,
    page: int,
    page_size: int,
    media_type: str,
    review_status: int | None,
    keyword: str | None,
    gender: int | None,
) -> MemberMediaPage:
    """分页查询指定类型的媒体（头像/照片/视频），仅取未软删记录。

    age 由 ``TIMESTAMPDIFF(YEAR, u.birthday, CURDATE())`` 推算；
    meta_text 由 ``生日年份 + 身高 + 学历`` 经 CONCAT_WS 拼接（NULL 自动跳过）。
    """
    where = ["m.deleted_at IS NULL", "m.media_type = :mt"]
    params: dict[str, Any] = {"mt": media_type}
    if review_status is not None:
        where.append("m.review_status = :rs")
        params["rs"] = review_status
    if gender is not None:
        where.append("u.gender = :gd")
        params["gd"] = gender
    if keyword:
        where.append(
            "(u.nickname LIKE CONCAT('%', :kw, '%') OR u.phone LIKE CONCAT('%', :kw, '%') "
            f"OR {_member_code_sql()} LIKE CONCAT('%', :kw, '%'))"
        )
        params["kw"] = keyword
    clause = " AND ".join(where)
    base = (
        "FROM user_media m JOIN users u ON u.id = m.user_id "
        "LEFT JOIN user_profile p ON p.user_id = u.id "
        "LEFT JOIN user_auth a ON a.user_id = u.id"
    )
    rows = await db.execute(
        text(
            "SELECT m.id, m.user_id, "
            f"{_member_code_sql()} AS member_code, u.nickname, u.avatar, "
            "m.media_type, m.file_url, m.thumbnail_url, m.mime_type, m.duration_seconds, "
            "m.review_status, m.review_reason, "
            "TIMESTAMPDIFF(YEAR, u.birthday, CURDATE()) AS age, "
            "CONCAT_WS(' ', IF(u.birthday IS NOT NULL, CONCAT(YEAR(u.birthday),'年'), NULL), "
            "IF(p.height IS NOT NULL, CONCAT(p.height,'cm'), NULL), a.education) AS meta_text, "
            f"m.created_at {base} WHERE {clause} ORDER BY m.created_at DESC, m.id DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    count = await db.execute(text(f"SELECT COUNT(*) {base} WHERE {clause}"), params)
    total = int(count.scalar() or 0)
    items = [_build_media(r) for r in rows.mappings().all()]
    return MemberMediaPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def review_media(db: AsyncSession, media_id: int, body: MemberMediaReview, actor_id: int) -> dict[str, Any]:
    """审核媒体：更新 review_status / review_reason / reviewed_at，并写审计 ``member.media.review``。"""
    exists = await db.execute(text("SELECT id FROM user_media WHERE id = :id"), {"id": media_id})
    if not exists.scalar():
        raise HTTPException(404, detail="媒体记录不存在")
    await db.execute(
        text(
            "UPDATE user_media SET review_status = :rs, review_reason = :reason, reviewed_at = UTC_TIMESTAMP() "
            "WHERE id = :id"
        ),
        {"rs": body.review_status, "reason": body.review_reason, "id": media_id},
    )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, 'member.media.review', 'user_media', :rid, :after)"
        ),
        {
            "actor": actor_id,
            "rid": media_id,
            "after": json.dumps(
                {"review_status": body.review_status, "review_reason": body.review_reason},
                ensure_ascii=False,
            ),
        },
    )
    await db.commit()
    return {"id": media_id, "review_status": body.review_status}


async def replace_media(db: AsyncSession, media_id: int, body: MemberMediaReplace, actor_id: int) -> dict[str, Any]:
    """重新上传媒体：覆盖文件地址，并将审核状态归零（待审核），并写审计 ``member.media.replace``。"""
    exists = await db.execute(text("SELECT id FROM user_media WHERE id = :id"), {"id": media_id})
    if not exists.scalar():
        raise HTTPException(404, detail="媒体记录不存在")
    await db.execute(
        text(
            "UPDATE user_media SET file_url = :url, thumbnail_url = :thumb, review_status = 0, "
            "reviewed_at = NULL WHERE id = :id"
        ),
        {"url": body.file_url, "thumb": body.thumbnail_url, "id": media_id},
    )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, 'member.media.replace', 'user_media', :rid, :after)"
        ),
        {
            "actor": actor_id,
            "rid": media_id,
            "after": json.dumps(
                {"file_url": body.file_url, "thumbnail_url": body.thumbnail_url},
                ensure_ascii=False,
            ),
        },
    )
    await db.commit()
    return {"id": media_id, "file_url": body.file_url}


async def delete_media(db: AsyncSession, media_id: int, actor_id: int) -> dict[str, Any]:
    """软删除媒体：deleted_at 置为 UTC_TIMESTAMP()，并写审计 ``member.media.delete``。"""
    exists = await db.execute(text("SELECT id FROM user_media WHERE id = :id"), {"id": media_id})
    if not exists.scalar():
        raise HTTPException(404, detail="媒体记录不存在")
    await db.execute(
        text("UPDATE user_media SET deleted_at = UTC_TIMESTAMP() WHERE id = :id"),
        {"id": media_id},
    )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id, after_json) "
            "VALUES (:actor, 'member.media.delete', 'user_media', :rid, :after)"
        ),
        {"actor": actor_id, "rid": media_id, "after": json.dumps({}, ensure_ascii=False)},
    )
    await db.commit()
    return {"id": media_id, "deleted": True}


async def list_avatar_history(db: AsyncSession, user_id: int, page: int, page_size: int) -> MemberMediaPage:
    """查询会员全部头像历史（含已软删记录），倒序分页。users 不存在返回 404。"""
    exists = await db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id})
    if not exists.scalar():
        raise HTTPException(404, detail="会员不存在")
    rows = await db.execute(
        text(
            "SELECT m.id, m.user_id, "
            f"{_member_code_sql()} AS member_code, u.nickname, u.avatar, "
            "m.media_type, m.file_url, m.thumbnail_url, m.mime_type, m.duration_seconds, "
            "m.review_status, m.review_reason, "
            "TIMESTAMPDIFF(YEAR, u.birthday, CURDATE()) AS age, "
            "CONCAT_WS(' ', IF(u.birthday IS NOT NULL, CONCAT(YEAR(u.birthday),'年'), NULL), "
            "IF(p.height IS NOT NULL, CONCAT(p.height,'cm'), NULL), a.education) AS meta_text, "
            "m.created_at "
            "FROM user_media m JOIN users u ON u.id = m.user_id "
            "LEFT JOIN user_profile p ON p.user_id = u.id "
            "LEFT JOIN user_auth a ON a.user_id = u.id "
            "WHERE m.user_id = :uid AND m.media_type = 'avatar' "
            "ORDER BY m.created_at DESC, m.id DESC LIMIT :limit OFFSET :offset"
        ),
        {"uid": user_id, "limit": page_size, "offset": (page - 1) * page_size},
    )
    count = await db.execute(
        text("SELECT COUNT(*) FROM user_media WHERE user_id = :uid AND media_type = 'avatar'"),
        {"uid": user_id},
    )
    total = int(count.scalar() or 0)
    items = [_build_media(r) for r in rows.mappings().all()]
    return MemberMediaPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)
