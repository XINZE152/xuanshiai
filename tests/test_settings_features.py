from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.main import app
from app.schemas.social import PrivacyResponse, PrivacyUpdateRequest
from app.services.app_version import _version_tuple, check_version


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _VersionDb:
    async def execute(self, statement: object, params: dict[str, object]) -> _Result:
        return _Result([
            {"version": "1.0.0", "is_force_update": 0, "download_url": None, "update_log": []},
            {"version": "1.0.1", "is_force_update": 1, "download_url": "https://example.com/update", "update_log": ["修复已知问题"]},
        ])


def test_privacy_schema_supports_visibility_and_message_permissions() -> None:
    request = PrivacyUpdateRequest(profile_visibility="friends", message_privacy="certified")
    assert (request.profile_visibility, request.message_privacy) == ("friends", "certified")

    response = PrivacyResponse(
        user_id=1, hide_phone=False, hide_school=False, hide_company=False, hide_distance=False,
        hide_online_status=False, only_auth_can_contact=False, only_vip_can_see_detail=False,
        who_can_see_me=1, match_status=1, anonymous_browse_enabled=False, show_profile=True,
        show_likes=True, show_posts=True, notify_like=True, notify_comment=True, notify_follow=True,
        notify_message=True, notify_match=True, notify_apply=True, notify_system=True,
        notify_activity=True,
    )
    assert (response.profile_visibility, response.message_privacy) == ("all", "all")


def test_privacy_put_and_patch_routes_are_registered() -> None:
    methods = app.openapi()["paths"]["/api/v1/users/me/privacy"]
    assert {"get", "put", "patch"}.issubset(methods)


@pytest.mark.asyncio
async def test_version_check_returns_latest_active_release() -> None:
    response = await check_version(_VersionDb(), "mp-weixin", "1.0.0")  # type: ignore[arg-type]
    assert response.model_dump() == {
        "platform": "mp-weixin", "latest_version": "1.0.1", "current_version": "1.0.0",
        "has_update": True, "is_force_update": True, "download_url": "https://example.com/update",
        "update_log": ["修复已知问题"],
    }


def test_version_validation_returns_contract_error() -> None:
    with pytest.raises(HTTPException) as exc:
        _version_tuple("1.x")
    assert exc.value.status_code == 400
