"""Data-scope-aware message administration services."""

import json
import re

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin
from app.core.sensitive_fields import (
    REDACTION_LEVEL_ELEVATED,
    REDACTION_LEVEL_STANDARD,
    mask_middle,
    redaction_level_from_permissions,
)
from app.schemas.message_admin import (
    AdminAnnouncementCreate,
    AdminAnnouncementItem,
    AdminMessageItem,
    AdminMessagePage,
)

_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD_PATTERN = re.compile(r"(?<!\d)(\d{17}[\dXx]|\d{15})(?!\d)")
_BANK_CARD_PATTERN = re.compile(r"(?<!\d)(\d{16,19})(?!\d)")
_WECHAT_PATTERN = re.compile(r"(?i)(wxid_[A-Za-z0-9_-]{3,})")
# 值组下界取 2（合计 3 字符）：微信号最短允许 6 位，但用户手写的短 ID 与带前后缀的
# 写法同样会泄露，原 {4,} 让短微信号整串可见。标签与值之间还允许「是/为」连接词，
# 否则「我的vx是 li_hua2024」这类最自然的写法整串漏出。
_WECHAT_LABEL_PATTERN = re.compile(r"(微信号|微信|weixin|wechat|vx|V信)[\s:：是为]*([A-Za-z][A-Za-z0-9_-]{2,})")
# 裸微信号：既无 wxid_ 前缀也无“微信”标签，只能靠接触意图线索词定位。线索词加上
# :func:`_looks_like_wechat_id`（须含数字或分隔符）两道门槛，避免把普通英文词、
# 域名和 URL 片段误判成微信号；宁漏不误伤正文，但带线索的裸号一定被掩掉。
_WECHAT_BARE_PATTERN = re.compile(
    r"(?i)((?:加|加上|添加|求|留|搜|搜索)\s*(?:我|一下|个)?\s*(?:微信|weixin|wechat|vx|V信)?\s*[:：]?\s*)"
    r"([A-Za-z][A-Za-z0-9_-]{5,19})(?![A-Za-z0-9_-])"
)
_ADDRESS_LABEL_PATTERN = re.compile(
    r"(地址|住址|现居|家住|收件地址)\s*[:：]?\s*([^\s，,。;；]{4,60})"
)

# 脱敏等级常量来自 app.core.sensitive_fields：结构化字段与自由文本共用同一套分档，
# 避免两处各自演化出“某档能看到完整值”的口径差异。

def redaction_level(admin: CurrentMatchmakerAdmin) -> str:
    """按账号权限决定脱敏粒度：具备处置/审计权限者可见更多位数（仍为掩码）。"""
    return redaction_level_from_permissions(admin.permissions)


def _looks_like_wechat_id(value: str) -> bool:
    """裸号候选是否像微信号：至少含一位数字或分隔符。

    微信号允许 6-20 位字母开头，普通英文词也满足该形状，唯一稳定区分点是真实微信号
    几乎总带数字或 ``_``/``-``。据此把 ``加我 goodmorning`` 这类正常句子放过。
    """
    return any(char.isdigit() or char in "_-" for char in value)


def _bare_wechat_replacer(elevated: bool):
    def _replace(match: re.Match[str]) -> str:
        candidate = match.group(2)
        if not _looks_like_wechat_id(candidate):
            return match.group(0)
        return f"{match.group(1)}{mask_middle(candidate, 3, 4 if elevated else 0)}"

    return _replace


def redact_sensitive(value: str | None, level: str = REDACTION_LEVEL_STANDARD) -> str | None:
    """对消息正文做分级脱敏：手机号 / 身份证 / 银行卡 / 微信号 / 带标签地址。

    `elevated` 保留更多首尾字符便于核对，但同样不返回完整敏感值。
    """
    if value is None:
        return None
    elevated = level == REDACTION_LEVEL_ELEVATED
    text_value = _PHONE_PATTERN.sub(
        (lambda m: mask_middle(m.group(0), 3, 4)) if elevated else (lambda m: "1**********"),
        value,
    )
    text_value = _ID_CARD_PATTERN.sub(
        (lambda m: mask_middle(m.group(0), 4, 4)) if elevated else (lambda m: "*" * len(m.group(0))),
        text_value,
    )
    text_value = _BANK_CARD_PATTERN.sub(
        (lambda m: mask_middle(m.group(0), 4, 4)) if elevated else (lambda m: "************" + m.group(0)[-4:]),
        text_value,
    )
    # wxid 与标签号一律走 mask_middle：其 keep_tail=0 分支已在 core 修正，
    # 早先直连切片会拼回完整 wxid。
    text_value = _WECHAT_PATTERN.sub(
        (lambda m: mask_middle(m.group(0), 5, 0)) if elevated else (lambda m: m.group(0)[:5] + "***"),
        text_value,
    )
    text_value = _WECHAT_LABEL_PATTERN.sub(
        lambda m: f"{m.group(1)}:{mask_middle(m.group(2), 3, 4 if elevated else 0)}",
        text_value,
    )
    text_value = _WECHAT_BARE_PATTERN.sub(_bare_wechat_replacer(elevated), text_value)
    # 地址无法可靠识别边界，仅处理带显式标签的写法：保留前 6 个字符（通常为省市区）。
    text_value = _ADDRESS_LABEL_PATTERN.sub(
        lambda m: f"{m.group(1)}:{m.group(2)[:6]}{'*' * max(0, len(m.group(2)) - 6)}",
        text_value,
    )
    return text_value


def _redact_content(value: str | None, level: str = REDACTION_LEVEL_STANDARD) -> str | None:
    """向后兼容入口：等价于 :func:`redact_sensitive`。"""
    return redact_sensitive(value, level)


def _message_scope(admin: CurrentMatchmakerAdmin, params: dict[str, object]) -> str:
    """消息可见范围：委托 `CurrentMatchmakerAdmin.scope_exists_clause` 的四档语义。

    消息的归属由 `resource_assignment` 决定，且一条消息涉及收发双方，
    因此在外层包一层 EXISTS 后套用统一作用域谓词：

    - ALL / `*`      → ``1=1``
    - SELF           → 本红娘名下会员收发的消息
    - STORE          → 本门店（组织）名下会员收发的消息（历史实现缺失该档，
                       门店级管理员会看到整个组织）
    - ORGANIZATION   → 本组织及其下属门店

    EXISTS 包装与四档判定已收敛到 `dependencies.py`，本函数只做
    ``1 = 1`` 的历史写法兼容（既有测试与 SQL 断言依赖无空格的 ``1=1``）。
    """
    clause = admin.scope_exists_clause(
        params,
        correlation="scope_assignment.user_id IN (chat_message.from_user_id, chat_message.to_user_id)",
        user_column="matchmaker_id",
        organization_column="organization_id",
        user_param="scope_matchmaker_id",
    )
    return "1=1" if clause == "1 = 1" else clause


def _message_item(row: dict, level: str = REDACTION_LEVEL_STANDARD) -> AdminMessageItem:
    data = dict(row)
    data["content"] = redact_sensitive(data.get("content"), level)
    # 语音 media_url 保持 DB 原始 URL，不按管理员重签：audio 类验签钩子
    # （media_access._user_active）查 users 表语义，与管理员后台账号不匹配，
    # 强行重签会产生永久 403 的假签名。已登记限制：管理端语音回放不可用，
    # 需要听证走既有运维手段；如需支持须以独立管理端钩子类别专项设计。
    return AdminMessageItem(**data)


async def list_admin_messages(
    db: AsyncSession,
    admin: CurrentMatchmakerAdmin,
    page: int,
    page_size: int,
    user_id: int | None = None,
    session_id: int | None = None,
    message_type: int | None = None,
) -> AdminMessagePage:
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    level = redaction_level(admin)
    where = [_message_scope(admin, params)]
    if user_id is not None:
        where.append("(chat_message.from_user_id=:user_id OR chat_message.to_user_id=:user_id)")
        params["user_id"] = user_id
    if session_id is not None:
        where.append("chat_message.session_id=:session_id")
        params["session_id"] = session_id
    if message_type is not None:
        where.append("chat_message.type=:message_type")
        params["message_type"] = message_type
    clause = " AND ".join(where)
    rows = (await db.execute(text(f"""SELECT id, session_id, from_user_id, to_user_id, type,
        content, media_url, is_read, revoked_at, created_at FROM chat_message
        WHERE {clause} ORDER BY id DESC LIMIT :limit OFFSET :offset"""), params)).mappings().all()
    count_params = {key: value for key, value in params.items() if key not in {"limit", "offset"}}
    total = int((await db.execute(text(f"SELECT COUNT(*) FROM chat_message WHERE {clause}"), count_params)).scalar() or 0)
    return AdminMessagePage(
        items=[_message_item(dict(row), level) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def moderate_admin_message(
    db: AsyncSession,
    admin: CurrentMatchmakerAdmin,
    message_id: int,
    action: str,
    reason: str,
) -> AdminMessageItem:
    params: dict[str, object] = {"id": message_id}
    level = redaction_level(admin)
    scope = _message_scope(admin, params)
    row = (await db.execute(text("""SELECT id, session_id, from_user_id, to_user_id, type,
        content, media_url, is_read, revoked_at, created_at FROM chat_message
        WHERE id=:id AND """ + scope + """ FOR UPDATE"""), params)).mappings().first()
    if not row:
        raise HTTPException(404, detail="\u6d88\u606f\u4e0d\u5b58\u5728")
    before = dict(row)
    if action == "recall":
        await db.execute(text("UPDATE chat_message SET revoked_at=COALESCE(revoked_at, UTC_TIMESTAMP()) WHERE id=:id"), {"id": message_id})
    elif action == "restore":
        await db.execute(text("UPDATE chat_message SET revoked_at=NULL WHERE id=:id"), {"id": message_id})
    else:
        raise HTTPException(422, detail="\u6d88\u606f\u5904\u7f6e\u52a8\u4f5c\u4ec5\u652f\u6301 recall \u6216 restore")
    updated = (await db.execute(text("""SELECT id, session_id, from_user_id, to_user_id, type,
        content, media_url, is_read, revoked_at, created_at FROM chat_message WHERE id=:id"""), {"id": message_id})).mappings().one()
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, before_json, after_json, reason)
        VALUES (:actor, :action, 'chat_message', :id, :before_json, :after_json, :reason)"""), {
        "actor": admin.account.id,
        "action": f"message.{action}",
        "id": message_id,
        "before_json": json.dumps({**before, "content": redact_sensitive(before.get("content"), level)}, ensure_ascii=False, default=str),
        "after_json": json.dumps({**dict(updated), "content": redact_sensitive(updated.get("content"), level)}, ensure_ascii=False, default=str),
        "reason": reason,
    })
    await db.commit()
    return _message_item(dict(updated), level)


async def create_admin_announcement(
    db: AsyncSession,
    admin: CurrentMatchmakerAdmin,
    body: AdminAnnouncementCreate,
) -> AdminAnnouncementItem:
    if admin.account.data_scope != "ALL" and "*" not in admin.permissions:
        raise HTTPException(403, detail="\u516c\u544a\u53d1\u5e03\u4ec5\u5141\u8bb8\u5168\u5c40\u8303\u56f4\u7ba1\u7406\u5458\u64cd\u4f5c")
    result = await db.execute(text("""INSERT INTO admin_announcement
        (tenant_id, category, title, link_to, published_at)
        VALUES (1, :category, :title, :link_to, :published_at)"""), body.model_dump())
    announcement_id = int(result.lastrowid)
    await db.execute(text("""INSERT INTO business_audit_log
        (actor_user_id, action, resource_type, resource_id, after_json)
        VALUES (:actor, 'announcement.create', 'admin_announcement', :id, :after_json)"""), {
        "actor": admin.account.id,
        "id": announcement_id,
        "after_json": json.dumps(body.model_dump(), ensure_ascii=False, default=str),
    })
    await db.commit()
    row = (await db.execute(text("""SELECT id, category, title, link_to, published_at, created_at
        FROM admin_announcement WHERE id=:id"""), {"id": announcement_id})).mappings().one()
    return AdminAnnouncementItem(**dict(row))
