from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.main import app
from app.schemas.customer_lead_admin import CustomerLeadCreate, CustomerLeadUpdate
from app.schemas.matchmaker_workspace import (
    MatchmakerWorkspaceProfileUpdate,
    PromoterLeadCreate,
    WorkspaceLeadCreate,
    WorkspaceLeadUpdate,
    WorkspaceMemberReviewUpdate,
)
from app.services.customer_lead_contact import normalize_contact


client = TestClient(app)


def test_workspace_routes_are_registered_and_require_normal_user_authentication() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/v1/matchmaker/management-center/access",
        "/api/v1/matchmaker/management-center/dashboard",
        "/api/v1/matchmaker/workbench/members",
        "/api/v1/matchmaker/workbench/introductions",
        "/api/v1/matchmaker/workbench/meeting-requests",
        "/api/v1/matchmaker/workbench/members/{member_id}/contact",
        "/api/v1/matchmaker/partner-center",
        "/api/v1/matchmaker/promoter-center",
        "/api/v1/matchmaker/promoter-center/leads",
    }
    assert expected <= set(paths)
    assert client.get("/api/v1/matchmaker/management-center/access").status_code == 401
    assert paths["/api/v1/matchmaker/workbench/members"]["get"].get("security")


def test_workspace_schemas_enforce_non_payment_and_review_boundaries() -> None:
    assert MatchmakerWorkspaceProfileUpdate(display_name="林老师").display_name == "林老师"
    assert WorkspaceLeadCreate(name="王女士", phone="13800000000", source="线下活动").intention_level == 1
    assert PromoterLeadCreate(name="李先生", wechat="wxid_123", source="朋友介绍").name == "李先生"
    assert WorkspaceMemberReviewUpdate(status="PASSED").reason is None
    with pytest.raises(ValidationError):
        WorkspaceLeadCreate(name="王女士", source="线下活动")
    with pytest.raises(ValidationError):
        WorkspaceMemberReviewUpdate(status="REJECTED")


def test_lead_contact_schemas_normalize_and_still_require_a_contact() -> None:
    """联系方式唯一性口径：首尾空白归一化，空串视为未提供。"""
    assert WorkspaceLeadCreate(name="王女士", phone=" 13800000000 ", wechat="", source="线下活动").phone == "13800000000"
    assert WorkspaceLeadCreate(name="王女士", phone="", wechat=" wx_a ", source="线下活动").phone is None
    assert WorkspaceLeadUpdate(phone=" 13800000000 ").phone == "13800000000"
    assert PromoterLeadCreate(name="李先生", wechat=" wxid_123 ", source="朋友介绍").wechat == "wxid_123"
    assert CustomerLeadCreate(name="王女士", phone=" 13800000000 ", source="线下活动").phone == "13800000000"
    assert CustomerLeadUpdate(name="王女士", wechat="  ").wechat is None
    with pytest.raises(ValidationError):
        WorkspaceLeadCreate(name="王女士", phone="   ", wechat=None, source="线下活动")
    with pytest.raises(ValidationError):
        # 仅提交空白联系方式等价于没有任何修改项。
        CustomerLeadUpdate(wechat="  ")


def test_normalize_contact_treats_blank_as_missing() -> None:
    assert normalize_contact("  13800000000  ") == "13800000000"
    assert normalize_contact("   ") is None
    assert normalize_contact(None) is None
    assert normalize_contact(13800000000) == "13800000000"
