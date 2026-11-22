"""虚拟支付签名与道具映射契约。"""

from __future__ import annotations

from app.services.payment.xpay import (
    build_sign_data,
    calc_pay_sig,
    calc_user_signature,
    resolve_product_id,
)


def test_pay_sig_matches_official_hmac_example_shape() -> None:
    sign_data = build_sign_data(
        offer_id="123",
        product_id="vip_monthly",
        goods_price=100,
        out_trade_no="P120260101ABCDEF",
        env=0,
    )
    assert '"offerId":"123"' in sign_data
    assert '"goodsPrice":100' in sign_data
    assert " " not in sign_data  # separators compact

    pay_sig = calc_pay_sig("requestVirtualPayment", sign_data, "test-app-key")
    assert len(pay_sig) == 64
    assert pay_sig == calc_pay_sig("requestVirtualPayment", sign_data, "test-app-key")

    user_sig = calc_user_signature(sign_data, "session-key")
    assert len(user_sig) == 64
    assert user_sig != pay_sig


def test_resolve_product_id_falls_back_to_product_type(monkeypatch) -> None:
    from app.core import config as config_mod

    monkeypatch.setattr(
        config_mod.settings,
        "wechat_xpay_product_map_json",
        '{"monthly":"vip_monthly"}',
    )
    assert resolve_product_id("monthly") == "vip_monthly"
    assert resolve_product_id("boost_1d") == "boost_1d"


def test_xpay_notify_extract_goods_deliver() -> None:
    from app.services.payment.xpay_notify import extract_goods_deliver

    event = {
        "Event": "xpay_goods_deliver_notify",
        "OpenId": "oTEST",
        "OutTradeNo": "P120260101ABCDEF",
        "WeChatPayInfo": {"TransactionId": "tx_1"},
        "GoodsInfo": {"ProductId": "vip_monthly", "ActualPrice": 990},
    }
    deliver = extract_goods_deliver(event)
    assert deliver["out_trade_no"] == "P120260101ABCDEF"
    assert deliver["transaction_id"] == "tx_1"
    assert deliver["amount_cents"] == 990
    assert deliver["product_id"] == "vip_monthly"
