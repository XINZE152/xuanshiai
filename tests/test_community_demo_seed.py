from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_community_demo_seed_is_development_only_and_contains_main_demo_data() -> None:
    from scripts.seed_community_demo import (
        DEMO_ACTIVITY_TITLES,
        DEMO_AVAILABLE_PLANE_CONTENTS,
        DEMO_POST_DECLARATIONS,
        DEMO_POST_CONTENTS,
        DEMO_PROFILE_PHONES,
        DEMO_TOPIC_NAMES,
        seed_community_demo,
    )

    assert len(DEMO_TOPIC_NAMES) >= 4
    assert len(DEMO_ACTIVITY_TITLES) >= 3
    assert len(DEMO_PROFILE_PHONES) >= 5
    assert len(DEMO_POST_CONTENTS) >= 8
    assert len(DEMO_AVAILABLE_PLANE_CONTENTS) >= 4
    assert set(DEMO_POST_DECLARATIONS) <= {
        "",
        "内容包含虚构演绎",
        "内容包含广告推广",
        "内容可能引起不适",
    }
    with pytest.raises(RuntimeError, match="development/testing"):
        seed_community_demo(connection=object(), environment="production")


def test_community_demo_seed_contains_profile_feed_comment_and_signup_writes() -> None:
    script = (ROOT / "scripts" / "seed_community_demo.py").read_text(encoding="utf-8")

    assert "INSERT INTO user_profile" in script
    assert "INSERT INTO community_post" in script
    assert "INSERT INTO community_comment" in script
    assert "INSERT INTO activity_signup" in script
    assert "ON DUPLICATE KEY UPDATE" in script


def test_paper_plane_message_response_exposes_viewer_message_ownership() -> None:
    from app.schemas.community import PaperPlaneMessageResponse

    assert "mine" in PaperPlaneMessageResponse.model_fields


def test_community_demo_seed_uses_idempotent_writes_and_keeps_paper_plane_conversation() -> None:
    script = (ROOT / "scripts" / "seed_community_demo.py").read_text(encoding="utf-8")

    assert "INSERT INTO community_topic" in script
    assert "ON DUPLICATE KEY UPDATE" in script
    assert "config_banner" in script
    assert "link_type" in script
    assert "paper_plane_conversation" in script
    assert "paper_plane_message" in script
    assert "expire_at" in script


def test_community_demo_seed_can_import_backend_modules_when_run_as_a_script() -> None:
    script = (ROOT / "scripts" / "seed_community_demo.py").read_text(encoding="utf-8")

    assert "sys.path.insert" in script


def test_member_seed_refuses_non_development_environments() -> None:
    from scripts.seed_community_members import seed_community_members

    with pytest.raises(RuntimeError, match="development/testing"):
        seed_community_members(connection=object(), environment="production")


def test_member_seed_roster_is_complete_and_balanced() -> None:
    """每个演示会员都要有足以打开 C 端门禁的完整资料。"""
    from scripts.seed_community_members import MEMBERS

    assert len(MEMBERS) >= 10
    assert len({member["phone"] for member in MEMBERS}) == len(MEMBERS)
    assert len({member["id_card"] for member in MEMBERS}) == len(MEMBERS)
    genders = {member["gender"] for member in MEMBERS}
    assert genders == {1, 2}
    for member in MEMBERS:
        assert len(member["id_card"]) == 18
        # 自我介绍需 ≥20 字，否则完整度「自我介绍」项不达标。
        assert len(member["intro"]) >= 20
        # 兴趣 + 性格标签需 ≥3 条。
        assert len(member["interest_tags"]) + len(member["personality_tags"]) >= 3
        assert member["age_range"][0] <= member["age_range"][1]
        assert member["height_range"][0] <= member["height_range"][1]


def test_member_seed_assets_resolve_to_frontend_files() -> None:
    from scripts.seed_community_members import MEMBERS, _asset_source

    for member in MEMBERS:
        for token in (member["avatar"], *member["photos"]):
            assert _asset_source(token).exists(), f"缺少素材 {token}"


def test_member_seed_uses_idempotent_writes_only() -> None:
    """脚本必须可重复执行且不删除既有数据。"""
    script = (ROOT / "scripts" / "seed_community_members.py").read_text(encoding="utf-8")

    assert "ON DUPLICATE KEY UPDATE" in script
    assert "INSERT IGNORE" in script
    # 删除仅允许清理本脚本自己生成的演示媒体。
    assert "DELETE FROM user_media" in script
    assert "DELETE FROM users" not in script
    assert "TRUNCATE" not in script
    assert "DROP TABLE" not in script
