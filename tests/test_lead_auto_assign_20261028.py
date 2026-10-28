"""客源智能分派（auto_assign）回归测试。

覆盖：
- 引擎 resolve_assigned_matchmaker：designated 命中/失效、round_robin_random 池选/池空、
  其它策略或未配置/停用 → None（选不到人保持未分派）；auto_assign=false 直接 None；
- 单条重算 auto_assign_lead：已分派不 force 跳过、未分派写入、选不到人不改、force 覆盖；
- 契约：CustomerLeadCreate 带 auto_assign(默认 False)、CustomerLeadCreateResult 带 assigned；
- 路由：POST /admin/customer-leads/{lead_id}/auto-assign 已注册；建单响应含 assigned。

无真实 DB：monkeypatch 引擎/服务的 DB 助手；FakeDb 仅记录写调用。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.schemas.customer_lead_admin import (
    CustomerLead,
    CustomerLeadCreate,
    CustomerLeadCreateResult,
)
from app.services import customer_lead_admin as lead_service
from app.services import lead_apportion as engine


async def _resolve(monkeypatch: pytest.MonkeyPatch, config: dict | None, *, valid: bool = True, pool: int | None = 77) -> int | None:
    async def fake_load(_db):
        return config

    async def fake_valid(_db, _uid):
        return valid

    async def fake_pool(_db):
        return pool

    monkeypatch.setattr(engine, "_load_assign_config", fake_load)
    monkeypatch.setattr(engine, "_is_valid_service_matchmaker", fake_valid)
    monkeypatch.setattr(engine, "_pick_random_pool_matchmaker", fake_pool)
    return await engine.resolve_assigned_matchmaker(object(), auto_assign=True)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_designated_returns_configured_target(monkeypatch: pytest.MonkeyPatch) -> None:
    got = await _resolve(monkeypatch, {"strategy": "designated", "target_matchmaker_id": 55, "is_enabled": 1})
    assert got == 55


@pytest.mark.asyncio
async def test_designated_invalid_target_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    got = await _resolve(monkeypatch, {"strategy": "designated", "target_matchmaker_id": 55, "is_enabled": 1}, valid=False)
    assert got is None


@pytest.mark.asyncio
async def test_round_robin_picks_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    got = await _resolve(monkeypatch, {"strategy": "round_robin_random", "target_matchmaker_id": None, "is_enabled": 1}, pool=88)
    assert got == 88


@pytest.mark.asyncio
async def test_round_robin_empty_pool_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    got = await _resolve(monkeypatch, {"strategy": "round_robin_random", "target_matchmaker_id": None, "is_enabled": 1}, pool=None)
    assert got is None


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["by_region", "by_promoter", "by_partner", "none", None])
async def test_unsupported_or_none_strategy_returns_none(monkeypatch: pytest.MonkeyPatch, strategy: str | None) -> None:
    got = await _resolve(monkeypatch, {"strategy": strategy, "target_matchmaker_id": 55, "is_enabled": 1})
    assert got is None


@pytest.mark.asyncio
async def test_disabled_config_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    got = await _resolve(monkeypatch, {"strategy": "designated", "target_matchmaker_id": 55, "is_enabled": 0})
    assert got is None


@pytest.mark.asyncio
async def test_no_config_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    got = await _resolve(monkeypatch, None)
    assert got is None


@pytest.mark.asyncio
async def test_auto_assign_flag_off_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_db):
        raise AssertionError("不应读取配置")

    monkeypatch.setattr(engine, "_load_assign_config", boom)
    assert await engine.resolve_assigned_matchmaker(object(), auto_assign=False) is None  # type: ignore[arg-type]


class _FakeResult:
    def scalar(self):
        return 1


class _FakeDb:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []
        self.committed = False

    async def execute(self, stmt, params=None):  # noqa: ANN001
        self.writes.append((str(stmt), dict(params or {})))
        return _FakeResult()

    async def commit(self) -> None:
        self.committed = True


def _lead(**overrides) -> CustomerLead:
    now = datetime(2026, 10, 28)
    base = dict(
        id=10, name="小李", phone="13800138000", wechat=None, source="抖音",
        intention_level=1, gender=1, status="NEW", audit_status="active",
        matchmaker_id=None, organization_id=None, promoter_id=None,
        next_follow_at=None, converted_user_id=None, remark=None, created_by=1,
        created_at=now, updated_at=now,
    )
    base.update(overrides)
    return CustomerLead(**base)


async def _patch_get_lead(monkeypatch: pytest.MonkeyPatch, lead: CustomerLead) -> None:
    async def fake_get_lead(_db, _lead_id, _redaction="standard", **_k):
        return lead

    monkeypatch.setattr(lead_service, "get_lead", fake_get_lead)


async def _patch_resolve(monkeypatch: pytest.MonkeyPatch, value: int | None) -> None:
    async def fake_resolve(_db, *, auto_assign):
        return value

    monkeypatch.setattr(lead_service, "resolve_assigned_matchmaker", fake_resolve)


@pytest.mark.asyncio
async def test_auto_assign_lead_skips_when_already_assigned(monkeypatch: pytest.MonkeyPatch) -> None:
    await _patch_get_lead(monkeypatch, _lead(matchmaker_id=99))
    await _patch_resolve(monkeypatch, 123)  # 不应被调用（未 force）
    db = _FakeDb()

    result = await lead_service.auto_assign_lead(db, 1, 10)

    assert result.assigned is True
    assert result.matchmaker_id == 99
    assert db.writes == []  # 已分派且不 force：不写库、不改归属


@pytest.mark.asyncio
async def test_auto_assign_lead_assigns_when_unassigned(monkeypatch: pytest.MonkeyPatch) -> None:
    await _patch_resolve(monkeypatch, 123)
    db = _FakeDb()
    updated = _lead(matchmaker_id=123)

    # 第一次取原始 lead（未分派），UPDATE 后再取新值
    calls = [_lead(matchmaker_id=None), updated]
    idx = {"i": 0}

    async def seq_get_lead(_db, _lead_id, _redaction="standard", **_k):
        item = calls[idx["i"]]
        idx["i"] += 1
        return item

    monkeypatch.setattr(lead_service, "get_lead", seq_get_lead)

    result = await lead_service.auto_assign_lead(db, 1, 10)

    assert result.assigned is True
    assert result.matchmaker_id == 123
    assert db.committed is True
    assert any("UPDATE customer_lead SET matchmaker_id" in sql for sql, _ in db.writes)


@pytest.mark.asyncio
async def test_auto_assign_lead_no_target_keeps_unassigned(monkeypatch: pytest.MonkeyPatch) -> None:
    await _patch_get_lead(monkeypatch, _lead(matchmaker_id=None))
    await _patch_resolve(monkeypatch, None)
    db = _FakeDb()

    result = await lead_service.auto_assign_lead(db, 1, 10)

    assert result.assigned is False
    assert result.matchmaker_id is None
    assert db.writes == []
    assert db.committed is False


@pytest.mark.asyncio
async def test_auto_assign_lead_force_overwrites_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = [_lead(matchmaker_id=99), _lead(matchmaker_id=123)]
    idx = {"i": 0}

    async def seq_get_lead(_db, _lead_id, _redaction="standard", **_k):
        item = calls[idx["i"]]
        idx["i"] += 1
        return item

    monkeypatch.setattr(lead_service, "get_lead", seq_get_lead)
    await _patch_resolve(monkeypatch, 123)
    db = _FakeDb()

    result = await lead_service.auto_assign_lead(db, 1, 10, force=True)

    assert result.assigned is True
    assert result.matchmaker_id == 123
    assert any("UPDATE customer_lead SET matchmaker_id" in sql for sql, _ in db.writes)


def test_create_schema_auto_assign_default_and_result_assigned() -> None:
    body = CustomerLeadCreate(name="小李", phone="13800138000", source="抖音")
    assert body.auto_assign is False  # 默认待分派，向后兼容
    lead = _lead()
    result = CustomerLeadCreateResult(**lead.model_dump(), assigned=True)
    assert result.assigned is True
    with pytest.raises(ValueError):
        CustomerLeadCreateResult(**lead.model_dump())  # assigned 必填


def test_auto_assign_route_registered_and_create_response_has_assigned() -> None:
    from app.api.routes import customer_leads_admin as lead_routes
    from app.main import app

    post_paths = {r.path for r in lead_routes.router.routes if "POST" in (r.methods or set())}
    assert "/admin/customer-leads/{lead_id}/auto-assign" in post_paths

    spec = app.openapi()
    create_schema = spec["components"]["schemas"]["CustomerLeadCreate"]
    assert "auto_assign" in create_schema["properties"]
    result_schema = spec["components"]["schemas"]["CustomerLeadCreateResult"]
    assert "assigned" in result_schema["properties"]
