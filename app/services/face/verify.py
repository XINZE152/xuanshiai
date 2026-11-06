"""头像人脸自助核验（Q32，2026-11-02）。

现状：``get_face_provider()`` 已存在但全仓无调用点，``face_verified`` 只能由
后台人工审核写入。本模块把「本人已上传头像 → 人脸服务核验 → 写认证标记」
串成一条自助链路；provider 未配置时保持 fail-closed（503），不允许静默通过。
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import consume_daily, daily_quota_key, get_daily_used
from app.core.security import decrypt_sensitive
from app.schemas.face import FaceVerifyResponse
from app.services.face.providers import DisabledFaceProvider, get_face_provider

DAILY_FACE_VERIFY_LIMIT = 3
_DAILY_KEY_PREFIX = "face:verify"


async def verify_avatar(db: AsyncSession, user_id: int, avatar_url: str) -> FaceVerifyResponse:
    row = (
        await db.execute(
            text(
                """SELECT u.avatar, ua.real_name, ua.id_card
                   FROM users u
                   LEFT JOIN user_auth ua
                     ON ua.user_id = u.id AND ua.realname_status = 2
                   WHERE u.id = :id"""
            ),
            {"id": user_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not row.get("avatar") or avatar_url != row["avatar"]:
        raise HTTPException(status_code=403, detail="只能核验本人已上传的头像")

    real_name = str(row.get("real_name") or "").strip() or None
    id_card_number = None
    if row.get("id_card"):
        try:
            id_card_number = decrypt_sensitive(str(row["id_card"]))
        except Exception:  # 解密失败按未实名处理，不阻断请求也不泄露密文
            id_card_number = None

    provider = get_face_provider()
    if isinstance(provider, DisabledFaceProvider):
        # 未配置供应商时先 fail-closed，不消耗用户当日次数。
        raise HTTPException(status_code=503, detail="人脸认证服务未配置")
    if getattr(provider, "name", "") == "aliyun" and (not real_name or not id_card_number):
        raise HTTPException(status_code=503, detail="请先完成实名认证后再进行人脸核验")

    # 读取实名与人脸材料的数据库事务必须在外部核验前关闭。
    await db.rollback()

    key = daily_quota_key(_DAILY_KEY_PREFIX, user_id)
    if not await consume_daily(key, DAILY_FACE_VERIFY_LIMIT):
        raise HTTPException(status_code=429, detail="今日人脸核验次数已达上限（3 次）")
    remaining = max(0, DAILY_FACE_VERIFY_LIMIT - await get_daily_used(key))

    result = await provider.verify(
        user_id=user_id,
        image_ref=avatar_url,
        scene="face_auth",
        real_name=real_name,
        id_card_number=id_card_number,
    )
    if not result.verified:
        raise HTTPException(
            status_code=422,
            detail={"code": "FACE_NOT_MATCHED", "remaining_attempts": remaining},
        )

    await db.execute(
        text(
            "INSERT INTO user_auth (user_id, face_verified, face_score, face_method, face_vendor, verified_at) "
            "VALUES (:user_id, 1, :score, 'self_service', :provider, UTC_TIMESTAMP()) "
            "ON DUPLICATE KEY UPDATE face_verified = 1, face_score = :score, "
            "face_method = 'self_service', face_vendor = :provider, verified_at = UTC_TIMESTAMP()"
        ),
        {"user_id": user_id, "score": result.score, "provider": result.provider},
    )
    await db.execute(
        text(
            "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id) "
            "VALUES (:actor, 'user.face_verify', 'user_auth', :id)"
        ),
        {"actor": user_id, "id": user_id},
    )
    await db.commit()
    return FaceVerifyResponse(
        verified=True,
        provider=result.provider,
        score=result.score,
        remaining_attempts=remaining,
    )
