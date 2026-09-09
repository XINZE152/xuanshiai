"""三类推荐打分核心（WP-P6b）单测：纯函数，不触 DB/LLM/任务系统。

量纲锁定：score 0..100（与 compatibility 引擎/快照一致），coverage 0..1。
"""

from __future__ import annotations

import json

import pytest

from app.services.ai.compatibility import (
    COVERAGE_THRESHOLD,
    REASON_AGE,
    REASON_CITY,
    REASON_INTEREST,
)
from app.services.ai.recommend import (
    RecommendationScore,
    load_candidate_pool,
    load_recommendation_inputs,
    positive_dimension_codes,
    score_i_like,
    score_likes_me,
    similarity_score,
    viewer_projection_is_current,
)


def _rich_preference() -> dict:
    return {
        "age": {"min": 25, "max": 35},
        "city_code": ["3100000"],
        "interest_tags": ["hiking", "摄影"],
    }


def _matching_profile() -> dict:
    return {
        "age": 30,
        "city_code": "3100000",
        "interest_tags": ["hiking", "travel"],
    }


def test_score_i_like_known_dimensions_weighted() -> None:
    card = score_i_like(_rich_preference(), _matching_profile())
    assert isinstance(card, RecommendationScore)
    # age(20)*100 + city(15)*100 + interest(15)*50，其余维度未知
    assert card.score == 85.0
    assert card.coverage == 0.5  # 50/100 可用权重
    assert REASON_AGE in card.reason_codes
    assert REASON_CITY in card.reason_codes
    assert REASON_INTEREST in card.reason_codes


def test_score_i_like_below_coverage_threshold_is_none() -> None:
    card = score_i_like({"age": {"min": 25, "max": 35}}, {"age": 30})
    assert COVERAGE_THRESHOLD == 0.5
    assert card.score is None
    assert card.coverage == 0.2


def test_score_i_like_no_dimensions_at_all() -> None:
    card = score_i_like({}, {})
    assert card.score is None
    assert card.coverage == 0.0


def test_score_likes_me_is_directional_reverse() -> None:
    """likes_me = 对方的理想型投影 × 我的个人画像，与 i_like 同一套规则。"""
    card = score_likes_me(_rich_preference(), _matching_profile())
    assert card.score == 85.0
    assert card.coverage == 0.5
    assert REASON_AGE in card.reason_codes


def test_similarity_score_same_city_close_age_ranks_high() -> None:
    a = {
        "interest_tags": ["a", "b", "c"],
        "city_code": "3100000",
        "age": 30,
        "height_cm": 175,
    }
    b = {
        "interest_tags": ["a", "b"],
        "city_code": "3100000",
        "age": 32,
        "height_cm": 175,
    }
    card = similarity_score(a, b)
    # Jaccard 按维度先舍入(2/3→66.67, w0.30) + city(0.10) + age(w0.10) + height(w0.10)
    assert card.score == round((0.30 * 66.67 + 0.10 * 100 + 0.10 * 100 + 0.10 * 100) / 0.60, 2)
    assert card.coverage == 0.6
    assert "SIM_INTEREST_TAGS" in card.reason_codes
    assert "SIM_CITY_CODE" in card.reason_codes


def test_similarity_score_symmetric() -> None:
    a = {"interest_tags": ["a", "b"], "age": 30}
    b = {"interest_tags": ["b", "a"], "age": 31}
    assert similarity_score(a, b).score == similarity_score(b, a).score


def test_similarity_score_both_empty_is_none() -> None:
    card = similarity_score({}, {})
    assert card.score is None
    assert card.coverage == 0.0


def test_similarity_score_below_coverage_is_none() -> None:
    card = similarity_score({"height_cm": 175}, {"height_cm": 176})
    assert card.score is None  # 仅 0.10 权重可用 < 0.5


def test_positive_dimension_codes_only_positive_hits() -> None:
    codes = positive_dimension_codes(
        {"age": {"min": 25, "max": 35}, "city_code": ["3100000"]},
        {"age": 30, "city_code": "4400000"},
    )
    assert REASON_AGE in codes
    assert REASON_CITY not in codes  # 已知但不满足 → 不产正向码
    assert "DIMENSION_UNKNOWN" in codes  # 缺失维度显式标注


def test_scores_round_to_two_decimals() -> None:
    card = similarity_score(
        {"interest_tags": ["a", "b", "c", "d", "e", "f", "g"], "age": 30, "city_code": "3100000"},
        {"interest_tags": ["a"], "age": 30, "city_code": "3100000"},
    )
    assert card.score is not None
    assert card.score == round(card.score, 2)


# ----------------------------------------------------------------------
# WP-P6e 读取端点（API 层，monkeypatch 服务函数）
# ----------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.api.dependencies import CurrentUser, get_current_user, get_db  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


class _FakeDb:
    """API 单测的 DB 桩：路由的读路径不触库（服务函数被 monkeypatch）。"""

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _override_auth(user_id: int = 9_876_549_001) -> None:
    def fake_current_user() -> CurrentUser:
        return CurrentUser(
            id=user_id, session_id=1, phone="13800000000", status=1, realname_status=2
        )

    def fake_db():
        yield _FakeDb()

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_db


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _enable_recommend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_master_enabled", True)
    monkeypatch.setattr(settings, "ai_recommend_enabled", True)


def test_api_recommendations_returns_ranked_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.routes.ai_recommend as route_mod

    async def fake_read(db, viewer_id, view_kind, limit):
        assert view_kind == "i_like"
        return [
            {
                "target_user_id": 2,
                "score": 85.0,
                "coverage": 0.5,
                "rank_no": 1,
                "engine": "llm-v1",
                "reason_codes": ["AGE_MUTUAL_WITHIN_RANGE"],
                "reason_texts": ["年龄正处你期待区间"],
            },
            {
                "target_user_id": 3,
                "score": 70.0,
                "coverage": 0.6,
                "rank_no": 2,
                "engine": "rule-v1",
                "reason_codes": [],
                "reason_texts": [],
            },
        ]

    called = {"enqueue": False}

    async def fail_enqueue(db, viewer_id):
        called["enqueue"] = True
        return None

    monkeypatch.setattr(route_mod, "read_recommendations", fake_read)
    monkeypatch.setattr(route_mod, "enqueue_recommendation_rebuild", fail_enqueue)
    _enable_recommend(monkeypatch)
    _override_auth()
    try:
        response = client.get("/api/v1/ai/recommendations", params={"view": "i_like"})
    finally:
        _clear_overrides()
    assert response.status_code == 200
    body = response.json()
    assert body["regenerating"] is False
    assert [item["rank_no"] for item in body["items"]] == [1, 2]
    assert body["items"][0]["engine"] == "llm-v1"
    assert body["items"][0]["reason_texts"] == ["年龄正处你期待区间"]
    assert called["enqueue"] is False


def test_api_recommendations_miss_triggers_regenerating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.routes.ai_recommend as route_mod

    async def fake_read(db, viewer_id, view_kind, limit):
        return []

    enqueued = {"count": 0}

    async def fake_enqueue(db, viewer_id):
        enqueued["count"] += 1
        return SimpleNamespace(status="queued")

    monkeypatch.setattr(route_mod, "read_recommendations", fake_read)
    monkeypatch.setattr(route_mod, "enqueue_recommendation_rebuild", fake_enqueue)
    _enable_recommend(monkeypatch)
    _override_auth()
    try:
        response = client.get("/api/v1/ai/recommendations", params={"view": "similar"})
    finally:
        _clear_overrides()
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["regenerating"] is True
    assert enqueued["count"] == 1


def test_api_recommendations_terminal_task_is_not_regenerating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同日任务已终态（如空池 succeeded）→ 如实 regenerating=false，禁止无限轮询。"""
    import app.api.routes.ai_recommend as route_mod
    from types import SimpleNamespace

    async def fake_read(db, viewer_id, view_kind, limit):
        return []

    async def fake_enqueue(db, viewer_id):
        return SimpleNamespace(status="succeeded")

    monkeypatch.setattr(route_mod, "read_recommendations", fake_read)
    monkeypatch.setattr(route_mod, "enqueue_recommendation_rebuild", fake_enqueue)
    _enable_recommend(monkeypatch)
    _override_auth()
    try:
        response = client.get("/api/v1/ai/recommendations", params={"view": "i_like"})
    finally:
        _clear_overrides()
    assert response.status_code == 200
    assert response.json()["regenerating"] is False


def test_api_recommendations_no_task_is_not_regenerating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无授权/无投影（未入队）→ regenerating=false（诚实空列表）。"""
    import app.api.routes.ai_recommend as route_mod

    async def fake_read(db, viewer_id, view_kind, limit):
        return []

    async def fake_enqueue(db, viewer_id):
        return None

    monkeypatch.setattr(route_mod, "read_recommendations", fake_read)
    monkeypatch.setattr(route_mod, "enqueue_recommendation_rebuild", fake_enqueue)
    _enable_recommend(monkeypatch)
    _override_auth()
    try:
        response = client.get("/api/v1/ai/recommendations", params={"view": "i_like"})
    finally:
        _clear_overrides()
    assert response.status_code == 200
    assert response.json()["regenerating"] is False


def test_api_recommendations_gate_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ai_master_enabled", False)
    _override_auth()
    try:
        response = client.get("/api/v1/ai/recommendations", params={"view": "i_like"})
    finally:
        _clear_overrides()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AI_FEATURE_DISABLED"


def test_api_recommendations_rejects_unknown_view() -> None:
    # view 参数校验发生在路由进入前（FastAPI），与门禁状态无关 → 422。
    _override_auth()
    try:
        response = client.get(
            "/api/v1/ai/recommendations", params={"view": "everyone"}
        )
    finally:
        _clear_overrides()
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Phase 2 Task 7：memory 投影接入 recommend
# ---------------------------------------------------------------------------


async def _recommend_memory_store():
    from app.services.ai.memory.projections import (
        MemoryProjectionService,
        derive_consent_snapshot_id,
    )
    from tests.test_ai_memory_projections import (
        POLICY_REVISION,
        FakeProjectionSession,
        ProjectionStore,
        seed_claim,
    )
    from app.services.ai.memory.policy import MemoryPolicy

    store = ProjectionStore()
    consent = {
        "scope": "profile_text_extract",
        "version": "profile_text_extract-v3",
        "policy_revision": POLICY_REVISION,
        "granted_at": "2026-09-05T08:00:00",
    }
    snapshot_id = derive_consent_snapshot_id(consent)
    structured_key = MemoryPolicy.canonical_key(
        "personal", "lifestyle", MemoryPolicy.candidate_identity("structured", "height_cm", None, None)
    )
    for uid in (701, 702, 703):
        store.consents[uid] = dict(consent)
        for function_key, category in (
            ("compatibility", "compatibility_features"),
            ("recommend", "ideal_partner_preference"),
        ):
            store.grants[(uid, function_key, "candidate_rank", category)] = {
                "grant_id": f"g-{uid}-{category}",
                "owner_user_id": uid,
                "function_key": function_key,
                "purpose": "candidate_rank",
                "data_category": category,
                "status": "active",
                "consent_snapshot_id": snapshot_id,
                "policy_revision": POLICY_REVISION,
                "granted_at": "2026-09-05T08:00:00",
                "revoked_at": None,
            }
        if uid in (701, 702):
            seed_claim(
                store,
                f"c-{uid}-height",
                owner_user_id=uid,
                canonical_key=structured_key,
                value=175,
            )
        seed_claim(
            store,
            f"c-{uid}-ideal",
            owner_user_id=uid,
            subject="ideal_partner",
            dimension="lifestyle",
            value="希望对方爱运动",
        )
    session = FakeProjectionSession(store)
    service = MemoryProjectionService(session, policy_revision=POLICY_REVISION)
    for uid in (701, 702, 703):
        if uid in (701, 702):
            await service.build(
                owner_user_id=uid,
                function_key="compatibility",
                purpose="candidate_rank",
                data_category="compatibility_features",
            )
        await service.build(
            owner_user_id=uid,
            function_key="recommend",
            purpose="candidate_rank",
            data_category="ideal_partner_preference",
        )
    return session, store


@pytest.mark.asyncio
async def test_memory_mode_load_recommendation_inputs(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store = await _recommend_memory_store()
    inputs = await load_recommendation_inputs(session, 701)
    assert inputs is not None
    assert inputs["profile"].get("height_cm") == 175
    assert inputs["preference"], "ideal_partner 仅作为当前用户 preference input"
    assert inputs["source"] == "memory_projection"


@pytest.mark.asyncio
async def test_memory_mode_inputs_missing_profile_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store = await _recommend_memory_store()
    # 703 只有 ideal_partner 投影，无 personal 画像 → None
    assert await load_recommendation_inputs(session, 703) is None


@pytest.mark.asyncio
async def test_memory_mode_candidate_pool_excludes_ideal_only_and_viewer(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, _store = await _recommend_memory_store()
    pool = await load_candidate_pool(session, 701, 10)
    ids = [entry["user_id"] for entry in pool]
    assert 701 not in ids, "viewer 不进入自己的候选池"
    assert 702 in ids and 703 not in ids, "只有带本人画像投影的候选人进入池"
    entry = next(entry for entry in pool if entry["user_id"] == 702)
    assert entry["profile_fields"].get("height_cm") == 175
    assert entry["source"] == "memory_projection"


@pytest.mark.asyncio
async def test_memory_mode_viewer_projection_is_current(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_memory_projection_read_mode", "memory")
    session, store = await _recommend_memory_store()
    assert await viewer_projection_is_current(session, 701) is True
    store.consents[702] = None
    assert await viewer_projection_is_current(session, 702) is False


@pytest.mark.asyncio
async def test_unconfirmed_importance_entry_is_neutral_no_hard_exclusion() -> None:
    """未确认 importance 以中性 0.5 入投影、constraint_type 为空——
    下游据此不生成 hard exclusion（计划验收门槛 4）。"""
    from app.services.ai.memory.projections import _claim_to_entry

    entry = _claim_to_entry(
        {
            "claim_id": "c-1",
            "subject": "personal",
            "canonical_key": "personal:lifestyle:digest",
            "dimension": "lifestyle",
            "value_json": json.dumps("喜欢安静"),
            "confidence": 0.9,
            "stability": 0.85,
            "importance": 0.9,
            "constraint_type": "must_not_smoke",
            "importance_confirmed": 0,
            "fact_kind": "about_user",
            "status": "confirmed",
            "source_kind": "inferred",
            "last_event_id": "e1",
            "last_event_seq": 3,
        }
    )
    assert entry is not None
    assert entry["importance"] == 0.5, "未确认 importance 不得携带原值"
    assert entry["constraint_type"] is None, "inferred 来源不得携带 constraint_type"
