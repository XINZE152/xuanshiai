import pytest
from fastapi import HTTPException
from types import SimpleNamespace
import json
from datetime import datetime, timezone

from app.services import ai_advisor
from app.schemas.ai_advisor import AdvisorAdviceRequest


class _Result:
    lastrowid = 9

    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row

    def one(self):
        return self.row


class _DB:
    def __init__(self, *, fail_commit=False, fail_rollback=False):
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.rollback_calls = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM ai_advisor_session" in sql:
            return _Result({"id": 1, "chat_session_id": None})
        if "FROM ai_advisor_message WHERE id" in sql:
            return _Result({"id": 9, "session_id": 1, "scenario": "reply",
                            "output_json": json.dumps({"analysis": "ok", "suggestions": [], "risk_level": "none"}),
                            "created_at": datetime.now(timezone.utc)})
        return _Result()

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("audit commit failed")

    async def rollback(self):
        self.rollback_calls += 1
        if self.fail_rollback:
            raise RuntimeError("rollback failed")


def _request() -> AdvisorAdviceRequest:
    return AdvisorAdviceRequest(scenario="reply", incoming_message="hello")


@pytest.fixture
def advisor_stubs(monkeypatch):
    monkeypatch.setattr(ai_advisor, "_require_vip", lambda *args: _ok())
    monkeypatch.setattr(ai_advisor, "_load_knowledge", lambda *args: _knowledge())
    monkeypatch.setattr(ai_advisor, "assert_text_allowed", lambda *args, **kwargs: _ok())
    monkeypatch.setattr(ai_advisor, "_consume_quota", lambda *args: _quota())
    monkeypatch.setattr(ai_advisor, "complete", lambda *args, **kwargs: _complete())
    monkeypatch.setattr(ai_advisor, "parse_json", lambda raw: {"analysis": "ok", "suggestions": [{"content": "hi", "style": "natural", "reason": "r"}], "risk_level": "none"})


async def _ok():
    return None


async def _knowledge():
    return []


async def _quota():
    return "quota-key"


async def _complete():
    return "{}"


@pytest.mark.asyncio
async def test_get_advice_success_audits_success(monkeypatch, advisor_stubs):
    statuses = []
    monkeypatch.setattr(ai_advisor, "_write_call_log", lambda *args, **kwargs: statuses.append(kwargs["status"]) or _ok())
    result = await ai_advisor.get_advice(_DB(), 1, 1, _request())
    assert result.id == 9
    assert statuses == ["success"]


@pytest.mark.asyncio
async def test_get_advice_risk_audits_blocked(monkeypatch, advisor_stubs):
    monkeypatch.setattr(ai_advisor, "parse_json", lambda raw: {"analysis": "验证码", "suggestions": [], "risk_level": "none"})
    statuses = []
    monkeypatch.setattr(ai_advisor, "_write_call_log", lambda *args, **kwargs: statuses.append(kwargs["status"]) or _ok())
    with pytest.raises(HTTPException):
        await ai_advisor.get_advice(_DB(), 1, 1, _request())
    assert statuses == ["blocked"]


@pytest.mark.asyncio
async def test_get_advice_provider_failure_stays_failed_when_refund_fails(monkeypatch, advisor_stubs):
    async def fail_complete(*args, **kwargs):
        raise RuntimeError("provider down")
    async def fail_refund(*args):
        raise RuntimeError("redis down")
    statuses = []
    monkeypatch.setattr(ai_advisor, "complete", fail_complete)
    monkeypatch.setattr(ai_advisor, "refund_daily", fail_refund)
    monkeypatch.setattr(ai_advisor, "_write_call_log", lambda *args, **kwargs: statuses.append(kwargs) or _ok())
    with pytest.raises(HTTPException) as caught:
        await ai_advisor.get_advice(_DB(), 1, 1, _request())
    assert caught.value.status_code == 503
    assert statuses[0]["status"] == "failed"
    assert statuses[0]["quota_refunded"] is False


@pytest.mark.asyncio
async def test_get_advice_audit_and_rollback_failures_do_not_replace_original(monkeypatch, advisor_stubs):
    async def fail_complete(*args, **kwargs):
        raise RuntimeError("provider original")
    async def fail_audit(*args, **kwargs):
        raise RuntimeError("audit write failed")
    monkeypatch.setattr(ai_advisor, "complete", fail_complete)
    monkeypatch.setattr(ai_advisor, "_write_call_log", fail_audit)
    with pytest.raises(HTTPException) as caught:
        await ai_advisor.get_advice(_DB(fail_rollback=True), 1, 1, _request())
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "provider original" in str(caught.value.__cause__)


def test_risk_policy_http_exception_is_blocked() -> None:
    exc = ai_advisor._AdvisorRiskBlocked()
    assert ai_advisor._status_for_advisor_exception(exc) == "blocked"


@pytest.mark.parametrize(
    "detail",
    ["AI军师服务暂时不可用", "provider timeout", "业务校验失败"],
)
def test_non_risk_http_exception_is_failed(detail: str) -> None:
    assert ai_advisor._status_for_advisor_exception(HTTPException(503, detail=detail)) == "failed"


def test_same_detail_without_risk_marker_is_failed() -> None:
    exc = HTTPException(422, detail="AI建议命中高风险规则，暂不返回")
    assert ai_advisor._status_for_advisor_exception(exc) == "failed"


@pytest.mark.asyncio
async def test_refund_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_refund(_: str) -> None:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(ai_advisor, "refund_daily", fail_refund)
    assert await ai_advisor._refund_quota_safely("advisor:quota") is False
