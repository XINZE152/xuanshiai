"""支付履约 Handler 协议与注册中心。

约束：
- Handler 只写业务表，**绝对不能 commit/rollback**；
- 事务由 Worker 或调用方统一控制；
- Handler 必须依赖数据库唯一键保证幂等，重复执行不能重复发权益。
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class FulfillmentError(Exception):
    """履约失败；默认由 Worker 按退避策略重试。"""


class PermanentFulfillmentError(FulfillmentError):
    """不可恢复的履约失败（订单不存在、订单状态错误等），Worker 直接置 DEAD。"""


class UnsupportedOrderType(FulfillmentError):
    """没有注册对应 order_type 的 Handler。"""


class FulfillmentHandler(Protocol):
    """单个订单类型的履约/退款回收协议。"""

    order_type: int

    async def fulfill(self, db: AsyncSession, order: Mapping[str, Any]) -> dict[str, Any]:
        """发放权益。

        返回示例::

            {"fulfilled": True, "membership_id": 123}

        实现要求：
        - 幂等：重复调用不能重复发权益；
        - 不 commit；
        - 失败抛 :class:`FulfillmentError`。
        """
        ...

    async def revoke(
        self,
        db: AsyncSession,
        order: Mapping[str, Any],
        refund: Mapping[str, Any],
    ) -> None:
        """退款后回收权益。

        实现要求同 ``fulfill``：幂等、不 commit；退款金额/原因从 ``refund`` 读取。
        """
        ...


_HANDLERS: dict[int, FulfillmentHandler] = {}


def register(handler: FulfillmentHandler) -> None:
    """注册 Handler；重复注册同一 order_type 直接报错，避免静默覆盖。"""
    order_type = int(handler.order_type)
    if order_type in _HANDLERS:
        raise ValueError(f"order_type={order_type} 的 FulfillmentHandler 已注册")
    _HANDLERS[order_type] = handler


def get_handler(order_type: int) -> FulfillmentHandler:
    """按订单类型取 Handler；未注册抛 :class:`UnsupportedOrderType`。"""
    handler = _HANDLERS.get(int(order_type))
    if handler is None:
        raise UnsupportedOrderType(f"不支持的支付订单类型：{order_type}")
    return handler


def registered_order_types() -> tuple[int, ...]:
    """返回已注册的订单类型，便于启动自检/后台展示。"""
    return tuple(sorted(_HANDLERS))
