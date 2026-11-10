"""通知语义分类（2026-11-10，Q52）。

前端通知页只有评论/点赞/活动三个 Tab，匹配类通知没有入口。本文件锁定：

1. 每个已知 event_type 都映射到稳定的语义分类；
2. 未登记的新类型归入 ``system``，不会从列表消失；
3. ``category`` 筛选与未读汇总使用同一份映射；
4. ``system`` 采用排除式条件，新增类型自动落入。
"""

from __future__ import annotations

import pytest

from app.services.notifications import (
    NOTIFICATION_CATEGORIES,
    aggregate_unread_by_category,
    notification_category,
    notification_category_filter,
)


@pytest.mark.parametrize(
    ("event_type", "category"),
    [
        ("like", "like"),
        ("superlike", "like"),
        ("comment", "comment"),
        ("reply", "comment"),
        ("follow", "follow"),
        ("message", "message"),
        ("message_recalled", "message"),
        ("chat_session_request", "message"),
        ("match", "match"),
        ("match_application", "match"),
        ("match_application_accepted", "match"),
        ("match_application_rejected", "match"),
        ("activity", "activity"),
        ("community_post_remind", "activity"),
        ("system", "system"),
        ("report_result", "system"),
        ("community_moderation_result", "system"),
    ],
)
def test_known_event_types_map_to_category(event_type: str, category: str) -> None:
    assert notification_category(event_type) == category


def test_unknown_event_type_falls_back_to_system() -> None:
    assert notification_category("brand_new_event") == "system"
    assert notification_category(None) == "system"


def test_match_category_types() -> None:
    operator, types = notification_category_filter("match")
    assert operator == "IN"
    assert set(types) == {
        "match",
        "match_application",
        "match_application_accepted",
        "match_application_rejected",
        "apply",
    }


def test_system_category_is_open_ended() -> None:
    operator, types = notification_category_filter("system")
    assert operator == "NOT IN"
    assert "like" in types
    assert "match_application" in types
    assert "brand_new_event" not in types


def test_invalid_category_rejected() -> None:
    with pytest.raises(ValueError):
        notification_category_filter("unknown")


def test_unread_aggregation_covers_every_category() -> None:
    by_category = aggregate_unread_by_category(
        {
            "like": 2,
            "comment": 1,
            "match_application": 3,
            "community_post_remind": 1,
            "brand_new_event": 4,
        }
    )
    assert set(by_category) == set(NOTIFICATION_CATEGORIES)
    assert by_category["like"] == 2
    assert by_category["comment"] == 1
    assert by_category["match"] == 3
    assert by_category["activity"] == 1
    assert by_category["system"] == 4
    assert by_category["follow"] == 0
    assert by_category["message"] == 0


def test_unread_total_matches_type_sum() -> None:
    by_type = {"like": 2, "match_application": 3, "brand_new_event": 4}
    assert sum(aggregate_unread_by_category(by_type).values()) == sum(by_type.values())
