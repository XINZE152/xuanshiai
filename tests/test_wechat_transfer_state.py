import pytest

from app.services.payment.base import PaymentValidationError
from app.services.payment.transfer_state import withdrawal_state_from_wechat


@pytest.mark.parametrize(('wechat', 'expected'), [
    ('ACCEPTED', 'PROCESSING'), ('PROCESSING', 'PROCESSING'),
    ('WAIT_USER_CONFIRM', 'PROCESSING'), ('TRANSFERING', 'PROCESSING'), ('CANCELING', 'PROCESSING'),
    ('SUCCESS', 'SUCCEEDED'), ('FAIL', 'FAILED'), ('CANCELLED', 'FAILED'),
])
def test_transfer_state(wechat, expected):
    assert withdrawal_state_from_wechat(wechat) == expected


def test_unknown_transfer_state_fails_closed():
    with pytest.raises(PaymentValidationError):
        withdrawal_state_from_wechat('UNKNOWN')
