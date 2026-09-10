"""Live matchmaking R0 contracts and secure defaults."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.live import INTERACTION_STATUS, TRANSITIONS
from app.services.live_tencent import LiveProviderUnavailable, generate_user_sig


def test_live_state_machine_is_forward_only() -> None:
    assert TRANSITIONS["CLOSED"] == set()
    assert TRANSITIONS["HEART_LIGHT"] == {"SELECT", "CLOSED"}
    assert INTERACTION_STATUS["CONFIRM"] == "DOUBLE_CONFIRM"


def test_live_routes_registered_in_openapi() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/live/sessions" in paths
    assert "/api/v1/live/host/sessions/{session_id}/transition" in paths
    assert "/api/v1/admin/live/sessions" in paths
    assert "/api/v1/admin/live/sessions/{session_id}/roles" in paths
    assert "/api/v1/live/host/sessions/{session_id}/stage/remove" in paths
    assert "/api/v1/live/sessions/{session_id}/stage/leave" in paths
    assert "/api/v1/live/sessions/{session_id}/rtc-ticket" in paths
    assert "/api/v1/live/callbacks/tencent" not in paths


def test_tencent_ticket_fails_closed_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "live_enabled", False)
    with pytest.raises(LiveProviderUnavailable):
        generate_user_sig("1")


def test_tencent_callback_rejects_bad_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "tencent_live_callback_secret", SecretStr("test-secret"))
    client = TestClient(app)
    response = client.post(
        "/api/v1/live/callbacks/tencent",
        content=json.dumps({"EventId": "event-1"}),
        headers={"X-Tencent-Signature": "invalid"},
    )
    assert response.status_code == 401


def test_tencent_callback_signature_shape() -> None:
    body = json.dumps({"EventId": "event-1"}).encode()
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    assert len(signature) == 64
