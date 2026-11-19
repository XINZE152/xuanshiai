"""红娘工作台会员卡片数据来源回归。

修复点：卡片此前只读 user_auth.education / user_auth.job 和 user_profile 的旧文本列
hometown / residence。会员在小程序「我的-资料填写」里补的职业、家乡、居住地只写
user_profile.occupation 和省市区编码，user_auth 一张表全是空的，导致待审列表里
会员明明填了资料，红娘看到的卡片职业和地区全是空白。
钉住新契约：
1. 职业优先会员自填的 user_profile.occupation，为空才回落到红娘录入/认证用的 user_auth.job；
2. 家乡/居住地在旧文本列为空时按省市区编码现拼（与会员本人资料卡同一套 region_display）；
3. 旧文本列有值时保持原样，红娘录入的会员不受影响；
4. 编码列只做内部计算，不出现在接口返回里；
5. 学历按 user_profile.education_level 的存储刻度（1=高中及以下…5=博士，与档案编辑页
   educationOptions 下标、发现页筛选字典同一套）翻译，学历认证通过后改用认证结论。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.schemas.matchmaker_workspace import WorkspaceMember
from app.services.matchmaker_workspace import WorkspaceActor, _workspace_member_row, list_workspace_members

ACTOR = WorkspaceActor(user_id=2, display_name="红娘测试账号", level="SUPER", organization_id=1)


class StubResult:
    def __init__(self, rows: list[dict], scalar_value: int = 0) -> None:
        self._rows = rows
        self._scalar = scalar_value

    def mappings(self) -> "StubResult":
        return self

    def all(self) -> list[dict]:
        return self._rows

    def scalar(self) -> int:
        return self._scalar


class StubDB:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        self.statements.append(str(sql))
        return StubResult(self.rows, scalar_value=len(self.rows))

    async def scalar(self, sql: object, params: dict | None = None) -> int:
        self.statements.append(str(sql))
        return len(self.rows)


def _row(**overrides: object) -> dict:
    row: dict = {
        "user_id": 33, "nickname": "林晚意（示例）", "avatar": None, "gender": 2, "birthday": None,
        "hometown": None, "residence": None,
        "hometown_province_code": "330000", "hometown_city_code": "330100", "hometown_district_code": None,
        "residence_province_code": "110000", "residence_city_code": "110100", "residence_district_code": None,
        "education_level": 3, "auth_education": None, "auth_education_verified": 0,
        "job": "儿科医生", "is_married": 1, "height": 168, "realname_status": 0,
        "matchmaker_id": 2, "matchmaker_name": "红娘测试账号", "profile_completion_score": 71.0,
        "review_status": "PENDING", "review_reason": None, "reviewed_at": None, "next_follow_at": None,
        "created_at": datetime(2026, 10, 19, 18, 6, 53),
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_member_list_reads_self_filled_occupation_and_region_codes() -> None:
    db = StubDB([_row()])
    page = await list_workspace_members(db, ACTOR, 1, 20, None, None)
    select_sql = db.statements[0]
    assert "COALESCE(NULLIF(p.occupation, ''), ua.job) AS job" in select_sql
    member = page.items[0]
    assert member.job == "儿科医生"
    assert member.hometown and "杭" in member.hometown
    assert member.residence and "北京" in member.residence


@pytest.mark.asyncio
async def test_occupation_falls_back_to_the_matchmaker_recorded_job() -> None:
    db = StubDB([_row(job=None)])
    page = await list_workspace_members(db, ACTOR, 1, 20, None, None)
    assert page.items[0].job is None
    db = StubDB([_row(job="产品经理")])
    page = await list_workspace_members(db, ACTOR, 1, 20, None, None)
    assert page.items[0].job == "产品经理"


@pytest.mark.asyncio
async def test_legacy_text_region_is_kept_as_is() -> None:
    db = StubDB([_row(hometown="江苏省苏州市", residence="江苏省南京市")])
    page = await list_workspace_members(db, ACTOR, 1, 20, None, None)
    assert page.items[0].hometown == "江苏省苏州市"
    assert page.items[0].residence == "江苏省南京市"


@pytest.mark.asyncio
async def test_region_code_columns_stay_internal() -> None:
    row = _workspace_member_row(_row())
    assert not any(key.endswith("_code") for key in row)
    assert set(row) <= set(WorkspaceMember.model_fields)


@pytest.mark.asyncio
async def test_education_uses_the_member_level_dictionary() -> None:
    db = StubDB([_row()])
    page = await list_workspace_members(db, ACTOR, 1, 20, None, None)
    select_sql = db.statements[0]
    assert "p.education_level" in select_sql
    assert "COALESCE(ua.education_verified, 0) AS auth_education_verified" in select_sql
    assert page.items[0].education == "本科"
    # 存储刻度逐个档位钉死：AI 画像那套 1-6（1=初中及以下…6=博士）不得混进来，
    # 否则同一个 education_level=4 在卡片上会从「硕士」错显示成「本科」。
    assert [
        _workspace_member_row(_row(education_level=level))["education"]
        for level in (1, 2, 3, 4, 5)
    ] == ["高中及以下", "大专", "本科", "硕士", "博士"]


@pytest.mark.asyncio
async def test_verified_education_overrides_the_self_filled_level() -> None:
    """学历认证通过后，认证结论就是核验过的事实，覆盖会员自填档位。"""
    db = StubDB([_row(education_level=6, auth_education="本科", auth_education_verified=1)])
    page = await list_workspace_members(db, ACTOR, 1, 20, None, None)
    assert page.items[0].education == "本科"


@pytest.mark.asyncio
async def test_unverified_matchmaker_recorded_education_is_still_shown() -> None:
    """红娘代录的会员只有 user_auth.education 文本，没有自填档位。"""
    db = StubDB([_row(education_level=None, auth_education="大专")])
    page = await list_workspace_members(db, ACTOR, 1, 20, None, None)
    assert page.items[0].education == "大专"


@pytest.mark.asyncio
async def test_unknown_education_level_is_not_guessed() -> None:
    """字典外的档位（后台资料编辑可写到 7/8）不猜文案，避免显示错误学历。"""
    assert _workspace_member_row(_row(education_level=8))["education"] is None
    assert _workspace_member_row(_row(education_level=8, auth_education="硕士"))["education"] == "硕士"
    assert _workspace_member_row(_row(education_level=None, auth_education=None))["education"] is None
