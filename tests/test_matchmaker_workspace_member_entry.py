"""红娘录入会员必须当场把手机号办成可用账号，并把资料落到会员自助档案的规范列上。

修复点：录入只写了 users/user_profile/审核/归属四张表，既没有 user_role 默认角色，
也从没重算资料完整度，会员用同一个手机号登录后角色查询为空、待审卡片完整度恒为 0；
而学历/职业/地区又只写进 user_auth 和 user_profile 的旧文本列，主页与发现页读的是
user_profile.education_level、occupation 和省市区编码，红娘代录的会员在主页一律显示
「未填写」。钉住新契约：
1. users 行落地后立刻补 'user' 默认角色，与手机号验证码注册同一路径；
2. 资料与审核行写完后当场折算完整度，红娘和后台看到的是真实进度；
3. 不伪造 phone_verified_at，手机号是否已核验仍由会员本人验证码登录决定；
4. 学历翻译成存储档位、职业照抄文本、家乡/现居/户口解析出省市区编码，一并写进
   user_profile，user_auth 里的原文保持不动。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from fastapi import HTTPException

from app.api.dependencies import CurrentUser
from app.schemas.matchmaker_workspace import WorkspaceMemberEntryCreate
from app.services import matchmaker_workspace

ACTOR = CurrentUser(id=2, session_id=9, phone="13900000002", status=1, realname_status=2)
MEMBER_ID = 501

PROFILE_COLUMNS = (
    "height", "residence", "hometown", "household", "tags", "occupation", "education_level",
    "hometown_province_code", "hometown_city_code", "hometown_district_code",
    "residence_province_code", "residence_city_code", "residence_district_code",
    "household_province_code", "household_city_code", "household_district_code",
)


class FakeResult:
    def __init__(self, rows: list[object] | None = None, scalar_value: object = None, lastrowid: int | None = None) -> None:
        self._rows = rows or []
        self._scalar = scalar_value
        self.lastrowid = lastrowid

    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[object]:
        return self._rows

    def first(self) -> object | None:
        return self._rows[0] if self._rows else None

    def one(self) -> object:
        return self._rows[0]

    def scalar(self) -> object:
        return self._scalar


class FakeDB:
    def __init__(self, profile_columns: tuple[str, ...] = PROFILE_COLUMNS) -> None:
        self.statements: list[str] = []
        self.calls: list[tuple[str, dict | None]] = []
        self.profile_columns = profile_columns

    async def execute(self, statement: object, params: dict | None = None) -> FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        self.calls.append((sql, params))
        if "SELECT id FROM users WHERE phone = :phone" in sql:
            return FakeResult()
        if "SELECT COLUMN_NAME FROM information_schema.COLUMNS" in sql:
            return FakeResult(rows=[(column,) for column in self.profile_columns])
        if sql.lstrip().startswith("INSERT INTO users"):
            return FakeResult(lastrowid=MEMBER_ID)
        if "SELECT level, organization_id FROM matchmaker_workspace_profile" in sql:
            return FakeResult(rows=[{"level": "SUPER", "organization_id": 1}])
        if "COALESCE(p.display_name" in sql:
            return FakeResult(rows=[{"display_name": "王红娘", "level": "SUPER", "organization_id": 1}])
        return FakeResult()

    async def scalar(self, statement: object, params: dict | None = None) -> object:
        self.statements.append(str(statement))
        return 1

    async def commit(self) -> None:
        self.statements.append("COMMIT")

    def params_for(self, prefix: str) -> dict:
        for sql, params in self.calls:
            if sql.lstrip().startswith(prefix):
                return params or {}
        raise AssertionError(f"没有执行过 {prefix}")


@pytest.mark.asyncio
async def test_workspace_member_entry_registers_role_and_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_role(_db: object, user_id: int) -> None:
        assert any("INSERT INTO users" in sql for sql in db.statements)
        calls.append(f"role:{user_id}")

    async def fake_completion(_db: object, user_id: int) -> None:
        assert any("matchmaker_member_review" in sql for sql in db.statements)
        calls.append(f"completion:{user_id}")

    db = FakeDB()
    monkeypatch.setattr(matchmaker_workspace, "ensure_default_user_role", fake_role)
    monkeypatch.setattr(matchmaker_workspace, "recalculate_completion", fake_completion)

    result = await matchmaker_workspace.create_workspace_member(
        db,  # type: ignore[arg-type]
        ACTOR,
        WorkspaceMemberEntryCreate(
            phone="13922220009",
            nickname="录入验证会员",
            gender=2,
            height=163,
            residence="浙江 杭州",
            tags="徒步|看展",
        ),
    )

    assert result.member_id == MEMBER_ID
    assert result.review_status == "PENDING"
    assert calls == [f"role:{MEMBER_ID}", f"completion:{MEMBER_ID}"]
    assert not any("phone_verified_at" in sql for sql in db.statements)
    assert any("INSERT INTO resource_assignment" in sql for sql in db.statements)


@pytest.mark.asyncio
async def test_workspace_member_entry_writes_canonical_profile_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主页读的是 user_profile 的规范列，代录资料必须同步落到这些列上。"""

    async def fake_role(_db: object, _user_id: int) -> None:
        return None

    async def fake_completion(_db: object, _user_id: int) -> None:
        return None

    db = FakeDB()
    monkeypatch.setattr(matchmaker_workspace, "ensure_default_user_role", fake_role)
    monkeypatch.setattr(matchmaker_workspace, "recalculate_completion", fake_completion)

    await matchmaker_workspace.create_workspace_member(
        db,  # type: ignore[arg-type]
        ACTOR,
        WorkspaceMemberEntryCreate(
            phone="13922220010",
            nickname="规范列验证会员",
            gender=1,
            education="本科",
            job="儿科医生",
            residence="浙江省 杭州市 西湖区",
            hometown="江苏省 南京市",
            household="火星 不在",
        ),
    )

    profile = db.params_for("INSERT INTO user_profile")
    assert profile["education_level"] == 3
    assert profile["occupation"] == "儿科医生"
    assert profile["residence_province_code"] == "330000"
    assert profile["residence_city_code"] == "330100"
    assert profile["residence_district_code"] == "330106"
    # 只填到两级的地区只落省市编码，解析不出来的那一级留空而不是猜。
    assert profile["hometown_province_code"] == "320000"
    assert profile["hometown_city_code"] == "320100"
    assert "hometown_district_code" not in profile
    assert not any(key.startswith("household_") for key in profile)

    # user_auth 里红娘录入的原文照旧保留，认证与后台仍读那张表。
    auth = db.params_for("INSERT INTO user_auth")
    assert auth["education"] == "本科"
    assert auth["job"] == "儿科医生"


@pytest.mark.asyncio
async def test_workspace_member_entry_reports_missing_profile_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """规范列在当前库里不存在时明确报 503，不静默丢掉学历和地区。"""

    async def fake_role(_db: object, _user_id: int) -> None:
        return None

    db = FakeDB(profile_columns=("height", "residence", "occupation", "education_level"))
    monkeypatch.setattr(matchmaker_workspace, "ensure_default_user_role", fake_role)

    with pytest.raises(HTTPException) as raised:
        await matchmaker_workspace.create_workspace_member(
            db,  # type: ignore[arg-type]
            ACTOR,
            WorkspaceMemberEntryCreate(
                phone="13922220011",
                nickname="缺列会员",
                gender=1,
                residence="浙江省 杭州市",
            ),
        )
    assert raised.value.status_code == 503


def test_member_entry_marriage_write_domain_matches_the_profile_page() -> None:
    """红娘录入的婚况档位必须与会员本人资料编辑页同一套（1 未婚 / 2 离异 / 3 丧偶）。

    库里 4~7 是旧的离异细分档，只读展示仍支持；写入会被 Pydantic 挡成 422，
    否则录入端和会员端各自一套编码，待审卡片与主页又会互相打脸。
    """
    for level in (1, 2, 3):
        body = WorkspaceMemberEntryCreate(phone="13922220099", nickname="婚况档位", gender=1, is_married=level)
        assert body.is_married == level
    assert WorkspaceMemberEntryCreate(phone="13922220099", nickname="婚况档位", gender=1).is_married is None
    for legacy in (0, 4, 5, 6, 7):
        with pytest.raises(ValidationError):
            WorkspaceMemberEntryCreate(phone="13922220099", nickname="婚况档位", gender=1, is_married=legacy)
