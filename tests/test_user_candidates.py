"""单元测试：后台统一的「搜索可绑定用户」能力（app/services/user_candidates.py）。

覆盖点：
- `_normalize_candidate`：三种 scope 下的「可用 / 不可用 + 原因」判定；
- `search_user_candidates`：空关键字短路、limit 夹取、纯数字关键字追加 uid 条件、
  同时按昵称/手机号/实名检索；
- `resolve_user_id`：精确命中、userid 兜底、模糊唯一命中、模糊多命中 422、查不到 404。
"""

import pytest
from fastapi import HTTPException

from app.services.user_candidates import (
    _normalize_candidate,
    resolve_user_id,
    search_user_candidates,
)

BASE_ROW = {
    "id": 7,
    "nickname": "张三",
    "real_name": "张真人",
    "phone": "13800000000",
    "avatar": None,
    "wechat_bound": 1,
    "is_service_matchmaker": 0,
    "is_promoter": 0,
    "has_team": 0,
}


# ── 极简假 Session：只满足本模块用到的三种取值方式 ────────────────
class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Mappings(self._rows)

    def scalars(self):
        return _Scalars([r["id"] if isinstance(r, dict) else r for r in self._rows])

    def scalar(self):
        if not self._rows:
            return None
        first = self._rows[0]
        return first["id"] if isinstance(first, dict) else first


class _FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.sql: list[str] = []
        self.params: list[dict] = []

    async def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        self.params.append(dict(params or {}))
        return _Result(self.results.pop(0) if self.results else [])


# ── 候选行 decoration ─────────────────────────────────────────
def test_normalize_available_user() -> None:
    item = _normalize_candidate(dict(BASE_ROW), "promoter")
    assert item["unavailable"] is False
    assert item["unavailable_reason"] is None
    assert item["wechat_bound"] is True
    assert item["is_service_matchmaker"] is False


def test_normalize_existing_service_matchmaker() -> None:
    row = {**BASE_ROW, "is_service_matchmaker": 1}
    assert _normalize_candidate(row, "service_matchmaker")["unavailable_reason"] == "该用户已是服务红娘"
    assert _normalize_candidate(row, "partner")["unavailable_reason"] == "该用户已是服务红娘"


def test_normalize_existing_promoter_for_promoter_scope() -> None:
    row = {**BASE_ROW, "is_promoter": 1}
    item = _normalize_candidate(row, "promoter")
    assert item["unavailable"] is True
    assert item["unavailable_reason"] == "该用户已是推广红娘"
    # 合伙人允许兼任推广红娘，因此 partner scope 下仍可选
    assert _normalize_candidate(row, "partner")["unavailable"] is False


def test_normalize_partner_with_team() -> None:
    row = {**BASE_ROW, "has_team": 1}
    item = _normalize_candidate(row, "partner")
    assert item["unavailable"] is True
    assert item["unavailable_reason"] == "该用户已有合伙团队"


# ── search_user_candidates ────────────────────────────────────
@pytest.mark.asyncio
async def test_search_blank_keyword_short_circuit() -> None:
    db = _FakeSession()
    assert await search_user_candidates(db, "   ", "promoter") == []
    assert db.sql == []


@pytest.mark.asyncio
async def test_search_supports_all_lookup_channels() -> None:
    db = _FakeSession([dict(BASE_ROW)])
    result = await search_user_candidates(db, "张三", "promoter")
    assert len(result) == 1
    assert result[0]["nickname"] == "张三"
    sql = db.sql[0]
    assert "u.nickname LIKE" in sql
    assert "u.phone LIKE" in sql
    assert "auth.real_name LIKE" in sql
    assert "OR u.id = :uid" not in sql  # 非纯数字不拼 ID 条件


@pytest.mark.asyncio
async def test_search_appends_user_id_for_numeric_keyword() -> None:
    db = _FakeSession([dict(BASE_ROW)])
    await search_user_candidates(db, "138", "promoter")
    assert "OR u.id = :uid" in db.sql[0]
    assert db.params[0]["uid"] == 138


@pytest.mark.asyncio
async def test_search_limit_is_clamped() -> None:
    db = _FakeSession([])
    await search_user_candidates(db, "张三", "promoter", limit=999)
    assert db.params[0]["limit"] == 50
    await search_user_candidates(db, "张三", "promoter", limit=0)
    assert db.params[1]["limit"] == 1


# ── resolve_user_id ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_resolve_requires_value() -> None:
    with pytest.raises(HTTPException) as exc:
        await resolve_user_id(_FakeSession(), "  ")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_resolve_exact_match_wins() -> None:
    db = _FakeSession([{"id": 7}])
    assert await resolve_user_id(db, "张三", "nickname") == 7
    assert db.sql[0].count("SELECT") == 1


@pytest.mark.asyncio
async def test_resolve_numeric_input_falls_back_to_user_id() -> None:
    db = _FakeSession([], [{"id": 9}])
    assert await resolve_user_id(db, "10086", "nickname") == 9
    assert db.params[1]["uid"] == 10086


@pytest.mark.asyncio
async def test_resolve_single_fuzzy_match_is_accepted() -> None:
    db = _FakeSession([], [{"id": 3}])
    assert await resolve_user_id(db, "张三", "nickname") == 3


@pytest.mark.asyncio
async def test_resolve_multiple_fuzzy_matches_are_rejected() -> None:
    db = _FakeSession([], [{"id": 3}, {"id": 4}])
    with pytest.raises(HTTPException) as exc:
        await resolve_user_id(db, "张", "nickname")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_resolve_not_found_is_404() -> None:
    db = _FakeSession([], [])
    with pytest.raises(HTTPException) as exc:
        await resolve_user_id(db, "不存在的人", "nickname")
    assert exc.value.status_code == 404
