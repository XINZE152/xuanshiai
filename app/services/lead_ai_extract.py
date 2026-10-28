"""AI 智能录入（客源文本抽取）服务。

从红娘粘贴的一段自由文本中抽取结构化客源字段，**只回传解析结果供表单预填，
不写库**（红娘核对后再调既有创建接口）。

实现复用一期 AI 助手同款边界：
- 生成走 :func:`app.services.ai_provider.complete`（统一 Gateway/审计/mock 回退/门禁）；
- 解析走 :func:`app.services.ai_provider.parse_json`（容忍围栏/多余文本）；
- 每日限额沿用 Redis ``consume_daily``，调用失败即退还（与 polish/search 一致）。

安全：入参文本与模型原文都不写日志、不落库；手机号/微信等联系方式仅原样回传给
发起抽取的红娘本人，不进入任何审计或持久化。字段严格白名单，模型编造的额外键一律丢弃。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from app.core.config import settings
from app.core.redis import consume_daily, daily_quota_key, refund_daily
from app.schemas.lead_ai_extract import (
    LEAD_EXTRACT_FIELDS,
    LeadExtractRequest,
    LeadExtractResult,
    LeadExtractedFields,
)
from app.services.ai.audit import emit_ai_metric
from app.services.ai_provider import complete, parse_json

logger = logging.getLogger(__name__)

# 意向字母 A-G → 1-7（模型偶尔回字母而非数字时的兜底归一）。
_INTENTION_LETTER_TO_LEVEL = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7,
}
_GENDER_ALIASES = {
    "男": 1, "male": 1, "m": 1,
    "女": 2, "female": 2, "f": 2,
    "未知": 0, "unknown": 0,
}

_SYSTEM_PROMPT = (
    "你是婚恋红娘后台的客源信息录入助手。从用户提供的自由文本中抽取客源结构化字段，"
    "只输出 JSON，不要任何解释文字。"
    "顶层键只允许：name, phone, wechat, source, intention_level, gender, remark, "
    "confidence, snippets, notes。"
    "抽不到的字段填 null，绝不编造文本中不存在的信息。"
    "intention_level 输出 1-7 的整数（1 A未接 2 B初步沟通 3 C深入沟通未缔结 "
    "4 D待确定到店 5 E已确定到店 6 F预约需二邀 7 G已到店未签约），无法判断填 null。"
    "gender 输出 0 未知 / 1 男 / 2 女，无法判断填 null。"
    "confidence 为对象，键是已抽取字段名、值是 0~1 的小数（该字段的可信度）。"
    "snippets 为对象，键是已抽取字段名、值是从原文截取的支持片段（不超过 60 字）。"
    "notes 为字符串数组，列出歧义或缺失告警（如手机号位数不足），没有则空数组。"
)


def _clean_str(value: Any, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:max_len]


def _norm_phone(value: Any) -> str | None:
    """手机号归一：去空格/连字符，仅保留数字与前导 +，限长 32。"""
    cleaned = _clean_str(value, 64)
    if cleaned is None:
        return None
    compact = cleaned.replace(" ", "").replace("-", "").replace("\u200b", "")
    # 允许一个前导 +（国际号段），其余仅数字；含非法字符则整体放弃归一
    body = compact[1:] if compact.startswith("+") else compact
    if not body.isdigit():
        return None
    return compact[:32]


def _norm_intention(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 7 else None
    if isinstance(value, str):
        token = value.strip().upper()
        if not token:
            return None
        if token in _INTENTION_LETTER_TO_LEVEL:
            return _INTENTION_LETTER_TO_LEVEL[token]
        digits = "".join(ch for ch in token if ch.isdigit())
        if digits and 1 <= int(digits) <= 7:
            return int(digits)
    return None


def _norm_gender(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in (0, 1, 2) else None
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _GENDER_ALIASES:
            return _GENDER_ALIASES[token]
        if token.isdigit() and int(token) in (0, 1, 2):
            return int(token)
    return None


def _norm_confidence(raw: Any, kept_fields: set[str]) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, val in raw.items():
        if key not in kept_fields or isinstance(val, bool):
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        out[key] = round(max(0.0, min(1.0, num)), 4)
    return out


def _norm_snippets(raw: Any, kept_fields: set[str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key not in kept_fields:
            continue
        snippet = _clean_str(val, 120)
        if snippet:
            out[key] = snippet
    return out


def _norm_notes(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    notes: list[str] = []
    for item in raw:
        note = _clean_str(item, 200)
        if note:
            notes.append(note)
        if len(notes) >= 10:
            break
    return notes


def _normalize(data: dict[str, Any]) -> LeadExtractResult:
    """把模型 JSON 收敛为契约结果：仅白名单字段、逐字段类型归一、丢弃编造键。"""
    coercers: dict[str, Any] = {
        "name": lambda v: _clean_str(v, 128),
        "phone": _norm_phone,
        "wechat": lambda v: _clean_str(v, 128),
        "source": lambda v: _clean_str(v, 64),
        "intention_level": _norm_intention,
        "gender": _norm_gender,
        "remark": lambda v: _clean_str(v, 2000),
    }
    values: dict[str, Any] = {}
    for field in LEAD_EXTRACT_FIELDS:
        values[field] = coercers[field](data.get(field))
    kept_fields = {f for f in LEAD_EXTRACT_FIELDS if values[f] is not None}
    fields = LeadExtractedFields(**values)
    return LeadExtractResult(
        fields=fields,
        confidence=_norm_confidence(data.get("confidence"), kept_fields),
        snippets=_norm_snippets(data.get("snippets"), kept_fields),
        notes=_norm_notes(data.get("notes")),
    )


async def _consume_quota(account_id: int) -> str:
    key = daily_quota_key("ai:lead_extract", account_id)
    if not await consume_daily(key, settings.ai_daily_lead_extract_limit):
        raise HTTPException(429, detail="今日 AI 智能录入次数已用完")
    return key


async def _refund_quota_safely(quota_key: str | None) -> None:
    if not quota_key:
        return
    try:
        await refund_daily(quota_key)
    except Exception:
        emit_ai_metric("quota_refund_failure", 1)
        logger.exception("Failed to refund lead-extract quota", extra={"quota_key": quota_key})


async def extract_lead_fields(
    account_id: int, request: LeadExtractRequest
) -> LeadExtractResult:
    """文本 → 结构化客源字段（只读，不落库）。"""
    quota_key: str | None = None
    try:
        quota_key = await _consume_quota(account_id)
        raw = await complete(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"LEAD_EXTRACT\n{request.text}"},
            ],
            json_mode=True,
            scene="lead_extract",
        )
        data = parse_json(raw)
        return _normalize(data)
    except Exception:
        # 任意失败（限流外的调用异常、AI 不可用 503、非法 JSON）都退还当日额度，
        # 与 polish/search 一致，避免失败消耗配额。
        await _refund_quota_safely(quota_key)
        raise
