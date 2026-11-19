import json
from unittest.mock import Mock

import pytest

from app.services.payment.base import PaymentSignatureError, PaymentValidationError
from app.services.payment.transfer_notification import parse_transfer_notification


def notification():
    return {
        'out_bill_no': 'WD1', 'transfer_bill_no': 'WX1', 'state': 'SUCCESS',
        'mch_id': '123', 'transfer_amount': 1, 'openid': 'user1',
    }


def test_verify_and_decrypt_notification():
    crypto = Mock()
    crypto.decrypt_resource.return_value = notification()
    raw = json.dumps({'id': 'event1', 'resource': {'ciphertext': 'encrypted'}}).encode()
    result = parse_transfer_notification(crypto, {'signature': 'value'}, raw)
    crypto.verify_notification.assert_called_once_with({'signature': 'value'}, raw)
    assert result.amount_cents == 1
    assert result.out_bill_no == 'WD1'
    assert result.openid == 'user1'


def test_bad_signature_never_decrypts():
    crypto = Mock()
    crypto.verify_notification.side_effect = PaymentSignatureError('invalid')
    with pytest.raises(PaymentSignatureError):
        parse_transfer_notification(crypto, {}, b'{}')
    crypto.decrypt_resource.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('openid', ''), ('mch_id', None), ('out_bill_no', ''),
    ('transfer_amount', 0), ('transfer_amount', True),
    ('state', 'PROCESSING'), ('fail_reason', 3),
])
def test_reject_invalid_notification(field, value):
    payload = notification()
    payload[field] = value
    crypto = Mock()
    crypto.decrypt_resource.return_value = payload
    with pytest.raises(PaymentValidationError):
        parse_transfer_notification(crypto, {}, b'{"id":"event1","resource":{}}')
