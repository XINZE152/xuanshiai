"""真实记忆 Provider 探测的配置选择测试（不发起网络调用）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

def test_probe_selects_configured_dots_without_requiring_advisor_key(monkeypatch) -> None:
    from scripts import verify_memory_real_provider as probe

    monkeypatch.setattr(
        probe,
        "settings",
        SimpleNamespace(
            ai_enabled=False,
            ai_api_key=None,
            ai_model="deepseek-chat",
            ai_dots_api_key=object(),
            ai_dots_model="dots3-note-prev",
        ),
    )

    assert probe._provider_ready("dots") is True
    assert probe._provider_model("dots") == "dots3-note-prev"
    assert probe._provider_ready("advisor") is False


def test_probe_rejects_unconfigured_dots(monkeypatch) -> None:
    from scripts import verify_memory_real_provider as probe

    monkeypatch.setattr(
        probe,
        "settings",
        SimpleNamespace(
            ai_enabled=True,
            ai_api_key=object(),
            ai_model="configured-advisor-model",
            ai_dots_api_key=None,
            ai_dots_model="dots3-note-prev",
        ),
    )

    assert probe._provider_ready("dots") is False
    assert probe._provider_ready("advisor") is True


@pytest.mark.asyncio
async def test_probe_collects_only_dots_content_chunks(monkeypatch) -> None:
    from scripts import verify_memory_real_provider as probe

    captured: list[tuple[list[dict[str, str]], bool]] = []

    class Dots:
        async def stream_chat(self, messages, *, json_mode):
            captured.append((messages, json_mode))
            yield "reasoning", "internal-only"
            yield "content", '{"suggestions": []}'
            yield "finish", "stop"

    monkeypatch.setattr(probe, "DotsAIProvider", Dots)
    raw = await probe._complete_with_provider(
        "dots", [{"role": "user", "content": "synthetic"}]
    )

    assert raw == '{"suggestions": []}'
    assert captured == [([{"role": "user", "content": "synthetic"}], True)]
