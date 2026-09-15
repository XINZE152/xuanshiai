import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.profile_tags import (
    TAG_CATEGORIES,
    TAG_CATALOG_REVISION,
    personal_tags,
    split_personal_tags,
)
from app.schemas.auth import ProfileUpdateRequest
from app.services import profile
from app.services.discovery import _all_tags, _card


def test_catalog_only_describes_the_person_and_has_unique_options():
    keys = {key for key, _, _ in TAG_CATEGORIES}
    assert not keys & {"ideal_partner", "relationship_expectation", "future_plan", "education", "occupation", "city", "marriage_status", "annual_income", "height"}
    options = [item for _, _, items in TAG_CATEGORIES for item in items]
    assert len(options) == len(set(options)) == len({option.strip().casefold() for option in options})
    assert all(12 <= len(group) <= 30 for _, _, group in TAG_CATEGORIES)
    assert all(label not in group for _, label, group in TAG_CATEGORIES)
    assert not set(options) & {
        "旅行", "音乐", "阅读", "电影", "游戏", "汽车文化",
        "健身", "喜欢宠物", "园艺", "自我提升", "看现场演出",
    }


@pytest.mark.parametrize("key,label,options", TAG_CATEGORIES)
def test_every_catalog_option_round_trips_through_new_and_legacy_contracts(key, label, options):
    for option in options:
        assert ProfileUpdateRequest(personal_tags=[option]).personal_tags == [option]
        interests, personalities = split_personal_tags([option])
        assert interests == ([] if key == "personality" else [option])
        assert personalities == ([option] if key == "personality" else [])
        legacy = ProfileUpdateRequest(interest_tags=interests, personality_tags=personalities)
        assert legacy.interest_tags == interests
        assert legacy.personality_tags == personalities


@pytest.mark.parametrize("tags", [[], ["科幻小说"], ["科幻小说", "有幽默感"], list(TAG_CATEGORIES[1][2][:10])])
def test_zero_to_ten_tags_are_saved_without_a_per_category_minimum(tags):
    assert ProfileUpdateRequest(personal_tags=tags).personal_tags == tags


def test_custom_tags_are_normalized_and_share_the_total_limit():
    request = ProfileUpdateRequest(
        personal_tags=["逛独立书店", "  手碟  ", "城市 骑行", "去看livehouse"],
        custom_tag_categories={"手碟": "music", "城市 骑行": "sports"},
    )
    assert request.personal_tags == ["逛独立书店", "手碟", "城市 骑行", "去看Livehouse"]
    assert request.custom_tag_categories == {"手碟": "music", "城市 骑行": "sports"}
    assert personal_tags(request.personal_tags, ["手碟", "城市 骑行"]) == request.personal_tags
    assert personal_tags(request.personal_tags) == ["逛独立书店", "去看Livehouse"]


@pytest.mark.parametrize("payload", [
    {"personal_tags": ["手碟"]},
    {"personal_tags": ["手碟"], "custom_tag_categories": {}},
    {"personal_tags": ["手碟"], "custom_tag_categories": {"手碟": "unknown"}},
    {"personal_tags": ["手碟"], "custom_tag_categories": {"别的标签": "music"}},
    {"personal_tags": ["民谣"], "custom_tag_categories": {"民谣": "music"}},
    {"custom_tag_categories": {"手碟": "music"}},
])
def test_custom_tag_category_mapping_is_required_and_exact(payload):
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(**payload)


@pytest.mark.parametrize("tags", [
    ["手碟", "观鸟", "木刻", "皮划艇"],
    ["1"], ["123456"], ["手碟!"], ["微信abc"], ["vxabc"],
    ["寻找长期伴侣"], ["本科"], ["真的非常非常非常喜欢手碟"],
    ["C++", "c++"],
])
def test_invalid_custom_tags_are_rejected(tags):
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(personal_tags=tags, custom_tag_categories={})


@pytest.mark.parametrize("payload", [
    {"personal_tags": None},
    {"personal_tags": ["科幻小说", "科幻小说"]},
    {"personal_tags": ["寻找长期伴侣"]},
    {"personal_tags": ["本科"]},
    {"personal_tags": list(TAG_CATEGORIES[1][2][:11])},
    {"personal_tags": [], "interest_tags": []},
    {"personal_tags": [], "personality_tags": None},
    {"personal_tags": [], "tag_selections": {}},
    {"interest_tags": ["有幽默感"]},
    {"personality_tags": ["健身"]},
    {"tag_selections": {"city": ["上海"]}},
])
def test_invalid_and_ambiguous_writes_are_rejected(payload):
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(**payload)


@pytest.mark.parametrize("category,tag", [
    ("arts_leisure", "Livehouse"), ("food_lifestyle", "咖啡"),
    ("games", "KTV"), ("pets", "养植物"),
])
def test_legacy_category_keys_still_accept_tags_that_moved_to_new_sections(category, tag):
    request = ProfileUpdateRequest(tag_selections={category: [tag]})
    assert request.tag_selections == {category: [tag]}


def test_legacy_read_deduplicates_and_reclassifies_without_inference():
    row = {"interest_tags": '["寻找长期伴侣","有幽默感","阅读"]',
           "personality_tags": '["阅读","温柔细心"]',
           "tags": {"city": ["上海"], "sports": ["健身"]}}
    values = profile._profile_tag_values(row)
    assert personal_tags(values) == ["有幽默感", "温柔细心"]
    assert split_personal_tags(values) == ([], ["有幽默感", "温柔细心"])
    assert row["tags"]["city"] == ["上海"]


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [[], ["科幻小说", "有幽默感"], list(TAG_CATEGORIES[1][2][:10]), ["熟人面前话多", "云吸猫", "周末煲汤", "去看Livehouse"]])
async def test_unified_write_uses_existing_columns_and_invalidates_existing_consumers(monkeypatch, selected):
    db = AsyncMock()
    monkeypatch.setattr(profile, "recalculate_completion", AsyncMock())
    monkeypatch.setattr(profile, "get_profile", AsyncMock(return_value={"personal_tags": selected}))
    revision = AsyncMock()
    monkeypatch.setattr(profile, "increment_revision_and_enqueue", revision)
    result = await profile.update_profile(db, 42, ProfileUpdateRequest(personal_tags=selected))
    statement, params = db.execute.call_args.args
    assert "personal_tags" not in str(statement)
    interests, personalities = split_personal_tags(selected)
    assert json.loads(params["interest_tags"]) == interests
    assert json.loads(params["personality_tags"]) == personalities
    assert set(item for items in json.loads(params["tags"]).values() for item in items) == set(selected)
    assert result["personal_tags"] == selected
    assert set(revision.call_args.args[3]) == {"interest_tags", "personality_tags", "tags"}
    db.commit.assert_awaited_once()


def test_combined_completion_keeps_total_weight():
    assert ("personal_tags", "兴趣标签", 8) in profile.COMPLETION_RULES
    assert sum(weight for _, _, weight in profile.COMPLETION_RULES) == 100
    assert not {key for key, _, _ in profile.COMPLETION_RULES} & {"interest", "personality"}


@pytest.mark.asyncio
async def test_catalog_version_and_limits():
    result = await profile.get_tag_options()
    assert result.version == "personal-v2"
    assert result.catalog_revision == TAG_CATALOG_REVISION
    assert result.catalog_revision == "2026-09-12.1"
    assert result.max_tags == 10
    assert result.recommended_min == 3
    assert result.custom.enabled is True
    assert result.custom.category_required is True
    assert (result.custom.max_tags, result.custom.min_length, result.custom.max_length) == (3, 2, 10)


@pytest.mark.asyncio
async def test_custom_write_is_moderated_and_persisted_in_explicit_group(monkeypatch):
    selected = ["逛独立书店", "手碟", "城市骑行"]
    db = AsyncMock()
    moderate = AsyncMock(return_value=MagicMock(action="allow"))
    monkeypatch.setattr(profile, "moderate_text", moderate)
    monkeypatch.setattr(profile, "recalculate_completion", AsyncMock())
    monkeypatch.setattr(profile, "get_profile", AsyncMock(return_value={"personal_tags": selected, "custom_tags": ["手碟", "城市骑行"]}))
    monkeypatch.setattr(profile, "increment_revision_and_enqueue", AsyncMock())

    await profile.update_profile(db, 42, ProfileUpdateRequest(
        personal_tags=selected,
        custom_tag_categories={"手碟": "music", "城市骑行": "sports"},
    ))

    _, params = db.execute.call_args.args
    assert json.loads(params["interest_tags"]) == selected
    assert json.loads(params["tags"])["custom"] == ["手碟", "城市骑行"]
    assert json.loads(params["tags"])["custom_categories"] == {"手碟": "music", "城市骑行": "sports"}
    assert [call.args[1] for call in moderate.await_args_list] == ["手碟", "城市骑行"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_custom_tag_is_not_written(monkeypatch):
    db = AsyncMock()
    monkeypatch.setattr(profile, "moderate_text", AsyncMock(return_value=MagicMock(action="reject")))
    with pytest.raises(Exception) as exc:
        await profile.update_profile(db, 42, ProfileUpdateRequest(
            personal_tags=["手碟"], custom_tag_categories={"手碟": "music"}
        ))
    assert getattr(exc.value, "status_code", None) == 422
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


def test_custom_tags_reach_discovery_only_with_explicit_storage_marker():
    marked = {"interest_tags": '["手碟"]', "personality_tags": "[]", "tags": {
        "custom": ["手碟"], "custom_categories": {"手碟": "music"}
    }}
    unmarked = {"interest_tags": '["手碟"]', "personality_tags": "[]", "tags": {}}
    assert _all_tags(marked) == {"手碟"}
    assert _all_tags(unmarked) == set()


def test_unclassified_development_custom_tags_are_dropped():
    row = {"interest_tags": '["手碟"]', "personality_tags": "[]", "tags": {"custom": ["手碟"]}}
    assert profile._profile_custom_tags(row) == []
    assert profile._profile_custom_tag_categories(row) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("public,locked", [(False, False), (True, False), (True, True)])
async def test_read_preserves_legacy_only_for_owner_and_respects_privacy(monkeypatch, public, locked):
    row = {"birthday": None, "interest_tags": '["寻找长期伴侣","阅读"]',
           "personality_tags": '["有幽默感","熟人面前话多"]', "tags": '{"city":["上海"],"pets":["云吸猫"]}',
           "hide_school": False, "hide_company": False, "only_vip_can_see_detail": locked}
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    db = AsyncMock()
    db.execute.return_value = result
    monkeypatch.setattr(profile, "_get_media", AsyncMock(return_value=[]))
    data = await profile.get_profile(db, 42, public=public)
    assert data["legacy_tags"] == []
    assert data["personal_tags"] == ([] if locked else ["有幽默感", "熟人面前话多", "云吸猫"])
    if locked:
        assert data["interest_tags"] == data["personality_tags"] == []
        assert data["tag_selections"] == {}
    db.commit.assert_not_awaited()
    assert db.execute.await_count == 1, "GET must not migrate stored data"


def test_new_tags_reach_discovery_and_recommendation_without_leaking_locked_details():
    row = {"user_id": 42, "interest_tags": '["去看Livehouse","云吸猫","本科"]',
           "personality_tags": '["熟人面前话多"]', "tags": {"food_lifestyle": ["周末煲汤"]}}
    assert _all_tags(row) == {"去看Livehouse", "云吸猫", "熟人面前话多", "周末煲汤"}
    card = _card(row, 50, "资料匹配")
    assert card.personal_tags == ["去看Livehouse", "云吸猫", "熟人面前话多"]
    assert card.interest_tags == ["去看Livehouse", "云吸猫"]
    locked = _card(row, 50, "资料匹配", detail_locked=True)
    assert locked.personal_tags == locked.interest_tags == []


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 2, 3, 10])
async def test_completion_threshold_counts_distinct_valid_tags_across_categories(count):
    fields = ("gender birthday is_married avatar is_single_pledge realname_status occupation "
              "education_level income height weight self_intro hometown_province_code hometown_city_code "
              "residence_province_code residence_city_code mbti preference_age_min preference_age_max album_done")
    row = dict.fromkeys(fields.split())
    selected = ["熟人面前话多", "云吸猫", "周末煲汤", "去看Livehouse", "练普拉提", "看日出", "拼图", "AI工具", "爵士", "做手账"][:count]
    interests, personalities = split_personal_tags(selected)
    row.update(interest_tags=interests, personality_tags=personalities,
               tags={"sports": ["本科", *interests]})
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    result.mappings.return_value.one.return_value = row
    db = AsyncMock()
    db.execute.return_value = result
    completion = await profile.get_completion(db, 42)
    assert completion.score == (8 if count >= 3 else 0)
    tag_item = next(item for item in completion.items if item.key == "personal_tags")
    assert tag_item.completed == (count >= 3)
    assert tag_item.weight == 8
