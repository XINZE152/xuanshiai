"""WP-P1b entry 条目链路真实库集成测试。

用真库验证三件事（fake 单测覆盖不到的持久化与 SQL 过滤语义）：
1. 抽取 handler 在网关注入 entry 结果后，草稿落出 field_kind='entry' 行
   （value_json 恒 NULL、正文在 content），structured 行不受影响；
2. entry 确认/编辑走真实 PATCH 事务并持久化（commit 后新会话重读成立）；
3. ``_load_field_keys`` 与发布门槛对 entry 的过滤防回归——entry 确认数
   不计入题目推进、进度与发布门槛（Global Constraint）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.schemas.ai_common import AiConsentGrantRequest
from app.services.ai.consents import grant_consent

POLICY_REVISION = "ai-policy-2026-08-07-v1"
CONSENT_VERSION = "profile-text-v1"
USER_EXTRACT = 9_880_000_101
USER_EDIT = 9_880_000_102


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


async def _clean(db: AsyncSession, user_id: int) -> None:
    for statement in (
        "DELETE FROM ai_profile_draft_field WHERE draft_id IN (SELECT draft_id FROM ai_profile_draft WHERE user_id = :user_id)",
        "DELETE FROM ai_profile_draft WHERE user_id = :user_id",
        "DELETE FROM ai_profile_turn WHERE user_id = :user_id",
        "DELETE FROM ai_profile_session WHERE user_id = :user_id",
        "DELETE FROM ai_profile_summary WHERE user_id = :user_id",
        "DELETE FROM ai_profile_revision_field WHERE revision_id IN (SELECT id FROM ai_profile_revision WHERE user_id = :user_id)",
        "DELETE FROM ai_profile_revision WHERE user_id = :user_id",
        "DELETE FROM ai_task WHERE owner_user_id = :user_id",
        "DELETE FROM ai_consent_operation WHERE user_id = :user_id",
        "DELETE FROM ai_consent_grant WHERE user_id = :user_id",
        "DELETE FROM derivation_outbox WHERE aggregate_id = :user_id",
        "DELETE FROM user_revision_state WHERE user_id = :user_id",
    ):
        await db.execute(text(statement), {"user_id": user_id})
    await db.commit()


class _FakeOutcome:
    def __init__(self, result: object) -> None:
        self.result = result
        self.error_code = None
        self.retryable = False


@pytest.mark.asyncio
async def test_real_projection_entry_digest_and_structured_only_null(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
) -> None:
    """发布含条目的 revision → 投影行 entry_digest 非空且带分类前缀；
    纯 structured 用户的投影 entry_digest 为 NULL。rollback-then-assert。"""
    from app.schemas.ai_common import ProjectionKind
    from app.services.ai.features import build_feature_projection

    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)
    user_entries = 9_880_000_201
    user_plain = 9_880_000_202
    await _clean(real_db_session, user_entries)
    await _clean(real_db_session, user_plain)
    zero_vector = (
        '{"profile": 0, "preference": 0, "privacy": 0, '
        '"relationship": 0, "policy": 0}'
    )

    async def _seed_user(db: AsyncSession, user_id: int, with_entries: bool) -> int:
        """Seed consent + revision(+fields)；返回 revision_id。"""
        await db.execute(
            text(
                "INSERT INTO user_revision_state "
                "(user_id, profile_revision, preference_revision, privacy_revision, "
                "relationship_revision, policy_revision) VALUES (:user_id, 0, 0, 0, 0, 0)"
            ),
            {"user_id": user_id},
        )
        await grant_consent(
            db,
            user_id,
            "profile_text_extract",
            AiConsentGrantRequest(
                consent_version=CONSENT_VERSION,
                policy_revision=POLICY_REVISION,
            ),
            f"digest-grant-{user_id}",
            0,
        )
        await db.execute(
            text(
                "INSERT INTO ai_profile_revision "
                "(user_id, subject, revision_no, draft_id, source_revision_json, "
                " policy_revision, published_by) "
                "VALUES (:user_id, 'personal', 1, NULL, :source_json, "
                " :policy_revision, :user_id)"
            ),
            {
                "user_id": user_id,
                "source_json": zero_vector,
                "policy_revision": POLICY_REVISION,
            },
        )
        row = (
            await db.execute(
                text(
                    "SELECT id FROM ai_profile_revision "
                    "WHERE user_id = :user_id AND subject = 'personal' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"user_id": user_id},
            )
        ).one()
        revision_id = int(row[0])
        fields = [
            ("height_cm", None, None, "175", '"175"'),
            ("city_code", None, None, "杭州", '"330100"'),
        ]
        if with_entries:
            fields.extend(
                [
                    ("entry_values_digest01", "values", "欣赏阳光开朗、品行端正的人", None, None),
                    ("entry_interests_digest02", "interests", "周末旅行与看展", None, None),
                ]
            )
        for field_key, category, content, display, value_json in fields:
            await db.execute(
                text(
                    "INSERT INTO ai_profile_revision_field "
                    "(revision_id, field_key, subject, field_kind, category, content, "
                    " value_json, display_value, confidence, content_hash) "
                    "VALUES (:revision_id, :field_key, 'personal', "
                    " :field_kind, :category, :content, :value_json, :display_value, "
                    " 0.9, :content_hash)"
                ),
                {
                    "revision_id": revision_id,
                    "field_key": field_key,
                    "field_kind": "entry" if category else "structured",
                    "category": category,
                    "content": content,
                    "value_json": value_json,
                    "display_value": display or content,
                    "content_hash": f"hash-{field_key}",
                },
            )
        return revision_id

    async with factory() as seed_db:
        await _seed_user(seed_db, user_entries, with_entries=True)
        await _seed_user(seed_db, user_plain, with_entries=False)
        await seed_db.commit()

    async with factory() as build_db:
        projection_a = await build_feature_projection(
            build_db, user_entries, ProjectionKind.PERSONAL_SEARCHABLE,
            revision_vector=None,
        )
        projection_b = await build_feature_projection(
            build_db, user_plain, ProjectionKind.PERSONAL_SEARCHABLE,
            revision_vector=None,
        )
        assert projection_a.id is not None
        await build_db.commit()

    async with factory() as check_db:
        digest_row = (
            await check_db.execute(
                text(
                    "SELECT entry_digest FROM ai_feature_projection "
                    "WHERE subject_user_id = :user_id AND status = 'active' "
                    "AND projection_kind = 'personal_searchable'"
                ),
                {"user_id": user_entries},
            )
        ).one()
        digest = digest_row[0]
        assert digest is not None
        assert "价值观：欣赏阳光开朗、品行端正的人" in digest
        assert "兴趣爱好：周末旅行与看展" in digest
        plain_row = (
            await check_db.execute(
                text(
                    "SELECT entry_digest FROM ai_feature_projection "
                    "WHERE subject_user_id = :user_id AND status = 'active' "
                    "AND projection_kind = 'personal_searchable'"
                ),
                {"user_id": user_plain},
            )
        ).one()
        assert plain_row[0] is None

    # 共享测试库纪律：清场。
    async with factory() as cleanup_db:
        await _clean(cleanup_db, user_entries)
        await _clean(cleanup_db, user_plain)


@pytest.mark.asyncio
async def test_real_published_fields_is_new_across_two_revisions(
    real_db_session: AsyncSession,
    real_db_engine: AsyncEngine,
) -> None:
    """WP-P4b 真库：两轮发布后第二轮新增条目 is_new=true 置顶，首轮条目
    is_new=false 且仍在（追加不覆盖）；structured 恒 False；投影物化
    first_seen_revision。"""
    from app.schemas.ai_common import ProjectionKind
    from app.services.ai.features import build_feature_projection
    from app.services.ai.profile import list_published_profile_fields

    user_id = 9_880_000_401
    await _clean(real_db_session, user_id)
    factory = async_sessionmaker(real_db_engine, expire_on_commit=False)

    async def _seed_revision(db, revision_no: int, entries: list[tuple[str, str]]) -> int:
        await db.execute(
            text(
                "INSERT INTO ai_profile_revision "
                "(user_id, subject, revision_no, draft_id, source_revision_json, "
                " policy_revision, published_by) "
                "VALUES (:user_id, 'personal', :revision_no, NULL, "
                " '{\"profile\": 1, \"preference\": 0, \"privacy\": 0, "
                "\"relationship\": 0, \"policy\": 0}', :policy_revision, :user_id)"
            ),
            {
                "user_id": user_id,
                "revision_no": revision_no,
                "policy_revision": POLICY_REVISION,
            },
        )
        revision_id = (
            await db.execute(
                text(
                    "SELECT id FROM ai_profile_revision "
                    "WHERE user_id = :user_id AND subject = 'personal' "
                    "AND revision_no = :revision_no LIMIT 1"
                ),
                {"user_id": user_id, "revision_no": revision_no},
            )
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO ai_profile_revision_field "
                "(revision_id, field_key, subject, field_kind, value_json, "
                " display_value, confidence, content_hash) "
                "VALUES (:revision_id, 'height_cm', 'personal', 'structured', "
                " '175', '175', 0.9, 'hash-height')"
            ),
            {"revision_id": revision_id},
        )
        for field_key, content in entries:
            await db.execute(
                text(
                    "INSERT INTO ai_profile_revision_field "
                    "(revision_id, field_key, subject, field_kind, category, content, "
                    " value_json, display_value, confidence, content_hash) "
                    "VALUES (:revision_id, :field_key, 'personal', 'entry', 'values', "
                    " :content, NULL, :content, 0.9, :content_hash)"
                ),
                {
                    "revision_id": revision_id,
                    "field_key": field_key,
                    "content": content,
                    "content_hash": f"hash-{field_key}-{revision_no}",
                },
            )
        return revision_id

    async with factory() as seed_db:
        await seed_db.execute(
            text(
                "INSERT INTO user_revision_state "
                "(user_id, profile_revision, preference_revision, privacy_revision, "
                "relationship_revision, policy_revision) VALUES (:user_id, 2, 0, 0, 0, 0)"
            ),
            {"user_id": user_id},
        )
        await grant_consent(
            seed_db,
            user_id,
            "profile_text_extract",
            AiConsentGrantRequest(
                consent_version=CONSENT_VERSION,
                policy_revision=POLICY_REVISION,
            ),
            f"new-grant-{user_id}",
            0,
        )
        await _seed_revision(seed_db, 1, [("entry_values_r1", "欣赏踏实上进的人")])
        # 真实 update 流程会把旧字段（含条目）以 confirmed 拷入新草稿再发布，
        # 因此第二轮 revision 同时包含旧条目与新条目。
        await _seed_revision(
            seed_db,
            2,
            [
                ("entry_values_r1", "欣赏踏实上进的人"),
                ("entry_interests_r2", "热爱艺术愿意看展"),
            ],
        )
        await seed_db.commit()

    async with factory() as read_db:
        fields = await list_published_profile_fields(read_db, user_id, "personal")
        by_key = {item["field_key"]: item for item in fields}
        # 第二轮新增条目：is_new=True。
        assert by_key["entry_interests_r2"]["is_new"] is True
        # 首轮条目：is_new=False 但仍在（追加不覆盖）。
        assert by_key["entry_values_r1"]["is_new"] is False
        assert by_key["entry_values_r1"]["content"] == "欣赏踏实上进的人"
        # structured 恒 False；New 条目排在最前。
        assert by_key["height_cm"]["is_new"] is False
        assert fields[0]["field_key"] == "entry_interests_r2"
        # 投影物化：isNew 群组最早来源 revision_no = 1。
        projection = await build_feature_projection(
            read_db, user_id, ProjectionKind.PERSONAL_SEARCHABLE, revision_vector=None
        )
        assert projection.id is not None
        await read_db.commit()
    async with factory() as check_db:
        first_seen = (
            await check_db.execute(
                text(
                    "SELECT first_seen_revision FROM ai_feature_projection "
                    "WHERE subject_user_id = :user_id AND status = 'active' "
                    "AND projection_kind = 'personal_searchable'"
                ),
                {"user_id": user_id},
            )
        ).scalar_one()
        assert first_seen == 1

    # 共享测试库纪律：清场（含 projection 入队外的行）。
    async with factory() as cleanup_db:
        await cleanup_db.execute(
            text("DELETE FROM ai_feature_projection WHERE subject_user_id = :user_id"),
            {"user_id": user_id},
        )
        await cleanup_db.commit()
        await _clean(cleanup_db, user_id)
