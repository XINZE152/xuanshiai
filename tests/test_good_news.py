"""红娘喜讯接口与纯函数回归（无数据库依赖）。"""

from __future__ import annotations

import pathlib

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import app
from app.schemas.good_news import (
    GoodNewsBlessingResponse,
    GoodNewsReportCreate,
    GoodNewsReviewRequest,
)
from app.services import good_news as service

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_good_news_routes_registered() -> None:
    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/good-news",
        "/api/v1/good-news/config",
        "/api/v1/good-news/photo",
        "/api/v1/good-news/reports",
        "/api/v1/good-news/reports/mine",
        "/api/v1/good-news/reports/{report_id}",
        "/api/v1/good-news/{news_id}",
        "/api/v1/good-news/{news_id}/blessings",
        "/api/v1/good-news/{news_id}/consent/revoke",
        "/api/v1/matchmaker/workbench/good-news",
        "/api/v1/matchmaker/workbench/good-news/pennants",
        "/api/v1/matchmaker/workbench/good-news/photo",
        "/api/v1/admin/content/good_news",
        "/api/v1/admin/content/good_news/{news_id}",
        "/api/v1/admin/content/good_news/{news_id}/review",
        "/api/v1/admin/content/good_news/{news_id}/publish",
        "/api/v1/admin/content/good_news/{news_id}/unpublish",
        "/api/v1/admin/content/good_news/{news_id}/consents",
    }
    assert expected.issubset(set(paths))


def test_public_good_news_requires_login() -> None:
    spec = app.openapi()["paths"]["/api/v1/good-news"]["get"]
    assert spec.get("security")


def test_public_good_news_filters_review_public_and_consent() -> None:
    source = (ROOT / "app/services/good_news.py").read_text(encoding="utf-8")
    assert "m.review_status = 'APPROVED'" in source
    assert "m.public_status = 'PUBLISHED'" in source
    assert "cf.role = 'FROM'" in source
    assert "ct.role = 'TO'" in source


def test_admin_content_create_syncs_good_news_meta() -> None:
    source = (ROOT / "app/services/admin_content.py").read_text(encoding="utf-8")
    assert "INSERT INTO good_news_meta" in source
    assert "_sync_good_news_meta" in source


def test_workspace_report_checks_member_scope() -> None:
    source = (ROOT / "app/services/good_news.py").read_text(encoding="utf-8")
    assert "_ensure_workspace_member" in source
    assert "resource_assignment" in source


def test_public_item_hides_member_ids() -> None:
    row = {
        "id": 1,
        "extra_json": {
            "stage": "恋爱生活",
            "feedback_name": "张三",
            "partner_name": "李四",
            "from_avatar": "/a.webp",
            "to_avatar": "/b.webp",
        },
        "meta_stage": "恋爱生活",
        "meta_display_mode": "MASKED",
        "meta_review_status": "APPROVED",
        "meta_public_status": "PUBLISHED",
        "meta_from_user_id": 31,
        "meta_to_user_id": 42,
        "meta_matchmaker_id": 12,
        "meta_blessing_count": 0,
        "meta_verification_level": "MATCHMAKER_FOLLOWUP",
        "created_at": None,
        "meta_published_at": None,
    }
    item = service._item_from_row(row, public_view=True)
    assert item.from_user_id is None
    assert item.to_user_id is None
    assert item.from_name == "张*"


def test_mask_name_modes() -> None:
    assert service._mask_name("张三", "FULL") == "张三"
    assert service._mask_name("张三", "MASKED") == "张*"
    assert service._mask_name("张三", "ANONYMOUS") == "匿名会员"
    assert service._mask_name(None, "MASKED") is None


def test_format_duration_months() -> None:
    assert service._format_duration(None) is None
    assert service._format_duration(8) == "8 个月"
    assert service._format_duration(12) == "1 年"
    assert service._format_duration(14) == "1 年 2 个月"


def test_item_exposes_meet_duration_months() -> None:
    row = {
        "id": 9,
        "extra_json": {"stage": "已订婚", "content": "正文"},
        "meta_stage": "已订婚",
        "meta_display_mode": "MASKED",
        "meta_review_status": "APPROVED",
        "meta_public_status": "PUBLISHED",
        "meta_meet_duration_months": 8,
        "meta_blessing_count": 1,
        "meta_verification_level": "SELF_REPORTED",
        "created_at": None,
        "meta_published_at": None,
    }
    item = service._item_from_row(row, public_view=True)
    assert item.meet_duration_months == 8
    assert item.meet_duration_text == "8 个月"


def test_blessing_response_keeps_blessed_alias() -> None:
    response = GoodNewsBlessingResponse(
        news_id=1, blessing_count=2, blessed_by_me=True, blessed=True
    )
    assert response.blessed is True
    assert response.blessed_by_me is True


def test_legacy_consent_confirmed_maps_to_both_parties() -> None:
    body = GoodNewsReportCreate(
        stage="已订婚",
        content="我们已经订婚。",
        consent_confirmed=True,
    )
    assert body.consent_from is True
    assert body.consent_to is True


def test_explicit_consent_fields_override_legacy_alias() -> None:
    body = GoodNewsReportCreate(
        stage="已订婚",
        content="我们已经订婚。",
        consent_confirmed=True,
        consent_from=False,
        consent_to=False,
    )
    assert body.consent_from is False
    assert body.consent_to is False


def test_couple_key_is_stable_for_both_user_ids() -> None:
    assert service._couple_key(2, 1, "", 9) == "1:2"
    assert service._couple_key(1, 2, "", 9) == "1:2"
    assert service._couple_key(None, None, "王五", 9) == "submitter:9:王五"


def test_duration_parses_legacy_string() -> None:
    body = GoodNewsReportCreate(stage="恋爱生活", content="在一起了", meet_duration="8")
    assert service._duration_months(body) == 8


def test_review_requires_reason_for_reject_and_offline() -> None:
    with pytest.raises(ValidationError):
        GoodNewsReviewRequest(action="REJECT")
    with pytest.raises(ValidationError):
        GoodNewsReviewRequest(action="OFFLINE")
    assert GoodNewsReviewRequest(action="APPROVE").action == "APPROVE"


def test_report_rejects_blank_content() -> None:
    with pytest.raises(ValidationError):
        GoodNewsReportCreate(stage="牵手成功", content="   ")


def test_report_rejects_more_than_nine_images() -> None:
    with pytest.raises(ValidationError):
        GoodNewsReportCreate(
            stage="牵手成功",
            content="在一起了",
            images=[f"/storage/uploads/{index}.webp" for index in range(10)],
        )


def test_clean_images_rejects_non_storage_url() -> None:
    with pytest.raises(HTTPException) as error:
        service._clean_images(["javascript:alert(1)"])
    assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        service._clean_images(["https://external.example/tracker.webp"])
