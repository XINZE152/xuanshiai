from decimal import Decimal as D

import pytest

from app.services.payment.commission_math import commission_amount, cumulative_refund_adjustment


def test_commission_rules_and_cent_rounding():
    assert commission_amount(D('99.99'), 'rate', D('15')) == D('14.99')
    assert commission_amount(D('99.99'), 'fixed', D('5')) == D('5.00')
    with pytest.raises(ValueError):
        commission_amount(D('10'), 'rate', D('101'))
    with pytest.raises(ValueError):
        commission_amount(D('10'), 'fixed', D('11'))


def test_cumulative_refunds_recover_rounding_remainder_once():
    first = cumulative_refund_adjustment(D('10'), D('3'), D('1'), D('0'))
    second = cumulative_refund_adjustment(D('10'), D('3'), D('2'), first)
    last = cumulative_refund_adjustment(D('10'), D('3'), D('3'), first + second)
    assert (first, second, last) == (D('3.33'), D('3.33'), D('3.34'))
    assert cumulative_refund_adjustment(D('10'), D('3'), D('3'), D('10')) == D('0')
    with pytest.raises(ValueError):
        cumulative_refund_adjustment(D('10'), D('3'), D('4'), D('0'))
