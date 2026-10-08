"""S1 资料采用真实 MySQL 契约；不调用模型，不以 fake Session 代替事务证据。"""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.dependencies import CurrentUser
from app.api.routes.ai_profile_card import apply_profile_card_draft_route
from app.schemas.ai_common import AiConsentGrantRequest
from app.schemas.ai_profile_card import ProfileCardApplyRequest
from app.schemas.ai_profile import ProfileSubject
from app.schemas.auth import ProfileUpdateRequest
from app.services.ai.consents import grant_consent
from app.services.ai import profile
from app.services.ai.profile import AIConsentRequired, AIInputError
from app.services.ai.profile_card import (
    ProfileCardDraftNotFound,
    ProfileCardVersionConflict,
    apply_profile_card_draft,
    load_profile_card_draft,
    request_profile_card_summarize,
)
from app.services.ai.tasks import TaskError
from app.services.profile import get_profile, update_profile

USER_A = 9_876_549_610
USER_B = USER_A + 1
POLICY = "ai-policy-2026-08-07-v1"
DRAFT_A = "s1-card-a"
DRAFT_B = "s1-card-b"
INTRO = "周末喜欢徒步和看展，希望认真沟通，一起分享平凡的日常。"


@pytest_asyncio.fixture
async def seeded(real_db_engine: AsyncEngine):
    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    async with factory() as db:
        assert (await db.execute(text("SELECT DATABASE()"))).scalar_one() == "xuanshiai_ai_test"
        for uid, gender in ((USER_A, 1), (USER_B, 2)):
            await db.execute(text(
                "INSERT INTO users (id, nickname, gender, birthday, status, is_married) "
                "VALUES (:uid, 's1-test', :gender, '1994-01-01', 1, 1)"
            ), {"uid": uid, "gender": gender})
            await db.execute(text("INSERT INTO user_profile (user_id, tags) VALUES (:uid, '{}')"), {"uid": uid})
            await db.execute(text("INSERT INTO user_privacy (user_id, show_profile, match_status, who_can_see_me) VALUES (:uid, 1, 1, 1)"), {"uid": uid})
            await db.execute(text("INSERT INTO user_auth (user_id, realname_status) VALUES (:uid, 2)"), {"uid": uid})
            await grant_consent(db, uid, "profile_text_extract", AiConsentGrantRequest(
                consent_version="profile-text-v1", policy_revision=POLICY
            ), f"s1-grant-{uid}", 0)
        result = await db.execute(text(
            "INSERT INTO ai_profile_revision (user_id, subject, revision_no, policy_revision) "
            "VALUES (:uid, 'personal', 1, :policy)"
        ), {"uid": USER_A, "policy": POLICY})
        revision = result.lastrowid
        await db.execute(text(
            "INSERT INTO ai_profile_revision_field "
            "(revision_id, field_key, subject, value_json, display_value, content_hash) "
            "VALUES (:rid, 'interest_tags', 'personal', '[\"徒步\"]', '徒步', :hash)"
        ), {"rid": revision, "hash": "1" * 64})
        await db.execute(text(
            "INSERT INTO ai_profile_summary (revision_id, user_id, subject, summary_text, status) "
            "VALUES (:rid, :uid, 'personal', :summary, 'confirmed')"
        ), {"rid": revision, "uid": USER_A, "summary": json.dumps({"insight": INTRO}, ensure_ascii=False)})
        for draft in (DRAFT_A, DRAFT_B):
            await db.execute(text(
                "INSERT INTO ai_profile_card_draft "
                "(draft_id, user_id, source_revision_id, status, expected_revision, fields_json) "
                "VALUES (:draft, :uid, :rid, 'ready', 1, '{}')"
            ), {"draft": draft, "uid": USER_A, "rid": revision})
        await db.commit()
    return factory, revision


def _body(revision: int, draft: str = DRAFT_A, **overrides) -> ProfileCardApplyRequest:
    values = dict(draft_id=draft, expected_revision=1, source_revision_id=revision,
                  base_profile_revision=0, accepted={"self_intro": INTRO, "personal_tags": ["周末徒步"]})
    values.update(overrides)
    return ProfileCardApplyRequest(**values)


async def _snapshot(factory) -> dict:
    async with factory() as db:
        profile = await get_profile(db, USER_A)
        vector = (await db.execute(text("SELECT profile_revision FROM user_revision_state WHERE user_id=:uid"), {"uid": USER_A})).scalar_one()
        events = (await db.execute(text("SELECT COUNT(*) FROM derivation_outbox WHERE aggregate_id=:uid AND event_type='profile_updated'"), {"uid": USER_A})).scalar_one()
        drafts = (await db.execute(text(
            "SELECT draft_id, status, expected_revision, last_operation_idempotency_key, "
            "last_operation_request_digest, last_operation_response_json "
            "FROM ai_profile_card_draft WHERE user_id=:uid ORDER BY draft_id"
        ), {"uid": USER_A})).all()
        return {"profile": profile, "revision": vector, "events": events, "drafts": [tuple(row) for row in drafts]}


@pytest.mark.asyncio
async def test_exact_read_and_adoption_do_not_cross_drafts(seeded):
    factory, revision = seeded
    async with factory() as db:
        draft = await load_profile_card_draft(db, USER_A, DRAFT_A)
        assert draft.draft_id == DRAFT_A and draft.base_profile_revision == 0
    async with factory() as db:
        result = await apply_profile_card_draft(db, USER_A, _body(revision), "s1-exact-key")
        await db.commit()
    state = await _snapshot(factory)
    assert result["draft_id"] == DRAFT_A and result["expected_revision"] == 2
    assert state["profile"]["self_intro"] == result["profile"]["self_intro"] == INTRO
    assert state["revision"] == state["events"] == 1
    assert [(row[0], row[1]) for row in state["drafts"]] == [(DRAFT_A, "applied"), (DRAFT_B, "ready")]
    async with factory() as db:
        with pytest.raises(ProfileCardDraftNotFound):
            await load_profile_card_draft(db, USER_B, DRAFT_A)
        with pytest.raises(ProfileCardDraftNotFound):
            await load_profile_card_draft(db, USER_A, "s1-missing")


@pytest.mark.asyncio
@pytest.mark.parametrize("same_draft", [True, False])
async def test_two_sessions_have_one_winner_and_one_409(seeded, same_draft):
    factory, revision = seeded
    async def adopt(draft, key):
        async with factory() as db:
            result = await apply_profile_card_draft(db, USER_A, _body(revision, draft), key)
            await db.commit()
            return result
    results = await asyncio.wait_for(asyncio.gather(
        adopt(DRAFT_A, "s1-concurrent-1"),
        adopt(DRAFT_A if same_draft else DRAFT_B, "s1-concurrent-2"),
        return_exceptions=True,
    ), timeout=15)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, ProfileCardVersionConflict) for result in results) == 1, results
    state = await _snapshot(factory)
    assert state["revision"] == state["events"] == 1


@pytest.mark.asyncio
async def test_concurrent_replay_is_stable_and_digest_change_is_409(seeded):
    factory, revision = seeded
    body = _body(revision)
    async def adopt():
        async with factory() as db:
            result = await apply_profile_card_draft(db, USER_A, body, "s1-replay-key")
            await db.commit()
            return result
    results = await asyncio.wait_for(asyncio.gather(adopt(), adopt()), timeout=15)
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert results[0]["profile"] == results[1]["profile"]
    before = await _snapshot(factory)
    async with factory() as db:
        with pytest.raises(TaskError) as exc:
            await apply_profile_card_draft(db, USER_A, _body(revision, accepted={"personal_tags": ["跑步"]}), "s1-replay-key")
        assert exc.value.code == "TASK_IDEMPOTENCY_CONFLICT" and exc.value.status_code == 409
    assert await _snapshot(factory) == before
    assert before["revision"] == before["events"] == 1


@pytest.mark.asyncio
async def test_regular_edit_invalidates_read_profile_version(seeded):
    factory, revision = seeded
    async with factory() as db:
        await update_profile(db, USER_A, ProfileUpdateRequest(self_intro=INTRO))
    before = await _snapshot(factory)
    async with factory() as db:
        with pytest.raises(ProfileCardVersionConflict):
            await apply_profile_card_draft(db, USER_A, _body(revision), "s1-version-key")
    assert await _snapshot(factory) == before


@pytest.mark.asyncio
async def test_new_source_invalidates_old_card_without_writes(seeded):
    factory, revision = seeded
    async with factory() as db:
        await db.execute(text(
            "INSERT INTO ai_profile_revision (user_id, subject, revision_no, policy_revision) "
            "VALUES (:uid, 'personal', 2, :policy)"
        ), {"uid": USER_A, "policy": POLICY})
        await db.commit()
    before = await _snapshot(factory)
    async with factory() as db:
        with pytest.raises(TaskError) as exc:
            await apply_profile_card_draft(db, USER_A, _body(revision), "s1-source-key")
        assert exc.value.code == "RESULT_STALE" and exc.value.status_code == 409
    assert await _snapshot(factory) == before


class _FailCardWriteSession(AsyncSession):
    async def execute(self, statement, params=None, **kwargs):
        if str(statement).startswith("UPDATE ai_profile_card_draft SET status = 'applied'"):
            raise RuntimeError("s1 injected receipt write failure")
        return await super().execute(statement, params, **kwargs)


class _FailCommitSession(AsyncSession):
    async def commit(self):
        raise RuntimeError("s1 injected commit failure before durability")


@pytest.mark.asyncio
async def test_receipt_failure_rolls_back_profile_revision_and_outbox(seeded, real_db_engine):
    factory, revision = seeded
    before = await _snapshot(factory)
    failing = async_sessionmaker(real_db_engine, class_=_FailCardWriteSession)
    async with failing() as db:
        with pytest.raises(RuntimeError, match="receipt write failure"):
            await apply_profile_card_draft(db, USER_A, _body(revision), "s1-rollback-key")
    assert await _snapshot(factory) == before


@pytest.mark.asyncio
async def test_route_commit_failure_rolls_back_all_changes(seeded, real_db_engine):
    factory, revision = seeded
    before = await _snapshot(factory)
    failing = async_sessionmaker(real_db_engine, class_=_FailCommitSession)
    async with failing() as db:
        with pytest.raises(HTTPException) as exc:
            await apply_profile_card_draft_route(_body(revision), current=CurrentUser(id=USER_A, session_id=1, phone=None, status=1, realname_status=2), db=db, idempotency_key="s1-commit-key")
        assert exc.value.status_code == 503
    assert await _snapshot(factory) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("tags", [["跑步"] * 2, ["目录外标签"], ["跑步"] * 11])
async def test_invalid_tags_never_mutate_any_state(seeded, tags):
    factory, revision = seeded
    before = await _snapshot(factory)
    async with factory() as db:
        with pytest.raises(AIInputError):
            await apply_profile_card_draft(db, USER_A, _body(revision, accepted={"personal_tags": tags}), "s1-invalid-key")
    assert await _snapshot(factory) == before


@pytest.mark.asyncio
async def test_summarize_and_replay_return_the_same_precise_draft(seeded):
    factory, _ = seeded
    async with factory() as db:
        first = await request_profile_card_summarize(db, USER_A, force=True, idempotency_key="s1-summarize-key")
        await db.commit()
    async with factory() as db:
        replay = await request_profile_card_summarize(db, USER_A, force=True, idempotency_key="s1-summarize-key")
        assert first.draft_id and first.draft_id == replay.draft_id
        assert first.task.task_id == replay.task.task_id and replay.replayed
        exact = await load_profile_card_draft(db, USER_A, replay.draft_id)
        assert exact.draft_id == first.draft_id


@pytest.mark.asyncio
async def test_pending_regular_edit_serializes_card_adoption(seeded):
    factory, revision = seeded
    async def adopt():
        async with factory() as db:
            result = await apply_profile_card_draft(db, USER_A, _body(revision), "s1-editor-race")
            await db.commit()
            return result
    async with factory() as editor:
        await update_profile(editor, USER_A, ProfileUpdateRequest(self_intro=INTRO), commit=False)
        pending = asyncio.create_task(adopt())
        try:
            await asyncio.sleep(0.1)
            assert not pending.done(), "采用必须等普通编辑的用户行锁释放"
            await editor.commit()
            with pytest.raises(ProfileCardVersionConflict):
                await asyncio.wait_for(pending, timeout=10)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    state = await _snapshot(factory)
    assert state["profile"]["self_intro"] == INTRO
    assert state["revision"] == state["events"] == 1
    assert all(row[1] == "ready" for row in state["drafts"])


@pytest.mark.asyncio
async def test_removal_is_visible_on_fresh_public_profile_read(seeded):
    factory, revision = seeded
    async with factory() as db:
        await apply_profile_card_draft(db, USER_A, _body(revision), "s1-removal-key")
        await db.commit()
    async with factory() as db:
        public = await get_profile(db, USER_A, public=True)
        assert public["self_intro"] == INTRO and public["personal_tags"] == ["周末徒步"]
    async with factory() as db:
        await update_profile(db, USER_A, ProfileUpdateRequest(self_intro="", personal_tags=[]))
    async with factory() as db:
        public = await get_profile(db, USER_A, public=True)
        assert not public["self_intro"] and public["personal_tags"] == []



@pytest.mark.asyncio
async def test_ab_public_visibility_and_removal_use_existing_policy(seeded):
    # 服务层真实库 A/B 证据；不等同于物理真机或真实登录端到端验收。
    from app.services.discovery import _target_rows
    from app.services.profile import recalculate_completion
    factory, revision = seeded
    async with factory() as db:
        await db.execute(text("UPDATE users SET avatar='https://example.invalid/s1.png', is_single_pledge=1 WHERE id=:uid"), {"uid": USER_A})
        await db.execute(text(
            "UPDATE user_profile SET occupation='technology', education_level=4, income=12000, "
            "height=175, weight=70, residence_province_code='330000', residence_city_code='330100', "
            "hometown_province_code='330000', hometown_city_code='330100', mbti='INFJ', "
            "self_intro=:intro, interest_tags=:tags WHERE user_id=:uid"
        ), {"uid": USER_A, "intro": INTRO, "tags": json.dumps(["跑步", "瑜伽", "骑行"], ensure_ascii=False)})
        await db.execute(text("INSERT INTO user_media (user_id, media_type, file_url, review_status) VALUES (:uid, 'photo', 'https://example.invalid/photo.png', 1)"), {"uid": USER_A})
        await db.execute(text("INSERT INTO user_partner_preference (user_id, age_min, age_max) VALUES (:uid, 25, 38)"), {"uid": USER_A})
        assert await recalculate_completion(db, USER_A) == 100
        await db.commit()
    async with factory() as db:
        await apply_profile_card_draft(db, USER_A, _body(revision), "s1-ab-key")
        await db.commit()
    async with factory() as db:
        assert USER_A in await _target_rows(db, USER_B, [USER_A])
        public = await get_profile(db, USER_A, public=True)
        assert public["self_intro"] == INTRO and "周末徒步" in public["personal_tags"]
    async with factory() as db:
        await db.execute(text("UPDATE user_privacy SET profile_visibility='only_me' WHERE user_id=:uid"), {"uid": USER_A})
        await db.commit()
    async with factory() as db:
        assert await _target_rows(db, USER_B, [USER_A]) == {}
    async with factory() as db:
        await db.execute(text("UPDATE user_privacy SET profile_visibility='all', who_can_see_me=2 WHERE user_id=:uid"), {"uid": USER_A})
        await db.execute(text("UPDATE user_auth SET realname_status=0 WHERE user_id=:uid"), {"uid": USER_B})
        await db.commit()
    async with factory() as db:
        assert await _target_rows(db, USER_B, [USER_A]) == {}
    async with factory() as db:
        await update_profile(db, USER_A, ProfileUpdateRequest(self_intro="", personal_tags=[]))
    async with factory() as db:
        public = await get_profile(db, USER_A, public=True)
        assert not public["self_intro"] and not public["personal_tags"]
        assert await _target_rows(db, USER_B, [USER_A]) == {}


# ---------------------------------------------------------------------------
# 海报公开导出：只允许当前 personal 正式版本上已采用的公开字段
# ---------------------------------------------------------------------------


async def _adopt(factory, revision: int, draft: str = DRAFT_A, key: str = "s1-export-key") -> None:
    async with factory() as db:
        await apply_profile_card_draft(db, USER_A, _body(revision, draft), key)
        await db.commit()


@pytest.mark.asyncio
async def test_export_returns_only_adopted_public_fields(seeded):
    from app.services.ai.profile_card_export import export_public_profile_card

    factory, revision = seeded
    await _adopt(factory, revision)
    async with factory() as db:
        payload = await export_public_profile_card(
            db, USER_A, subject=ProfileSubject.PERSONAL, revision_id=revision
        )
    assert payload["status"] == "ready"
    assert payload["subject"] == "personal"
    assert payload["revision_id"] == revision
    # 只导出已采用的公开介绍与标签，不含任何心理洞察/正文/原始对话。
    assert payload["public"]["self_intro"] == INTRO
    assert "周末徒步" in payload["public"]["persona_tags"]
    assert set(payload["public"]) == {"persona_title", "persona_tags", "self_intro"}


@pytest.mark.asyncio
async def test_export_rejects_stale_revision(seeded):
    from app.services.ai.profile_card_export import (
        ProfileCardExportStale,
        export_public_profile_card,
    )

    factory, revision = seeded
    await _adopt(factory, revision)
    async with factory() as db:
        with pytest.raises(ProfileCardExportStale):
            await export_public_profile_card(
                db, USER_A, subject=ProfileSubject.PERSONAL, revision_id=revision + 1
            )


@pytest.mark.asyncio
async def test_export_requires_confirmed_narrative(seeded):
    from app.services.ai.profile_card_export import (
        ProfileCardExportUnavailable,
        export_public_profile_card,
    )

    factory, revision = seeded
    await _adopt(factory, revision)
    async with factory() as db:
        await db.execute(
            text("UPDATE ai_profile_summary SET status='pending_confirmation' WHERE user_id=:uid"),
            {"uid": USER_A},
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(ProfileCardExportUnavailable):
            await export_public_profile_card(
                db, USER_A, subject=ProfileSubject.PERSONAL, revision_id=revision
            )


@pytest.mark.asyncio
async def test_export_rejects_ideal_partner_and_unadopted(seeded):
    from app.services.ai.profile_card_export import (
        ProfileCardExportUnavailable,
        export_public_profile_card,
    )

    factory, revision = seeded
    # 未采用任何资料卡草稿：没有公开字段可导出。
    async with factory() as db:
        with pytest.raises(ProfileCardExportUnavailable):
            await export_public_profile_card(
                db, USER_A, subject=ProfileSubject.PERSONAL, revision_id=revision
            )
    await _adopt(factory, revision)
    # ideal_partner 永不可作为海报来源。
    async with factory() as db:
        with pytest.raises(AIInputError):
            await export_public_profile_card(
                db, USER_A, subject=ProfileSubject.IDEAL_PARTNER, revision_id=revision
            )


@pytest.mark.asyncio
async def test_export_requires_active_consent(seeded):
    from app.services.ai.consents import revoke_consent
    from app.services.ai.profile_card_export import export_public_profile_card

    factory, revision = seeded
    await _adopt(factory, revision)
    async with factory() as db:
        vector = await profile._load_revision_vector(db, USER_A)
        await revoke_consent(db, USER_A, "profile_text_extract", "s1-export-revoke", vector.privacy)
        await db.commit()
    async with factory() as db:
        with pytest.raises(AIConsentRequired):
            await export_public_profile_card(
                db, USER_A, subject=ProfileSubject.PERSONAL, revision_id=revision
            )


@pytest.mark.asyncio
async def test_export_route_maps_errors_and_returns_public_payload(seeded):
    """走真实路由函数：确认 HTTP 层接线、响应模型与错误码映射。"""
    from app.api.routes.ai_profile_card import export_profile_card_route

    factory, revision = seeded
    await _adopt(factory, revision)
    me = CurrentUser(id=USER_A, session_id=1, phone=None, status=1, realname_status=2)
    async with factory() as db:
        response = await export_profile_card_route(
            revision_id=revision, current=me, db=db
        )
    assert response.status == "ready"
    assert response.subject == "personal"
    assert response.revision_id == revision
    assert response.public.self_intro == INTRO
    assert "周末徒步" in response.public.persona_tags

    # 落后版本 → 路由映射为 409 RESULT_STALE。
    async with factory() as db:
        with pytest.raises(HTTPException) as stale:
            await export_profile_card_route(
                revision_id=revision + 1, current=me, db=db
            )
    assert stale.value.status_code == 409
    assert stale.value.detail["code"] == "RESULT_STALE"

    # 未采用任何草稿 → 404 PROFILE_CARD_EXPORT_NOT_AVAILABLE。
    async with factory() as db:
        await db.execute(
            text("UPDATE ai_profile_card_draft SET status='ready', applied_meta=NULL WHERE user_id=:uid"),
            {"uid": USER_A},
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(HTTPException) as unavailable:
            await export_profile_card_route(
                revision_id=revision, current=me, db=db
            )
    assert unavailable.value.status_code == 404
    assert unavailable.value.detail["code"] == "PROFILE_CARD_EXPORT_NOT_AVAILABLE"
