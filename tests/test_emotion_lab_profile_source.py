"""情感实验室 E1.5：`PUT /emotion-lab/profile-source`（2026-10-29）。

只做「把端侧 MBTI 写进资料」：
- 必须显式 `confirmed=true`，否则拒绝（避免"看起来保存成功但没进资料"）；
- 同一结果重复提交不写库、返回上次变更时间（幂等）；
- `source=assessment` 必须带 `resultId`；
- 写资料复用 `profile.update_profile`，完整度/修订号与其他资料字段同一条路径。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.auth import ProfileUpdateRequest
from app.schemas.emotion_lab import (
    EmotionLabProfileSourceRequest,
    EmotionLabProfileSourceResponse,
)
from app.services import emotion_lab as service


class _Mappings:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> _Mappings:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._row) if self._row else None


class _Db:
    """按顺序返回脚本化的 user_profile 行。"""

    def __init__(self, *rows: dict[str, Any] | None) -> None:
        self.rows = list(rows)
        self.calls = 0

    async def execute(self, *_args: object, **_kwargs: object) -> _Mappings:
        self.calls += 1
        return _Mappings(self.rows.pop(0) if self.rows else None)


def _request(**overrides: Any) -> EmotionLabProfileSourceRequest:
    payload: dict[str, Any] = {"mbtiType": "ENFP", "source": "self_reported", "confirmed": True}
    payload.update(overrides)
    return EmotionLabProfileSourceRequest.model_validate(payload)


def test_schema_accepts_camel_and_snake_case() -> None:
    camel = EmotionLabProfileSourceRequest.model_validate(
        {"mbtiType": "ENFP", "source": "self_reported", "confirmed": True}
    )
    snake = EmotionLabProfileSourceRequest.model_validate(
        {"mbti_type": "ENFP", "source": "self_reported", "confirmed": True}
    )
    assert camel == snake
    assert camel.mbti_type == "ENFP"


def test_schema_rejects_unknown_mbti_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EmotionLabProfileSourceRequest.model_validate({"mbtiType": "XXXX", "confirmed": True})
    with pytest.raises(ValidationError):
        EmotionLabProfileSourceRequest.model_validate(
            {"mbtiType": "ENFP", "confirmed": True, "unknown": 1}
        )


def test_response_serializes_to_camel_case() -> None:
    response = EmotionLabProfileSourceResponse(
        mbti_type="INFP",
        source="assessment",
        result_id="r-1",
        confirmed_at=datetime(2026, 10, 29, 7, 0, 0),
    )
    dumped = response.model_dump(by_alias=True)
    assert dumped["mbtiType"] == "INFP"
    assert dumped["resultId"] == "r-1"
    assert dumped["assessmentVersion"] is None
    assert dumped["confirmedAt"] == datetime(2026, 10, 29, 7, 0, 0)


@pytest.mark.asyncio
async def test_requires_explicit_confirmation() -> None:
    db = _Db({"mbti": None, "updated_at": None})
    with pytest.raises(HTTPException) as error:
        await service.sync_profile_source(db, 7, _request(confirmed=False))
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "PROFILE_SYNC_CONFIRMATION_REQUIRED"


@pytest.mark.asyncio
async def test_assessment_source_requires_result_id() -> None:
    db = _Db({"mbti": None, "updated_at": None})
    with pytest.raises(HTTPException) as error:
        await service.sync_profile_source(db, 7, _request(source="assessment"))
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "ASSESSMENT_RESULT_REQUIRED"


@pytest.mark.asyncio
async def test_missing_profile_returns_404() -> None:
    db = _Db(None)
    with pytest.raises(HTTPException) as error:
        await service.sync_profile_source(db, 7, _request())
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_changed_mbti_updates_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[Any] = []

    async def update_profile(_db: object, _uid: int, body: ProfileUpdateRequest) -> dict[str, Any]:
        writes.append(body)
        return {"mbti": body.mbti}

    monkeypatch.setattr(service, "update_profile", update_profile)
    stamp = datetime(2026, 10, 29, 6, 0, 0)
    db = _Db({"mbti": "INTJ", "updated_at": stamp}, {"mbti": "ENFP", "updated_at": stamp})

    result = await service.sync_profile_source(db, 7, _request())

    assert len(writes) == 1
    assert writes[0].mbti == "ENFP"
    assert result.mbti_type == "ENFP"
    assert result.confirmed_at == stamp


@pytest.mark.asyncio
async def test_same_mbti_is_idempotent_and_does_not_write(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[Any] = []

    async def update_profile(*args: object, **kwargs: object) -> dict[str, Any]:
        writes.append(args)
        return {}

    monkeypatch.setattr(service, "update_profile", update_profile)
    stamp = datetime(2026, 10, 20, 3, 0, 0)
    db = _Db({"mbti": "ENFP", "updated_at": stamp})

    result = await service.sync_profile_source(db, 7, _request())

    assert writes == []
    assert db.calls == 1, "资料未变化时不应再查一次"
    assert result.confirmed_at == stamp


def test_route_is_registered() -> None:
    import warnings

    warnings.filterwarnings("ignore")
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/emotion-lab/profile-source" in paths
    assert "put" in paths["/api/v1/emotion-lab/profile-source"]
