"""AI-CORE service layer: provider gateway, typed schemas and feature gates.

The legacy assistant service used to live at ``app.services.ai``.  It was
renamed to :mod:`app.services.ai_assistant` when this package was introduced,
but existing callers (and the public assistant contract) still import the
legacy symbols from this package path.  They are re-exported lazily (PEP 562):
an eager import here created an order-dependent cycle — entering Python via
``app.services.ai_provider`` pulled this package init, which pulled
``ai_assistant``, which re-imported the still-initializing ``ai_provider``.
Lazy resolution keeps the public surface identical without the eager edge.
"""

from typing import Any

_LEGACY_ASSISTANT_EXPORTS: dict[str, str] = {
    "THOUGHTFULNESS_KEY_LABELS": "THOUGHTFULNESS_KEY_LABELS",
    "THOUGHTFULNESS_SYSTEM_PROMPT": "THOUGHTFULNESS_SYSTEM_PROMPT",
    "_thoughtfulness_row_to_response": "_thoughtfulness_row_to_response",
    "analyze_thoughtfulness": "analyze_thoughtfulness",
    "assistant_message": "assistant_message",
    "create_assistant_session": "create_assistant_session",
    "get_thoughtfulness": "get_thoughtfulness",
    "list_assistant_sessions": "list_assistant_sessions",
    "match_page": "match_page",
    "parse_search": "parse_search",
    "polish_profile": "polish_profile",
}

__all__ = list(_LEGACY_ASSISTANT_EXPORTS)


def __getattr__(name: str) -> Any:
    """Lazily re-export the legacy assistant symbols on first access."""
    if name in _LEGACY_ASSISTANT_EXPORTS:
        from app.services import ai_assistant

        value = getattr(ai_assistant, _LEGACY_ASSISTANT_EXPORTS[name])
        globals()[name] = value  # cache so later lookups skip the branch
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_LEGACY_ASSISTANT_EXPORTS})
