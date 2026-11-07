from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.main import app
from app.api.dependencies import CurrentUser, get_current_user
from app.api.routes import matchmaker_workspace as workspace_routes
from app.db.session import get_db
from app.schemas.customer_lead_admin import CustomerLeadCreate, CustomerLeadUpdate
from app.schemas.matchmaker_workspace import (
    MatchmakerWorkspaceProfileUpdate,
    PromoterLeadCreate,
    WorkspaceLead,
    WorkspaceLeadCreate,
    WorkspaceLeadFollowUp,
    WorkspaceLeadFollowUpCreate,
    WorkspaceLeadUpdate,
    WorkspaceMemberReviewUpdate,
)
from app.services.customer_lead_contact import normalize_contact
from app.services.matchmaker_workspace import (
    WorkspaceActor,
    _LEAD_SELECT,
    _lead_from_row,
    require_review_authority,
    workspace_identity,
    workspace_role_name,
)


client = TestClient(app)


@pytest.mark.parametrize("level", range(1, 8))
def test_workspace_lead_levels_match_admin_and_preserve_stored_values(level: int) -> None:
    """等级 4 曾使整页 500；同库后台允许的七档必须原样读取和写入。"""
    row = {
        "id": 1, "name": "测试客源", "phone": None, "wechat": None,
        "source": "测试", "intention_level": level, "status": "NEW",
        "next_follow_at": None, "created_at": "2026-11-03T12:00:00",
        "updated_at": "2026-11-03T12:00:00",
    }
    assert _lead_from_row(row).intention_level == level
    payload = {"name": "测试客源", "phone": "13800000000", "source": "测试", "intention_level": level}
    assert CustomerLeadCreate(**payload).intention_level == WorkspaceLeadCreate(**payload).intention_level
    assert WorkspaceLeadUpdate(intention_level=level).intention_level == level
    assert WorkspaceLeadFollowUpCreate(method="PHONE", content="测试", intention_level=level).intention_level == level
    assert WorkspaceLeadFollowUp(
        id=1, lead_id=1, method="PHONE", content="测试", intention_level=level,
        next_follow_at=None, created_at="2026-11-03T12:00:00",
    ).intention_level == level


@pytest.mark.parametrize("level", [0, 8, -1])
def test_workspace_lead_rejects_levels_outside_admin_range(level: int) -> None:
    with pytest.raises(ValidationError):
        WorkspaceLeadUpdate(intention_level=level)
    with pytest.raises(ValidationError):
        WorkspaceLeadFollowUpCreate(method="PHONE", content="测试", intention_level=level)


def test_leads_http_list_serializes_all_seven_levels_without_local_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """走真实路由、列表服务及响应序列化；仅认证身份和 DB 查询结果由测试替身提供。"""
    rows = [{
        "id": level, "name": "测试客源", "phone": None, "wechat": None,
        "source": "测试", "intention_level": level, "status": "NEW",
        "next_follow_at": None, "created_at": "2026-11-03T12:00:00",
        "updated_at": "2026-11-03T12:00:00",
    } for level in range(1, 8)]
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))),
        scalar=AsyncMock(return_value=len(rows)),
    )
    actor = WorkspaceActor(user_id=21, display_name="测试红娘", level="NORMAL", organization_id=1)
    monkeypatch.setattr(workspace_routes, "require_workspace", AsyncMock(return_value=actor))
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=21, session_id=1, phone=None, status=1, realname_status=1,
    )
    try:
        response = client.get("/api/v1/matchmaker/workbench/leads?page=1&page_size=100")
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["intention_level"] for item in page["items"]] == list(range(1, 8))
    assert page["total"] == 7 and page["page_size"] == 100 and page["has_more"] is False
    assert db.execute.await_args.args[1]["scope_matchmaker_id"] == actor.user_id


def test_workspace_routes_are_registered_and_require_normal_user_authentication() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/v1/matchmaker/management-center/access",
        "/api/v1/matchmaker/management-center/dashboard",
        "/api/v1/matchmaker/workbench/identity",
        "/api/v1/matchmaker/workbench/members",
        "/api/v1/matchmaker/workbench/introductions",
        "/api/v1/matchmaker/workbench/meeting-requests",
        "/api/v1/matchmaker/workbench/commission-entries",
        "/api/v1/matchmaker/workbench/offline-income",
        "/api/v1/matchmaker/workbench/members/{member_id}/contact",
        "/api/v1/matchmaker/partner-center",
        "/api/v1/matchmaker/promoter-center",
        "/api/v1/matchmaker/promoter-center/leads",
    }
    assert expected <= set(paths)
    assert client.get("/api/v1/matchmaker/management-center/access").status_code == 401
    assert paths["/api/v1/matchmaker/workbench/members"]["get"].get("security")


def test_workspace_role_name_follows_level_and_store_binding() -> None:
    """角色徽标与数据范围同一口径：不挂门店的超级红娘是总店，挂门店的只在本店生效。"""
    headquarters = WorkspaceActor(user_id=2, display_name="红娘测试账号", level="SUPER", organization_id=None)
    store_super = WorkspaceActor(user_id=3, display_name="蛋希老师", level="SUPER", organization_id=1)
    ordinary = WorkspaceActor(user_id=21, display_name="钱多多", level="NORMAL", organization_id=1)

    assert workspace_role_name(headquarters) == "总店超级红娘"
    assert workspace_role_name(store_super) == "门店超级红娘"
    assert workspace_role_name(ordinary) == "服务红娘"
    identity = workspace_identity(headquarters)
    assert identity.role_name == "总店超级红娘"
    assert identity.name == "红娘测试账号"
    assert identity.avatar is None


def test_public_profile_review_is_limited_to_headquarters_matchmakers() -> None:
    """审核闸门：只有总店（SUPER）红娘能判定通过/驳回，普通红娘 403。"""
    headquarters = WorkspaceActor(user_id=2, display_name="总店红娘", level="SUPER", organization_id=None)
    ordinary = WorkspaceActor(user_id=21, display_name="普通红娘", level="NORMAL", organization_id=None)
    assert require_review_authority(headquarters) is headquarters
    with pytest.raises(HTTPException) as excinfo:
        require_review_authority(ordinary)
    assert excinfo.value.status_code == 403
    assert "总店" in excinfo.value.detail


def test_workspace_schemas_enforce_non_payment_and_review_boundaries() -> None:
    assert MatchmakerWorkspaceProfileUpdate(display_name="林老师").display_name == "林老师"
    assert WorkspaceLeadCreate(name="王女士", phone="13800000000", source="线下活动").intention_level == 1
    assert PromoterLeadCreate(name="李先生", phone="13800000000", wechat="wxid_123", source="朋友介绍").name == "李先生"
    assert WorkspaceMemberReviewUpdate(status="PASSED").reason is None
    with pytest.raises(ValidationError):
        WorkspaceLeadCreate(name="王女士", source="线下活动")
    with pytest.raises(ValidationError):
        PromoterLeadCreate(name="李先生", wechat="wxid_123", source="朋友介绍")
    with pytest.raises(ValidationError):
        WorkspaceMemberReviewUpdate(status="REJECTED")


def test_lead_contact_schemas_normalize_and_require_a_valid_phone() -> None:
    """录入线索口径：手机号必填且必须是 11 位有效号码；首尾空白归一化，空串视为未提供。"""
    assert WorkspaceLeadCreate(name="王女士", phone=" 13800000000 ", wechat="", source="线下活动").phone == "13800000000"
    assert WorkspaceLeadUpdate(phone=" 13800000000 ").phone == "13800000000"
    assert PromoterLeadCreate(name="李先生", phone="13800000000", wechat=" wxid_123 ", source="朋友介绍").wechat == "wxid_123"
    assert CustomerLeadCreate(name="王女士", phone=" 13800000000 ", source="线下活动").phone == "13800000000"
    assert CustomerLeadUpdate(name="王女士", wechat="  ").wechat is None
    # 后台表单预填的脱敏手机号（139****0101）视同「未修改」，整键丢弃而不是 422。
    assert CustomerLeadUpdate(name="王女士", phone="139****0101").model_dump(exclude_unset=True) == {"name": "王女士"}
    with pytest.raises(ValidationError):
        WorkspaceLeadCreate(name="王女士", phone="", wechat=" wx_a ", source="线下活动")
    with pytest.raises(ValidationError):
        WorkspaceLeadCreate(name="王女士", phone=" 138-0000-0000 ", source="线下活动")
    with pytest.raises(ValidationError):
        WorkspaceLeadCreate(name="王女士", phone="   ", wechat=None, source="线下活动")
    with pytest.raises(ValidationError):
        # 手机号只能改成有效号码，不能清空。
        WorkspaceLeadUpdate(phone="")
    with pytest.raises(ValidationError):
        CustomerLeadUpdate(phone=None)
    with pytest.raises(ValidationError):
        # 仅提交空白联系方式等价于没有任何修改项。
        CustomerLeadUpdate(wechat="  ")


def test_lead_update_accepts_the_source_channel_from_the_create_form() -> None:
    """后台「编辑客源」与录入同一套表单，来源必须可改；空白来源仍是无效输入。"""
    assert CustomerLeadUpdate(source="本地群").model_dump(exclude_unset=True) == {"source": "本地群"}
    with pytest.raises(ValidationError):
        CustomerLeadUpdate(source="")


def test_normalize_contact_treats_blank_as_missing() -> None:
    assert normalize_contact("  13800000000  ") == "13800000000"
    assert normalize_contact("   ") is None
    assert normalize_contact(None) is None
    assert normalize_contact(13800000000) == "13800000000"


def test_workspace_lead_carries_the_active_abandonment_and_stays_optional() -> None:
    """弃海池卡片要显示原因，所以列表/详情必须带出**当前生效**那条弃海记录。

    恢复过的客源会留下多条历史记录，取 restored_at IS NULL 的那条；两个字段都是可选，
    旧调用方与弃海动作自身的响应（操作前快照）都不会因此报错。
    """
    base = {
        "id": 23, "name": "周先生", "phone": "13800000000", "wechat": None, "source": "线下活动",
        "intention_level": 1, "status": "LOST", "next_follow_at": None,
        "created_at": "2026-10-01T09:00:00", "updated_at": "2026-10-28T10:02:49",
    }
    assert WorkspaceLead(**base).abandon_reason is None
    assert WorkspaceLead(**base).abandoned_at is None
    abandoned = WorkspaceLead(**{**base, "abandon_reason": "多次联系不上", "abandoned_at": "2026-10-28T18:02:49"})
    assert abandoned.abandon_reason == "多次联系不上"
    assert abandoned.abandoned_at is not None
    assert "WHERE restored_at IS NULL" in _LEAD_SELECT
    assert "ab.reason AS abandon_reason" in _LEAD_SELECT
    assert "ab.abandoned_at AS abandoned_at" in _LEAD_SELECT


def test_workspace_lead_names_the_recorder_and_stays_optional() -> None:
    """卡片要显示「录入人」，所以按 created_by 现拼红娘显示名（工作台档案优先，回落昵称）。"""
    base = {
        "id": 24, "name": "孙先生", "phone": "13800000000", "wechat": None, "source": "朋友介绍",
        "intention_level": 2, "status": "NEW", "next_follow_at": None,
        "created_at": "2026-10-20T09:00:00", "updated_at": "2026-10-20T09:00:00",
    }
    assert WorkspaceLead(**base).creator_name is None
    assert WorkspaceLead(**{**base, "creator_name": "林老师"}).creator_name == "林老师"
    assert "COALESCE(cp.display_name, cu.nickname) AS creator_name" in _LEAD_SELECT
    assert "cp.user_id = l.created_by AND cp.status = 1" in _LEAD_SELECT
    # 录入人是只读归属，不在可编辑字段里——改派走分派接口，换录入人只有捞取。
    assert "creator_name" not in WorkspaceLeadUpdate.model_fields
