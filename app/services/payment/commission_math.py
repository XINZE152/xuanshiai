"""Money arithmetic for order commission and cumulative refund adjustments."""
from decimal import Decimal, ROUND_DOWN

CENT = Decimal("0.01")


def commission_amount(base: Decimal, mode: str, value: Decimal) -> Decimal:
    """Calculate a non-negative commission from a paid amount, rounded down to cents."""
    if not base.is_finite() or not value.is_finite() or base < 0 or value < 0:
        raise ValueError("invalid commission inputs")
    if mode in ("rate", "ratio"):
        if value > 100:
            raise ValueError("commission rate exceeds 100 percent")
        result = base * value / Decimal(100)
    elif mode == "fixed":
        result = value
    else:
        raise ValueError("unknown commission mode")
    result = result.quantize(CENT, rounding=ROUND_DOWN)
    if result > base:
        raise ValueError("commission exceeds paid amount")
    return result


def cumulative_refund_adjustment(
    commission: Decimal, order_amount: Decimal, refunded: Decimal, already_reversed: Decimal,
) -> Decimal:
    """Return the additional debit needed for the cumulative refunded proportion."""
    values = (commission, order_amount, refunded, already_reversed)
    if any(not value.is_finite() or value < 0 for value in values) or order_amount <= 0:
        raise ValueError("invalid refund inputs")
    if refunded > order_amount or already_reversed > commission:
        raise ValueError("refund or reversal exceeds original amount")
    target = (commission * refunded / order_amount).quantize(CENT, rounding=ROUND_DOWN)
    if target < already_reversed:
        raise ValueError("cumulative reversal exceeds refund target")
    return target - already_reversed
