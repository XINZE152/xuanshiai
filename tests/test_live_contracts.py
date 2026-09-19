"""Live matchmaking R0 contracts and secure defaults."""

import hashlib
import hmac
import json
import base64
from pathlib import Path
import time
import zlib

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, settings
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
    assert "/api/v1/live/host/sessions/{session_id}/restrictions" in paths
    assert "/api/v1/live/host/sessions/{session_id}/operations" in paths
    assert "/api/v1/live/sessions/{session_id}/stage/leave" in paths
    assert "/api/v1/live/sessions/{session_id}/rtc-ticket" in paths
    assert "/api/v1/live/sessions/{session_id}/ws-ticket" in paths
    assert "/api/v1/live/callbacks/tencent" not in paths


def test_tencent_ticket_fails_closed_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "live_enabled", False)
    with pytest.raises(LiveProviderUnavailable):
        generate_user_sig("1")


def test_tencent_user_sig_uses_sdk_secret_not_cam_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued_at = 1_700_000_000
    monkeypatch.setattr(settings, "live_enabled", True)
    monkeypatch.setattr(settings, "tencent_live_sdk_app_id", 123456)
    monkeypatch.setattr(
        settings, "tencent_live_sdk_secret_key", SecretStr("sdk-secret")
    )
    monkeypatch.setattr(settings, "tencent_live_secret_key", SecretStr("cam-secret"))
    monkeypatch.setattr(time, "time", lambda: issued_at)

    _, user_sig, _ = generate_user_sig("user-1")
    compressed = base64.b64decode(
        user_sig.replace("*", "+").replace("-", "/").replace("_", "=")
    )
    payload = json.loads(zlib.decompress(compressed))
    content = (
        "TLS.identifier:user-1\n"
        "TLS.sdkappid:123456\n"
        f"TLS.time:{issued_at}\n"
        f"TLS.expire:{settings.tencent_live_user_sig_ttl_seconds}\n"
    )
    expected = base64.b64encode(
        hmac.new(b"sdk-secret", content.encode(), hashlib.sha256).digest()
    ).decode()
    cam_signature = base64.b64encode(
        hmac.new(b"cam-secret", content.encode(), hashlib.sha256).digest()
    ).decode()

    assert payload["TLS.sig"] == expected
    assert payload["TLS.sig"] != cam_signature


def test_tencent_live_configuration_requires_distinct_sdk_secret() -> None:
    values = {
        "debug": True,
        "environment": "testing",
        "live_enabled": True,
        "live_provider": "tencent",
        "tencent_live_sdk_app_id": 123456,
        "tencent_live_secret_id": "cam-id",
        "tencent_live_secret_key": "cam-secret",
        "tencent_live_callback_secret": "callback-secret",
        "tencent_live_callback_event_types_raw": "ROOM_CLOSE",
    }
    with pytest.raises(ValidationError, match="SDKSecretKey"):
        Settings(_env_file=None, **values)

    configured = Settings(
        _env_file=None,
        tencent_live_sdk_secret_key="sdk-secret",
        **values,
    )
    assert configured.tencent_live_sdk_secret_key is not None
    assert configured.tencent_live_sdk_secret_key.get_secret_value() == "sdk-secret"


def test_tencent_callback_rejects_bad_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "tencent_live_callback_secret", SecretStr("test-secret"))
    monkeypatch.setattr(settings, "tencent_live_callback_event_types_raw", "ROOM_CLOSE")
    client = TestClient(app)
    timestamp = str(int(time.time()))
    response = client.post(
        "/api/v1/live/callbacks/tencent",
        content=json.dumps({"EventId": "event-1", "EventType": "ROOM_CLOSE"}),
        headers={
            "X-Tencent-Signature": "invalid",
            "X-Tencent-Timestamp": timestamp,
        },
    )
    assert response.status_code == 401


def test_tencent_callback_rejects_non_object_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "tencent_live_callback_secret", SecretStr("test-secret"))
    monkeypatch.setattr(settings, "tencent_live_callback_event_types_raw", "ROOM_CLOSE")
    client = TestClient(app)
    timestamp = str(int(time.time()))
    body = "[]"
    signature = hmac.new(
        b"test-secret", f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    response = client.post(
        "/api/v1/live/callbacks/tencent",
        content=body,
        headers={
            "X-Tencent-Signature": signature,
            "X-Tencent-Timestamp": timestamp,
        },
    )
    assert response.status_code == 422


def test_tencent_callback_signature_shape() -> None:
    body = json.dumps({"EventId": "event-1"}).encode()
    timestamp = b"1700000000"
    signature = hmac.new(
        b"test-secret", timestamp + b"." + body, hashlib.sha256
    ).hexdigest()
    assert len(signature) == 64


def test_live_reliability_schema_and_migration_contracts() -> None:
    from app.db.business_schema import BUSINESS_TABLES

    assert {
        "live_match_result",
        "live_participant_restriction",
        "live_outbox_event",
    }.issubset(BUSINESS_TABLES)
    assert "uk_live_active_user" in BUSINESS_TABLES["live_stage_seat"]
    root = Path(__file__).resolve().parents[1]
    up = (root / "migrations/live/20260916_01_live_reliability_up.sql").read_text(
        encoding="utf-8"
    )
    down = (
        root / "migrations/live/20260916_01_live_reliability_down.sql"
    ).read_text(encoding="utf-8")
    assert "live_outbox_event" in up
    assert "uk_live_active_user" in up
    assert "DROP TABLE IF EXISTS `live_outbox_event`" in down


def test_live_worker_has_bounded_operator_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1]
    worker = (root / "app/workers/live_event_worker.py").read_text(encoding="utf-8")
    assert '"--once"' in worker
    assert '"--batch-size"' in worker
    assert "claim_pending_events" in worker
