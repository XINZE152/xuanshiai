"""微信商家转账状态映射；该模块不执行账本操作。"""

from app.services.payment.base import PaymentValidationError


def withdrawal_state_from_wechat(state: str) -> str:
    """只把微信明确的终态映射为成功或失败，未知状态拒绝处理。"""
    if state in {'ACCEPTED', 'PROCESSING', 'WAIT_USER_CONFIRM', 'TRANSFERING', 'CANCELING'}:
        return 'PROCESSING'
    if state == 'SUCCESS':
        return 'SUCCEEDED'
    if state in {'FAIL', 'CANCELLED'}:
        return 'FAILED'
    raise PaymentValidationError('无法识别的微信商家转账状态')
