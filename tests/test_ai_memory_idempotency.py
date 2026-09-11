from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.api.routes.ai_memory import _require_idempotency_key
from app.services.ai.memory.service import MemoryService


@pytest.mark.parametrize("raw", [None, "", "   ", "a b", "a\tb", "a\u0000b", "a\u0081b"])
def test_require_idempotency_key_rejects_missing_whitespace_and_control(raw: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_idempotency_key(raw)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "AI_INPUT_INVALID"


def test_require_idempotency_key_trims_and_allows_unicode_up_to_128() -> None:
    key = "  " + "键" * 128 + "  "
    assert _require_idempotency_key(key) == "键" * 128


def test_require_idempotency_key_rejects_over_128_after_trim() -> None:
    with pytest.raises(HTTPException):
        _require_idempotency_key("x" * 129)


def test_service_uses_the_same_c1_control_character_rule() -> None:
    with pytest.raises(ValueError, match="Idempotency-Key"):
        MemoryService._validate_idempotency_key("a\u0081b")


def test_propose_rejects_key_that_would_make_claim_suffix_over_128() -> None:
    # The internal ':claim' derivative must not be truncated or collide.
    assert len("x" * 123 + ":claim") > 128
    with pytest.raises(ValueError, match="Idempotency-Key"):
        MemoryService._validate_propose_idempotency_key("x" * 123)


def test_grant_revoke_service_receives_idempotency_key() -> None:
    assert "idempotency_key" in inspect.signature(MemoryService.revoke_projection_grant).parameters
