"""社交用户摘要：SocialUser / RelationUserSummary 需支持头像缩略图与性别。"""

from __future__ import annotations

from app.schemas.discovery import RelationUserSummary
from app.schemas.social import SocialUser
from app.services.social import _social_user


def test_social_user_accepts_avatar_thumb_and_gender() -> None:
    user = SocialUser(user_id=1, nickname="甲", avatar=None, avatar_thumb="thumb.webp", gender=1, age=30)
    assert user.avatar_thumb == "thumb.webp"
    assert user.gender == 1


def test_social_user_fields_are_optional_for_backcompat() -> None:
    # 旧调用方不传新字段也应能构造（只增不改）。
    user = SocialUser(user_id=1, nickname=None, avatar=None, age=None)
    assert user.avatar_thumb is None
    assert user.gender is None


def test_social_user_mapper_forwards_thumb_and_gender() -> None:
    row = {"user_id": 2, "nickname": "乙", "avatar": "a.webp", "avatar_thumb": "t.webp", "gender": 2, "birthday": None}
    user = _social_user(row)
    assert user.user_id == 2
    assert user.avatar_thumb == "t.webp"
    assert user.gender == 2


def test_relation_user_summary_accepts_avatar_thumb() -> None:
    summary = RelationUserSummary(user_id=3, nickname=None, avatar=None, avatar_thumb="t.webp", age=None, city_code=None)
    assert summary.avatar_thumb == "t.webp"
