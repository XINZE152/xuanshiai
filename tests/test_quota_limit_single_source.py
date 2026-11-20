"""额度上限单一来源（2026-10-29）。

历史问题：``/users/me/quotas`` 的 apply 上限用 ``settings.apply_daily_free_limit``，
而实际扣次用平台权限快照 + 套餐加次；``/community/quotas`` 又用 ``vip_apply_daily_limit``。
三处各算一套 ⇒ 后台改过配置就出现"接口说 3、实际扣 10"。

现在上限统一由 ``quotas.resolve_apply_limit / resolve_superlike_limit /
resolve_paper_plane_limit`` 给出，``summary`` 只是它的展示层。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import membership as membership_service
from app.services import quotas as quotas_service


def _patch(monkeypatch: pytest.MonkeyPatch, *, runtime: dict[str, int], line_perm: dict[str, Any],
           vip: bool = False, rights: dict[str, Any] | None = None) -> None:
    async def membership_rights(*_args: object, **_kwargs: object) -> tuple[bool, dict[str, Any]]:
        return vip, dict(rights or {})

    async def match_line(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return dict(line_perm)

    async def runtime_value(_db: object, _namespace: str, key: str, default: object = None) -> object:
        return runtime.get(key, default)

    monkeypatch.setattr(quotas_service, "_membership_rights", membership_rights)
    monkeypatch.setattr(quotas_service, "match_line_permissions", match_line)
    monkeypatch.setattr(quotas_service, "get_runtime_value", runtime_value)


@pytest.mark.asyncio
async def test_apply_limit_prefers_unified_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, runtime={"free_apply_daily_limit": 3}, line_perm={"apply_daily": 2})
    assert await quotas_service.resolve_apply_limit(object(), 7) == 2


@pytest.mark.asyncio
async def test_apply_limit_reads_platform_snapshot_for_free_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, runtime={"free_apply_daily_limit": 5}, line_perm={})
    assert await quotas_service.resolve_apply_limit(object(), 7, is_vip=False) == 5


@pytest.mark.asyncio
async def test_apply_limit_adds_membership_bonus_for_vip(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        runtime={"free_apply_daily_limit": 3},
        line_perm={},
        vip=True,
        rights={"apply_bonus": 2},
    )
    assert await quotas_service.resolve_apply_limit(object(), 7, is_vip=True) == 5


@pytest.mark.asyncio
async def test_apply_limit_resolves_vip_from_membership_when_unspecified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(
        monkeypatch,
        runtime={"free_apply_daily_limit": 3},
        line_perm={},
        vip=True,
        rights={"apply_bonus": 4},
    )
    assert await quotas_service.resolve_apply_limit(object(), 7) == 7


@pytest.mark.asyncio
async def test_superlike_and_paper_plane_limits_come_from_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(
        monkeypatch,
        runtime={
            "free_superlike_daily_limit": 4,
            "vip_superlike_daily_limit": 6,
            "paper_plane_daily_limit": 5,
        },
        line_perm={},
    )
    assert await quotas_service.resolve_superlike_limit(object(), 7, is_vip=False) == 4
    assert await quotas_service.resolve_superlike_limit(object(), 7, is_vip=True) == 6
    assert await quotas_service.resolve_paper_plane_limit(object()) == 5


@pytest.mark.asyncio
async def test_summary_uses_the_same_resolvers(monkeypatch: pytest.MonkeyPatch) -> None:
    """summary 不得自己算上限：把 resolver 换成哨兵值，summary 必须原样透出。"""
    _patch(monkeypatch, runtime={}, line_perm={})

    async def apply_limit(*_args: object, **_kwargs: object) -> int:
        return 11

    async def browse_limit(*_args: object, **_kwargs: object) -> int:
        return 12

    async def superlike_limit(*_args: object, **_kwargs: object) -> int:
        return 13

    async def paper_limit(*_args: object, **_kwargs: object) -> int:
        return 14

    async def used(*_args: object, **_kwargs: object) -> int:
        return 0

    async def extra(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(quotas_service, "_extra_remaining", extra)

    monkeypatch.setattr(quotas_service, "resolve_apply_limit", apply_limit)
    monkeypatch.setattr(quotas_service, "resolve_browse_limit", browse_limit)
    monkeypatch.setattr(quotas_service, "resolve_superlike_limit", superlike_limit)
    monkeypatch.setattr(quotas_service, "resolve_paper_plane_limit", paper_limit)
    monkeypatch.setattr(quotas_service, "get_daily_used", used)

    summary = await quotas_service.summary(object(), 7)  # type: ignore[arg-type]
    limits = {item.quota_code: item.daily_limit for item in summary.items}
    assert limits == {"apply": 11, "browse": 12, "superlike": 13, "paper_plane": 14}
    assert all(item.total_remaining == item.daily_limit for item in summary.items)


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [None, 0, 2])
async def test_membership_daily_limits_match_consumption_resolvers(monkeypatch, override):
    from datetime import datetime

    raw_rights = '{"apply_bonus": 4, "browse_daily_limit": 0, "superlike_daily_limit": 99}'
    end_at = datetime(2027, 1, 2, 12)
    active_row = {"package_type": "vip", "start_at": None, "end_at": end_at, "rights": raw_rights}
    runtime = {
        "free_apply_daily_limit": 5,
        "vip_apply_daily_limit": 99,
        "free_browse_daily_limit": 8,
        "vip_superlike_daily_limit": 6,
        "free_superlike_daily_limit": 0,
    }
    _patch(monkeypatch, runtime=runtime, line_perm={"apply_daily": override}, vip=True,
           rights=membership_service.parse_package_rights(raw_rights))

    async def active(_db, user_id):
        return active_row if user_id == 7 else None

    class PackageDb:
        async def execute(self, statement):
            assert "WHERE is_active=1 ORDER BY sort,id" in str(statement)
            return self

        def mappings(self):
            return self

        def all(self):
            package = {"name": "试验套餐", "duration_days": 37, "price": 12.34,
                       "original_price": None, "daily_price": None, "badge": None}
            return [dict(package, code="test-alpha", rights=raw_rights),
                    dict(package, code="test-beta", rights={"apply_bonus": 2})]

    monkeypatch.setattr(membership_service, "get_active_membership_row", active)
    db = PackageDb()
    first, second = await membership_service.list_packages(db)
    vip = await membership_service.get_status(db, 7)
    free = await membership_service.get_status(db, 8)

    assert (first.duration_days, first.price) == (37, 12.34)
    assert vip.is_vip and vip.end_at == end_at
    assert first.daily_limits == vip.daily_limits
    assert vip.daily_limits.model_dump() == {
        "apply": await quotas_service.resolve_apply_limit(db, 7, is_vip=True),
        "browse": await quotas_service.resolve_browse_limit(db, 7, is_vip=True),
        "superlike": await quotas_service.resolve_superlike_limit(db, 7, is_vip=True),
    } == {"apply": override or 9, "browse": 0, "superlike": 6}
    assert second.daily_limits.model_dump() == {"apply": override or 7, "browse": 20, "superlike": 6}
    assert not free.is_vip and free.end_at is None
    assert free.daily_limits.model_dump() == {
        "apply": await quotas_service.resolve_apply_limit(db, 8, is_vip=False),
        "browse": await quotas_service.resolve_browse_limit(db, 8, is_vip=False),
        "superlike": await quotas_service.resolve_superlike_limit(db, 8, is_vip=False),
    } == {"apply": override or 5, "browse": 8, "superlike": 0}
