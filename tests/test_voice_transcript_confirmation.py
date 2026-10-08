"""continuous_v2 转写确认边界的可执行行为测试。"""

from __future__ import annotations

import time

import pytest

from app.services.ai.profile import AIInputError
from app.services.voice.realtime.transcript_confirmation import (
    PendingTranscript,
    TranscriptBoundary,
    TranscriptCommand,
)


def _command(
    transcript_id: str,
    client_turn_id: str,
    session_id: str,
    text: str | None = None,
) -> TranscriptCommand:
    return TranscriptCommand(
        type="confirm_transcript",
        transcript_id=transcript_id,
        client_turn_id=client_turn_id,
        session_id=session_id,
        text=text,
    )


def test_partial_or_final_preview_does_not_create_a_receipt_without_confirmation() -> None:
    boundary = TranscriptBoundary()
    pending = boundary.stage(" partial 的最终文本 ", "turn-1", "session-1")

    assert pending.text == "partial 的最终文本"
    assert boundary.receipts == {}
    assert boundary.pending is pending


def test_confirm_edit_is_bound_to_current_session_and_replays_once() -> None:
    boundary = TranscriptBoundary()
    pending = boundary.stage("原始转写", "turn-1", "session-1")
    command = _command(pending.transcript_id, "turn-1", "session-1", "编辑后的转写")
    receipt = {
        "type": "transcript_confirmed",
        "transcript_id": pending.transcript_id,
        "client_turn_id": "turn-1",
        "session_id": "session-1",
        "source_id": "source-1",
        "text": "编辑后的转写",
    }

    assert boundary.resolve(command, "session-1") is pending
    boundary.complete(command, "编辑后的转写", receipt)
    assert boundary.replay(command, "编辑后的转写", "session-1") == receipt
    assert boundary.pending is None
    assert boundary.receipts[pending.transcript_id] == (
        "编辑后的转写",
        receipt,
    )


def test_cancel_expired_and_cross_session_confirmation_are_rejected() -> None:
    boundary = TranscriptBoundary()
    pending = boundary.stage("待确认", "turn-1", "session-1")
    cross_session = _command(
        pending.transcript_id, "turn-1", "session-2", "待确认"
    )
    with pytest.raises(AIInputError, match="TRANSCRIPT_STALE"):
        boundary.resolve(cross_session, "session-2")

    pending.expires_at = time.monotonic() - 1
    expired = _command(pending.transcript_id, "turn-1", "session-1", "待确认")
    with pytest.raises(AIInputError, match="TRANSCRIPT_EXPIRED"):
        boundary.resolve(expired, "session-1")
    assert boundary.pending is None


def test_conflicting_replay_cannot_change_confirmed_text_or_identity() -> None:
    boundary = TranscriptBoundary()
    pending = boundary.stage("原始", "turn-1", "session-1")
    command = _command(pending.transcript_id, "turn-1", "session-1", "确认")
    receipt = {"type": "transcript_confirmed", "text": "确认"}
    boundary.complete(command, "确认", receipt)

    with pytest.raises(AIInputError, match="TRANSCRIPT_CONFLICT"):
        boundary.replay(
            _command(pending.transcript_id, "turn-1", "session-1", "篡改"),
            "篡改",
            "session-1",
        )
    with pytest.raises(AIInputError, match="TRANSCRIPT_CONFLICT"):
        boundary.replay(
            _command(pending.transcript_id, "turn-2", "session-1", "确认"),
            "确认",
            "session-1",
        )


def test_pending_preview_shape_contains_expiry_and_session_binding() -> None:
    pending = PendingTranscript(
        transcript_id="tr-1",
        client_turn_id="ct-1",
        session_id="s-1",
        text="你好",
        expires_at=time.monotonic() + 120,
    )

    assert pending.preview() == {
        "type": "transcript_preview",
        "transcript_id": "tr-1",
        "client_turn_id": "ct-1",
        "session_id": "s-1",
        "text": "你好",
        "expires_in": 120,
    }
