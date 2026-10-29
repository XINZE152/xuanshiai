"""单元测试：客源跟进附件（文字+图片+录音）接口与纯函数逻辑（2026-10-29）。

对应前端「客源详情 → 跟进标签」上传附件缺口。覆盖范围：
- 路由已在 OpenAPI 注册（POST /admin/customer-leads/{lead_id}/follow-ups/media）；
- 未登录 401；
- _lead_fu_media_select 缺列降级 SELECT 片段拼接（先发版后迁移）；
- _build_lead_follow_up 对 images(JSON)/voice_duration_sec 的解析与缺列默认；
- CustomerLeadFollowUp 出参媒体字段默认值兼容旧数据（无附件列也不报错）。
"""

from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.customer_lead_admin import CustomerLeadFollowUp
from app.services.customer_lead_admin import (
    _build_lead_follow_up,
    _lead_fu_media_select,
)

client = TestClient(app)

BASE = "/api/v1/admin/customer-leads"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_media_follow_up_route_is_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths[f"{BASE}/{{lead_id}}/follow-ups/media"]


def test_media_follow_up_route_requires_authentication() -> None:
    response = client.post(
        f"{BASE}/1/follow-ups/media",
        data={"method": "PHONE", "content": "测试"},
    )
    assert response.status_code == 401


# ─── 缺列降级 SELECT 片段 ───────────────────────────────────────


def test_media_select_fragment_missing_columns_degrades() -> None:
    # 未迁移：三列都不存在 → 空片段，基础列不受影响。
    assert _lead_fu_media_select(frozenset()) == ""
    # 仅部分列存在 → 只拼存在的列，顺序稳定。
    assert _lead_fu_media_select(frozenset({"images"})) == ", images"
    assert (
        _lead_fu_media_select(frozenset({"images", "voice_url", "voice_duration_sec"}))
        == ", images, voice_url, voice_duration_sec"
    )


# ─── 行 -> 模型：images/voice 解析与缺列默认 ───────────────────


def _base_row() -> dict[str, object]:
    return {
        "id": 1, "lead_id": 9, "method": "PHONE", "content": "电话沟通",
        "intention_level": 3, "next_follow_at": None, "created_by": 1,
        "created_at": datetime(2026, 10, 29),
    }


def test_build_row_without_media_columns_uses_defaults() -> None:
    item = _build_lead_follow_up(_base_row())
    assert item.images == []
    assert item.voice_url is None
    assert item.voice_duration_sec is None


def test_build_row_parses_images_json_and_duration() -> None:
    row = {**_base_row(), "images": '["/storage/uploads/9/follow-up-img-a.webp"]',
           "voice_url": "/storage/uploads/9/follow-up-voice-b.mp3", "voice_duration_sec": 12}
    item = _build_lead_follow_up(row)
    assert item.images == ["/storage/uploads/9/follow-up-img-a.webp"]
    assert item.voice_url == "/storage/uploads/9/follow-up-voice-b.mp3"
    assert item.voice_duration_sec == 12


def test_lead_follow_up_schema_backward_compatible() -> None:
    # 旧数据/未迁移：仅基础字段即可构造，媒体字段取默认。
    item = CustomerLeadFollowUp(
        id=1, lead_id=9, method="WECHAT", content="微信跟进",
        intention_level=None, next_follow_at=None, created_by=1,
        created_at=datetime(2026, 10, 29),
    )
    assert item.images == [] and item.voice_url is None and item.voice_duration_sec is None
