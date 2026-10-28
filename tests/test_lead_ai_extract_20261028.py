"""AI 智能录入（客源文本抽取）回归测试。

覆盖：
- 正常抽取：白名单字段回填、逐字段 confidence/snippets 过滤、notes 透传；
- 归一与防编造：字母 F→6、"男"→1、带空格/连字符手机号压缩、模型编造的额外键被丢弃；
- 配额：调用失败（AI 503）当日额度退还；超限 429 且不退还；
- 路由：/admin/customer-leads/ai-extract 静态路径先于 /{lead_id} 注册；
- 请求体：text 长度边界（空/超长）。

无真实 Redis/DB/provider：monkeypatch 服务的 complete 与 Redis 配额助手。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.api.routes import customer_leads_admin as lead_routes
from app.schemas.lead_ai_extract import LeadExtractRequest
from app.services import lead_ai_extract as service


def _patch_quota(monkeypatch: pytest.MonkeyPatch, *, allowed: bool = True) -> list[str]:
    """consume_daily 返回 allowed；记录被退还的 key。"""
    refunded: list[str] = []

    async def fake_consume(_key: str, _limit: int) -> bool:
        return allowed

    async def fake_refund(key: str) -> None:
        refunded.append(key)

    monkeypatch.setattr(service, "consume_daily", fake_consume)
    monkeypatch.setattr(service, "refund_daily", fake_refund)
    return refunded


def _patch_complete(monkeypatch: pytest.MonkeyPatch, payload: object) -> list:
    captured: list = []

    async def fake_complete(messages, *, json_mode=False, scene=""):
        captured.append({"messages": messages, "json_mode": json_mode, "scene": scene})
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(service, "complete", fake_complete)
    return captured


@pytest.mark.asyncio
async def test_extract_success_normalizes_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_quota(monkeypatch)
    captured = _patch_complete(
        monkeypatch,
        {
            "name": " 小李 ",
            "phone": "13800138000",
            "wechat": "",
            "source": "抖音",
            "intention_level": 6,
            "gender": 1,
            "remark": "周末方便",
            "confidence": {"name": 0.9, "phone": 1.5, "ghost": 0.8},
            "snippets": {"phone": "13800138000", "ghost": "编造"},
            "notes": ["意向按 F 档"],
            "salary": "20000",  # 非白名单键，必须丢弃
        },
    )

    result = await service.extract_lead_fields(1, LeadExtractRequest(text="小李，13800138000，抖音来的"))

    assert captured[0]["json_mode"] is True
    assert captured[0]["scene"] == "lead_extract"
    assert "LEAD_EXTRACT" in captured[0]["messages"][1]["content"]
    fields = result.fields
    assert fields.name == "小李"
    assert fields.phone == "13800138000"
    assert fields.wechat is None  # 空串归一为 None
    assert fields.source == "抖音"
    assert fields.intention_level == 6
    assert fields.gender == 1
    assert fields.remark == "周末方便"
    assert not hasattr(fields, "salary")  # 编造键不进入契约
    # confidence 仅保留命中白名单字段且裁剪到 0~1
    assert result.confidence == {"name": 0.9, "phone": 1.0}
    assert result.snippets == {"phone": "13800138000"}
    assert result.notes == ["意向按 F 档"]


@pytest.mark.asyncio
async def test_extract_letter_and_alias_coercion(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_quota(monkeypatch)
    _patch_complete(
        monkeypatch,
        {
            "intention_level": "F",
            "gender": "男",
            "phone": "+86 138-0013-8000",
        },
    )

    result = await service.extract_lead_fields(1, LeadExtractRequest(text="x"))

    assert result.fields.intention_level == 6
    assert result.fields.gender == 1
    assert result.fields.phone == "+8613800138000"  # 去空格/连字符
    assert result.fields.name is None


@pytest.mark.asyncio
async def test_extract_out_of_range_values_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_quota(monkeypatch)
    _patch_complete(
        monkeypatch,
        {"intention_level": 9, "gender": 5},
    )

    result = await service.extract_lead_fields(1, LeadExtractRequest(text="x"))

    assert result.fields.intention_level is None
    assert result.fields.gender is None


@pytest.mark.asyncio
async def test_extract_refunds_quota_when_ai_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    refunded = _patch_quota(monkeypatch)

    async def boom(*_a, **_k):
        raise HTTPException(503, detail="AI服务暂时不可用")

    monkeypatch.setattr(service, "complete", boom)

    with pytest.raises(HTTPException) as exc:
        await service.extract_lead_fields(1, LeadExtractRequest(text="x"))

    assert exc.value.status_code == 503
    assert len(refunded) == 1  # 失败退还当日额度


@pytest.mark.asyncio
async def test_extract_quota_exceeded_raises_429_without_refund(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refunded = _patch_quota(monkeypatch, allowed=False)

    async def never_called(*_a, **_k):  # 若被调用说明未拦截
        raise AssertionError("complete should not run when quota exhausted")

    monkeypatch.setattr(service, "complete", never_called)

    with pytest.raises(HTTPException) as exc:
        await service.extract_lead_fields(1, LeadExtractRequest(text="x"))

    assert exc.value.status_code == 429
    assert refunded == []  # 未消费成功不退


@pytest.mark.asyncio
async def test_extract_invalid_json_yields_503_and_refunds(monkeypatch: pytest.MonkeyPatch) -> None:
    refunded = _patch_quota(monkeypatch)

    async def not_json(*_a, **_k):
        return "模型这次没吐 JSON"

    monkeypatch.setattr(service, "complete", not_json)

    with pytest.raises(HTTPException) as exc:
        await service.extract_lead_fields(1, LeadExtractRequest(text="x"))

    assert exc.value.status_code == 503
    assert len(refunded) == 1


def test_ai_extract_route_registered_before_dynamic_lead_id() -> None:
    paths = [r.path for r in lead_routes.router.routes]
    assert "/admin/customer-leads/ai-extract" in paths
    dynamic_idx = paths.index("/admin/customer-leads/{lead_id}")
    assert paths.index("/admin/customer-leads/ai-extract") < dynamic_idx
    post_paths = {
        r.path for r in lead_routes.router.routes if "POST" in (r.methods or set())
    }
    assert "/admin/customer-leads/ai-extract" in post_paths


def test_extract_request_text_bounds() -> None:
    with pytest.raises(ValueError):
        LeadExtractRequest(text="")  # min_length=1
    with pytest.raises(ValueError):
        LeadExtractRequest(text="x" * 4001)  # max_length=4000
    ok = LeadExtractRequest(text="x" * 4000)
    assert ok.text == "x" * 4000


def test_mock_fallback_returns_empty_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """dev mock 回退分支：LEAD_EXTRACT 命中确定性空结果 + 提示，不抛错。"""
    from app.services import ai_provider

    raw = ai_provider._mock_response(
        [{"role": "user", "content": "LEAD_EXTRACT\n随便一段文本"}], json_mode=True
    )
    data = json.loads(raw)
    result = service._normalize(data)
    assert isinstance(result, service.LeadExtractResult)
    assert result.fields.name is None
    assert any("mock" in n for n in result.notes)
