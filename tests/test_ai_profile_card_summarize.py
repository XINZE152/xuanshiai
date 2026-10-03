"""成稿反哺资料卡草稿契约测试（假 Session，不连真实库）。"""

from __future__ import annotations

import copy
from app.core.profile_tags import ALL_TAG_OPTIONS, MAX_PERSONAL_TAGS
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import CurrentUser, get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.main import app
import app.services.ai.profile_card as profile_card_service
from app.services.ai.profile import AIConsentRequired, AIInputError
from app.services.ai.profile_card import (
    PROFILE_CARD_SUMMARIZE_TASK_TYPE,
    ProfileCardVersionConflict,
    apply_profile_card_draft,
    load_profile_card_draft,
    request_profile_card_summarize,
)
from app.schemas.ai_profile_card import ProfileCardApplyRequest
from tests.test_ai_profile_sessions import (
    FakeProfileSession,
    ProfileStore,
    _MappingResult,
    _WriteResult,
    _now,
)

client = TestClient(app)


class CardFakeSession(FakeProfileSession):
    def __init__(self, store: ProfileStore) -> None:
        super().__init__(store)
        self._card_rollback_profiles = copy.deepcopy(store.profiles)
        self._card_rollback_revisions = copy.deepcopy(store.revision_rows)
        self._card_rollback_drafts = copy.deepcopy(store.card_drafts)

    async def rollback(self) -> None:
        await super().rollback()
        self._store.profiles = copy.deepcopy(self._card_rollback_profiles)
        self._store.revision_rows = copy.deepcopy(self._card_rollback_revisions)

    async def execute(self, statement: object, params: dict[str, Any] | None = None):
        sql = str(statement)
        values = dict(params or {})
        store = self._store
        if sql == "SELECT id FROM users WHERE id = :user_id FOR UPDATE":
            return _MappingResult([{"id": int(values["user_id"])}])
        if "FROM ai_profile_revision" in sql and "ORDER BY revision_no DESC" in sql:
            matching = [
                row
                for row in store.revisions
                if int(row["user_id"]) == int(values["user_id"])
                and row["subject"] == str(values["subject"])
            ]
            matching.sort(key=lambda row: int(row["revision_no"]), reverse=True)
            return _MappingResult([matching[0]] if matching else [])
        if "FROM ai_profile_summary" in sql:
            matching = [
                row
                for row in store.summaries
                if int(row["user_id"]) == int(values["user_id"])
                and row["subject"] == str(values["subject"])
            ]
            matching.sort(key=lambda row: row["created_at"], reverse=True)
            return _MappingResult([matching[0]] if matching else [])
        if "FROM ai_profile_revision_field" in sql:
            rows = [
                row
                for row in store.revision_fields
                if int(row["revision_id"]) == int(values["revision_id"])
            ]
            return _MappingResult(rows)
        if "SELECT COUNT(*) AS n FROM ai_task" in sql:
            count = sum(
                1
                for row in store.task_store.tasks.values()
                if int(row["owner_user_id"]) == int(values["user_id"])
                and row["task_type"] == str(values["task_type"])
            )
            return _MappingResult([{"n": count}])
        if "FROM ai_task WHERE owner_user_id = :user_id AND task_type = :task_type" in sql:
            row = store.task_store.find_by_idempotency(
                int(values["user_id"]),
                str(values["task_type"]),
                str(values["idempotency_key"]),
            )
            return _MappingResult([row] if row else [])
        if "INSERT INTO ai_profile_card_draft" in sql:
            store.card_drafts.append(
                {
                    "draft_id": values["draft_id"],
                    "user_id": int(values["user_id"]),
                    "task_id": values.get("task_id"),
                    "source_revision_id": values.get("source_revision_id"),
                    "status": "queued",
                    "expected_revision": 1,
                    "fields_json": values.get("fields_json"),
                    "prompt_version": values.get("prompt_version"),
                    "schema_version": values.get("schema_version"),
                    "applied_meta": None,
                    "last_operation_idempotency_key": None,
                    "last_operation_request_digest": None,
                    "last_operation_response_json": None,
                    "generated_at": None,
                    "applied_at": None,
                    "created_at": _now(),
                    "updated_at": _now(),
                }
            )
            return _WriteResult(rowcount=1)
        if "FROM ai_profile_card_draft" in sql:
            statuses = {
                str(values[key])
                for key in values
                if str(key).startswith("st")
            }
            matching = [
                row
                for row in store.card_drafts
                if int(row["user_id"]) == int(values["user_id"])
                and (not statuses or row["status"] in statuses)
                and (
                    "draft_id" not in values
                    or str(row["draft_id"]) == str(values["draft_id"])
                )
            ]
            matching.sort(key=lambda row: row["updated_at"], reverse=True)
            return _MappingResult([matching[0]] if matching else [])
        if sql.startswith("UPDATE ai_profile_card_draft"):
            for row in store.card_drafts:
                if str(row["draft_id"]) != str(values.get("draft_id") or ""):
                    continue
                if "status = 'applied'" in sql:
                    row["status"] = "applied"
                    row["last_operation_idempotency_key"] = values.get("idempotency_key")
                    row["last_operation_request_digest"] = values.get("request_digest")
                    row["last_operation_response_json"] = values.get("response_json")
                    row["applied_meta"] = values.get("applied_meta")
                    row["expected_revision"] = int(row.get("expected_revision") or 1) + 1
                row["updated_at"] = _now()
            return _WriteResult(rowcount=1)
        if "album_done" in sql:
            # recalculate_completion：真实 SQL 由 user_media EXISTS 子查询与
            # user_partner_preference/user_auth 连接算出这些列，假库按"无媒体、
            # 未实名、无择偶年龄"补零，使整份资料仍可算出完整度分数。
            profile = dict(
                store.profiles.get(int(values.get("id") or values.get("user_id") or 0), {})
            )
            profile.setdefault("album_done", 0)
            profile.setdefault("realname_status", 0)
            profile.setdefault("is_single_pledge", 0)
            profile.setdefault("preference_age_min", None)
            profile.setdefault("preference_age_max", None)
            return _MappingResult([profile] if profile else [])
        if "FROM user_profile" in sql or "LEFT JOIN user_profile" in sql:
            profile = store.profiles.get(int(values.get("id") or values.get("user_id") or 0), {})
            return _MappingResult([profile] if profile else [])
        if "INSERT INTO user_profile" in sql:
            user_id = int(values["user_id"])
            profile = store.profiles.setdefault(user_id, {"user_id": user_id})
            for key, value in values.items():
                if key != "user_id":
                    profile[key] = value
            return _WriteResult(rowcount=1)
        if "INSERT INTO user_profile_completion" in sql or "UPDATE users SET data_complete_rate" in sql:
            return _WriteResult(rowcount=1)
        if "FROM config_sensitive_word" in sql:
            return _MappingResult([])
        return await super().execute(statement, params)


class CardStore(ProfileStore):
    def __init__(self) -> None:
        super().__init__()
        self.revisions: list[dict[str, Any]] = []
        self.revision_fields: list[dict[str, Any]] = []
        self.summaries: list[dict[str, Any]] = []
        self.card_drafts: list[dict[str, Any]] = []
        self.profiles: dict[int, dict[str, Any]] = {
            10: {
                "user_id": 10,
                "nickname": "测",
                "gender": 1,
                "birthday": None,
                "is_married": 1,
                "avatar": None,
                "height": None,
                "weight": None,
                "occupation": None,
                "industry": None,
                "education_level": None,
                "income": None,
                "hometown_province_code": None,
                "hometown_city_code": None,
                "hometown_district_code": None,
                "residence_province_code": None,
                "residence_city_code": None,
                "residence_district_code": None,
                "self_intro": None,
                "qa_answers": None,
                "interest_tags": "[]",
                "personality_tags": "[]",
                "mbti": None,
                "tags": "{}",
                "completion_score": 0,
                "hide_school": 0,
                "hide_company": 0,
                "only_vip_can_see_detail": 0,
            }
        }
        self.session = CardFakeSession(self)
        self.db = self.session

    def seed_published(self, *, status: str = "confirmed", user_id: int = 10) -> None:
        self.revisions.append(
            {
                "id": 88,
                "user_id": user_id,
                "subject": "personal",
                "revision_no": 1,
            }
        )
        self.revision_fields.append(
            {
                "revision_id": 88,
                "field_key": "interest_tags",
                "display_value": "徒步",
                "value_json": '["徒步"]',
                "field_kind": "structured",
                "category": None,
                "content": None,
            }
        )
        self.summaries.append(
            {
                "user_id": user_id,
                "subject": "personal",
                "status": status,
                "summary_text": json.dumps(
                    {
                        "insight": "你愿意把日常过得具体一点。",
                        "conclusion": "认真靠近。",
                        "dimensions": [
                            {"key": "relationship", "title": "感情观", "summary": "尊重沟通"}
                        ],
                    },
                    ensure_ascii=False,
                ),
                "created_at": _now(),
            }
        )


def _override(store: CardStore) -> None:
    async def fake_current_user():
        return CurrentUser(
            id=10,
            session_id=1,
            phone="13800000000",
            status=1,
            realname_status=2,
        )

    def fake_db():
        yield store.db

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_db


def _clear() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_master_enabled", True)
    monkeypatch.setattr(settings, "ai_profile_enabled", True)


@pytest.mark.asyncio
async def test_summarize_requires_consent() -> None:
    store = CardStore()
    store.consents.clear()
    store.seed_published()
    with pytest.raises(AIConsentRequired):
        await request_profile_card_summarize(
            store.db, 10, force=False, idempotency_key="card-key-01"
        )
    assert store.card_drafts == []
    assert all(row["task_type"] != PROFILE_CARD_SUMMARIZE_TASK_TYPE for row in store.task_store.tasks.values())


@pytest.mark.asyncio
async def test_summarize_requires_confirmed_narrative() -> None:
    store = CardStore()
    store.seed_published(status="pending_confirmation")
    with pytest.raises(AIInputError):
        await request_profile_card_summarize(
            store.db, 10, force=False, idempotency_key="card-key-02"
        )


def test_cross_user_draft_is_404(monkeypatch) -> None:
    store = CardStore()
    store.seed_published()
    _enable(monkeypatch)
    _override(store)
    try:
        response = client.get("/api/v1/ai/profile-card/draft")
    finally:
        _clear()
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PROFILE_CARD_DRAFT_NOT_FOUND"


def test_summarize_route_returns_202(monkeypatch) -> None:
    store = CardStore()
    store.seed_published()
    _enable(monkeypatch)
    _override(store)
    try:
        response = client.post(
            "/api/v1/ai/profile-card/summarize",
            headers={"Idempotency-Key": "card-key-202ok"},
            json={"force": False},
        )
        replay = client.post(
            "/api/v1/ai/profile-card/summarize",
            headers={"Idempotency-Key": "card-key-202ok"},
            json={"force": False},
        )
    finally:
        _clear()
    assert response.status_code == 202
    body = response.json()
    assert body["task_id"]
    assert body["draft_id"] == store.card_drafts[0]["draft_id"]
    assert replay.json()["draft_id"] == body["draft_id"]
    assert body["poll_url"].endswith(body["task_id"])
    assert replay.status_code == 202
    assert replay.json()["task_id"] == body["task_id"]
    assert replay.json()["replayed"] is True


@pytest.mark.asyncio
async def test_apply_skips_existing_intro_unless_replace(monkeypatch) -> None:
    store = CardStore()
    store.seed_published()
    store.profiles[10]["self_intro"] = "已有自我介绍超过二十个字的内容。"
    store.card_drafts.append(
        {
            "draft_id": "draft-ready",
            "user_id": 10,
            "task_id": "task-ready",
            "source_revision_id": 88,
            "status": "ready",
            "expected_revision": 1,
            "fields_json": "{}",
            "prompt_version": "profile-card-summarize-v1",
            "schema_version": "profile-card-summarize-v1",
            "applied_meta": None,
            "last_operation_idempotency_key": None,
            "last_operation_request_digest": None,
            "last_operation_response_json": None,
            "generated_at": _now(),
            "applied_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
    )
    body = ProfileCardApplyRequest(
        expected_revision=1,
        accepted={"self_intro": "新的自我介绍草稿，超过二十个字。", "personal_tags": ["周末徒步"]},
        rejected=["height", "education", "income"],
    )
    result = await apply_profile_card_draft(store.db, 10, body, "apply-key-1")
    assert "self_intro" in result["skipped_fields"]
    assert "height" in result["skipped_fields"]
    assert store.profiles[10]["self_intro"] == "已有自我介绍超过二十个字的内容。"

    replace = ProfileCardApplyRequest(
        expected_revision=2,
        accepted={"self_intro": "新的自我介绍草稿，超过二十个字。"},
        replace_existing={"self_intro": True},
    )
    replaced = await apply_profile_card_draft(store.db, 10, replace, "apply-key-2")
    assert "self_intro" in replaced["written_fields"]


@pytest.mark.asyncio
async def test_daily_quota_sixth_force_is_exceeded() -> None:
    store = CardStore()
    store.seed_published()
    for index in range(5):
        await request_profile_card_summarize(
            store.db, 10, force=True, idempotency_key=f"quota-key-{index:02d}xx"
        )
    from app.services.ai.profile_card import ProfileCardQuotaExceeded

    with pytest.raises(ProfileCardQuotaExceeded):
        await request_profile_card_summarize(
            store.db, 10, force=True, idempotency_key="quota-key-06xxxx"
        )


def test_profile_card_routes_require_idempotency_header() -> None:
    from app.api.routes.ai_profile_card import router

    summarize = next(route for route in router.routes if route.path == "/profile-card/summarize")
    apply_route = next(route for route in router.routes if route.path == "/profile-card/draft/apply")
    for route in (summarize, apply_route):
        extra = getattr(route, "openapi_extra", None) or {}
        header = next(item for item in extra["parameters"] if item["name"] == "Idempotency-Key")
        assert header["required"] is True

def _append_ready_draft(
    store: CardStore,
    draft_id: str,
    *,
    expected_revision: int = 1,
    source_revision_id: int | None = 88,
) -> None:
    now = _now()
    store.card_drafts.append(
        {
            "draft_id": draft_id,
            "user_id": 10,
            "task_id": f"task-{draft_id}",
            "source_revision_id": source_revision_id,
            "status": "ready",
            "expected_revision": expected_revision,
            "fields_json": "{}",
            "prompt_version": "profile-card-summarize-v1",
            "schema_version": "profile-card-summarize-v1",
            "applied_meta": None,
            "last_operation_idempotency_key": None,
            "last_operation_request_digest": None,
            "last_operation_response_json": None,
            "generated_at": now,
            "applied_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )


@pytest.mark.asyncio
async def test_explicit_draft_id_does_not_cross_select() -> None:
    store = CardStore()
    store.seed_published()
    _append_ready_draft(store, "draft-old")
    _append_ready_draft(store, "draft-new")

    result = await apply_profile_card_draft(
        store.db,
        10,
        ProfileCardApplyRequest(
            draft_id="draft-old",
            expected_revision=1,
            source_revision_id=88,
            accepted={"personal_tags": ["跑步"]},
        ),
        "explicit-draft-key",
    )

    assert result["draft_id"] == "draft-old"
    assert result["expected_revision"] == 2
    assert next(row for row in store.card_drafts if row["draft_id"] == "draft-old")["status"] == "applied"
    assert next(row for row in store.card_drafts if row["draft_id"] == "draft-new")["status"] == "ready"


@pytest.mark.asyncio
async def test_apply_rejects_draft_version_conflict() -> None:
    store = CardStore()
    store.seed_published()
    _append_ready_draft(store, "draft-version")

    with pytest.raises(ProfileCardVersionConflict):
        await apply_profile_card_draft(
            store.db,
            10,
            ProfileCardApplyRequest(draft_id="draft-version", expected_revision=2),
            "version-key",
        )
    assert store.card_drafts[0]["status"] == "ready"


@pytest.mark.asyncio
async def test_tag_overflow_is_ai_input_invalid_not_truncated() -> None:
    store = CardStore()
    store.seed_published()
    _append_ready_draft(store, "draft-tags")
    tags = list(ALL_TAG_OPTIONS)[: MAX_PERSONAL_TAGS + 1]

    with pytest.raises(AIInputError) as exc_info:
        await apply_profile_card_draft(
            store.db,
            10,
            ProfileCardApplyRequest(
                draft_id="draft-tags", expected_revision=1, accepted={"personal_tags": tags}
            ),
            "tag-overflow-key",
        )
    assert exc_info.value.code == "AI_INPUT_INVALID"
    assert store.card_drafts[0]["status"] == "ready"
    assert json.loads(store.profiles[10]["interest_tags"]) == []


@pytest.mark.asyncio
async def test_merge_and_replace_selection_have_distinct_tag_semantics() -> None:
    store = CardStore()
    store.seed_published()
    store.profiles[10]["interest_tags"] = json.dumps(["跑步"], ensure_ascii=False)
    store.profiles[10]["tags"] = json.dumps({"sports": ["跑步"]}, ensure_ascii=False)
    _append_ready_draft(store, "draft-tag-mode")

    merged = await apply_profile_card_draft(
        store.db,
        10,
        ProfileCardApplyRequest(
            draft_id="draft-tag-mode",
            expected_revision=1,
            accepted={"personal_tags": ["瑜伽"]},
            tag_apply_mode="merge",
        ),
        "tag-merge-key",
    )
    assert merged["expected_revision"] == 2
    assert json.loads(store.profiles[10]["interest_tags"]) == ["跑步", "瑜伽"]

    replaced = await apply_profile_card_draft(
        store.db,
        10,
        ProfileCardApplyRequest(
            draft_id="draft-tag-mode",
            expected_revision=2,
            accepted={"personal_tags": ["滑雪"]},
            tag_apply_mode="replace_selection",
        ),
        "tag-replace-key",
    )
    assert replaced["expected_revision"] == 3
    assert json.loads(store.profiles[10]["interest_tags"]) == ["滑雪"]


@pytest.mark.asyncio
async def test_apply_exception_rolls_back_profile_revision_and_draft(monkeypatch) -> None:
    store = CardStore()
    store.seed_published()
    _append_ready_draft(store, "draft-rollback")
    before_profile = dict(store.profiles[10])
    before_revision = dict(store.revision_rows[10])

    async def fail_after_profile_write(*args, **kwargs):
        store.profiles[10]["self_intro"] = "不应落库"
        store.revision_rows[10]["profile_revision"] += 1
        raise RuntimeError("simulated apply failure")
    monkeypatch.setattr(profile_card_service, "update_profile", fail_after_profile_write)
    with pytest.raises(RuntimeError):
        await apply_profile_card_draft(
            store.db,
            10,
            ProfileCardApplyRequest(
                draft_id="draft-rollback",
                expected_revision=1,
                accepted={"self_intro": "会触发失败"},
            ),
            "rollback-key",
        )

    assert store.profiles[10] == before_profile
    assert store.revision_rows[10] == before_revision
    assert store.card_drafts[0]["status"] == "ready"
    assert store.db.rollbacks >= 1


@pytest.mark.asyncio
async def test_apply_replays_same_idempotent_request_with_receipt() -> None:
    store = CardStore()
    store.seed_published()
    _append_ready_draft(store, "draft-replay")
    body = ProfileCardApplyRequest(
        draft_id="draft-replay", expected_revision=1, accepted={"personal_tags": ["跑步"]}
    )

    first = await apply_profile_card_draft(store.db, 10, body, "replay-key")
    replay = await apply_profile_card_draft(store.db, 10, body, "replay-key")

    assert first["draft_id"] == replay["draft_id"] == "draft-replay"
    assert first["expected_revision"] == replay["expected_revision"] == 2
    assert replay["replayed"] is True
    assert store.revision_rows[10]["profile_revision"] == 1

@pytest.mark.asyncio
async def test_explicit_get_draft_id_does_not_fall_back_to_latest() -> None:
    store = CardStore()
    store.seed_published()
    _append_ready_draft(store, "draft-get-old")
    _append_ready_draft(store, "draft-get-new")

    result = await load_profile_card_draft(store.db, 10, draft_id="draft-get-old")

    assert result.draft_id == "draft-get-old"


@pytest.mark.parametrize("draft_id", ["", "x" * 65])
def test_get_rejects_invalid_draft_query(monkeypatch, draft_id) -> None:
    store = CardStore()
    _enable(monkeypatch)
    _override(store)
    try:
        response = client.get("/api/v1/ai/profile-card/draft", params={"draft_id": draft_id})
    finally:
        _clear()
    assert response.status_code == 422
