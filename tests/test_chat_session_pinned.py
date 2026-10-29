"""会话置顶回显：ChatSessionResponse 必须下发 pinned。

根因背景：services.social.list_chat_sessions 构造时传了 ``pinned=...``，但
schema 层缺字段，pydantic v2 默认 extra="ignore" 静默丢弃，导致前端永远拿不到
置顶态（R79）。这里用纯 schema 断言把字段契约锁死。
"""

from __future__ import annotations

from app.schemas.social import ChatSessionResponse, SocialUser


def _target() -> SocialUser:
    return SocialUser(user_id=2, nickname="对方", avatar=None, age=25)


def test_chat_session_response_exposes_pinned_field() -> None:
    assert "pinned" in ChatSessionResponse.model_fields


def test_pinned_defaults_to_false() -> None:
    item = ChatSessionResponse(
        id=1,
        target=_target(),
        last_message="你好",
        last_message_time=None,
        unread_count=0,
    )
    assert item.pinned is False


def test_pinned_true_roundtrips_in_serialization() -> None:
    item = ChatSessionResponse(
        id=1,
        target=_target(),
        last_message=None,
        last_message_time=None,
        unread_count=0,
        pinned=True,
    )
    assert item.pinned is True
    # 序列化结果必须带上 pinned，前端才能读到。
    assert item.model_dump()["pinned"] is True


def test_target_carries_avatar_thumb_and_gender() -> None:
    target = SocialUser(user_id=2, nickname="对方", avatar=None, avatar_thumb="thumb.webp", gender=2, age=25)
    item = ChatSessionResponse(id=1, target=target, last_message=None, last_message_time=None, unread_count=0, pinned=True)
    assert item.model_dump()["target"]["avatar_thumb"] == "thumb.webp"
    assert item.model_dump()["target"]["gender"] == 2
