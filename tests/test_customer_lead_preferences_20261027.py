"""客源线索「择偶要求」读写（方案①：复用 user_partner_preference）回归。

覆盖范围（无需 DB，纯 schema / 服务逻辑 / 路由注册）：
- 未入库线索：GET 返回 editable=False、preference=None（不报错）；PUT 返回 409。
- 已入库线索：GET/PUT 以 converted_user_id 复用 profile 的择偶读写；PUT 追加一份审计并 commit。
- 出参 schema：LeadPreferenceResponse 能把 profile 返回的 dict 归一化为 PreferenceResponse。
- 路由注册：GET/PUT /admin/customer-leads/{lead_id}/preferences 均挂在客源后台域。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import customer_leads_admin as lead_routes
from app.schemas.auth import PreferenceUpdateRequest
from app.schemas.customer_lead_admin import CustomerLead, LeadPreferenceResponse


class _Result:
    def __init__(self, *, rows=None, mappings_rows=None, scalar=None):
        self._rows = list(rows or [])
        self._mappings_rows = list(mappings_rows or [])
        self._scalar = scalar

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def mappings(self):
        return SimpleNamespace(all=lambda: self._mappings_rows, first=lambda: (self._mappings_rows[0] if self._mappings_rows else None))


class _StubDB:
    """记录写库语句与 commit 次数，用于无 DB 环境下的服务逻辑断言。"""

    def __init__(self, results: list[_Result] | None = None):
        self._results = list(results or [])
        self.writes: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        if self._results:
            return self._results.pop(0)
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self.writes.append((sql, dict(params or {})))
        return _Result(rows=[], mappings_rows=[])

    async def commit(self):
        self.commits += 1


def _lead(*, converted_user_id: int | None) -> CustomerLead:
    return CustomerLead(
        id=1, name="张女士", phone="13800000000", wechat=None, source="抖音",
        intention_level=2, status="CONVERTED", matchmaker_id=None, organization_id=None,
        next_follow_at=None, converted_user_id=converted_user_id, remark=None,
        created_by=1, created_at=datetime(2026, 10, 1), updated_at=datetime(2026, 10, 1),
    )


def _pref_dict(user_id: int) -> dict[str, Any]:
    """profile.get_preferences / update_preferences 的返回结构。"""
    return {
        "user_id": user_id, "age_min": 22, "age_max": 30, "height_min": 165, "height_max": 185,
        "education_min": 3, "income_min": 10.0, "marriage_status": 1,
        "preferred_province_code": "440000", "preferred_city_codes": ["440100"],
        "accept_long_distance": False, "accept_cross_province": True,
        "housing_requirement": 1, "smoking_requirement": 0, "drinking_requirement": 0,
        "dating_goal": "倾向结婚", "meeting_pace": "真诚高效", "children_intention": "想要孩子",
        "extra_requirement": None,
    }


# --------------------------------------------------------------------------- #
# 服务：未入库线索
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_preferences_unconverted_is_not_editable(monkeypatch) -> None:
    import app.services.customer_lead_admin as lead_service

    async def _fake_get_lead(db, lead_id, *args, **kwargs):
        return _lead(converted_user_id=None)

    monkeypatch.setattr(lead_service, "get_lead", _fake_get_lead)
    result = await lead_service.get_lead_preferences(_StubDB(), 1)
    assert result.editable is False
    assert result.converted_user_id is None
    assert result.preference is None


@pytest.mark.asyncio
async def test_update_preferences_unconverted_raises_409(monkeypatch) -> None:
    from fastapi import HTTPException

    import app.services.customer_lead_admin as lead_service

    async def _fake_get_lead(db, lead_id, *args, **kwargs):
        return _lead(converted_user_id=None)

    monkeypatch.setattr(lead_service, "get_lead", _fake_get_lead)
    with pytest.raises(HTTPException) as exc:
        await lead_service.update_lead_preferences(_StubDB(), 9, 1, PreferenceUpdateRequest(age_min=22))
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------- #
# 服务：已入库线索（复用 profile 择偶读写）
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_preferences_converted_reuses_profile(monkeypatch) -> None:
    import app.services.customer_lead_admin as lead_service
    import app.services.profile as profile_service

    async def _fake_get_lead(db, lead_id, *args, **kwargs):
        return _lead(converted_user_id=77)

    captured: dict[str, Any] = {}

    async def _fake_get_preferences(db, user_id):
        captured["user_id"] = user_id
        return _pref_dict(user_id)

    monkeypatch.setattr(lead_service, "get_lead", _fake_get_lead)
    monkeypatch.setattr(profile_service, "get_preferences", _fake_get_preferences)
    result = await lead_service.get_lead_preferences(_StubDB(), 1)
    assert captured["user_id"] == 77
    assert result.editable is True
    assert result.converted_user_id == 77
    # dict 被归一化为 PreferenceResponse
    assert isinstance(result.preference, type(LeadPreferenceResponse(lead_id=1, preference=_pref_dict(77)).preference))
    assert result.preference.age_min == 22


@pytest.mark.asyncio
async def test_update_preferences_converted_writes_audit_and_commits(monkeypatch) -> None:
    import app.services.customer_lead_admin as lead_service
    import app.services.profile as profile_service

    async def _fake_get_lead(db, lead_id, *args, **kwargs):
        return _lead(converted_user_id=77)

    calls: dict[str, Any] = {}

    async def _fake_update_preferences(db, user_id, request):
        calls["user_id"] = user_id
        calls["request"] = request
        return _pref_dict(user_id)

    monkeypatch.setattr(lead_service, "get_lead", _fake_get_lead)
    monkeypatch.setattr(profile_service, "update_preferences", _fake_update_preferences)
    db = _StubDB()
    body = PreferenceUpdateRequest(age_min=25, age_max=35)
    result = await lead_service.update_lead_preferences(db, 9, 1, body)
    assert calls["user_id"] == 77
    assert calls["request"] is body
    assert result.editable is True
    assert db.commits == 1
    audit = [sql for sql, _ in db.writes if "business_audit_log" in sql]
    assert len(audit) == 1
    params = next(params for sql, params in db.writes if "business_audit_log" in sql)
    assert params["actor"] == 9 and params["id"] == 1


# --------------------------------------------------------------------------- #
# schema / 路由注册
# --------------------------------------------------------------------------- #


def test_preference_range_validation_reused() -> None:
    PreferenceUpdateRequest(age_min=22, age_max=30)
    with pytest.raises(Exception):
        PreferenceUpdateRequest(age_min=40, age_max=30)


def test_lead_preference_response_accepts_dict_preference() -> None:
    resp = LeadPreferenceResponse(lead_id=1, converted_user_id=77, editable=True, preference=_pref_dict(77))
    assert resp.preference is not None and resp.preference.user_id == 77


def test_preference_routes_registered() -> None:
    get_paths = {r.path for r in lead_routes.router.routes if "GET" in (r.methods or set())}
    put_paths = {r.path for r in lead_routes.router.routes if "PUT" in (r.methods or set())}
    assert "/admin/customer-leads/{lead_id}/preferences" in get_paths
    assert "/admin/customer-leads/{lead_id}/preferences" in put_paths
