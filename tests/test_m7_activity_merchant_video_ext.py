"""单元测试：M7 活动报名 + 商家联盟 + 短视频。

覆盖范围：
- 新增路由注册（OpenAPI）：活动/报名、互选活动/记录、商家/分类/商品/订单、短视频/分类/评论/打赏/红包/会员主页
- 未登录访问上述 admin 端点均 401
- schema 层校验：活动起止时间、互选活动时间、商品字段、刷粉区间、评论修改、金额/枚举
- 服务层：金额与布尔序列化、标签/相册解析、视频文案 label 映射
- 配置域：``tools_active`` / ``tools_merchant_alliance`` / ``tools_short_video`` 字段与 UI 对齐
- 迁移：``_ensure_m7_columns`` 已定义并注册进主流程
"""

import inspect

from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.activity_admin import ActivityAdminCreate, ActivityAdminUpdate, ActivitySignupUpdate
from app.schemas.merchant_admin import (
    MerchantCategoryCreate,
    MerchantCreate,
    MerchantProductCreate,
    MerchantOrderStatusUpdate,
)
from app.schemas.mutual_selection_admin import MutualActivityCreate, MutualActivityUpdate
from app.schemas.short_video_admin import (
    ShortVideoCreate,
    ShortVideoUpdate,
    VideoBrushRequest,
    VideoCommentUpdate,
)
from app.services import merchant_admin as merchant_service
from app.services import short_video_admin as video_service
from app.services.admin_config import DEFAULT_CONFIGS

client = TestClient(app)

ACTIVITY_BASE = "/api/v1/admin/activities"
SIGNUP_BASE = "/api/v1/admin/activity-signups"
MUTUAL_BASE = "/api/v1/admin/mutual-activities"
MUTUAL_RECORD_BASE = "/api/v1/admin/mutual-records"
MERCHANT_BASE = "/api/v1/admin/merchants"
MERCHANT_CATEGORY_BASE = "/api/v1/admin/merchant-categories"
MERCHANT_PRODUCT_BASE = "/api/v1/admin/merchant-products"
MERCHANT_ORDER_BASE = "/api/v1/admin/merchant-orders"
VIDEO_BASE = "/api/v1/admin/short-videos"
VIDEO_CATEGORY_BASE = "/api/v1/admin/short-video-categories"
VIDEO_COMMENT_BASE = "/api/v1/admin/short-video-comments"
VIDEO_TIP_BASE = "/api/v1/admin/short-video-tips"
RED_PACKET_BASE = "/api/v1/admin/video-red-packets"
VIDEO_HOMEPAGE_BASE = "/api/v1/admin/short-video-homepages"


# ─── 路由注册 ────────────────────────────────────────────────────────


def test_activity_routes_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[ACTIVITY_BASE] and "post" in paths[ACTIVITY_BASE]
    assert "get" in paths[f"{ACTIVITY_BASE}/options"]
    detail = paths[f"{ACTIVITY_BASE}/{{activity_id}}"]
    assert "get" in detail and "patch" in detail and "delete" in detail
    assert "patch" in paths[f"{ACTIVITY_BASE}/{{activity_id}}/status"]
    assert "post" in paths[f"{ACTIVITY_BASE}/{{activity_id}}/copy"]
    assert "get" in paths[f"{ACTIVITY_BASE}/{{activity_id}}/signups"]
    assert "get" in paths[f"{ACTIVITY_BASE}/{{activity_id}}/link"]
    assert "get" in paths[f"{SIGNUP_BASE}/statistics"]
    assert "get" in paths[f"{SIGNUP_BASE}/options"]
    assert "get" in paths[f"{SIGNUP_BASE}/export"]
    assert "patch" in paths[f"{SIGNUP_BASE}/{{signup_id}}"]
    assert "delete" in paths[f"{SIGNUP_BASE}/{{signup_id}}"]


def test_mutual_routes_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[MUTUAL_BASE] and "post" in paths[MUTUAL_BASE]
    assert "patch" in paths[f"{MUTUAL_BASE}/{{activity_id}}"]
    assert "delete" in paths[f"{MUTUAL_BASE}/{{activity_id}}"]
    assert "post" in paths[f"{MUTUAL_BASE}/{{activity_id}}/copy"]
    assert "patch" in paths[f"{MUTUAL_BASE}/{{activity_id}}/visible"]
    assert "get" in paths[f"{MUTUAL_BASE}/{{activity_id}}/participants"]
    assert "post" in paths[f"{MUTUAL_BASE}/{{activity_id}}/participants"]
    assert "get" in paths[MUTUAL_RECORD_BASE]
    assert "get" in paths[f"{MUTUAL_RECORD_BASE}/options"]


def test_merchant_routes_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[MERCHANT_BASE] and "post" in paths[MERCHANT_BASE]
    assert "get" in paths[f"{MERCHANT_BASE}/options"]
    assert "put" in paths[f"{MERCHANT_BASE}/{{merchant_id}}"]
    assert "delete" in paths[f"{MERCHANT_BASE}/{{merchant_id}}"]
    assert "patch" in paths[f"{MERCHANT_BASE}/{{merchant_id}}/visible"]
    assert "get" in paths[MERCHANT_CATEGORY_BASE] and "post" in paths[MERCHANT_CATEGORY_BASE]
    assert "post" in paths[f"{MERCHANT_CATEGORY_BASE}/reorder"]
    assert "get" in paths[MERCHANT_PRODUCT_BASE] and "post" in paths[MERCHANT_PRODUCT_BASE]
    assert "patch" in paths[f"{MERCHANT_PRODUCT_BASE}/{{product_id}}/status"]
    assert "get" in paths[MERCHANT_ORDER_BASE]
    assert "get" in paths[f"{MERCHANT_ORDER_BASE}/export"]
    assert "patch" in paths[f"{MERCHANT_ORDER_BASE}/{{order_id}}"]


def test_video_routes_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[VIDEO_BASE] and "post" in paths[VIDEO_BASE]
    assert "post" in paths[f"{VIDEO_BASE}/brush"]
    assert "patch" in paths[f"{VIDEO_BASE}/{{video_id}}"]
    assert "delete" in paths[f"{VIDEO_BASE}/{{video_id}}"]
    assert "get" in paths[VIDEO_CATEGORY_BASE] and "post" in paths[VIDEO_CATEGORY_BASE]
    assert "get" in paths[VIDEO_COMMENT_BASE]
    assert "post" in paths[f"{VIDEO_COMMENT_BASE}/batch-delete"]
    assert "patch" in paths[f"{VIDEO_COMMENT_BASE}/{{comment_id}}"]
    assert "get" in paths[VIDEO_TIP_BASE]
    assert "get" in paths[RED_PACKET_BASE]
    assert "get" in paths[f"{RED_PACKET_BASE}/{{packet_id}}/claims"]
    assert "get" in paths[VIDEO_HOMEPAGE_BASE]
    assert "patch" in paths[f"{VIDEO_HOMEPAGE_BASE}/{{homepage_id}}"]


# ─── 未登录 401 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        ACTIVITY_BASE,
        MUTUAL_BASE,
        MERCHANT_BASE,
        MERCHANT_CATEGORY_BASE,
        MERCHANT_PRODUCT_BASE,
        MERCHANT_ORDER_BASE,
        VIDEO_BASE,
        VIDEO_CATEGORY_BASE,
        VIDEO_COMMENT_BASE,
        VIDEO_TIP_BASE,
        RED_PACKET_BASE,
        VIDEO_HOMEPAGE_BASE,
    ],
)
def test_m7_endpoints_require_login(url: str) -> None:
    assert client.get(url).status_code == 401


# ─── schema 校验 ─────────────────────────────────────────────────────


def _activity_payload(**overrides):
    from datetime import datetime, timedelta

    base = {
        "title": "7.26 一年内结婚专场",
        "start_time": datetime(2026, 7, 26, 14, 0),
        "end_time": datetime(2026, 7, 26, 17, 0),
    }
    base.update(overrides)
    return base


def test_activity_time_validation() -> None:
    from datetime import datetime

    with pytest.raises(ValidationError):
        ActivityAdminCreate(**_activity_payload(end_time=datetime(2026, 7, 26, 13, 0)))
    with pytest.raises(ValidationError):
        # 报名截止不得晚于开始时间
        ActivityAdminCreate(**_activity_payload(signup_deadline=datetime(2026, 7, 27, 14, 0)))
    ok = ActivityAdminCreate(**_activity_payload())
    assert ok.signup_mode == "anyone"
    assert ok.limit_mode == "gender"
    assert ok.fee_name == "报名费"


def test_activity_update_requires_field() -> None:
    with pytest.raises(ValidationError):
        ActivityAdminUpdate()
    assert ActivityAdminUpdate(online=False).online is False


def test_signup_update_requires_field() -> None:
    with pytest.raises(ValidationError):
        ActivitySignupUpdate()
    payload = ActivitySignupUpdate(checked_in=True, pay_status="paid", pay_amount=98)
    assert payload.checked_in is True and payload.pay_amount == 98


def test_mutual_activity_validation() -> None:
    from datetime import datetime

    with pytest.raises(ValidationError):
        MutualActivityCreate(title="6月互选开始啦", start_time=datetime(2026, 6, 30, 10), end_time=datetime(2026, 6, 30, 9))
    item = MutualActivityCreate(
        title="6月互选开始啦", start_time=datetime(2026, 6, 30, 9), end_time=datetime(2026, 6, 30, 23), pick_limit=5
    )
    assert item.success_mode == "show_wechat" and item.pick_limit == 5
    with pytest.raises(ValidationError):
        MutualActivityUpdate()


def test_merchant_schema_validation() -> None:
    with pytest.raises(ValidationError):
        MerchantCategoryCreate(name="")
    merchant = MerchantCreate(name="百年龙凤呈祥婚礼", tags=["鲜花", "婚庆"], gallery=["a.jpg"])
    assert merchant.visible is True and merchant.tags == ["鲜花", "婚庆"]
    with pytest.raises(ValidationError):
        MerchantProductCreate(merchant_id=0, name="商品")
    product = MerchantProductCreate(merchant_id=1, name="怦然心动套装", sale_price=88, promote_split_mode="by_level")
    assert product.promote_split_mode == "by_level" and product.buy_limit_mode == "account"
    with pytest.raises(ValidationError):
        MerchantOrderStatusUpdate(status="unknown")


def test_video_schema_validation() -> None:
    video = ShortVideoCreate(publisher_user_id=4, description="脱单干货", category_id=2)
    assert video.audit_status == "pending" and video.view_permission == "login"
    with pytest.raises(ValidationError):
        VideoBrushRequest(brush_type="views", min_value=999, max_value=100)
    with pytest.raises(ValidationError):
        ShortVideoUpdate()
    with pytest.raises(ValidationError):
        VideoCommentUpdate()


# ─── 服务层 ──────────────────────────────────────────────────────────


def test_money_and_bool_helpers() -> None:
    assert merchant_service._money("12.5") == "12.50"
    assert merchant_service._money(None) == "0.00"
    assert merchant_service._gallery('["a.jpg","b.jpg"]') == ["a.jpg", "b.jpg"]
    assert merchant_service._gallery(None) == []
    assert merchant_service._tags("鲜花,婚庆") == ["鲜花", "婚庆"]
    assert merchant_service._bool(1) is True and merchant_service._bool(0) is False


def test_video_label_mapping() -> None:
    row = {
        "id": 1,
        "publisher_user_id": 4,
        "publisher_nickname": "扒姐说媒",
        "duration_seconds": "126.36",
        "audit_status": "approved",
        "view_permission": "login",
        "link_type": "member",
        "tip_amount": "0.00",
    }
    item = video_service._video_item(row)
    assert item.duration_label == "126.36s"
    assert item.audit_label == "通过"
    assert item.view_permission_label == "必须先登录"
    assert item.link_label == "关联相亲资料"


def test_config_domains_match_ui() -> None:
    assert DEFAULT_CONFIGS["tools_active"][2]["column_name"] == "同城活动"
    assert "专场活动" in DEFAULT_CONFIGS["tools_active"][2]["categories"]
    merchant_cfg = DEFAULT_CONFIGS["tools_merchant_alliance"][2]
    assert merchant_cfg["title"] == "优选合作商城"
    assert merchant_cfg["share_cover_mode"] == "default"
    video_cfg = DEFAULT_CONFIGS["tools_short_video"][2]
    assert video_cfg["normal_post_review"] is True
    assert video_cfg["tip_min"] == 1 and video_cfg["red_packet_countdown"] == 10


def test_m7_activity_time_text_field() -> None:
    """活动 `time_text` 文本字段（UI 自由文本活动时间）应可写入并出现在出参。"""
    from app.schemas.activity_admin import (
        ActivityAdminCreate,
        ActivityAdminUpdate,
        ActivityAdminItem,
    )

    # 1) Create/Update 应接受 time_text，长度上限 128（与建表一致）
    payload = _activity_payload()
    payload["title"] = "夏季相亲游园会"
    payload["time_text"] = "2026 年 6 月 1 日 14:00-17:00（星期日）"
    create = ActivityAdminCreate(**payload)
    assert create.time_text == payload["time_text"]

    upd = ActivityAdminUpdate(**{"time_text": "本周末 14:00-17:00"})
    assert upd.time_text == "本周末 14:00-17:00"

    # 2) Item 出参默认 time_text=None，赋文案后保留
    item = ActivityAdminItem(
        id=1, title="x", cover=None, type=None, city=None, address=None,
        start_time=datetime(2026, 6, 1, 14, 0),
        end_time=datetime(2026, 6, 1, 17, 0),
        signup_deadline=None, max_people=10, current_people=0, price=99.0,
        status=1, description=None, created_by=None,
        created_at=datetime(2026, 1, 1),
        time_text="6 月 1 日 14:00",
    )
    assert item.time_text == "6 月 1 日 14:00"
    payload_item = item.model_dump()
    assert payload_item["time_text"] == "6 月 1 日 14:00"


def test_m7_migration_registered() -> None:
    import database_setup_marriage as setup

    assert hasattr(setup, "DatabaseManager")
    source = inspect.getsource(setup.DatabaseManager)
    assert "_ensure_m7_columns" in source
    assert "self._ensure_m7_columns(cursor)" in source
    assert "merchant_category" in source and "short_video_category" in source
    # time_text 列在迁移中
    assert "time_text" in source
