"""AI-CORE service layer: provider gateway, typed schemas and feature gates.

The legacy assistant service used to live at ``app.services.ai``.  It was
renamed to :mod:`app.services.ai_assistant` when this package was introduced,
but existing callers (and the public assistant contract) still import the
legacy symbols from this package path.  Re-export them here while keeping the
AI core modules available below the package namespace.
"""

from app.services.ai_assistant import (
    THOUGHTFULNESS_KEY_LABELS,
    THOUGHTFULNESS_SYSTEM_PROMPT,
    _thoughtfulness_row_to_response,
    analyze_thoughtfulness,
    assistant_message,
    create_assistant_session,
    get_thoughtfulness,
    list_assistant_sessions,
    match_page,
    parse_search,
    polish_profile,
)

__all__ = [
    "THOUGHTFULNESS_KEY_LABELS",
    "THOUGHTFULNESS_SYSTEM_PROMPT",
    "_thoughtfulness_row_to_response",
    "analyze_thoughtfulness",
    "assistant_message",
    "create_assistant_session",
    "get_thoughtfulness",
    "list_assistant_sessions",
    "match_page",
    "parse_search",
    "polish_profile",
]
