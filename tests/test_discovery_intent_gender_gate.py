"""注册身份决定推荐性别口径（RECO-MEETING-INTENT-GENDER-20261114）。

规则：
- 找对象（self_match 自己找 / parent_match 父母帮找）→ 只推异性；
- 找搭子（companion）→ 同性异性都推；
- 未选择身份的存量用户 → 按找对象处理，保持改动前行为；
- 用户显式筛了性别（filters.gender）时以筛选为准，否则「找对象 + 筛同性」恒空。

这些用例全部走纯函数 `_candidate_snapshot` / `_only_opposite_gender`，
不依赖数据库，可在 unit job 里稳定执行。
"""

from typing import Any

import pytest

from app.schemas.discovery import DiscoveryFilters
from app.services import discovery

BASE_VIEWER: dict[str, Any] = {
    "gender": 1,
    "birthday": None,
    "realname_status": 2,
    "completion_score": 100,
    "completion_grandfathered": 0,
    "phone": "13800138000",
}


def _snapshot(viewer: dict[str, Any], filters: DiscoveryFilters | None = None):
    return discovery._candidate_snapshot(
        viewer_id=1,
        viewer=viewer,
        viewer_is_vip=False,
        filters=filters or DiscoveryFilters(),
        plaza=False,
        nickname=None,
        tag=None,
        respect_preferences=False,
    )


# ── 1. 身份 → 是否只推异性 ────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("companion", False),
        ("self_match", True),
        ("parent_match", True),
        (None, True),
        ("", True),
        ("  companion  ", False),
        ("unknown_intent", True),
    ],
)
def test_only_opposite_gender_by_intent(intent: str | None, expected: bool) -> None:
    assert discovery._only_opposite_gender({"intent_type": intent}) is expected


def test_only_opposite_gender_tolerates_missing_key() -> None:
    """老调用方构造的 viewer 没有 intent_type 键时不得 KeyError。"""
    assert discovery._only_opposite_gender({"gender": 1}) is True


# ── 2. SQL 门禁：找对象拦住同性，找搭子放行 ──────────────────────────────
def test_self_match_only_recommends_opposite_gender() -> None:
    snapshot = _snapshot({**BASE_VIEWER, "intent_type": "self_match"})
    assert "u.gender <> :opposite_gender" in snapshot.where_sql
    assert snapshot.params["opposite_gender"] == 1


def test_parent_match_only_recommends_opposite_gender() -> None:
    snapshot = _snapshot({**BASE_VIEWER, "intent_type": "parent_match"})
    assert "u.gender <> :opposite_gender" in snapshot.where_sql


def test_legacy_user_without_intent_keeps_opposite_only() -> None:
    """存量用户没有身份记录，必须与改动前一致（只推异性）。"""
    snapshot = _snapshot(dict(BASE_VIEWER))
    assert "u.gender <> :opposite_gender" in snapshot.where_sql
    assert snapshot.params["opposite_gender"] == 1


def test_companion_intent_recommends_both_genders() -> None:
    snapshot = _snapshot({**BASE_VIEWER, "intent_type": "companion"})
    assert "u.gender <> :opposite_gender" not in snapshot.where_sql
    assert "opposite_gender" not in snapshot.params


# ── 3. 显式性别筛选优先于身份口径 ────────────────────────────────────────
def test_explicit_gender_filter_overrides_intent_gate() -> None:
    """找对象身份显式筛同性时不能再叠异性条件，否则结果恒空。"""
    snapshot = _snapshot(
        {**BASE_VIEWER, "intent_type": "self_match"},
        DiscoveryFilters(gender=1),
    )
    assert "u.gender = :filter_gender" in snapshot.where_sql
    assert "u.gender <> :opposite_gender" not in snapshot.where_sql
    assert snapshot.params["filter_gender"] == 1


# ── 4. 测试开关仍然优先 ──────────────────────────────────────────────────
def test_skip_gates_disables_intent_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_skip_discovery_gates", lambda: True)
    snapshot = _snapshot({**BASE_VIEWER, "intent_type": "self_match"})
    assert "u.gender <> :opposite_gender" not in snapshot.where_sql


# ── 5. 游标指纹跟随实际生效的门禁 ────────────────────────────────────────
def test_intent_switch_changes_query_fingerprint() -> None:
    """身份从找对象切到找搭子，候选集变了，旧 cursor 必须失效。"""
    dating = _snapshot({**BASE_VIEWER, "intent_type": "self_match"})
    buddy = _snapshot({**BASE_VIEWER, "intent_type": "companion"})
    assert dating.query_fingerprint != buddy.query_fingerprint


def test_same_intent_keeps_query_fingerprint_stable() -> None:
    first = _snapshot({**BASE_VIEWER, "intent_type": "companion"})
    second = _snapshot({**BASE_VIEWER, "intent_type": "companion"})
    assert first.query_fingerprint == second.query_fingerprint


# ── 6. viewer 上下文必须带出注册身份 ─────────────────────────────────────
def test_viewer_context_selects_registration_intent() -> None:
    import inspect

    source = inspect.getsource(discovery._viewer_context)
    assert "user_registration_intent" in source
    assert "ri.intent_type" in source
    # 只认当前有效那条，撤销记录不得影响推荐口径
    assert "ri.status = 1" in source
