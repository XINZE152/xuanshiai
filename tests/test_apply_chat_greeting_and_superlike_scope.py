"""需求「申请通过后要显示聊天」与「爆灯统计口径」的行为测试。

覆盖两个本轮修复点：
1. ``respond_application(accepted=True)``：会话已在时必须复位 ``is_user*_hidden``；
   问候消息按 ``client_message_id`` 幂等键去重（不再依赖 content 全等）。
2. ``user_boost`` 复用承载「置顶助推」（``user_id = target_user_id = 自己``），
   所有「爆灯」统计/筛选都必须排除自指记录，否则自己买的置顶会被当成一次爆灯。
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.services import discovery

ROOT = Path(__file__).resolve().parents[1]
CREATED_AT = datetime(2026, 10, 26, 12, 0, 0)


class _Result:
    """最小 SQLAlchemy Result 替身：只提供被测代码用到的读取入口。"""

    def __init__(self, *, one: dict[str, Any] | None = None, scalar: Any = None, lastrowid: int = 0) -> None:
        self._one = one
        self._scalar = scalar
        self.lastrowid = lastrowid

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._one

    def one(self) -> dict[str, Any]:
        assert self._one is not None
        return self._one

    def scalar(self) -> Any:
        return self._scalar


class _FakeDb:
    """按 SQL 片段分流的假会话，记录执行过的语句与参数。"""

    def __init__(self, *, existing_session_id: int | None = None, greeting_exists: bool = False) -> None:
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []
        self.existing_session_id = existing_session_id
        self.greeting_exists = greeting_exists

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        self.params.append(dict(params or {}))
        if "FROM match_apply" in sql and "FOR UPDATE" in sql:
            return _Result(one={
                "id": 7,
                "from_user_id": 2,
                "to_user_id": 1,
                "message": None,
                "status": 0,
                "expire_at": None,
                "created_at": CREATED_AT,
            })
        if "FROM match_apply" in sql:
            return _Result(one={
                "id": 7,
                "from_user_id": 2,
                "to_user_id": 1,
                "message": None,
                "status": 1,
                "expire_at": None,
                "created_at": CREATED_AT,
            })
        if "FROM chat_session WHERE user1_id" in sql:
            return _Result(scalar=self.existing_session_id)
        if "FROM chat_message" in sql:
            return _Result(scalar=1 if self.greeting_exists else None)
        if sql.strip().startswith("INSERT INTO chat_session"):
            return _Result(lastrowid=99)
        return _Result()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    # —— 断言辅助 ——
    def find(self, *markers: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (sql, params)
            for sql, params in zip(self.statements, self.params)
            if all(marker in sql for marker in markers)
        ]


@pytest.fixture(autouse=True)
def _silence_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allow_target(_: Any, __: int, ___: int) -> None:
        return None

    async def no_notification(*_: Any, **__: Any) -> None:
        return None

    async def runtime_default(_: Any, __: str, ___: str, fallback: Any) -> Any:
        return fallback

    async def realname_not_required(_: Any) -> dict[str, Any]:
        # 2026-10-27：respond_application 新增接收方实名闸口，走 match_line_permissions 开关。
        # 这些用例只验证会话/问候/隐藏位，故放开开关，避免假 DB 撞上 user_auth 查询。
        return {
            "realname_allowed": True,
            "be_linked_allowed": None,
            "apply_daily": None,
            "active": True,
        }

    monkeypatch.setattr(discovery, "_ensure_target", allow_target)
    monkeypatch.setattr(discovery, "_notify", no_notification)
    monkeypatch.setattr(discovery, "get_runtime_value", runtime_default)
    monkeypatch.setattr(discovery, "match_line_permissions", realname_not_required)


@pytest.mark.asyncio
async def test_accept_resets_hidden_flags_on_existing_session() -> None:
    db = _FakeDb(existing_session_id=42)

    await discovery.respond_application(db, 1, 7, accepted=True)

    resets = db.find("UPDATE chat_session", "is_user1_hidden = 0")
    assert resets, "复用时必须复位双方隐藏标记，否则用户看不到会话"
    assert resets[0][1] == {"session_id": 42}
    # 会话已存在时不应重复插入
    assert not db.find("INSERT INTO chat_session")


@pytest.mark.asyncio
async def test_accept_creates_session_and_idempotent_greeting() -> None:
    db = _FakeDb(existing_session_id=None)

    await discovery.respond_application(db, 1, 7, accepted=True)

    assert db.find("INSERT INTO chat_session"), "没有会话时应创建"
    inserts = db.find("INSERT INTO chat_message")
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert "client_message_id" in sql
    assert params["client_message_id"] == "accept-greeting:7"
    assert params["greeting"] == discovery.DEFAULT_AUTO_GREETING
    assert params["from_user_id"] == 1, "系统问候仍以受理方身份落库（type=6 由前端居中渲染）"
    assert params["to_user_id"] == 2


@pytest.mark.asyncio
async def test_accept_does_not_duplicate_greeting_for_same_application() -> None:
    db = _FakeDb(existing_session_id=42, greeting_exists=True)

    await discovery.respond_application(db, 1, 7, accepted=True)

    assert not db.find("INSERT INTO chat_message")
    # 没有新问候就不该动 last_message
    assert not db.find("UPDATE chat_session", "last_message = :greeting")


@pytest.mark.asyncio
async def test_greeting_dedup_uses_idempotency_key_not_content() -> None:
    db = _FakeDb(existing_session_id=42)

    await discovery.respond_application(db, 1, 7, accepted=True)

    lookups = db.find("FROM chat_message")
    assert lookups
    sql = lookups[0][0]
    assert "client_message_id" in sql
    assert "content = :greeting" not in sql, "按 content 去重在改文案后会插入第二条"


@pytest.mark.asyncio
async def test_auto_greeting_honors_admin_override_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def override(_: Any, __: str, ___: str, ____: Any) -> str:
        return "  你好呀，我们可以开始聊天啦～  "

    monkeypatch.setattr(discovery, "get_runtime_value", override)
    assert await discovery._auto_greeting(object()) == "你好呀，我们可以开始聊天啦～"

    async def blank(_: Any, __: str, ___: str, ____: Any) -> str:
        return "   "

    monkeypatch.setattr(discovery, "get_runtime_value", blank)
    assert await discovery._auto_greeting(object()) == discovery.DEFAULT_AUTO_GREETING


def test_superlike_scope_excludes_self_boost_everywhere() -> None:
    """user_boost 复用「置顶」后，任何爆灯口径都必须排除 user_id = target_user_id。"""
    targets = {
        "app/services/discovery.py": ["b.user_id <> b.target_user_id"],
        "app/services/profile.py": ["b.user_id <> b.target_user_id"],
        "app/services/member_behavior_admin.py": ["b.user_id <> b.target_user_id"],
        "app/api/routes/member_follow_up_admin.py": ["b.user_id <> b.target_user_id"],
        "app/services/member_crm_filter.py": ["bo.user_id <> bo.target_user_id"],
    }
    for relative, markers in targets.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source, f"{relative} 缺少自置顶排除条件：{marker}"

    crm = (ROOT / "app/services/member_crm_filter.py").read_text(encoding="utf-8")
    assert crm.count("bo.user_id <> bo.target_user_id") == 2, "boosted / boosted_by 两个方向都要排除"
