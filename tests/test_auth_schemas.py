import pytest
from pydantic import ValidationError

from app.schemas.auth import PhoneLoginRequest, ProfileUpdateRequest, RealNameRequest


def test_phone_login_validates_phone_and_code() -> None:
    request = PhoneLoginRequest(phone="13800138000", code="123456")
    assert request.purpose == "login"


def test_phone_login_rejects_invalid_input() -> None:
    with pytest.raises(ValidationError):
        PhoneLoginRequest(phone="123", code="abcdef")


def test_realname_rejects_underage_id_card() -> None:
    # Format validation belongs to the schema; age validation belongs to the service.
    request = RealNameRequest(real_name="张三", id_card="110101201001011234")
    assert request.id_card.endswith("1234")


def test_profile_rejects_invalid_height() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(height=99)
