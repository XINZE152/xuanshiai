"""详细资料 12 项读写与「实名后性别/生日锁定」（2026-10-27 需求 3 + D1）。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.schemas.auth import ProfileUpdateRequest
from app.services import profile


class _Result:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        first: dict[str, Any] | None = None,
        scalar: Any = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._one = one
        self._first = first
        self._scalar = scalar
        self._rows = rows or []

    def mappings(self) -> "_Result":
        return self

    def one(self) -> dict[str, Any]:
        assert self._one is not None
        return self._one

    def first(self) -> dict[str, Any] | None:
        return self._first

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar(self) -> Any:
        return self._scalar


_PROFILE_ROW: dict[str, Any] = {
    "user_id": 7,
    "nickname": "小张",
    "gender": 1,
    "birthday": date(1996, 3, 1),
    "is_married": 1,
    "avatar": None,
    "height": 178,
    "weight": 70,
    "occupation": "教师",
    "industry": "教育",
    "education_level": 3,
    "income": 200000,
    "constellation": "双鱼座",
    "zodiac": "鼠",
    "household": "杭州",
    "ethnicity": "汉族",
    "house": "已购房",
    "car": "已购车",
    "smoking": "不吸烟",
    "drinking": "偶尔",
    "religion": "无",
    "marriage_plan": "一年内",
    "household_province_code": None,
    "household_city_code": None,
    "household_district_code": None,
    "school": "浙江大学",
    "company": "某中学",
    "hometown_province_code": None,
    "hometown_city_code": None,
    "hometown_district_code": None,
    "residence_province_code": None,
    "residence_city_code": None,
    "residence_district_code": None,
    "self_intro": "认真生活的人",
    "qa_answers": None,
    "interest_tags": None,
    "personality_tags": None,
    "mbti": "INTJ",
    "tags": None,
    "completion_score": 100,
    "hide_school": 0,
    "hide_company": 0,
    "only_vip_can_see_detail": 0,
}


class _ProfileDb:
    def __init__(self, *, row: dict[str, Any]) -> None:
        self.row = row

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        if "FROM user_profile p" in sql or "FROM users u LEFT JOIN user_profile p" in sql:
            return _Result(first=self.row)
        if "FROM user_media" in sql:
            return _Result(rows=[])
        return _Result()


@pytest.mark.asyncio
async def test_get_profile_returns_detail_fields() -> None:
    data = await profile.get_profile(_ProfileDb(row=_PROFILE_ROW), 7, public=False)  # type: ignore[arg-type]

    assert data["zodiac"] == "鼠"
    assert data["household"] == "杭州"
    assert data["household_display"] == "杭州"
    assert data["ethnicity"] == "汉族"
    assert data["school"] == "浙江大学"
    assert data["company"] == "某中学"
    assert data["marriage_plan"] == "一年内"
    assert data["constellation"] == "双鱼座"


@pytest.mark.asyncio
async def test_public_profile_masks_school_and_company() -> None:
    row = dict(_PROFILE_ROW, hide_school=1, hide_company=1)

    data = await profile.get_profile(_ProfileDb(row=row), 7, public=True)  # type: ignore[arg-type]

    assert data["school"] is None
    assert data["company"] is None
    assert data["education_level"] is None
    assert data["occupation"] is None


class _WriteDb:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        self.statements.append(str(statement))
        return _Result()

    async def commit(self) -> None:
        return None


@pytest.fixture()
def _silence_profile_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def empty_profile(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(profile, "_lock_profile_update", noop)
    monkeypatch.setattr(profile, "recalculate_completion", noop)
    monkeypatch.setattr(profile, "increment_revision_and_enqueue", noop)
    monkeypatch.setattr(profile, "get_profile", empty_profile)


@pytest.mark.asyncio
async def test_school_and_company_go_to_user_auth(_silence_profile_writes: None) -> None:
    db = _WriteDb()

    await profile.update_profile(
        db,  # type: ignore[arg-type]
        7,
        ProfileUpdateRequest(school="浙江大学", company="某中学", zodiac="鼠"),
    )

    joined = "\n".join(db.statements)
    assert "INSERT IGNORE INTO user_auth" in joined
    assert "UPDATE user_auth SET school = :school, company = :company" in joined
    profile_inserts = [s for s in db.statements if s.startswith("INSERT INTO user_profile")]
    assert profile_inserts, "user_profile 仍应写入其余字段"
    assert "zodiac" in profile_inserts[0]
    assert "school" not in profile_inserts[0], "school 不是 user_profile 的列，必须被弹出"


class _IdentityDb:
    def __init__(self, *, realname_status: int, old_gender: int, old_birthday: date) -> None:
        self.realname_status = realname_status
        self.old_gender = old_gender
        self.old_birthday = old_birthday
        self.statements: list[str] = []

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM user_auth" in sql:
            return _Result(scalar=self.realname_status)
        if "SELECT gender, birthday FROM users" in sql:
            return _Result(first={"gender": self.old_gender, "birthday": self.old_birthday})
        return _Result()

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_gender_change_locked_after_realname(_silence_profile_writes: None) -> None:
    db = _IdentityDb(realname_status=2, old_gender=1, old_birthday=date(1996, 3, 1))

    with pytest.raises(profile.HTTPException) as error:
        await profile.update_profile(db, 7, ProfileUpdateRequest(gender=2))  # type: ignore[arg-type]

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "GENDER_LOCKED_BY_REALNAME"
    assert "性别" in error.value.detail["message"]


@pytest.mark.asyncio
async def test_birthday_change_locked_after_realname(_silence_profile_writes: None) -> None:
    db = _IdentityDb(realname_status=2, old_gender=1, old_birthday=date(1996, 3, 1))

    with pytest.raises(profile.HTTPException) as error:
        await profile.update_profile(
            db,  # type: ignore[arg-type]
            7,
            ProfileUpdateRequest(birthday=date(2000, 1, 1)),
        )

    assert error.value.detail["code"] == "GENDER_LOCKED_BY_REALNAME"
    assert "生日" in error.value.detail["message"]


@pytest.mark.asyncio
async def test_gender_editable_before_realname(_silence_profile_writes: None) -> None:
    db = _IdentityDb(realname_status=0, old_gender=1, old_birthday=date(1996, 3, 1))

    await profile.update_profile(db, 7, ProfileUpdateRequest(gender=2))  # type: ignore[arg-type]

    assert any("UPDATE users SET gender = :gender" in statement for statement in db.statements)
