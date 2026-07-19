import pytest
from pydantic import ValidationError

from app.schemas.auth import MatchmakerReviewRequest, RegistrationIntentUpdate


def test_registration_intent_is_limited_to_supported_values() -> None:
    assert RegistrationIntentUpdate(intent_type="companion").intent_type == "companion"
    with pytest.raises(ValidationError):
        RegistrationIntentUpdate(intent_type="other")


def test_review_requires_reason_for_rejection() -> None:
    with pytest.raises(ValidationError):
        MatchmakerReviewRequest(status=2)
    assert MatchmakerReviewRequest(status=1).status == 1
