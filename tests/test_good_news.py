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
from app.services import admin_content as admin_service
from app.services import good_news as service

ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict | None:
        return dict(self._rows[0]) if self._rows else None

    def all(self) -> list[dict]:
        return [dict(row) for row in self._rows]


class _Db:
    def __init__(self, *results: _Result) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params: dict | None = None) -> _Result:
        self.calls.append((str(statement), dict(params or {})))
        return self.results.pop(0) if self.results else _Result()

    async def commit(self) -> None:
        return None


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
    assert item.from_avatar is None
    assert item.to_avatar is None


def test_mask_avatar_only_full_exposes_real_avatar() -> None:
    assert service._mask_avatar("/storage/a.webp", "FULL") == "/storage/a.webp"
    assert service._mask_avatar("/storage/a.webp", "MASKED") is None
    assert service._mask_avatar("/storage/a.webp", "ANONYMOUS") is None


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


def test_clean_images_enforces_owner_prefix() -> None:
    assert service._clean_images(
        ["/storage/uploads/good_news/7/a.webp"], owner_ids={7}
    ) == ["/storage/uploads/good_news/7/a.webp"]
    with pytest.raises(HTTPException) as error:
        service._clean_images(["/storage/uploads/good_news/8/a.webp"], owner_ids={7})
    assert error.value.status_code == 422


def test_public_where_checks_content_name_and_photo_scopes() -> None:
    source = (ROOT / "app/services/good_news.py").read_text(encoding="utf-8")
    assert "JSON_CONTAINS" in source
    assert "scopes_json" in source
    assert "JSON_QUOTE('content')" in source
    assert "JSON_QUOTE('name')" in source
    assert "JSON_QUOTE('photos')" in source


def test_can_revoke_consent_role_boundaries() -> None:
    assert service._can_revoke_consent(
        role="FROM", user_id=31, meta_from_user_id=31, meta_to_user_id=42, consent_user_id=31
    )
    assert service._can_revoke_consent(
        role="TO", user_id=42, meta_from_user_id=31, meta_to_user_id=42, consent_user_id=42
    )
    assert not service._can_revoke_consent(
        role="FROM", user_id=42, meta_from_user_id=31, meta_to_user_id=42, consent_user_id=31
    )
    assert not service._can_revoke_consent(
        role="TO", user_id=31, meta_from_user_id=31, meta_to_user_id=42, consent_user_id=42
    )
    assert not service._can_revoke_consent(
        role="FROM", user_id=31, meta_from_user_id=None, meta_to_user_id=42, consent_user_id=None
    )
    assert not service._can_revoke_consent(
        role="FROM", user_id=31, meta_from_user_id=31, meta_to_user_id=42, consent_user_id=99
    )


def test_member_report_party_ids_reject_arbitrary_user_and_require_name() -> None:
    with pytest.raises(HTTPException) as error:
        service._resolve_report_party_ids(
            submitter_id=7,
            submitter_role="MEMBER",
            body=GoodNewsReportCreate(stage="恋爱生活", content="在一起了", partner_name="王先生", to_user_id=42),
        )
    assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        service._resolve_report_party_ids(
            submitter_id=7,
            submitter_role="MEMBER",
            body=GoodNewsReportCreate(stage="恋爱生活", content="在一起了"),
        )
    assert service._resolve_report_party_ids(
        submitter_id=7,
        submitter_role="MEMBER",
        body=GoodNewsReportCreate(stage="恋爱生活", content="在一起了", partner_name="王先生"),
    ) == (7, None)


def test_matchmaker_report_party_ids_keep_explicit_ids() -> None:
    assert service._resolve_report_party_ids(
        submitter_id=12,
        submitter_role="MATCHMAKER",
        body=GoodNewsReportCreate(
            stage="已订婚",
            content="订婚了",
            partner_name="王先生",
            from_user_id=31,
            to_user_id=42,
        ),
    ) == (31, 42)


def test_couple_stage_pending_duplicate_guard_is_used() -> None:
    source = (ROOT / "app/services/good_news.py").read_text(encoding="utf-8")
    assert "m.couple_key = :couple_key" in source
    assert "r.status = 'PENDING'" in source
    schema = (ROOT / "app/db/business_schema.py").read_text(encoding="utf-8")
    assert "UNIQUE KEY `uk_good_news_meta_couple`" not in schema


def test_review_relevant_change_detects_content_and_metadata() -> None:
    old = {"content": "旧正文", "display_mode": "MASKED", "sort": 1}
    assert admin_service._good_news_review_changed(
        old,
        {"content": "新正文", "display_mode": "MASKED", "sort": 1},
        old_title="A & B",
        new_title="A & B",
        old_subtitle="红娘",
        new_subtitle="红娘",
        old_image_url="/a.webp",
        new_image_url="/a.webp",
        old_status=1,
        new_status=1,
    )
    assert admin_service._good_news_review_changed(
        old,
        {"content": "旧正文", "display_mode": "FULL", "sort": 1},
        old_title="A & B",
        new_title="A & B",
        old_subtitle="红娘",
        new_subtitle="红娘",
        old_image_url="/a.webp",
        new_image_url="/a.webp",
        old_status=1,
        new_status=1,
    )
    assert admin_service._good_news_review_changed(
        old,
        {"content": "旧正文", "display_mode": "MASKED", "sort": 1},
        old_title="A & B",
        new_title="A & C",
        old_subtitle="红娘",
        new_subtitle="红娘",
        old_image_url="/a.webp",
        new_image_url="/a.webp",
        old_status=1,
        new_status=1,
    )
    assert not admin_service._good_news_review_changed(
        old,
        {"content": "旧正文", "display_mode": "MASKED", "sort": 2},
        old_title="A & B",
        new_title="A & B",
        old_subtitle="红娘",
        new_subtitle="红娘",
        old_image_url="/a.webp",
        new_image_url="/a.webp",
        old_status=1,
        new_status=1,
    )


@pytest.mark.asyncio
async def test_reset_good_news_review_marks_pending_and_audits() -> None:
    db = _Db(
        _Result(rows=[{"review_status": "APPROVED", "public_status": "PUBLISHED"}]),
    )
    await admin_service._reset_good_news_review(
        db, news_id=5, admin_id=1, reason="内容变更，需重新审核"
    )
    sql = "\n".join(statement for statement, _ in db.calls)
    assert "review_status = 'PENDING'" in sql
    assert "public_status = :after_public" in sql
    assert db.calls[1][1]["after_public"] == "UNPUBLISHED"
    assert "UPDATE good_news_report SET status = 'PENDING'" in sql
    assert "good_news_audit" in sql
    assert "CONTENT_CHANGED" in sql


def test_record_consent_unpublishes_published_news() -> None:
    source = (ROOT / "app/services/good_news.py").read_text(encoding="utf-8")
    assert "CONSENT_CHANGED" in source
    assert "public_status = 'UNPUBLISHED'" in source


def test_admin_content_update_passes_admin_id_for_audit() -> None:
    source = (ROOT / "app/api/routes/admin_content.py").read_text(encoding="utf-8")
    assert "update_item(db, domain, item_id, body, current.account.id)" in source
