from __future__ import annotations

import json

import httpx
import pytest
from fastapi import HTTPException

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
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
async def test_privacy_put_persists_both_switch_values_and_enqueues_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fields = (
        "hide_online_status", "anonymous_browse_enabled", "notify_like", "notify_comment",
        "notify_follow", "notify_message", "notify_match", "notify_apply", "notify_system",
        "notify_activity",
    )
    current = CurrentUser(id=7, session_id=9, phone=None, status=1, realname_status=0)

    class PrivacyRow:
        def __init__(self, row: dict[str, object]) -> None:
            self.row = row

        def mappings(self) -> PrivacyRow:
            return self

        def one(self) -> dict[str, object]:
            return self.row

        def first(self) -> dict[str, object]:
            return self.row

    class PrivacyDb:
        def __init__(self) -> None:
            self.pending: dict[str, int] = {}
            self.saved: dict[str, int] = {}
            self.order: list[str] = []
            self.revision = 0
            self.reject_outbox = False

        async def execute(self, statement: object, params: dict) -> PrivacyRow | None:
            sql = str(statement)
            if sql.startswith("INSERT INTO user_privacy"):
                assert params["user_id"] == current.id
                self.order.append("settings")
                self.pending = {field: params[field] for field in params if field != "user_id"}
                assert len(self.pending) == 1
                assert all(type(value) is int for value in self.pending.values())
            elif sql.startswith("INSERT INTO user_revision_state"):
                self.order.append("revision")
                self.revision += 1
            elif "FROM user_revision_state" in sql:
                return PrivacyRow(dict.fromkeys((
                    "profile_revision", "preference_revision", "relationship_revision", "policy_revision",
                ), 0) | {"privacy_revision": self.revision})
            elif sql.startswith("INSERT INTO derivation_outbox"):
                self.order.append("outbox")
                assert params["event_type"] == "privacy_updated"
                assert json.loads(params["changed_fields"]) == list(self.pending)
                if self.reject_outbox:
                    raise RuntimeError("simulated outbox write failure")
            elif sql.startswith("SELECT * FROM user_privacy"):
                return PrivacyRow({"user_id": current.id, **self.saved})
            else:
                pytest.fail(f"Unexpected SQL in settings flow: {sql}")
            return None

        async def commit(self) -> None:
            self.order.append("commit")
            self.saved.update(self.pending)

    db = PrivacyDb()
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: current)
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    path = "/api/v1/users/me/privacy"
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for field in fields:
            for checked in (True, False):
                db.order.clear()
                reply = await client.put(path, json={field: checked})
                assert reply.status_code == 200, reply.text
                assert reply.json()[field] is checked
                readback = await client.get(path)
                assert readback.json()[field] is checked
                assert db.order == ["settings", "revision", "outbox", "commit"]
        db.order.clear()
        db.reject_outbox = True
        reply = await client.put(path, json={"anonymous_browse_enabled": True})
        assert reply.status_code == 500
        assert db.order == ["settings", "revision", "outbox"]
        assert (await client.get(path)).json()["anonymous_browse_enabled"] is False


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
