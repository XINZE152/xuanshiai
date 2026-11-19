"""红娘主页编辑的落库与校验契约（2026-10-23 定）。

口径：
- 主页六项字段落在入驻申请表：昵称写 users.nickname，签名/介绍写 user_matchmaker_apply.slogan/intro，
  擅长领域与成功案例写 application_details；不新增表或列。
- application_details 同时承载审核资料，保存主页只覆盖 specialties/success_cases，
  wechat、expected_price 等原样保留。
- 校验沿用入驻口径：擅长领域 ≤5 且不重复，成功案例 ≤3（描述 1-50、图 ≤3），介绍 10-2000，签名 ≤64。
  50 字是 2026-10-28 收敛的保存口径；读取模型仍放到 1000，改口径之前入库的长案例要能读出来。
- 后台红娘编辑改 description/slogan 时同步写申请表，公开主页与后台不再两套口径。
全部用 StubDB 按 SQL 文本分派，不依赖 MySQL。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.matchmaker_staff_admin import MatchmakerStaffUpdate
from app.schemas.matchmaker_workspace import (
    MatchmakerHomepageCase,
    MatchmakerHomepageCaseInput,
    MatchmakerHomepageUpdate,
)
from app.services.matchmaker import MatchmakerCard, _card
from app.services.matchmaker_staff_admin import update_staff
from app.services.matchmaker_workspace import (
    WorkspaceActor,
    get_workspace_homepage,
    update_workspace_homepage,
)

MATCHMAKER_ID = 21
APPLIED = {
    "application_id": 1,
    "user_id": MATCHMAKER_ID,
    "nickname": "钱多多（示例）",
    "avatar": "/static/avatars/matchmaking-anime-058.jpg",
    "slogan": "认真牵线",
    "intro": "示例测试账号，用于验证入驻审核链路是否真正可用。",
    "application_details": json.dumps(
        {
            "wechat": "demo_wx_qdd",
            "expected_price": 1999.0,
            "specialties": ["情感咨询", "沟通协调"],
            "success_cases": [{"description": "帮助多对会员建立稳定联系", "images": []}],
        },
        ensure_ascii=False,
    ),
}


class StubResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "StubResult":
        return self

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def scalar(self) -> object:
        if not self._rows:
            return None
        return next(iter(self._rows[0].values()))


class StubDB:
    def __init__(self, apply_row: dict[str, object] | None = APPLIED, **staff_row: object) -> None:
        self.apply_row = apply_row
        self.staff_row = {"id": MATCHMAKER_ID, "nickname": "钱多多（示例）", **staff_row}
        self.writes: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        statement = str(sql)
        if statement.startswith("UPDATE") or statement.startswith("INSERT"):
            self.writes.append((statement, dict(params or {})))
            return StubResult()
        if "FROM user_matchmaker_apply app" in statement or "SELECT app.id AS application_id" in statement:
            return StubResult([] if self.apply_row is None else [dict(self.apply_row)])
        if "FROM users u JOIN user_matchmaker_apply ma" in statement or "SELECT u.id, u.avatar" in statement:
            return StubResult([self.staff_row])
        if "information_schema.TABLES" in statement:
            # 上游起 get_staff 会先探测 matchmaker_menu_permission_state 是否存在（旧库降级），
            # 桩按「不存在」回空行，不影响本用例断言的同步写入。
            return StubResult()
        raise AssertionError(f"未预期的语句：{statement}")

    async def scalar(self, sql: object, params: dict | None = None) -> object:
        raise AssertionError(f"未预期的读查询：{sql}")

    async def commit(self) -> None:
        self.commits += 1

    def write_for(self, marker: str) -> tuple[str, dict]:
        for statement, params in self.writes:
            if marker in statement:
                return statement, params
        raise AssertionError(f"没有命中写入：{marker}")


def actor() -> WorkspaceActor:
    return WorkspaceActor(
        user_id=MATCHMAKER_ID, display_name="钱多多", level="NORMAL", organization_id=None
    )


def apply_details(db: StubDB) -> dict[str, object]:
    _, params = db.write_for("SET application_details")
    return json.loads(params["value"])


@pytest.mark.asyncio
async def test_homepage_reads_the_application_row() -> None:
    db = StubDB()
    page = await get_workspace_homepage(db, actor())
    assert page.user_id == MATCHMAKER_ID
    assert page.nickname == "钱多多（示例）"
    assert page.slogan == "认真牵线"
    assert page.introduction.startswith("示例测试账号")
    assert page.specialties == ["情感咨询", "沟通协调"]
    assert [case.description for case in page.success_cases] == ["帮助多对会员建立稳定联系"]


@pytest.mark.asyncio
async def test_homepage_without_application_row_is_not_editable() -> None:
    with pytest.raises(HTTPException) as error:
        await get_workspace_homepage(StubDB(apply_row=None), actor())
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_blank_slogan_clears_it_instead_of_sending_empty_text() -> None:
    db = StubDB()
    await update_workspace_homepage(db, actor(), MatchmakerHomepageUpdate(slogan="   "))
    _, params = db.write_for("UPDATE user_matchmaker_apply SET slogan")
    assert params == {"value": None, "id": 1}
    assert not any("UPDATE users" in text for text, _ in db.writes)


@pytest.mark.asyncio
async def test_nickname_is_written_to_users_and_audited() -> None:
    db = StubDB()
    await update_workspace_homepage(db, actor(), MatchmakerHomepageUpdate(nickname="  钱多多  "))
    assert db.write_for("UPDATE users SET nickname")[1] == {"value": "钱多多", "id": MATCHMAKER_ID}
    assert db.write_for("UPDATE matchmaker_workspace_profile SET display_name")[1] == {"value": "钱多多", "id": MATCHMAKER_ID}
    assert db.write_for("INTO business_audit_log")
    assert db.commits == 1


@pytest.mark.asyncio
async def test_homepage_save_keeps_review_only_fields_in_application_details() -> None:
    db = StubDB()
    await update_workspace_homepage(
        db,
        actor(),
        MatchmakerHomepageUpdate(
            specialties=["高知青年", "关系澄清"],
            success_cases=[
                MatchmakerHomepageCaseInput(description="  节奏复盘后确认长期方向  ", images=[" /static/a.webp ", ""])
            ],
        ),
    )
    stored = apply_details(db)
    assert stored["wechat"] == "demo_wx_qdd"
    assert stored["expected_price"] == 1999.0
    assert stored["specialties"] == ["高知青年", "关系澄清"]
    assert stored["success_cases"] == [
        {"description": "节奏复盘后确认长期方向", "images": ["/static/a.webp"]}
    ]


@pytest.mark.asyncio
async def test_empty_specialties_clears_the_tags() -> None:
    db = StubDB()
    await update_workspace_homepage(db, actor(), MatchmakerHomepageUpdate(specialties=[]))
    assert apply_details(db)["specialties"] == []


def test_homepage_validation_follows_the_application_rules() -> None:
    with pytest.raises(ValidationError):
        MatchmakerHomepageUpdate(specialties=["A", "A"])
    with pytest.raises(ValidationError):
        MatchmakerHomepageUpdate(specialties=[f"S{i}" for i in range(6)])
    with pytest.raises(ValidationError):
        MatchmakerHomepageUpdate(introduction="太短了")
    with pytest.raises(ValidationError):
        MatchmakerHomepageUpdate(introduction="   ")
    with pytest.raises(ValidationError):
        MatchmakerHomepageUpdate(
            success_cases=[MatchmakerHomepageCaseInput(description="案例") for _ in range(4)]
        )
    with pytest.raises(ValidationError):
        MatchmakerHomepageUpdate(
            success_cases=[MatchmakerHomepageCaseInput(description="案例", images=["1", "2", "3", "4"])]
        )
    with pytest.raises(ValidationError):
        MatchmakerHomepageUpdate(success_cases=[MatchmakerHomepageCaseInput(description="案" * 51)])
    assert MatchmakerHomepageUpdate(
        success_cases=[MatchmakerHomepageCaseInput(description="案" * 50)]
    ).success_cases is not None
    # 读取模型保持宽松：改成 50 字之前入库的长案例仍要能读出来，不能因为收敛口径把主页读成 500。
    assert MatchmakerHomepageCase(description="案" * 800).description
    with pytest.raises(ValidationError):
        MatchmakerHomepageUpdate(slogan="签名超过六十四字" + "一" * 60)
    assert MatchmakerHomepageUpdate(slogan="认真牵线，对每一次遇见负责").slogan == "认真牵线，对每一次遇见负责"


def test_public_card_exposes_homepage_fields() -> None:
    card: MatchmakerCard = _card(
        {
            **APPLIED,
            "cert_images": "[]",
            "success_count": 3,
            "rating_score": 4.5,
            "rating_count": 2,
            "role_status": 1,
        }
    )
    assert card.slogan == "认真牵线"
    assert card.specialties == ["情感咨询", "沟通协调"]
    assert [case.description for case in card.cases] == ["帮助多对会员建立稳定联系"]


def test_public_card_tolerates_missing_details() -> None:
    card = _card(
        {
            "application_id": 1,
            "user_id": MATCHMAKER_ID,
            "nickname": None,
            "avatar": None,
            "slogan": None,
            "intro": None,
            "application_details": None,
            "cert_images": "[]",
            "success_count": None,
            "rating_score": None,
            "rating_count": None,
            "role_status": 1,
        }
    )
    assert card.slogan is None
    assert card.specialties == []
    assert card.cases == []


@pytest.mark.asyncio
async def test_admin_staff_edit_syncs_the_application_columns() -> None:
    db = StubDB(apply_row=None, description="新的个人介绍", slogan="新的签名")
    await update_staff(db, MATCHMAKER_ID, MatchmakerStaffUpdate(description="新的个人介绍", slogan="新的签名"))
    statement, params = db.write_for("UPDATE user_matchmaker_apply")
    assert "intro = :intro" in statement and "slogan = :slogan" in statement
    assert params == {"intro": "新的个人介绍", "slogan": "新的签名", "id": MATCHMAKER_ID}


@pytest.mark.asyncio
async def test_admin_staff_edit_without_homepage_fields_leaves_apply_alone() -> None:
    db = StubDB()
    await update_staff(db, MATCHMAKER_ID, MatchmakerStaffUpdate(sort=3))
    assert not any("user_matchmaker_apply" in statement for statement, _ in db.writes)
