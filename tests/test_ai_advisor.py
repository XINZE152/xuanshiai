import json

import pytest
from pydantic import ValidationError

from app.api.routes import ai_advisor
from app.schemas.ai_advisor import AdvisorAdviceRequest, AdvisorSessionCreate
from app.services.ai_advisor import _normalize_result, _risk_level
from app.services.ai_provider import _mock_response


def test_advisor_routes_are_registered() -> None:
    paths = {route.path: route.methods for route in ai_advisor.router.routes}
    assert paths["/ai/advisor/sessions"] == {"GET"}
    assert paths["/ai/advisor/sessions/{session_id}"] == {"DELETE"}
    assert paths["/ai/advisor/sessions/{session_id}/advice"] == {"POST"}
    assert paths["/ai/advisor/messages/{message_id}/feedback"] == {"POST"}


def test_advisor_request_contracts() -> None:
    request = AdvisorAdviceRequest(
        scenario="reply",
        incoming_message="hello",
        include_history=True,
        chat_session_id=1,
    )
    assert request.max_suggestions == 3
    assert AdvisorSessionCreate().advisor_type == "relationship"
    with pytest.raises(ValidationError):
        AdvisorAdviceRequest(scenario="unknown", incoming_message="hello")
    with pytest.raises(ValidationError):
        AdvisorAdviceRequest(scenario="reply", incoming_message="hello", max_suggestions=4)


def test_advisor_mock_returns_structured_json() -> None:
    raw = _mock_response([{"role": "user", "content": "ADVISOR_ADVICE\nScenario: reply\nTone: natural"}], json_mode=True)
    data = json.loads(raw)
    assert data["suggestions"]
    assert data["risk_level"] == "none"




def test_advisor_mock_varies_by_scenario_and_tone() -> None:
    opening = json.loads(_mock_response([{"role": "user", "content": "ADVISOR_ADVICE\nScenario: opening\nTone: natural"}], json_mode=True))
    rescue = json.loads(_mock_response([{"role": "user", "content": "ADVISOR_ADVICE\nScenario: rescue\nTone: humorous"}], json_mode=True))
    mature_reply = json.loads(_mock_response([{"role": "user", "content": "ADVISOR_ADVICE\nScenario: reply\nTone: mature"}], json_mode=True))
    warm_reply = json.loads(_mock_response([{"role": "user", "content": "ADVISOR_ADVICE\nScenario: reply\nTone: warm"}], json_mode=True))
    natural_reply = json.loads(_mock_response([{"role": "user", "content": "ADVISOR_ADVICE\nScenario: reply\nTone: natural"}], json_mode=True))
    assert opening["suggestions"][0]["content"] != rescue["suggestions"][0]["content"]
    assert rescue["suggestions"][0]["style"] == "humorous"
    assert mature_reply["suggestions"][0]["style"] == "mature"
    assert warm_reply["suggestions"][0]["style"] == "warm"
    assert len({
        natural_reply["suggestions"][0]["content"],
        warm_reply["suggestions"][0]["content"],
        mature_reply["suggestions"][0]["content"],
    }) == 3

def test_advisor_result_normalization_limits_suggestions() -> None:
    request = AdvisorAdviceRequest(scenario="reply", incoming_message="hello", max_suggestions=1)
    data = _normalize_result({
        "analysis": "brief analysis",
        "suggestions": [
            {"content": "first", "style": "natural", "reason": "reason"},
            {"content": "second", "style": "warm", "reason": "reason"},
        ],
        "risk_level": "none",
    }, request)
    assert len(data["suggestions"]) == 1


def test_advisor_detects_high_risk_terms() -> None:
    assert _risk_level("\u8bf7\u628a\u9a8c\u8bc1\u7801\u53d1\u7ed9\u6211") == "high"
    assert _risk_level("ordinary conversation") == "none"


def test_advisor_detects_manipulative_terms() -> None:
    assert _risk_level("故意冷落他，让他后悔") == "medium"


def test_advisor_database_contract_contains_idempotency_and_audit() -> None:
    from pathlib import Path

    schema_text = Path("database_setup_marriage.py").read_text(encoding="utf-8")
    message_sql = schema_text
    audit_sql = schema_text
    assert "idempotency_key" in message_sql
    assert "uk_ai_advisor_message_idempotency" in message_sql
    assert "quota_refunded" in audit_sql
    assert "error_detail" in audit_sql


