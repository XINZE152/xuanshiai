"""首页卡片预览：不扣浏览额度、不写浏览记录、不返回 profile（2026-10-29）。

``GET /users/{user_id}/card-preview`` 的存在意义就是"只读公开卡字段"：
一旦它开始扣额度或写 ``user_browse_history``，就等于把首页预取变成一次资料查看
（对方「谁看过我」会出现你）。因此本文件除了断言字段，还断言**扣费/留痕函数根本没被调用**。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.services import discovery as discovery_service


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "user_id": 12,
        "nickname": "阿丙",
        "avatar": "c.png",
        "birthday": None,
        "height": 178,
        "education_level": 3,
        "occupation": "设计师",
        "income": 15000,
        "residence_province_code": "360000",
        "residence_city_code": "360100",
        "residence_district_code": None,
        "hometown_province_code": None,
        "hometown_city_code": None,
        "hometown_district_code": None,
        "same_city": 1,
        "mbti": "INFP",
        "self_intro": "hi",
        "qa_answers": None,
        "interest_tags": "[]",
        "personality_tags": "[]",
        "tags": None,
        "realname_status": 2,
        "is_single_pledge": 0,
        "is_vip": 0,
        "is_boosted": 0,
        "is_favorite": False,
        "is_liked": True,
        "weight": 70,
        "zodiac": None,
        "online_status": 1,
        "hide_online_status": 0,
        "is_married": 1,
    }
    row.update(overrides)
    return row


_MEDIA = [
    {"media_type": "background", "file_url": "bg.png"},
    {"media_type": "photo", "file_url": "p1.png"},
    {"media_type": "photo", "file_url": "p2.png"},
]


def _patch_common(monkeypatch: pytest.MonkeyPatch, row: dict[str, Any] | None) -> None:
    async def browse_open(*_args: object, **_kwargs: object) -> bool:
        return True

    async def ensure_target(*_args: object, **_kwargs: object) -> None:
        return None

    async def target_rows(*_args: object, **_kwargs: object) -> dict[int, dict[str, Any]]:
        return {} if row is None else {int(row["user_id"]): row}

    async def viewer_context(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"completion_score": 100}

    def candidate_score(*_args: object, **_kwargs: object) -> tuple[float, str]:
        return 72.0, "同城"

    async def not_vip(*_args: object, **_kwargs: object) -> bool:
        return False

    async def media(*_args: object, **_kwargs: object) -> list[dict[str, Any]]:
        return list(_MEDIA)

    async def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("预览接口不允许扣额度/写浏览记录")

    monkeypatch.setattr(discovery_service, "platform_browse_open", browse_open)
    monkeypatch.setattr(discovery_service, "_ensure_target", ensure_target)
    monkeypatch.setattr(discovery_service, "_target_rows", target_rows)
    monkeypatch.setattr(discovery_service, "_viewer_context", viewer_context)
    monkeypatch.setattr(discovery_service, "_candidate_score", candidate_score)
    monkeypatch.setattr(discovery_service, "_is_vip", not_vip)
    monkeypatch.setattr(discovery_service, "_get_media", media)
    monkeypatch.setattr(discovery_service, "_consume_browse", forbidden)
    monkeypatch.setattr(discovery_service, "_record_browse", forbidden)


@pytest.mark.asyncio
async def test_preview_never_charges_quota_or_writes_history(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, _row())
    result = await discovery_service.preview_card(object(), 7, 12)  # type: ignore[arg-type]
    assert result.user_id == 12
    assert result.card.is_liked is True
    assert result.background_wall == "bg.png"
    assert result.photos == ["p1.png"], "相册只下发首图，避免变成一个资料浏览入口"
    assert result.income_display == "1.5w"
    # 预览响应没有 profile 字段：不能成为绕过计费的通道
    assert not hasattr(result, "profile")


@pytest.mark.asyncio
async def test_preview_hides_income_when_detail_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, _row(only_vip_can_see_detail=1))
    result = await discovery_service.preview_card(object(), 7, 12)  # type: ignore[arg-type]
    assert result.card.detail_locked is True
    assert result.income_display is None
    # 背景仍下发：首页要能显示背景，这正是本接口存在的理由
    assert result.background_wall == "bg.png"


@pytest.mark.asyncio
async def test_preview_rejects_self_and_invisible_target(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch, _row())
    with pytest.raises(HTTPException) as self_error:
        await discovery_service.preview_card(object(), 12, 12)  # type: ignore[arg-type]
    assert self_error.value.status_code == 422

    _patch_common(monkeypatch, None)
    with pytest.raises(HTTPException) as missing:
        await discovery_service.preview_card(object(), 7, 12)  # type: ignore[arg-type]
    assert missing.value.status_code == 404
