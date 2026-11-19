"""微信支付 APIv3 对外响应模型。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class JsapiPayParams(BaseModel):
    """小程序 ``wx.requestPayment`` 所需参数。

    字段名按项目既有 snake_case 契约输出；前端映射为:

    ``app_id -> appId``、``time_stamp -> timeStamp``、
    ``nonce_str -> nonceStr``、``sign_type -> signType``、
    ``pay_sign -> paySign``。
    """

    app_id: str = Field(min_length=1, max_length=64, description="小程序 AppID")
    time_stamp: str = Field(min_length=1, max_length=32, description="秒级时间戳字符串")
    nonce_str: str = Field(min_length=1, max_length=64, description="随机字符串")
    package: str = Field(
        min_length=1,
        max_length=128,
        description="预支付包，固定格式 prepay_id=wx...",
    )
    sign_type: Literal["RSA"] = Field(default="RSA", description="签名类型")
    pay_sign: str = Field(min_length=1, max_length=512, description="RSA 签名")

    def to_wx_payload(self) -> dict[str, str]:
        """转换为前端 ``wx.requestPayment`` 直接可用的驼峰参数。"""
        return {
            "appId": self.app_id,
            "timeStamp": self.time_stamp,
            "nonceStr": self.nonce_str,
            "package": self.package,
            "signType": self.sign_type,
            "paySign": self.pay_sign,
        }


class VirtualPayParamsRequest(BaseModel):
    """虚拟支付签名参数请求：需要新鲜的 ``uni.login`` code 换 session_key。"""

    js_code: str = Field(min_length=1, max_length=256, description="wx.login / uni.login 返回的 code")


class VirtualPayParamsResponse(BaseModel):
    """小程序 ``wx.requestVirtualPayment`` 所需参数（snake_case）。"""

    sign_data: str = Field(min_length=1, description="原样透传给客户端的 signData 字符串")
    mode: Literal["short_series_goods"] = "short_series_goods"
    pay_sig: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=1, max_length=128)
    offer_id: str = Field(min_length=1, max_length=64)
    product_id: str = Field(min_length=1, max_length=128)
    goods_price: int = Field(ge=1, description="单价（分），须与 MP 道具价格一致")
    out_trade_no: str = Field(min_length=8, max_length=64)
    env: Literal[0, 1] = 0


class XpayStatusResponse(BaseModel):
    """前端据此决定走虚拟支付还是旧 JSAPI。"""

    enabled: bool
    mode: Literal["disabled", "mock", "real"]
    env: Literal[0, 1]
    offer_id_configured: bool


class RefundCreateRequest(BaseModel):
    """后台发起微信退款。"""

    amount: Decimal = Field(
        gt=0,
        le=1_000_000,
        decimal_places=2,
        description="本次退款金额（元），不得超过订单可退余额",
    )
    reason: str = Field(min_length=1, max_length=255, description="退款原因")


class RefundResponse(BaseModel):
    """退款单摘要。"""

    refund_no: str
    order_no: str
    amount: Decimal
    refund_type: Literal["FULL", "PARTIAL"]
    status: str
    wechat_refund_id: str | None = None
