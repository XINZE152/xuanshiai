from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.schemas.ai import (
    AIProfilePolishRequest,
    AIProfileThoughtfulnessRequest,
    AISearchRequest,
)
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

    def first(self):
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

    with pytest.raises(HTTPException) as exc_info:
        await service.polish_profile(object(), 7, AIProfilePolishRequest(content="原文"))
    assert exc_info.value.detail["code"] == "AI_OUTPUT_INVALID"
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


class _ThoughtfulnessDb:
    """按 analyze_thoughtfulness 的查询顺序应答。

    顺序：资料行 → 相册照片数 → 上一次评审 → UPSERT → 回读结果行。
    """

    def __init__(self, result_row: dict | None = None) -> None:
        self.calls = 0
        self.committed = False
        self._result_row = result_row

    async def execute(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:  # 资料行
            return _Result(row=_PROFILE_ROW)
        if self.calls == 2:  # 相册照片数
            return _Result(scalar_value=0)
        if self.calls == 3:  # 上一次评审
            return _Result(row=None)
        if self.calls == 4:  # UPSERT 评审结果
            return _Result()
        if self.calls == 5:  # 回读结果行
            if self._result_row is None:
                raise AssertionError("回读结果行未预置")
            return _Result(row=self._result_row)
        raise AssertionError(f"unexpected execute call {self.calls}")

    async def commit(self):
        self.committed = True


_PROFILE_ROW = {
    "nickname": "测试用户",
    "gender": 1,
    "birthday": None,
    "is_married": 1,
    "avatar": "a.png",
    "height": 175,
    "weight": 68,
    "occupation": "工程师",
    "industry": None,
    "education_level": 3,
    "income": 20000,
    "residence_province_code": "110000",
    "residence_city_code": "110100",
    "residence_district_code": None,
    "hometown_province_code": None,
    "hometown_city_code": None,
    "hometown_district_code": None,
    "self_intro": "认真生活，也认真工作，喜欢运动和阅读。",
    "interest_tags": '["阅读"]',
    "personality_tags": '["温和"]',
    "mbti": "INFP",
    "tags": None,
    "qa_answers": None,
    "love_view": None,
    "ideal_partner": None,
    "hobbies": None,
    "family_background": None,
    "single_reason": None,
    "household": None,
    "house": None,
    "car": None,
    "smoking": None,
    "constellation": None,
    "zodiac": None,
}


@pytest.mark.asyncio
async def test_thoughtfulness_refunds_when_model_output_is_not_json(monkeypatch):
    """模型输出非法 JSON 时，用心度必须退还额度（与另外四个 AI 接口一致）。"""
    db = _ThoughtfulnessDb()
    refunded: list[str] = []

    async def complete_not_json(*_args, **_kwargs):
        return "好的，我分析完了：你的资料不错"  # 非 JSON

    monkeypatch.setattr(service, "complete", complete_not_json)
    monkeypatch.setattr(service, "_consume_ai_quota", _consumed("quota:thoughtfulness", refunded))
    monkeypatch.setattr(service, "refund_daily", lambda key: _record_refund(refunded, key))

    with pytest.raises(HTTPException) as exc_info:
        await service.analyze_thoughtfulness(
            db, 7, AIProfileThoughtfulnessRequest(trigger="manual")
        )
    assert exc_info.value.detail["code"] == "AI_OUTPUT_INVALID"

    assert refunded == ["quota:thoughtfulness"]
    assert db.committed is False


@pytest.mark.asyncio
async def test_thoughtfulness_keeps_quota_after_success(monkeypatch):
    """生成成功后不退款，避免「失败退、成功也退」导致额度等于没扣。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    db = _ThoughtfulnessDb(
        result_row={
            "score": 72,
            "summary": "资料框架完整，可以再补充具体场景。",
            "todos": json.dumps([], ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
        }
    )
    refunded: list[str] = []

    async def complete_valid(*_args, **_kwargs):
        return json.dumps(
            {
                "score": 72,
                "summary": "资料框架完整，可以再补充具体场景。",
                "todos": [
                    {
                        "key": "self_intro",
                        "label": "自我介绍",
                        "advice": "补充一个具体的生活场景。",
                        "priority": "medium",
                    }
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(service, "complete", complete_valid)
    monkeypatch.setattr(service, "_consume_ai_quota", _consumed("quota:thoughtfulness", refunded))
    monkeypatch.setattr(service, "refund_daily", lambda key: _record_refund(refunded, key))

    await service.analyze_thoughtfulness(
        db, 7, AIProfileThoughtfulnessRequest(trigger="manual")
    )

    assert refunded == []
    assert db.committed is True


@pytest.mark.asyncio
async def test_thoughtfulness_retries_once_when_first_output_is_not_json(monkeypatch):
    """首次非 JSON 时追加严格提示重试一次；第二次合法即成功且不退款。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    db = _ThoughtfulnessDb(
        result_row={
            "score": 66,
            "summary": "重试后成功的资料评审。",
            "todos": json.dumps([], ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
        }
    )
    refunded: list[str] = []
    scenes: list[str] = []

    async def complete_first_invalid_then_valid(*_args, **kwargs):
        scenes.append(str(kwargs.get("scene")))
        if len(scenes) == 1:
            return "好的，我分析完了：你的资料不错"
        return json.dumps({"score": 66, "summary": "重试后成功的资料评审。", "todos": []}, ensure_ascii=False)

    monkeypatch.setattr(service, "complete", complete_first_invalid_then_valid)
    monkeypatch.setattr(service, "_consume_ai_quota", _consumed("quota:thoughtfulness", refunded))
    monkeypatch.setattr(service, "refund_daily", lambda key: _record_refund(refunded, key))

    await service.analyze_thoughtfulness(
        db, 7, AIProfileThoughtfulnessRequest(trigger="manual")
    )

    assert scenes == ["thoughtfulness", "thoughtfulness_json_retry"]
    assert refunded == []
    assert db.committed is True


@pytest.mark.asyncio
async def test_polish_reports_mock_echo_and_unchanged(monkeypatch):
    monkeypatch.setattr(service.settings, "ai_enabled", False)
    monkeypatch.setattr(service.settings, "environment", "testing")
    monkeypatch.setattr(service.settings, "ai_allow_mock_fallback", True)

    async def complete_echo(*_args, **_kwargs):
        return json.dumps({"polished": "原文", "changed_points": []}, ensure_ascii=False)

    monkeypatch.setattr(service, "complete", complete_echo)
    monkeypatch.setattr(service, "_require_vip", _noop)
    monkeypatch.setattr(service, "_consume_ai_quota", _consumed("quota:polish", []))

    response = await service.polish_profile(
        object(), 7, AIProfilePolishRequest(content="原文")
    )

    assert response.changed is False
    assert response.fallback == "mock_echo"


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
