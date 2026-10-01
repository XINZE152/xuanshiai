"""字段级敏感值脱敏：结构化列（微信号、手机号）与自由文本共用同一套分级口径。

与 :func:`app.services.message_admin.redact_sensitive` 的分工：后者面向**自由文本**，
靠正则定位嵌入的敏感串；而 ``users.wechat``、``customer_lead.wechat`` 这类**结构化列**
整列就是敏感值本身，用正则既无必要也会误伤正常内容，必须走无条件字段级掩码。
两者共用本模块的 :func:`mask_middle` 与等级常量，保证任何一档都不会返回完整值。
"""

from __future__ import annotations

from typing import Final

# 与 message_admin 保持一致的两档语义：普通只读 / 具备处置与审计权限。
# 两档都只展示部分字符，差别仅在保留位数，任何一档都不返回完整敏感值。
REDACTION_LEVEL_STANDARD: Final[str] = "standard"
REDACTION_LEVEL_ELEVATED: Final[str] = "elevated"


#: 具备处置/审计权限即可看到更多位数（仍为掩码），与消息后台原口径一致。
_ELEVATED_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {"message.moderate", "message.manage", "admin.moderate"}
)


def redaction_level_from_permissions(permissions: frozenset[str] | set[str]) -> str:
    """按权限集合决定脱敏档位。

    只接受权限集合而不接受后台账号对象：``CurrentMatchmakerAdmin`` 定义在
    ``app/api/dependencies.py``，core 反向依赖 api 层会打乱分层。消息后台、会员 CRM
    与客源线索共用本函数，保证同一操作人在不同模块看到的粒度一致。
    """
    if "*" in permissions or (set(permissions) & _ELEVATED_PERMISSIONS):
        return REDACTION_LEVEL_ELEVATED
    return REDACTION_LEVEL_STANDARD

def mask_middle(value: str, keep_head: int, keep_tail: int) -> str:
    """保留首 ``keep_head`` 位与尾 ``keep_tail`` 位，中间以等长星号替代。

    ``keep_tail=0`` 时必须走空串分支：``value[-0:]`` 在 Python 里等价于
    ``value[0:]``（即整串），早先实现因此把完整微信号原样拼回掩码尾部。
    可保留字符数不足时退化为全掩码，宁可不给核对线索也不泄露原值。
    """
    hidden = len(value) - keep_head - keep_tail
    if hidden <= 0:
        return "*" * len(value)
    tail = value[-keep_tail:] if keep_tail > 0 else ""
    return f"{value[:keep_head]}{'*' * hidden}{tail}"


def mask_contact(value: str | None, level: str = REDACTION_LEVEL_STANDARD) -> str | None:
    """脱敏结构化联系方式标识（微信号一类短标识）。

    标准档只保留前 3 位；处置/审计档保留前 3 后 4 位便于核对。长度不足时全掩码，
    因此不存在“短微信号反而整串可见”的边界。
    """
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    if level == REDACTION_LEVEL_ELEVATED:
        return mask_middle(text_value, 3, 4)
    return mask_middle(text_value, 3, 0)


def mask_phone(value: str | None) -> str | None:
    """手机号统一口径：前 3 后 4，异常长度直接全掩码。"""
    if not value:
        return None
    text_value = str(value).strip()
    if len(text_value) < 7:
        return "***"
    return mask_middle(text_value, 3, 4)


def is_masked_value(value: str | None) -> bool:
    """判断回写值是否已是掩码形态。

    后台表单会把脱敏后的值预填进输入框，原样提交就会把 ``***`` 落库覆盖真实微信号。
    更新路径据此丢弃该字段，保持库中原值不变。
    """
    return value is not None and "*" in value
