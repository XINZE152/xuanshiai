import pytest

from app.services.candidate_visibility import (
    CandidateContext,
    CandidateVisibilityService,
    ViewerContext,
    VisibilityScene,
    evaluate_visibility,
)


@pytest.mark.parametrize(
    ("who_can_see_me", "realname_status", "is_vip", "allowed"),
    [
        (1, 0, False, True),
        (2, 0, True, False),
        (2, 2, False, True),
        (3, 2, False, False),
        (3, 0, True, True),
        (4, 2, True, False),
    ],
)
def test_visibility_truth_table(
    who_can_see_me: int, realname_status: int, is_vip: bool, allowed: bool
) -> None:
    viewer = ViewerContext(
        user_id=1,
        realname_status=realname_status,
        is_vip=is_vip,
    )
    candidate = CandidateContext.visible(user_id=2, who_can_see_me=who_can_see_me)

    assert evaluate_visibility(viewer, candidate).allowed is allowed


def test_block_in_either_direction_is_fail_closed() -> None:
    viewer = ViewerContext(user_id=1, realname_status=2, is_vip=True)
    candidate = CandidateContext.visible(user_id=2, who_can_see_me=1, blocked=True)

    decision = evaluate_visibility(viewer, candidate)

    assert decision.allowed is False
    assert decision.denial_code == "BLOCKED_RELATIONSHIP"


@pytest.mark.parametrize(
    ("overrides", "denial_code"),
    [
        ({"account_active": False}, "ACCOUNT_INACTIVE"),
        ({"profile_visible": False}, "CANDIDATE_NOT_VISIBLE"),
        ({"match_active": False}, "MATCH_DISABLED"),
        ({"not_restricted": False}, "ACCOUNT_RESTRICTED"),
        ({"profile_complete": False}, "PROFILE_INCOMPLETE"),
        ({"media_approved": False}, "MEDIA_REVIEW_PENDING"),
    ],
)
def test_visibility_denies_inactive_or_unreviewed_candidates(
    overrides: dict[str, bool], denial_code: str
) -> None:
    viewer = ViewerContext(user_id=1, realname_status=2, is_vip=True)
    candidate = CandidateContext.visible(
        user_id=2,
        who_can_see_me=1,
        **overrides,
    )

    decision = evaluate_visibility(viewer, candidate)

    assert decision.allowed is False
    assert decision.denial_code == denial_code


def test_unknown_visibility_policy_is_fail_closed() -> None:
    viewer = ViewerContext(user_id=1, realname_status=None, is_vip=True)
    candidate = CandidateContext.visible(user_id=2, who_can_see_me=99)

    decision = evaluate_visibility(viewer, candidate)

    assert decision.allowed is False
    assert decision.denial_code == "UNKNOWN_VISIBILITY_POLICY"


def test_sql_predicate_rejects_unknown_visibility_policies() -> None:
    predicate = CandidateVisibilityService().predicate(
        ViewerContext(user_id=1, realname_status=2, is_vip=True),
        VisibilityScene.DISCOVERY,
    )

    assert "COALESCE(pr.who_can_see_me, 1) IN (1, 2, 3)" in predicate.clause


@pytest.mark.parametrize("candidate_alias", ["candidate.id", "1candidate", "u; DROP TABLE users"])
def test_sql_predicate_rejects_non_identifier_aliases(candidate_alias: str) -> None:
    with pytest.raises(ValueError, match="invalid SQL alias"):
        CandidateVisibilityService().predicate(
            ViewerContext(user_id=1, realname_status=2, is_vip=True),
            VisibilityScene.DISCOVERY,
            candidate_alias=candidate_alias,
        )


@pytest.mark.asyncio
async def test_decide_loads_current_candidate_facts_from_the_database() -> None:
    class MappingResult:
        def mappings(self) -> "MappingResult":
            return self

        def first(self) -> dict[str, int]:
            return {
                "candidate_id": 2,
                "viewer_realname_status": 2,
                "viewer_is_vip": 0,
                "who_can_see_me": 2,
                "account_active": 1,
                "profile_visible": 1,
                "match_active": 1,
                "not_restricted": 1,
                "profile_complete": 1,
                "media_approved": 1,
                "blocked": 0,
                "profile_visibility": "all",
                "paired_with_viewer": 0,
            }

    class RecordingDb:
        statement: object | None = None
        params: dict[str, int] | None = None

        async def execute(
            self, statement: object, params: dict[str, int]
        ) -> MappingResult:
            self.statement = statement
            self.params = params
            return MappingResult()

    db = RecordingDb()

    decision = await CandidateVisibilityService().decide(
        db,
        viewer_id=1,
        candidate_id=2,
        scene=VisibilityScene.PROFILE,
    )

    assert decision.allowed is True
    assert db.params == {
        "visibility_viewer_id": 1,
        "candidate_id": 2,
    }
    assert db.statement is not None
    assert "FROM users candidate" in str(db.statement)


@pytest.mark.asyncio
async def test_decide_denies_a_candidate_with_an_active_total_ban() -> None:
    class MappingResult:
        def mappings(self) -> "MappingResult":
            return self

        def first(self) -> dict[str, int]:
            return {
                "candidate_id": 2,
                "viewer_realname_status": 2,
                "viewer_is_vip": 1,
                "who_can_see_me": 1,
                "account_active": 1,
                "profile_visible": 1,
                "match_active": 1,
                "not_restricted": 0,
                "profile_complete": 1,
                "media_approved": 1,
                "blocked": 0,
                "profile_visibility": "all",
                "paired_with_viewer": 0,
            }
    class RecordingDb:
        async def execute(
            self, _: object, __: dict[str, int]
        ) -> MappingResult:
            return MappingResult()

    decision = await CandidateVisibilityService().decide(
        RecordingDb(),
        viewer_id=1,
        candidate_id=2,
        scene=VisibilityScene.PROFILE,
    )

    assert decision.allowed is False
    assert decision.denial_code == "ACCOUNT_RESTRICTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "allowed", "denial_code"),
    [
        ({}, True, None),
        ({"who_can_see_me": 2, "viewer_realname_status": 0}, False, "REALNAME_REQUIRED"),
        ({"who_can_see_me": 2, "viewer_realname_status": 1}, False, "REALNAME_REQUIRED"),
        ({"who_can_see_me": 2, "viewer_realname_status": 2}, True, None),
        ({"who_can_see_me": 3, "viewer_is_vip": 0}, False, "VIP_REQUIRED"),
        ({"who_can_see_me": 3, "viewer_is_vip": 1}, True, None),
        ({"who_can_see_me": 4}, False, "PRIVATE_PROFILE"),
        ({"who_can_see_me": 99}, False, "UNKNOWN_VISIBILITY_POLICY"),
        ({"blocked": 1}, False, "BLOCKED_RELATIONSHIP"),
        ({"account_active": 0}, False, "ACCOUNT_INACTIVE"),
        ({"not_restricted": 0}, False, "ACCOUNT_RESTRICTED"),
        ({"profile_visible": 0}, False, "CANDIDATE_NOT_VISIBLE"),
        ({"match_active": 0}, False, "MATCH_DISABLED"),
        ({"profile_complete": 0}, False, "PROFILE_INCOMPLETE"),
        ({"media_approved": 0}, False, "MEDIA_REVIEW_PENDING"),
    ],
)
async def test_decide_applies_the_full_database_fact_matrix(
    overrides: dict[str, int],
    allowed: bool,
    denial_code: str | None,
) -> None:
    class MappingResult:
        def mappings(self) -> "MappingResult":
            return self

        def first(self) -> dict[str, int]:
            return {
                "candidate_id": 2,
                "viewer_realname_status": 0,
                "viewer_is_vip": 0,
                "who_can_see_me": 1,
                "account_active": 1,
                "profile_visible": 1,
                "match_active": 1,
                "not_restricted": 1,
                "profile_complete": 1,
                "media_approved": 1,
                "blocked": 0,
                "profile_visibility": "all",
                "paired_with_viewer": 0,
                **overrides,
            }

    class RecordingDb:
        async def execute(
            self, _: object, __: dict[str, int]
        ) -> MappingResult:
            return MappingResult()

    decision = await CandidateVisibilityService().decide(
        RecordingDb(),
        viewer_id=1,
        candidate_id=2,
        scene=VisibilityScene.INTERACTION,
    )

    assert decision.allowed is allowed
    assert decision.denial_code == denial_code
    assert decision.scene is VisibilityScene.INTERACTION


@pytest.mark.parametrize(
    ("profile_visibility", "paired_with_viewer", "allowed", "denial_code"),
    [
        ("all", False, True, None),
        ("all", True, True, None),
        ("friends", True, True, None),
        ("friends", False, False, "PROFILE_VISIBILITY_FRIENDS_ONLY"),
        ("only_me", True, False, "PROFILE_VISIBILITY_ONLY_ME"),
        ("only_me", False, False, "PROFILE_VISIBILITY_ONLY_ME"),
        ("unexpected", True, False, "UNKNOWN_PROFILE_VISIBILITY"),
    ],
)
def test_profile_visibility_truth_table(
    profile_visibility: str,
    paired_with_viewer: bool,
    allowed: bool,
    denial_code: str | None,
) -> None:
    """profile_visibility 必须真实生效：friends 仅对已匹配可见，only_me 仅自己可见。"""
    viewer = ViewerContext(user_id=1, realname_status=2, is_vip=True)
    candidate = CandidateContext.visible(
        user_id=2,
        who_can_see_me=1,
        profile_visibility=profile_visibility,
        paired_with_viewer=paired_with_viewer,
    )

    decision = evaluate_visibility(viewer, candidate)

    assert decision.allowed is allowed
    assert decision.denial_code == denial_code


def test_profile_visibility_and_legacy_fields_do_not_widen_each_other() -> None:
    """新旧字段叠加取更严格限制：旧字段已隐藏时，profile_visibility=all 不能放开。"""
    viewer = ViewerContext(user_id=1, realname_status=2, is_vip=True)
    still_hidden = CandidateContext.visible(
        user_id=2,
        who_can_see_me=1,
        profile_visible=False,
        profile_visibility="all",
        paired_with_viewer=True,
    )
    hidden_decision = evaluate_visibility(viewer, still_hidden)
    assert hidden_decision.allowed is False
    assert hidden_decision.denial_code == "CANDIDATE_NOT_VISIBLE"

    private_candidate = CandidateContext.visible(
        user_id=2,
        who_can_see_me=4,
        profile_visibility="all",
        paired_with_viewer=True,
    )
    assert evaluate_visibility(viewer, private_candidate).denial_code == "PRIVATE_PROFILE"


def test_sql_predicate_enforces_profile_visibility() -> None:
    """共享谓词必须与对象判定同口径，避免只有前端隐藏而 API 仍返回数据。"""
    predicate = CandidateVisibilityService().predicate(
        ViewerContext(user_id=1, realname_status=2, is_vip=True),
        VisibilityScene.DISCOVERY,
    )

    assert "COALESCE(pr.profile_visibility, 'all') <> 'only_me'" in predicate.clause
    assert "COALESCE(pr.profile_visibility, 'all') IN ('all', 'friends', 'only_me')" in predicate.clause
    assert "user_match vm" in predicate.clause
    assert "vm.status IN (1, 2)" in predicate.clause
    assert predicate.clause.count("(") == predicate.clause.count(")")
