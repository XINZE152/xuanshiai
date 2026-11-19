"""Parse and authenticate WeChat merchant-transfer notifications."""

import json
from dataclasses import dataclass
from typing import Mapping

from app.services.payment.base import PaymentValidationError
from app.services.payment.crypto import WechatPayCrypto


@dataclass(frozen=True, slots=True)
class TransferNotification:
    notification_id: str
    out_bill_no: str
    transfer_bill_no: str
    state: str
    mch_id: str
    amount_cents: int
    openid: str
    fail_reason: str | None


def parse_transfer_notification(
    crypto: WechatPayCrypto, headers: Mapping[str, str], raw_body: bytes
) -> TransferNotification:
    """Verify before decrypting; callers must match every field to their own bill."""
    crypto.verify_notification(headers, raw_body)
    try:
        event = json.loads(raw_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaymentValidationError('微信转账通知不是合法 JSON') from exc
    if not isinstance(event, dict) or not isinstance(event.get('resource'), dict):
        raise PaymentValidationError('微信转账通知缺少资源')
    notification_id = event.get('id')
    if not isinstance(notification_id, str) or not notification_id.strip():
        raise PaymentValidationError('微信转账通知缺少通知编号')
    payload = crypto.decrypt_resource(event['resource'])
    fields = ('out_bill_no', 'transfer_bill_no', 'state', 'mch_id', 'openid')
    if any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in fields):
        raise PaymentValidationError('微信转账通知缺少必要字段')
    amount = payload.get('transfer_amount')
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise PaymentValidationError('微信转账通知金额无效')
    if payload['state'] not in {'SUCCESS', 'FAIL', 'CANCELLED'}:
        raise PaymentValidationError('微信转账通知状态无效')
    reason = payload.get('fail_reason')
    if reason is not None and not isinstance(reason, str):
        raise PaymentValidationError('微信转账失败原因无效')
    return TransferNotification(
        notification_id=notification_id, out_bill_no=payload['out_bill_no'],
        transfer_bill_no=payload['transfer_bill_no'], state=payload['state'],
        mch_id=payload['mch_id'], amount_cents=amount, openid=payload['openid'],
        fail_reason=reason,
    )
