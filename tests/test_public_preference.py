"""「Ta的理想型」公开偏好归一（2026-10-27 需求 2 / C1）。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import discovery


class _Result:
    def __init__(self, *, first: dict[str, Any] | None = None) -> None:
        self._first = first

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._first


class _PreferenceDb:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.statements: list[str] = []

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        self.statements.append(str(statement))
        return _Result(first=self.row)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "age_min": 26,
        "age_max": 34,
        "height_min": 170,
        "height_max": 185,
        "education_min": 3,
        "education_requirement": "本科及以上",
        "income_min": None,
        "income_range": "20-50万",
        "marriage_status": 1,
        "marriage_requirement": "未婚",
        "housing_requirement": 1,
        "housing_expectation": "有房",
        "smoking_requirement": 1,
        "smoking_expectation": "不吸烟",
        "drinking_requirement": 2,
        "drinking_expectation": "可接受",
        "preferred_province_code": "33",
        "preferred_city_codes": json.dumps(["330100"]),
        "accept_long_distance": 0,
        "accept_cross_province": 0,
        "dating_goal": "倾向结婚",
        "meeting_pace": "真诚高效",
        "children_intention": "想要孩子",
        "marriage_timeline": "一年内",
        "preferred_occupation": "教师",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_public_preference_normalizes_texts() -> None:
    data = await discovery._public_preference(_PreferenceDb(_row()), 7)  # type: ignore[arg-type]

    assert data is not None
    assert data["distance_text"] == "最好同城"
    assert data["education_min_text"] == "本科"
    assert data["preferred_city_codes"] == ["330100"]
    assert data["dating_goal"] == "倾向结婚"


@pytest.mark.asyncio
async def test_public_preference_excludes_free_text_requirement() -> None:
    """extra_requirement 是自由文本，按决策不对他人下发。"""
    db = _PreferenceDb(_row())
    data = await discovery._public_preference(db, 7)  # type: ignore[arg-type]

    assert data is not None
    assert "extra_requirement" not in data
    assert "extra_requirement" not in db.statements[0]


@pytest.mark.asyncio
async def test_public_preference_returns_none_without_row() -> None:
    assert await discovery._public_preference(_PreferenceDb(None), 7) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("accept_long", "accept_cross", "expected"),
    [
        (False, False, "最好同城"),
        (True, False, "异地也可以"),
        (True, True, "距离不是问题"),
        (False, True, "距离不是问题"),
    ],
)
def test_distance_text_mapping(accept_long: bool, accept_cross: bool, expected: str) -> None:
    assert discovery._preference_distance_text(accept_long, accept_cross) == expected


@pytest.mark.asyncio
async def test_preference_public_returns_has_data_false_when_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未解锁/未填写时前端靠 has_data 静默隐藏整块。"""

    async def locked(*_args: Any, **_kwargs: Any):
        return SimpleNamespace(preference=None)

    monkeypatch.setattr(discovery, "view_profile", locked)

    payload = await discovery.public_preference(_PreferenceDb(None), 1, 7)  # type: ignore[arg-type]

    assert payload == {"has_data": False, "user_id": 7}


@pytest.mark.asyncio
async def test_preference_public_merges_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unlocked(*_args: Any, **_kwargs: Any):
        return SimpleNamespace(preference={"age_min": 26, "education_min_text": "本科", "distance_text": "最好同城"})

    monkeypatch.setattr(discovery, "view_profile", unlocked)

    payload = await discovery.public_preference(_PreferenceDb(None), 1, 7)  # type: ignore[arg-type]

    assert payload["has_data"] is True
    assert payload["education_min_text"] == "本科"
    assert payload["age_min"] == 26


@pytest.mark.asyncio
async def test_preference_public_rejects_self_view() -> None:
    with pytest.raises(discovery.HTTPException) as error:
        await discovery.public_preference(_PreferenceDb(None), 7, 7)  # type: ignore[arg-type]

    assert error.value.status_code == 422
