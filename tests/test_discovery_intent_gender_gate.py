"""注册身份决定推荐性别口径（RECO-MEETING-INTENT-GENDER-20261114）。

规则：
- 找对象（self_match 自己找 / parent_match 父母帮找）→ 只推异性；
- 找搭子（companion）→ 同性异性都推；
- 未选择身份的存量用户 → 按找对象处理，保持改动前行为；
- 用户显式筛了性别（filters.gender）时以筛选为准，否则「找对象 + 筛同性」恒空；
- 搜索单独放开：search_discovery 传 apply_intent_gender_gate=False，
  推荐流、广场、AI 助理标签推荐仍按身份口径过滤。

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


def _snapshot(
    viewer: dict[str, Any],
    filters: DiscoveryFilters | None = None,
    *,
    apply_intent_gender_gate: bool = True,
):
    return discovery._candidate_snapshot(
        viewer_id=1,
        viewer=viewer,
        viewer_is_vip=False,
        filters=filters or DiscoveryFilters(),
        plaza=False,
        nickname=None,
        tag=None,
        respect_preferences=False,
        apply_intent_gender_gate=apply_intent_gender_gate,
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


# ── 7. 搜索单独放开性别（产品确认 2026-11-14）────────────────────────────
def test_search_flag_disables_gate_for_self_match() -> None:
    """找对象用户在搜索里也要能搜到同性，否则按昵称找人永远搜不到好友。"""
    snapshot = _snapshot(
        {**BASE_VIEWER, "intent_type": "self_match"},
        apply_intent_gender_gate=False,
    )
    assert "u.gender <> :opposite_gender" not in snapshot.where_sql
    assert "opposite_gender" not in snapshot.params


def test_search_flag_keeps_other_filters_intact() -> None:
    """只关性别，其他条件（拉黑、隐私、显式筛选）不变。"""
    snapshot = _snapshot(
        {**BASE_VIEWER, "intent_type": "self_match"},
        DiscoveryFilters(gender=2),
        apply_intent_gender_gate=False,
    )
    assert "u.gender = :filter_gender" in snapshot.where_sql
    assert snapshot.params["filter_gender"] == 2


def test_search_flag_changes_query_fingerprint() -> None:
    """同一用户搜与推是两套候选集，cursor 不能互相续用。"""
    discovery_gated = _snapshot({**BASE_VIEWER, "intent_type": "self_match"})
    search_open = _snapshot(
        {**BASE_VIEWER, "intent_type": "self_match"},
        apply_intent_gender_gate=False,
    )
    assert discovery_gated.query_fingerprint != search_open.query_fingerprint


def test_only_search_disables_the_gate() -> None:
    """守住「单独放开」的边界：只有 search_discovery 关，推荐/广场/AI 助理不关。"""
    import inspect

    from app.services import ai_assistant

    search_source = inspect.getsource(discovery.search_discovery)
    assert "apply_intent_gender_gate=False" in search_source

    # 推荐流与广场走 get_discovery_page，不得关闭该门禁
    assert "apply_intent_gender_gate" not in inspect.getsource(
        discovery.get_discovery_page
    )
    # AI 助理的标签推荐是推荐行为，不是搜索行为，同样保持门禁
    assert "apply_intent_gender_gate" not in inspect.getsource(ai_assistant)


# ── 8. 贯通性：search_discovery 真的把开关送到 SQL ────────────────────────
@pytest.mark.asyncio
async def test_search_discovery_snapshot_has_no_gender_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不只校验源码里有这行字，而是把整条链路跑一遍看快照里有没有门禁。"""
    from app.schemas.discovery import DiscoverySearch
    from app.services.candidate_query import CandidatePage

    captured: dict[str, Any] = {}

    async def fake_viewer_context(db: Any, user_id: int) -> dict[str, Any]:
        return {**BASE_VIEWER, "intent_type": "self_match"}

    async def fake_is_vip(db: Any, user_id: int) -> bool:
        return False

    async def fake_browse_open(db: Any) -> bool:
        return True

    async def fake_fetch_page(db: Any, snapshot: Any, cursor: Any = None, page_size: int = 20):
        captured["snapshot"] = snapshot
        return CandidatePage(
            items=[],
            page=1,
            page_size=page_size,
            total=0,
            has_more=False,
            next_cursor=None,
        )

    monkeypatch.setattr(discovery, "_viewer_context", fake_viewer_context)
    monkeypatch.setattr(discovery, "_is_vip", fake_is_vip)
    monkeypatch.setattr(discovery, "platform_browse_open", fake_browse_open)
    monkeypatch.setattr(
        discovery.candidate_query_service, "fetch_page", fake_fetch_page
    )

    await discovery.search_discovery(None, 1, DiscoverySearch(nickname="小明"))

    snapshot = captured["snapshot"]
    assert "u.gender <> :opposite_gender" not in snapshot.where_sql
    assert "opposite_gender" not in snapshot.params


@pytest.mark.asyncio
async def test_recommendation_and_plaza_still_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一个用户，推荐/广场必须仍然只推异性——证明是「单独」放开而非整体放开。"""
    from app.services.candidate_query import CandidatePage

    snapshots: list[Any] = []

    async def fake_viewer_context(db: Any, user_id: int) -> dict[str, Any]:
        return {**BASE_VIEWER, "intent_type": "self_match"}

    async def fake_is_vip(db: Any, user_id: int) -> bool:
        return False

    async def fake_browse_open(db: Any) -> bool:
        return True

    async def fake_fetch_page(db: Any, snapshot: Any, cursor: Any = None, page_size: int = 20):
        snapshots.append(snapshot)
        return CandidatePage(
            items=[],
            page=1,
            page_size=page_size,
            total=0,
            has_more=False,
            next_cursor=None,
        )

    monkeypatch.setattr(discovery, "_viewer_context", fake_viewer_context)
    monkeypatch.setattr(discovery, "_is_vip", fake_is_vip)
    monkeypatch.setattr(discovery, "platform_browse_open", fake_browse_open)
    monkeypatch.setattr(
        discovery.candidate_query_service, "fetch_page", fake_fetch_page
    )

    for plaza in (False, True):
        await discovery.get_discovery_page(None, 1, DiscoveryFilters(), plaza=plaza)

    assert len(snapshots) == 2
    for snapshot in snapshots:
        assert "u.gender <> :opposite_gender" in snapshot.where_sql
        assert snapshot.params["opposite_gender"] == 1
