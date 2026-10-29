"""撤回回写会话预览：撤回后 chat_session.last_message 重算，避免列表"露原文"（R78-1）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.services import social as social_service


def test_message_preview_maps_text_directly() -> None:
    assert social_service._message_preview(1, "你好") == "你好"
    assert social_service._message_preview(1, None) == ""
    assert social_service._message_preview(1, "") == ""


def test_message_preview_maps_media_and_system_types() -> None:
    assert social_service._message_preview(2, None) == "[图片]"
    assert social_service._message_preview(3, None) == "[语音]"
    assert social_service._message_preview(4, None) == "[视频]"
    assert social_service._message_preview(5, None) == "[小程序]"
    assert social_service._message_preview(6, None) == "[系统消息]"
    assert social_service._message_preview(99, None) == "[媒体消息]"


def test_message_preview_keeps_system_greeting_text() -> None:
    """type 6 有正文时展示原文：申请通过的自动问候走的就是这条分支。"""
    assert social_service._message_preview(6, "你好，很高兴认识你") == "你好，很高兴认识你"
    assert social_service._message_preview(6, "") == "[系统消息]"


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar


class _FakeSession:
    """只覆盖 recall_message 会用到的 SQL 分支，记录 last_message 回写值。"""

    def __init__(self, message_row: dict[str, Any], latest_row: dict[str, Any] | None) -> None:
        self.message_row = message_row
        self.latest_row = latest_row
        self.preview_updates: list[str] = []

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = params or {}
        if "FROM chat_message WHERE id = :id AND from_user_id = :user_id" in sql:
            return _Result([self.message_row])
        if "SELECT revoked_at FROM chat_message" in sql:
            return _Result(scalar=datetime(2026, 9, 23, 10, 1, 0))
        if "revoked_at IS NULL" in sql:
            return _Result([self.latest_row] if self.latest_row else [])
        if "UPDATE chat_session SET last_message" in sql:
            self.preview_updates.append(str(params.get("preview")))
            return _Result()
        if "UPDATE chat_message SET revoked_at" in sql:
            return _Result()
        return _Result()

    async def commit(self) -> None:
        return None


def _message_row(message_id: int = 7, content: str = "被撤回的内容") -> dict[str, Any]:
    return {
        "id": message_id,
        "session_id": 3,
        "from_user_id": 7,
        "to_user_id": 20,
        "type": 1,
        "content": content,
        "media_url": None,
        "client_message_id": None,
        "is_read": 0,
        "revoked_at": None,
        "created_at": datetime(2026, 9, 23, 10, 0, 0),
    }


@pytest.mark.asyncio
async def test_recall_falls_back_to_previous_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _session(_db: object, _user_id: int, _session_id: int) -> tuple[dict[str, int], int]:
        return {"user1_id": 7, "user2_id": 20}, 20

    async def _notify(_db: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(social_service, "_session", _session)
    monkeypatch.setattr(social_service, "emit_notification", _notify)

    db = _FakeSession(_message_row(), {"type": 1, "content": "上一条还在"})
    await social_service.recall_message(db, 7, 7)
    assert db.preview_updates == ["上一条还在"]


@pytest.mark.asyncio
async def test_recall_of_only_message_yields_recalled_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _session(_db: object, _user_id: int, _session_id: int) -> tuple[dict[str, int], int]:
        return {"user1_id": 7, "user2_id": 20}, 20

    async def _notify(_db: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(social_service, "_session", _session)
    monkeypatch.setattr(social_service, "emit_notification", _notify)

    db = _FakeSession(_message_row(), None)
    await social_service.recall_message(db, 7, 7)
    assert db.preview_updates == ["消息已撤回"]


@pytest.mark.asyncio
async def test_recall_keeps_system_greeting_as_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    """撤回后重算落到 type=6 自动问候时，预览必须是问候原文而不是占位符。"""

    async def _session(_db: object, _user_id: int, _session_id: int) -> tuple[dict[str, int], int]:
        return {"user1_id": 7, "user2_id": 20}, 20

    async def _notify(_db: object, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(social_service, "_session", _session)
    monkeypatch.setattr(social_service, "emit_notification", _notify)

    db = _FakeSession(_message_row(), {"type": 6, "content": "你好，很高兴认识你"})
    await social_service.recall_message(db, 7, 7)
    assert db.preview_updates == ["你好，很高兴认识你"]
