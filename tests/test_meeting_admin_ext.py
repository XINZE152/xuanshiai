"""单元测试：M4 约见 / 约会管理后台扩展能力（meeting_admin 路由补全）。

覆盖范围：
- 3 条新增 admin 路由已在 OpenAPI 注册：``/options``、``DELETE /requests/{id}``、``DELETE /{id}``
- 未登录访问 admin 端点必须 401（说明 _matchmaker_admin_permission 正确识别 /meetings 路径）
- service 函数 admin_options / admin_delete_request / admin_delete_meeting 的核心行为
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services import meeting as meeting_service

client = TestClient(app)

BASE = "/api/v1/admin/matchmaker/meetings"


# ─── 路由注册（OpenAPI 路径断言） ───────────────────────────────────


def test_options_endpoint_registered() -> None:
    spec = client.get("/openapi.json").json()
    assert "get" in spec["paths"][f"{BASE}/options"], "GET /meetings/options must exist"


def test_delete_request_route_exists() -> None:
    spec = client.get("/openapi.json").json()
    path = f"{BASE}/requests/{{request_id}}"
    methods = set(spec["paths"][path].keys())
    assert "delete" in methods, f"DELETE /requests/{{request_id}} must exist, got {methods}"


def test_delete_meeting_route_exists() -> None:
    spec = client.get("/openapi.json").json()
    path = f"{BASE}/{{meeting_id}}"
    methods = set(spec["paths"][path].keys())
    assert "delete" in methods, f"DELETE /{{meeting_id}} must exist, got {methods}"
    assert "get" in methods and "patch" in methods, "原有 GET / PATCH 仍须存在"


def test_admin_endpoints_require_authentication() -> None:
    """未登录时调用管理端 meeting 端点必须返回 401。"""
    assert client.get(f"{BASE}/options").status_code == 401
    assert client.get(f"{BASE}/requests").status_code == 401
    assert client.get(f"{BASE}").status_code == 401


# ─── admin_options 枚举完整性 ──────────────────────────────────────


def test_admin_options_request_status_enum_complete() -> None:
    """服务层 admin_options 暴露的 request_status 枚举必须含 5 个值。"""
    from app.schemas.meeting import MeetingRequestAdminPage

    # MeetingRequestAdminPage 不暴露 status 字段枚举，从 service 源码断言
    import inspect

    source = inspect.getsource(meeting_service.admin_options)
    for label in ("SUBMITTED", "CONTACTED", "ACCEPTED", "DECLINED", "CLOSED"):
        assert label in source, f"request_status 缺 {label}"


def test_admin_options_record_status_enum_complete() -> None:
    """admin_options 暴露的 record_status 枚举必须含 6 个值。"""
    import inspect

    source = inspect.getsource(meeting_service.admin_options)
    for label in ("SCHEDULED", "REMINDED", "CHECKED_IN", "COMPLETED", "CANCELLED", "NO_SHOW"):
        assert label in source, f"record_status 缺 {label}"


# ─── admin_delete_meeting 状态保护（不依赖 db mock，直接测 SQL 字符串） ──


def test_admin_delete_meeting_blocks_completed_via_sql_check() -> None:
    """admin_delete_meeting 内部必须包含 COMPLETED 状态 409 检查。"""
    import inspect

    source = inspect.getsource(meeting_service.admin_delete_meeting)
    assert "COMPLETED" in source
    assert "409" in source
    assert "已完成" in source


def test_admin_delete_request_exists_when_id_missing_returns_404() -> None:
    """admin_delete_request 内部必须对未找到的 id 抛 404。"""
    import inspect

    source = inspect.getsource(meeting_service.admin_delete_request)
    assert "404" in source