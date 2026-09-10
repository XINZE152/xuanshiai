from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.schemas.ai import AISearchRequest, AIProfilePolishRequest
from app.services import ai_assistant as service


class _Result:
    def __init__(self, *, scalar_value=None, rows=(), row=None, lastrowid=1):
        self._scalar_value = scalar_value
        self._rows = rows
        self._row = row
        self.lastrowid = lastrowid

    def scalar(self):
        return self._scalar_value

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def one(self):
        return self._row


class _AssistantDb:
    def __init__(self):
        self.calls = 0
        self.rollback_count = 0
        self.commit_count = 0

    async def execute(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:  # membership
            return _Result(scalar_value=1)
        if self.calls == 2:  # session ownership
            return _Result(scalar_value=10)
        if self.calls == 3:  # chat context
            return _Result(rows=[])
        if self.calls == 4:  # user message insert
            return _Result(lastrowid=20)
        raise AssertionError(f"unexpected execute call {self.calls}")

    async def rollback(self):
        self.rollback_count += 1

    async def commit(self):
        self.commit_count += 1


@pytest.mark.asyncio
async def test_assistant_message_refunds_and_rolls_back_when_provider_fails(monkeypatch):
    db = _AssistantDb()
    refunded: list[str] = []

    async def fail_complete(*_args, **_kwargs):
        raise HTTPException(504, detail="provider timeout")

    monkeypatch.setattr(service, "complete", fail_complete)
    monkeypatch.setattr(service, "_require_vip", _noop)
    monkeypatch.setattr(service, "_consume_ai_quota", _consumed("quota:assistant", refunded))
    monkeypatch.setattr(service, "refund_daily", lambda key: _record_refund(refunded, key))

    with pytest.raises(HTTPException) as exc_info:
        await service.assistant_message(db, 7, 10, "请帮我回复")

    assert exc_info.value.status_code == 504
    assert db.rollback_count == 1
    assert db.commit_count == 0
    assert refunded == ["quota:assistant"]


@pytest.mark.asyncio
async def test_polish_profile_refunds_when_provider_payload_is_invalid(monkeypatch):
    refunded: list[str] = []

    async def complete_invalid(*_args, **_kwargs):
        return "not-json"

    monkeypatch.setattr(service, "complete", complete_invalid)
    monkeypatch.setattr(service, "_require_vip", _noop)
    monkeypatch.setattr(service, "_consume_ai_quota", _consumed("quota:polish", refunded))
    monkeypatch.setattr(service, "refund_daily", lambda key: _record_refund(refunded, key))

    with pytest.raises(HTTPException, match="AI返回格式无效"):
        await service.polish_profile(object(), 7, AIProfilePolishRequest(content="原文"))
    assert refunded == ["quota:polish"]


@pytest.mark.asyncio
async def test_parse_search_refunds_when_provider_fails(monkeypatch):
    refunded: list[str] = []

    async def fail_complete(*_args, **_kwargs):
        raise RuntimeError("upstream failed")

    monkeypatch.setattr(service, "complete", fail_complete)
    monkeypatch.setattr(service, "_require_vip", _noop)
    monkeypatch.setattr(service, "_consume_ai_quota", _consumed("quota:search", refunded))
    monkeypatch.setattr(service, "refund_daily", lambda key: _record_refund(refunded, key))

    with pytest.raises(RuntimeError, match="upstream failed"):
        await service.parse_search(object(), 7, AISearchRequest(query="找对象"))
    assert refunded == ["quota:search"]


@pytest.mark.asyncio
async def test_match_page_consumes_once_for_multiple_candidate_explanations(monkeypatch):
    consumed: list[tuple[str, int]] = []
    explanations = 0

    async def consume(_db, _user_id, code, _limit):
        consumed.append((code, _user_id))
        return "quota:match"

    async def complete_valid(*_args, **_kwargs):
        nonlocal explanations
        explanations += 1
        return json.dumps({"reason": "有共同兴趣", "suggestions": ["从兴趣开始聊天"]})

    async def viewer(*_args, **_kwargs):
        return {"interest_tags": "[\"阅读\"]", "mbti": "INFP", "residence_city_code": "1101"}

    async def rows(*_args, **_kwargs):
        return [
            {"user_id": 11, "nickname": "甲", "avatar": None, "interest_tags": "[\"阅读\"]", "mbti": "INFP", "residence_city_code": "1101", "last_active_at": datetime.now(UTC).replace(tzinfo=None)},
            {"user_id": 12, "nickname": "乙", "avatar": None, "interest_tags": "[\"旅行\"]", "mbti": "ENFP", "residence_city_code": "1102", "last_active_at": datetime.now(UTC).replace(tzinfo=None)},
        ]

    monkeypatch.setattr(service, "_require_vip", _noop)
    monkeypatch.setattr(service, "_consume_ai_quota", consume)
    monkeypatch.setattr(service, "complete", complete_valid)
    monkeypatch.setattr(service, "_viewer_context", viewer)
    monkeypatch.setattr(service, "_fetch_rows", rows)

    result = await service.match_page(object(), 7, "material", 1, 2)
    assert len(result.items) == 2
    assert consumed == [("match", 7)]
    assert explanations == 2


@pytest.mark.asyncio
async def test_refund_failure_does_not_mask_original_exception(monkeypatch):
    async def failing_refund(_key):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(service, "refund_daily", failing_refund)
    await service._refund_ai_quota_safely("quota:test")


async def _noop(*_args, **_kwargs):
    return None


def _consumed(key, _refunded):
    async def consume(*_args, **_kwargs):
        return key

    return consume


async def _record_refund(refunded, key):
    refunded.append(key)
