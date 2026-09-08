"""隔离环境中的记忆上下文真实模型探测。

本脚本不连接业务数据库，也不会读取真实用户记忆。它只构造与
``CounselorMemoryAdapter`` 相同的消毒载荷，走显式选择的开发/测试 Provider
调用，并验证模型返回能通过既有军师 JSON 契约。

用法（仅开发/测试环境，凭据来自被忽略的 .env）：

    uv run python scripts/verify_memory_real_provider.py --allow-real-provider --provider dots

输出只有模型名、耗时、长度与 SHA-256 摘要；不会打印 API Key、endpoint、
请求正文、原始模型回复或任何用户资料。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from typing import Any

from app.core.config import settings
from app.schemas.ai_advisor import AdvisorAdviceRequest
from app.services.ai.memory.consumers import (
    SanitizedMemoryContext,
    SanitizedMemoryEntry,
)
from app.services.ai_advisor import _build_prompt, _normalize_result
from app.services.ai_provider import complete, parse_json
from app.services.ai.providers import DotsAIProvider

_FORBIDDEN_TOKENS = ("source_quote", "transcript", "evidence_ref", "source_kind")


def _synthetic_memory_context() -> SanitizedMemoryContext:
    """构造无个人信息的最小 Adapter 输出，防止探测误带真实记忆。"""

    return SanitizedMemoryContext(
        function_key="counselor_context",
        purpose="session_context",
        owner_user_id=999_991,
        entries_by_subject=(
            (
                "personal",
                (
                    SanitizedMemoryEntry(
                        field_key="relationship_goal",
                        value="serious_relationship",
                        value_type="string",
                        stability=0.9,
                        importance=0.8,
                        constraint_type=None,
                        claim_id="synthetic-claim-personal",
                        projection_version=1,
                    ),
                ),
            ),
            (
                "ideal_partner",
                (
                    SanitizedMemoryEntry(
                        field_key="relationship_goal",
                        value="mutual_respect",
                        value_type="string",
                        stability=0.9,
                        importance=0.8,
                        constraint_type=None,
                        claim_id="synthetic-claim-preference",
                        projection_version=1,
                    ),
                ),
            ),
        ),
    )


def _provider_ready(provider_name: str) -> bool:
    """只报告配置是否存在；绝不读取 SecretStr 的值。"""

    if provider_name == "dots":
        return settings.ai_dots_api_key is not None
    return settings.ai_enabled and settings.ai_api_key is not None


def _provider_model(provider_name: str) -> str:
    return settings.ai_dots_model if provider_name == "dots" else settings.ai_model


async def _complete_with_provider(
    provider_name: str, messages: list[dict[str, str]]
) -> str:
    """用项目已配置的 Provider 执行一次 JSON 调用，不输出消息或原始回复。"""

    if provider_name == "advisor":
        return await complete(messages, json_mode=True)

    chunks: list[str] = []
    provider = DotsAIProvider()
    async for kind, value in provider.stream_chat(messages, json_mode=True):
        if kind == "content":
            chunks.append(value)
    raw = "".join(chunks).strip()
    if not raw:
        raise ValueError("dots provider returned empty content")
    return raw


def _redacted_result(
    *, provider_name: str, elapsed_ms: int, raw: str, normalized: dict[str, Any]
) -> dict[str, Any]:
    return {
        "result": "PASS",
        "provider": provider_name,
        "model": _provider_model(provider_name),
        "elapsed_ms": elapsed_ms,
        "response_bytes": len(raw.encode("utf-8")),
        "response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "suggestion_count": len(normalized["suggestions"]),
        "risk_level": normalized["risk_level"],
        "memory_context_sanitized": True,
    }


async def _run(provider_name: str) -> int:
    if settings.environment == "production":
        print(json.dumps({"result": "BLOCKED", "reason": "production environment is not allowed"}))
        return 2
    if not _provider_ready(provider_name):
        print(
            json.dumps(
                {
                    "result": "NOT_RUN",
                    "reason": f"{provider_name} provider is not configured",
                }
            )
        )
        return 2

    context = _synthetic_memory_context()
    memory_json = json.dumps(context.to_prompt_payload(), ensure_ascii=False)
    if any(token in memory_json for token in _FORBIDDEN_TOKENS):
        print(json.dumps({"result": "FAIL", "reason": "unsanitized memory payload"}))
        return 1

    request = AdvisorAdviceRequest(
        scenario="reply",
        incoming_message="我也希望彼此尊重，慢慢了解。",
        goal="给出一条尊重边界的回复建议",
        tone="natural",
        max_suggestions=1,
    )
    prompt = _build_prompt(request, "", [], "none", memory_json)
    started = time.monotonic()
    raw = await _complete_with_provider(
        provider_name,
        [
            {
                "role": "system",
                "content": "You are a cautious, privacy-respecting relationship advisor clearly identified as AI.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    normalized = _normalize_result(parse_json(raw), request)
    print(
        json.dumps(
            _redacted_result(
                provider_name=provider_name,
                elapsed_ms=elapsed_ms,
                raw=raw,
                normalized=normalized,
            )
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-real-provider",
        action="store_true",
        help="explicitly authorize a non-production external provider request",
    )
    parser.add_argument(
        "--provider",
        choices=("dots", "advisor"),
        default="dots",
        help="configured non-production provider to probe (default: dots)",
    )
    args = parser.parse_args()
    if not args.allow_real_provider:
        parser.error("--allow-real-provider is required")
    try:
        return asyncio.run(_run(args.provider))
    except Exception as exc:
        # 不显示 exception 文本：httpx 及 Provider 有时会把 URL/请求片段带入错误。
        print(json.dumps({"result": "FAIL", "reason": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
