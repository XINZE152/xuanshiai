"""单元测试：会员资料媒体验证（M3-2）管理后台扩展能力。

覆盖范围：
- ``MemberIntroUpdate`` 入参校验（空串允许 / 超 500 报错）；
- ``MemberMediaReview`` 状态枚举校验（0/4 报错，1/2/3 通过）；
- ``MemberMediaReplace`` 文件地址必填且不可为空串；
- ``MemberIntroPage`` / ``MemberMediaPage`` 可构造；
- 全部新增路由已在 OpenAPI 注册（含动态 /media/{media_id} 与静态 /media/intros）。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.member_media_admin import (
    MemberIntroItem,
    MemberIntroPage,
    MemberIntroUpdate,
    MemberMediaItem,
    MemberMediaPage,
    MemberMediaReplace,
    MemberMediaReview,
)

client = TestClient(app)

BASE = "/api/v1/admin/members"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────


def test_new_member_media_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    # 静态路径（先于动态注册）
    assert "get" in paths[f"{BASE}/media/intros"]
    assert "put" in paths[f"{BASE}/media/intros/{{user_id}}"]
    assert "get" in paths[f"{BASE}/media"]
    assert "get" in paths[f"{BASE}/media/{{user_id}}/history"]
    # 动态路径 /media/{media_id}
    dynamic = f"{BASE}/media/{{media_id}}"
    assert "patch" in paths[dynamic]
    assert "put" in paths[dynamic]
    assert "delete" in paths[dynamic]


def test_new_member_media_routes_require_authentication() -> None:
    # 读接口需 matchmaker.member.read，未登录应 401
    assert client.get(f"{BASE}/media/intros").status_code == 401
    assert client.get(f"{BASE}/media?media_type=avatar").status_code == 401
    assert client.get(f"{BASE}/media/396140/history").status_code == 401
    # 写接口需 matchmaker.member.manage，未登录应 401
    assert client.put(f"{BASE}/media/intros/1", json={"self_intro": "x"}).status_code == 401
    assert client.patch(f"{BASE}/media/1", json={"review_status": 2}).status_code == 401
    assert client.put(f"{BASE}/media/1", json={"file_url": "/a.jpg"}).status_code == 401
    assert client.delete(f"{BASE}/media/1").status_code == 401


# ─── 入参模型校验 ───────────────────────────────────────────────


def test_intro_update_allows_empty_string() -> None:
    # 空串合法（清空自白），不限制最小长度
    body = MemberIntroUpdate(self_intro="")
    assert body.self_intro == ""


def test_intro_update_too_long() -> None:
    with pytest.raises(ValidationError):
        MemberIntroUpdate(self_intro="x" * 501)


def test_intro_update_max_length_ok() -> None:
    body = MemberIntroUpdate(self_intro="x" * 500)
    assert len(body.self_intro) == 500


def test_media_review_status_must_be_1_to_3() -> None:
    assert MemberMediaReview(review_status=1).review_status == 1
    assert MemberMediaReview(review_status=2).review_status == 2
    assert MemberMediaReview(review_status=3).review_status == 3
    with pytest.raises(ValidationError):
        MemberMediaReview(review_status=0)  # 0 审核中由上传自动置位，不可手动
    with pytest.raises(ValidationError):
        MemberMediaReview(review_status=4)


def test_media_replace_file_url_required_and_non_empty() -> None:
    # 必填
    with pytest.raises(ValidationError):
        MemberMediaReplace()
    # 空串报错（min_length=1）
    with pytest.raises(ValidationError):
        MemberMediaReplace(file_url="")
    # 合法
    body = MemberMediaReplace(file_url="/uploads/avatar/1.jpg")
    assert body.file_url == "/uploads/avatar/1.jpg"
    assert body.thumbnail_url is None


def test_media_replace_too_long() -> None:
    with pytest.raises(ValidationError):
        MemberMediaReplace(file_url="x" * 513)


# ─── 返回模型可构造 ─────────────────────────────────────────────


def test_intro_pages_construct() -> None:
    page = MemberIntroPage(
        items=[
            MemberIntroItem(
                id=1,
                user_id=1,
                member_code="G000001",
                nickname="测试",
                avatar=None,
                self_intro="你好",
                updated_at=None,
            )
        ],
        page=1,
        page_size=20,
        total=1,
        has_more=False,
    )
    assert len(page.items) == 1
    assert page.items[0].member_code == "G000001"
    assert page.items[0].self_intro == "你好"


def test_media_pages_construct() -> None:
    page = MemberMediaPage(
        items=[
            MemberMediaItem(
                id=10,
                user_id=2,
                member_code="G000002",
                nickname="会员B",
                avatar=None,
                media_type="avatar",
                file_url="/uploads/avatar/2.jpg",
                thumbnail_url=None,
                mime_type="image/jpeg",
                duration_seconds=None,
                review_status=0,
                review_status_label="审核中",
                review_reason=None,
                age=28,
                meta_text="1997年 175cm 本科",
                created_at=None,
            )
        ],
        page=1,
        page_size=20,
        total=1,
        has_more=False,
    )
    item = page.items[0]
    assert item.media_type == "avatar"
    assert item.review_status_label == "审核中"
    assert item.age == 28
    assert item.meta_text == "1997年 175cm 本科"
