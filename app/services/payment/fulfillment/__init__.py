"""支付履约 Handler 注册入口。

Worker 导入本模块即可完成 type=1~6 的注册；新增类型只需新增 Handler
并在下方 ``register``。
"""

from app.services.payment.fulfillment.base import (
    FulfillmentError,
    FulfillmentHandler,
    PermanentFulfillmentError,
    UnsupportedOrderType,
    get_handler,
    register,
    registered_order_types,
)
from app.services.payment.fulfillment.activity import ActivityFulfillmentHandler
from app.services.payment.fulfillment.boost import BoostFulfillmentHandler
from app.services.payment.fulfillment.matchmaker_service import (
    MatchmakerServiceFulfillmentHandler,
)
from app.services.payment.fulfillment.membership import MembershipFulfillmentHandler
from app.services.payment.fulfillment.spotlight import SpotlightFulfillmentHandler
from app.services.payment.fulfillment.virtual_goods import (
    VirtualGoodsFulfillmentHandler,
)

register(MembershipFulfillmentHandler())
register(SpotlightFulfillmentHandler())
register(MatchmakerServiceFulfillmentHandler())
register(BoostFulfillmentHandler())
register(ActivityFulfillmentHandler())
register(VirtualGoodsFulfillmentHandler())

__all__ = [
    "FulfillmentError",
    "FulfillmentHandler",
    "PermanentFulfillmentError",
    "UnsupportedOrderType",
    "get_handler",
    "register",
    "registered_order_types",
]
