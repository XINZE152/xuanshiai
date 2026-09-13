"""统一的「搜索可绑定用户」能力。

后台多个角色模块需要把已注册的普通用户绑定成服务红娘 / 推广红娘 / 合伙人，
三处原本各自写了一份 SQL，行为不一致（有的只能搜昵称/手机号、有的长度要求不同）。
这里收敛成一份实现：统一支持用户 ID、昵称、手机号、实名姓名四种检索方式，
并统一输出「是否可用 + 不可用原因」，便于前端明确提示而不是静默搜不到。

可用规则（scope）：
- ``service_matchmaker``：排除已经是服务红娘的用户
- ``promoter``：排除已经是推广红娘的用户
- ``partner``：排除服务红娘；已是推广红娘或已有团队的用户仍然返回，
  由前端据 ``is_promoter`` / ``has_team`` 提示（合伙人允许兼任推广红娘）
"""

from typing import Literal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CandidateScope = Literal["service_matchmaker", "promoter", "partner"]

_LOOKUP_COLUMNS = {
    "nickname": "u.nickname",
    "phone": "u.phone",
    "real_name": "auth.real_name",
    "id": "CAST(u.id AS CHAR)",
}

# scope -> 不可用原因
_UNAVAILABLE_REASON = {
    "service_matchmaker": "该用户已是服务红娘",
    "promoter": "该用户已是推广红娘",
}


def _normalize_candidate(row: dict, scope: CandidateScope) -> dict:
    """把一行原始结果补成前端统一消费的候选结构（纯函数，便于单测）。"""
    item = dict(row)
    item["wechat_bound"] = bool(item.get("wechat_bound"))
    item["is_service_matchmaker"] = bool(item.get("is_service_matchmaker"))
    item["is_promoter"] = bool(item.get("is_promoter"))
    item["has_team"] = bool(item.get("has_team"))
    reason: str | None = None
    if scope == "partner":
        if item["is_service_matchmaker"]:
            reason = _UNAVAILABLE_REASON["service_matchmaker"]
        elif item["has_team"]:
            reason = "该用户已有合伙团队"
    elif scope in _UNAVAILABLE_REASON and item[f"is_{scope}"]:
        reason = _UNAVAILABLE_REASON[scope]
    item["unavailable"] = reason is not None
    item["unavailable_reason"] = reason
    return item


async def search_user_candidates(
    db: AsyncSession,
    keyword: str,
    scope: CandidateScope,
    limit: int = 10,
) -> list[dict]:
    """按关键字搜索普通用户，返回统一的候选结构。

    返回字段：id / nickname / real_name / phone / avatar / wechat_bound /
    is_service_matchmaker / is_promoter / has_team / unavailable / unavailable_reason
    """
    kw = (keyword or "").strip()
    if not kw:
        return []

    params: dict[str, object] = {"kw": kw, "limit": max(1, min(int(limit), 50))}
    # 纯数字关键字额外允许按用户 ID 精确查找（后台常见诉求：只知道 uid）
    id_match = ""
    if kw.isdigit():
        id_match = "OR u.id = :uid"
        params["uid"] = int(kw)

    rows = (
        await db.execute(
            text(
                f"""SELECT u.id,
                       u.nickname,
                       u.phone,
                       u.avatar,
                       auth.real_name AS real_name,
                       (u.wechat_bound_at IS NOT NULL) AS wechat_bound,
                       EXISTS (
                           SELECT 1 FROM user_matchmaker_apply ma
                           WHERE ma.user_id = u.id
                             AND ma.application_type = 'service_matchmaker'
                             AND ma.status IN (1, 2)
                       ) AS is_service_matchmaker,
                       EXISTS (
                           SELECT 1 FROM user_matchmaker_apply ma
                           WHERE ma.user_id = u.id
                             AND ma.application_type = 'promoter'
                             AND ma.status IN (1, 2)
                       ) AS is_promoter,
                       EXISTS (
                           SELECT 1 FROM partner_team t
                           WHERE t.owner_user_id = u.id AND t.status = 1
                       ) AS has_team
                FROM users u
                LEFT JOIN user_auth auth ON auth.user_id = u.id
                WHERE u.status = 1
                  AND u.deleted_at IS NULL
                  AND (
                        u.nickname LIKE CONCAT('%', :kw, '%')
                        OR u.phone LIKE CONCAT('%', :kw, '%')
                        OR auth.real_name LIKE CONCAT('%', :kw, '%')
                        {id_match}
                  )
                ORDER BY u.id DESC
                LIMIT :limit"""
            ),
            params,
        )
    ).mappings().all()

    return [_normalize_candidate(dict(row), scope) for row in rows]


async def resolve_user_id(
    db: AsyncSession,
    lookup: str,
    lookup_by: str = "nickname",
    *,
    not_found_detail: str = "未找到匹配的用户账号",
) -> int:
    """把后台手输的「账号」解析成 users.id。

    历史实现只做 ``=`` 精确匹配，运营只要少打一个字就报「未找到」，表现为搜索框"没用"。
    这里改成两段式：先精确匹配，再退化到模糊匹配；模糊命中多值时给出明确提示，
    避免替用户随便选一个造成绑错人。
    """
    value = (lookup or "").strip()
    if not value:
        raise HTTPException(422, detail="请先填写要绑定的用户账号")

    column = _LOOKUP_COLUMNS.get(lookup_by or "nickname", _LOOKUP_COLUMNS["nickname"])
    join = (
        " LEFT JOIN user_auth auth ON auth.user_id = u.id"
        if lookup_by == "real_name"
        else ""
    )
    base_where = "WHERE u.status = 1 AND u.deleted_at IS NULL"

    exact = (
        await db.execute(
            text(
                f"SELECT u.id FROM users u{join} {base_where} "
                f"AND {column} = :value ORDER BY u.id DESC LIMIT 1"
            ),
            {"value": value},
        )
    ).scalar()
    if exact:
        return int(exact)

    # 数字输入额外允许按用户 ID 精确匹配
    if value.isdigit():
        uid = (
            await db.execute(
                text(f"SELECT u.id FROM users u {base_where} AND u.id = :uid LIMIT 1"),
                {"uid": int(value)},
            )
        ).scalar()
        if uid:
            return int(uid)

    fuzzy = (
        await db.execute(
            text(
                f"SELECT u.id FROM users u{join} {base_where} "
                f"AND {column} LIKE CONCAT('%', :value, '%') ORDER BY u.id DESC LIMIT 2"
            ),
            {"value": value},
        )
    ).scalars().all()
    if len(fuzzy) == 1:
        return int(fuzzy[0])
    if len(fuzzy) > 1:
        raise HTTPException(
            422, detail="匹配到多个用户，请填写更完整的昵称/手机号或从下拉列表中选择"
        )
    raise HTTPException(404, detail=not_found_detail)
