"""红娘「分成级别」页面数据契约（COMMISSION-LEVEL-20261110 新增）。

口径：级别总览只回页面版式需要的字段（名称 / 升级条件 / 平台额外奖励），
不把 rate_percent 等内部计提配置暴露给小程序；本人当前级别取
`matchmaker_profile.commission_level_id`，后台未指派时返回 null，前端只能显示占位；
按项目分成取 `commission_rule` 中 service_matchmaker 的生效最新版本。用 StubDB，不依赖 MySQL。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.matchmaker_workspace import WorkspaceActor, get_workspace_commission_standards


class StubResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> "StubResult":
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class StubDB:
    def __init__(
        self,
        *,
        levels: list[dict[str, object]],
        profile: list[dict[str, object]],
        events: list[dict[str, object]],
    ) -> None:
        self.levels = levels
        self.profile = profile
        self.events = events
        self.sql: list[str] = []
        self.params: list[dict[str, object]] = []

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        text = str(sql)
        self.sql.append(text)
        self.params.append(dict(params or {}))
        if "FROM commission_level WHERE status = 1" in text:
            return StubResult(self.levels)
        if "FROM matchmaker_profile profile" in text:
            return StubResult(self.profile)
        if "FROM commission_rule rule" in text:
            return StubResult(self.events)
        raise AssertionError(f"未预期的查询：{text}")


def _actor() -> WorkspaceActor:
    return WorkspaceActor(user_id=21, display_name="钱多多（示例）", level="NORMAL", organization_id=3)


def _level(code: str, name: str, condition: str | None, extra: str) -> dict[str, object]:
    return {
        "code": code,
        "name": name,
        "promotion_condition": condition,
        "platform_extra_amount": Decimal(extra),
    }


LEVELS = [
    _level("junior", "初级分成", "默认", "5.00"),
    _level("intermediate", "中级分成", "牵线成功累计>=10次", "1000.00"),
    _level("senior", "高级分成", "牵线成功累计>=100次", "1000.00"),
    _level("partner", "合伙分成", "牵线成功累计>=300次", "5000.00"),
]


@pytest.mark.asyncio
async def test_commission_standards_read_levels_current_level_and_events() -> None:
    db = StubDB(
        levels=LEVELS,
        profile=[
            {
                "level_id": 2,
                "code": "intermediate",
                "name": "中级分成",
                "promotion_condition": "牵线成功累计>=10次",
                "platform_extra_amount": Decimal("1000.00"),
            }
        ],
        events=[
            {"name": "资料审核费", "mode": "fixed", "fixed_amount": Decimal("2.00"), "rate_percent": None},
            {"name": "季度会员", "mode": "rate", "fixed_amount": None, "rate_percent": Decimal("15.0000")},
        ],
    )
    result = await get_workspace_commission_standards(db, _actor())

    assert [level.name for level in result.levels] == ["初级分成", "中级分成", "高级分成", "合伙分成"]
    assert result.levels[1].promotion_condition == "牵线成功累计>=10次"
    assert result.levels[1].platform_extra_amount == "1000.00"
    assert result.current_level is not None
    assert (result.current_level.code, result.current_level.name) == ("intermediate", "中级分成")
    assert [(event.name, event.mode) for event in result.events] == [
        ("资料审核费", "fixed"),
        ("季度会员", "rate"),
    ]
    assert (result.events[0].amount, result.events[0].rate_percent) == ("2.00", None)
    assert (result.events[1].amount, result.events[1].rate_percent) == (None, "15.00")

    # 级别只回生效行；本人级别按 user_id 取档案；事件只认 service_matchmaker 且同名取最新版本。
    assert "WHERE status = 1 ORDER BY sort, id" in db.sql[0]
    assert "rate_percent" not in db.sql[0]
    assert db.params[1]["user_id"] == 21
    assert "profile.user_id = :user_id AND profile.deleted_at IS NULL" in db.sql[1]
    assert "beneficiary_type = 'service_matchmaker' AND status = 1" in db.sql[2]
    assert "MAX(version)" in db.sql[2]


@pytest.mark.asyncio
async def test_commission_standards_stay_empty_without_assignment_or_rules() -> None:
    db = StubDB(
        levels=LEVELS,
        profile=[{"level_id": None, "code": None, "name": None, "promotion_condition": None, "platform_extra_amount": None}],
        events=[],
    )
    result = await get_workspace_commission_standards(db, _actor())

    # 后台未指派级别、未配置按项目分成：返回 null 与空数组，不能由服务端或前端推断。
    assert result.current_level is None
    assert result.events == []
    assert len(result.levels) == 4


@pytest.mark.asyncio
async def test_commission_standards_treat_disabled_assigned_level_as_unassigned() -> None:
    # 档案指向已停用级别：LEFT JOIN 只认生效行，页面按未指派显示占位，不显示空名称徽标。
    db = StubDB(
        levels=LEVELS[:1],
        profile=[{"level_id": 2, "code": None, "name": None, "promotion_condition": None, "platform_extra_amount": None}],
        events=[],
    )
    result = await get_workspace_commission_standards(db, _actor())

    assert result.current_level is None
    assert "AND level.status = 1" in db.sql[1]
