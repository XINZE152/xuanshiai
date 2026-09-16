"""Live matchmaking R0 contracts and secure defaults."""

import hashlib
import hmac
import json
from pathlib import Path
import time

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
    assert "/api/v1/live/host/sessions/{session_id}/restrictions" in paths
    assert "/api/v1/live/host/sessions/{session_id}/operations" in paths
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
