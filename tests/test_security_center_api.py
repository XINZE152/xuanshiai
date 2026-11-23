"""安全中心接口契约（2026-11-23，P1 + P2）。

背景：前端 ``pagesSub/profileExtra/security.uvue`` 依赖 ``GET /security/center``，
后端曾长期没实现，全站 404 →「安全中心数据加载失败」。本文件锁定：

1. 路由必须真实注册（防再次「前端已上、后端没有」）；
2. 举报口径只统计**本月已完结**（status 1/2 且 reviewed_at 在本月）；
3. 封禁口径来自 ``user_restriction``：``TOTAL_BAN`` 按 ``ends_at`` 分临时/永久；
4. **没有「警告」这种处置类型**，契约里不得出现 ``warnings``；
5. 全部为 0 时 ``governance=None``；只有封禁没有工单时仍要出数据；
6. 举报成立直接封号必须落 ``user_restriction``（否则治理统计与后台记录都看不到）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.schemas.security import SecurityCenterResponse, SecurityGovernance
from app.services import security_center


class _Result:
    def __init__(self, row: dict[str, Any] | None = None, rowcount: int = 1, lastrowid: int = 1) -> None:
        self._row = row
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._row

    def one(self) -> dict[str, Any]:
        assert self._row is not None
        return self._row

    def all(self) -> list[dict[str, Any]]:
        return [] if self._row is None else [self._row]

    def scalar(self) -> Any:
        return None if self._row is None else list(self._row.values())[0]


class _FakeDb:
    """按 SQL 内容分流：举报聚合 / 封禁聚合 / 配置快照。"""

    def __init__(
        self,
        *,
        report_row: dict[str, Any] | None = None,
        restriction_row: dict[str, Any] | None = None,
    ) -> None:
        self.report_row = report_row or {"total_processed": 0, "content_actions": 0, "dismissed": 0}
        self.restriction_row = restriction_row or {
            "temporary_bans": 0,
            "permanent_bans": 0,
            "other_restrictions": 0,
        }
        self.calls: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.calls.append(sql)
        if "FROM user_report" in sql:
            return _Result(self.report_row)
        if "FROM user_restriction" in sql:
            return _Result(self.restriction_row)
        return _Result(None)


def _report_scans(db: _FakeDb) -> int:
    return sum(1 for sql in db.calls if "FROM user_report" in sql)


@pytest.mark.asyncio
async def test_route_is_registered() -> None:
    from app.main import app

    paths = set(app.openapi().get("paths", {}))
    assert "/api/v1/security/center" in paths, "前端已调用 /security/center，路由必须存在"


def test_contract_has_only_real_sources() -> None:
    fields = set(SecurityGovernance.model_fields)
    assert fields == {
        "month_label",
        "total_processed",
        "content_actions",
        "dismissed",
        "temporary_bans",
        "permanent_bans",
        "other_restrictions",
    }
    assert "warnings" not in fields, "系统没有「警告」处置类型，不得进契约"


@pytest.mark.asyncio
async def test_governance_counts_reports_and_restrictions() -> None:
    security_center.clear_governance_cache()
    db = _FakeDb(
        report_row={"total_processed": 12, "content_actions": 9, "dismissed": 3},
        restriction_row={"temporary_bans": 4, "permanent_bans": 2, "other_restrictions": 6},
    )
    result = await security_center._governance(
        db, now=datetime(2026, 11, 23, tzinfo=UTC), use_cache=False
    )
    assert isinstance(result, SecurityGovernance)
    assert result.month_label == "11月"
    assert (result.total_processed, result.content_actions, result.dismissed) == (12, 9, 3)
    assert (result.temporary_bans, result.permanent_bans, result.other_restrictions) == (4, 2, 6)
    report_sql = next(sql for sql in db.calls if "FROM user_report" in sql)
    assert "reviewed_at >= :month_start" in report_sql and "status IN (1, 2)" in report_sql
    restriction_sql = next(sql for sql in db.calls if "FROM user_restriction" in sql)
    assert "TOTAL_BAN" in restriction_sql and "ends_at IS NULL" in restriction_sql


@pytest.mark.asyncio
async def test_governance_is_none_when_month_is_empty() -> None:
    security_center.clear_governance_cache()
    assert await security_center._governance(_FakeDb(), use_cache=False) is None


@pytest.mark.asyncio
async def test_governance_shows_bans_even_without_reports() -> None:
    """只有封禁、没有工单的月份也要出数字（否则封了人却显示「暂无治理数据」）。"""
    security_center.clear_governance_cache()
    db = _FakeDb(
        report_row={"total_processed": 0, "content_actions": 0, "dismissed": 0},
        restriction_row={"temporary_bans": 0, "permanent_bans": 1, "other_restrictions": 0},
    )
    result = await security_center._governance(
        db, now=datetime(2026, 11, 23, tzinfo=UTC), use_cache=False
    )
    assert result is not None and result.permanent_bans == 1


@pytest.mark.asyncio
async def test_governance_cache_avoids_repeated_scan() -> None:
    security_center.clear_governance_cache()
    db = _FakeDb(report_row={"total_processed": 5, "content_actions": 3, "dismissed": 2})
    now = datetime(2026, 11, 23, tzinfo=UTC)
    await security_center._governance(db, now=now)
    await security_center._governance(db, now=now)
    assert _report_scans(db) == 1, "同月内应命中缓存，不重复聚合"
    security_center.clear_governance_cache()


@pytest.mark.asyncio
async def test_center_uses_config_overrides_and_real_article_type(monkeypatch: pytest.MonkeyPatch) -> None:
    security_center.clear_governance_cache()

    async def _fake_config(_db: Any, _namespace: str) -> dict[str, Any]:
        # load_config 对非 AsyncSession 直接返回 {}，这里打个桩才测得到配置覆盖。
        return {"security_hero_title": "平台安全", "security_risk_notice": "先核实再行动"}

    monkeypatch.setattr(security_center, "load_config", _fake_config)
    db = _FakeDb(report_row={"total_processed": 1, "content_actions": 0, "dismissed": 1})
    response = await security_center.get_security_center(db)
    assert isinstance(response, SecurityCenterResponse)
    assert response.hero.title == "平台安全"
    assert response.risk_notice == "先核实再行动"
    assert response.hero.subtitle  # 未配置的字段回退到默认常量
    assert response.fraud_articles, "反诈卡片不能为空，否则页面出现空白区"
    # 卡片 type 必须能被 /public/articles/{type} 解析，否则点开 404
    from app.services.public_articles import ARTICLE_BY_TYPE

    for article in response.fraud_articles:
        assert article.type in ARTICLE_BY_TYPE
    security_center.clear_governance_cache()


class _ReviewDb:
    """举报审核用：第一次查回举报行，其余写语句返回 rowcount/lastrowid。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._first = True

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        self.calls.append(sql)
        if self._first:
            self._first = False
            return _Result(
                {
                    "id": 4,
                    "user_id": 7,
                    "target_user_id": 5,
                    "target_type": "user",
                    "target_id": None,
                    "status": 0,
                }
            )
        return _Result(None)

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_upholding_user_report_records_permanent_ban(monkeypatch: pytest.MonkeyPatch) -> None:
    """status=1 + action=none 的用户举报会直接封号，必须同时落 user_restriction。"""
    from app.schemas.admin import ReportReviewRequest
    from app.services import social

    created: list[tuple[int, str, Any, int]] = []

    async def _fake_create_restriction(
        db: Any, user_id: int, request: Any, actor_id: int, *, commit: bool = True
    ) -> Any:
        created.append((user_id, request.restriction_type, request.ends_at, actor_id))
        return object()

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(social, "create_restriction", _fake_create_restriction)
    monkeypatch.setattr(social, "emit_notification", _noop)

    db = _ReviewDb()
    await social.review_report(
        db,
        4,
        ReportReviewRequest(status=1, result="举报成立，封禁账号", action="none"),
        actor_id=9,
    )

    assert created == [(5, "TOTAL_BAN", None, 9)], "封号必须记为永久封禁（ends_at 为空）"
    joined = " | ".join(db.calls)
    assert "UPDATE users SET status=2" in joined
    assert "UPDATE user_report" in joined
