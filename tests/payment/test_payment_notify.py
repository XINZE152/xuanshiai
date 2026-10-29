"""微信支付回调安全、防重放与金额校验测试。"""

from __future__ import annotations

import base64
import time
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from app.api.routes import payments as payment_routes
from app.db.session import get_db
from app.main import app
from app.services.payment.base import PaymentNotification
from app.services.payment.crypto import WechatPayCrypto
from tests.payment.conftest import FakeResult, FakeSession

_PUBLIC_KEY_ID = "PUB_KEY_ID_TEST"


def _build_crypto() -> tuple[WechatPayCrypto, rsa.RSAPrivateKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    from app.services.payment.base import WechatPayConfig

    config = WechatPayConfig(
        mch_id="1900000001",
        app_id="wx_test",
        serial_no="MCH_SERIAL",
        private_key_pem=private_pem,
        api_v3_key="0123456789abcdef0123456789abcdef",
        notify_url="https://example.test/notify",
        public_key_id=_PUBLIC_KEY_ID,
        public_key_pem=public_pem,
    )
    return WechatPayCrypto(config), private_key


def _headers(
    private_key: rsa.RSAPrivateKey,
    body: bytes,
    *,
    timestamp: int | None = None,
    nonce: str = "nonce-1",
    signature_override: str | None = None,
) -> dict[str, str]:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    message = f"{ts}\n{nonce}\n{body.decode('utf-8')}\n".encode()
    signature = (
        signature_override
        if signature_override is not None
        else base64.b64encode(
            private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        ).decode()
    )
    return {
        "Wechatpay-Timestamp": ts,
        "Wechatpay-Nonce": nonce,
        "Wechatpay-Signature": signature,
        "Wechatpay-Serial": _PUBLIC_KEY_ID,
    }


def _request(
    client: TestClient,
    body: bytes,
    headers: dict[str, str],
):
    return client.post(
        "/api/v1/payments/wechat/notify",
        content=body,
        headers=headers,
    )


@pytest.fixture
def override_db():
    sessions: list[FakeSession] = []

    def install(session: FakeSession) -> FakeSession:
        sessions.append(session)
        app.dependency_overrides[get_db] = lambda: session
        return session

    yield install
    app.dependency_overrides.pop(get_db, None)


def test_notify_invalid_signature_returns_401_and_does_not_touch_db(
    monkeypatch: pytest.MonkeyPatch,
    override_db,
) -> None:
    crypto, private_key = _build_crypto()
    monkeypatch.setattr(payment_routes, "build_payment_crypto", lambda: crypto)
    db = override_db(FakeSession())

    body = b'{"id":"notify-invalid-signature"}'
    headers = _headers(private_key, body, signature_override="not-a-valid-signature")

    client = TestClient(app)
    response = _request(client, body, headers)

    assert response.status_code == 401
    assert response.json()["code"] == "FAIL"
    assert db.statements == [], "验签失败不得触碰数据库"


def test_notify_expired_timestamp_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    override_db,
) -> None:
    crypto, private_key = _build_crypto()
    monkeypatch.setattr(payment_routes, "build_payment_crypto", lambda: crypto)
    db = override_db(FakeSession())

    body = b'{"id":"notify-expired"}'
    headers = _headers(private_key, body, timestamp=int(time.time()) - 301)

    client = TestClient(app)
    response = _request(client, body, headers)

    assert response.status_code == 401
    assert response.json()["code"] == "FAIL"
    assert db.statements == []


def test_notify_duplicate_id_returns_204_without_second_fulfillment(
    monkeypatch: pytest.MonkeyPatch,
    override_db,
) -> None:
    class DummyCrypto:
        config = None

    notification = PaymentNotification(
        notify_id="notify-duplicate",
        event_type="TRANSACTION.SUCCESS",
        out_trade_no="TEST12345678",
        transaction_id="42000001",
        trade_state="SUCCESS",
        amount_total=100,
        currency="CNY",
        mchid="1900000001",
        appid="wx_test",
    )

    monkeypatch.setattr(payment_routes, "build_payment_crypto", lambda: DummyCrypto())
    monkeypatch.setattr(
        payment_routes,
        "parse_wechat_notification",
        lambda *_args, **_kwargs: notification,
    )

    async def responder(sql: str, _params: dict[str, Any], _session: FakeSession):
        if "SELECT id, processed FROM payment_notify_log" in sql:
            return FakeResult(first={"id": 1, "processed": 1})
        return FakeResult()

    db = override_db(FakeSession(responder=responder))

    client = TestClient(app)
    response = _request(client, b'{"id":"notify-duplicate"}', {})

    assert response.status_code == 204
    assert "UPDATE payment_order SET status = 1" not in db.sql_text(), (
        "重复 notify_id 不得再次履约"
    )


def test_notify_amount_tamper_returns_409_and_does_not_mark_paid(
    monkeypatch: pytest.MonkeyPatch,
    override_db,
) -> None:
    class DummyCrypto:
        config = None

    # 本地订单 100.00 元，微信通知多 1 分钱。
    notification = PaymentNotification(
        notify_id="notify-amount-tamper",
        event_type="TRANSACTION.SUCCESS",
        out_trade_no="TEST12345678",
        transaction_id="42000001",
        trade_state="SUCCESS",
        amount_total=10001,
        currency="CNY",
        mchid="1900000001",
        appid="wx_test",
    )

    monkeypatch.setattr(payment_routes, "build_payment_crypto", lambda: DummyCrypto())
    monkeypatch.setattr(
        payment_routes,
        "parse_wechat_notification",
        lambda *_args, **_kwargs: notification,
    )
    monkeypatch.setattr(payment_routes.settings, "wechat_pay_mch_id", "1900000001")
    monkeypatch.setattr(payment_routes.settings, "wechat_pay_app_id", "wx_test")

    async def responder(sql: str, _params: dict[str, Any], _session: FakeSession):
        if "SELECT id, processed FROM payment_notify_log" in sql:
            return FakeResult(first=None)
        if "FROM payment_order" in sql and "FOR UPDATE" in sql:
            return FakeResult(
                first={
                    "id": 1,
                    "order_no": "TEST12345678",
                    "user_id": 7,
                    "type": 1,
                    "amount": "100.00",
                    "status": 0,
                    "transaction_id": None,
                    "channel": "wechat",
                    "fulfillment_status": "PENDING",
                }
            )
        return FakeResult()

    db = override_db(FakeSession(responder=responder))

    client = TestClient(app)
    response = _request(client, b'{"id":"notify-amount-tamper"}', {})

    assert response.status_code == 409
    assert response.json()["code"] == "FAIL"
    assert "金额" in response.json()["message"]
    assert "UPDATE payment_order SET status = 1" not in db.sql_text()
    assert any("payment_notify_log" in sql for sql, _ in db.statements)
