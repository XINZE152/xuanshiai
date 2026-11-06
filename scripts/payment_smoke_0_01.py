#!/usr/bin/env python
"""0.01 元微信支付冒烟测试（仅限隔离的 development/testing 环境）。

阶段：
- prepay：为测试用户签发临时会话 -> 调用创建订单接口 -> 调用预支付接口。
- notify：用离线生成的测试密钥对构造 APIv3 回调，走真实 notify 路由，
  对测试库执行两次相同回调，验证已支付与幂等。
- cleanup：下架并删除测试商品、测试订单、回调日志、履约任务、会员记录和临时会话，
  并尝试关闭微信侧未支付的预支付单。

安全边界：
- 不打印商户号、AppID、私钥、APIv3 密钥、openid 或 access token；
- 所有微信凭据只从 ``settings``/环境变量读取；
- 环境不是 development/testing 时直接退出；
- 只删除本脚本 state 文件里记录的测试行。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.core.security import create_token, hash_token  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.services.payment.base import WechatPayConfig  # noqa: E402
from app.services.payment.crypto import WechatPayCrypto  # noqa: E402

USER_ID = int(os.environ.get("PAYMENT_SMOKE_USER_ID", "19"))
PACKAGE_CODE = "test_001"
API_BASE = "http://127.0.0.1:8000"
STATE_PATH = Path("/root/payment-smoke-state.json")
EVIDENCE_PATH = Path("/root/payment-smoke-evidence.json")
TEST_PUBLIC_KEY_ID = "PUB_KEY_ID_SMOKE"
# 每次运行随机生成 32 字节 ASCII 测试密钥，仅用于本次进程内回调模拟。
TEST_API_V3_KEY = secrets.token_hex(16)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _guard() -> None:
    if settings.environment not in {"development", "testing"}:
        raise SystemExit(
            f"拒绝执行：ENVIRONMENT={settings.environment}，仅允许 development/testing"
        )
    if not settings.wechat_pay_mch_id or not settings.wechat_pay_app_id:
        raise SystemExit("拒绝执行：WECHAT_PAY_MCH_ID / WECHAT_PAY_APP_ID 未配置")


def _write_private(path: Path, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


def _read_state() -> dict:
    if not STATE_PATH.exists():
        raise SystemExit(f"缺少状态文件 {STATE_PATH}，请先执行 prepay")
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


async def _create_temp_session() -> tuple[int, str]:
    async with session_factory() as db:
        refresh = secrets.token_urlsafe(32)
        now = datetime.now(UTC).replace(tzinfo=None)
        result = await db.execute(
            text(
                """INSERT INTO user_session
                   (user_id, refresh_token_hash, device_id, platform, app_version, ip,
                    user_agent, created_at, last_used_at, access_expire_at,
                    refresh_expire_at, status)
                   VALUES (:user_id, :refresh_hash, 'smoke-0.01', 'smoke', '0.01',
                           '127.0.0.1', 'payment-smoke', UTC_TIMESTAMP(), UTC_TIMESTAMP(),
                           :access_expire, :refresh_expire, 1)"""
            ),
            {
                "user_id": USER_ID,
                "refresh_hash": hash_token(refresh),
                "access_expire": now + timedelta(minutes=30),
                "refresh_expire": now + timedelta(days=1),
            },
        )
        session_id = int(result.lastrowid)
        await db.commit()
    token = create_token(USER_ID, session_id, "access", timedelta(minutes=30))
    return session_id, token


async def _seed() -> int:
    """Idempotently publish the 0.01 test package in the test DB."""
    _guard()
    async with session_factory() as db:
        await db.execute(
            text(
                """INSERT IGNORE INTO config_membership_package
                   (name, code, duration_days, price, original_price, daily_price,
                    sort, is_active, is_recommend, badge, rights)
                   VALUES ('【测试】0.01元验证商品', :code, 1, 0.01, NULL, NULL,
                           900, 1, 0, 'SMOKE', '{"smoke": true}')"""
            ),
            {"code": PACKAGE_CODE},
        )
        await db.execute(
            text(
                "UPDATE config_membership_package SET is_active=1 "
                "WHERE code=:code AND name LIKE '【测试】%'"
            ),
            {"code": PACKAGE_CODE},
        )
        await db.commit()
        row = (
            await db.execute(
                text(
                    """SELECT id, code, name, price, is_active
                       FROM config_membership_package WHERE code=:code"""
                ),
                {"code": PACKAGE_CODE},
            )
        ).mappings().first()
    if (
        not row
        or not str(row["name"]).startswith("【测试】")
        or str(row["price"]) not in {"0.01", "0.0100"}
    ):
        print(f"seed_failed row={dict(row) if row else None}")
        return 1
    print(
        f"seed_ok id={row['id']} code={row['code']} "
        f"price={row['price']} is_active={row['is_active']}"
    )
    return 0


async def _prepay() -> int:
    import httpx

    _guard()
    if settings.wechat_pay_mode != "real":
        raise SystemExit("拒绝执行：prepay 阶段要求 WECHAT_PAY_MODE=real")
    seed_rc = await _seed()
    if seed_rc != 0:
        return seed_rc

    run_id = f"{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    idempotency_key = f"smoke-001-{run_id}"
    session_id, token = await _create_temp_session()
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }

    async with httpx.AsyncClient(base_url=API_BASE, timeout=30) as client:
        first = await client.post(
            "/api/v1/membership/orders",
            json={"package_code": PACKAGE_CODE},
            headers=headers,
        )
        print(f"create_order_http={first.status_code}")
        print(f"create_order_body={first.text}")
        first.raise_for_status()
        order = first.json()
        order_no = order["order_no"]
        if str(order.get("amount")) not in {"0.01", "0.0100"}:
            raise SystemExit(
                f"拒绝继续：测试商品金额为 {order.get('amount')}，不是 0.01"
            )

        second = await client.post(
            "/api/v1/membership/orders",
            json={"package_code": PACKAGE_CODE},
            headers=headers,
        )
        second_body = second.json()
        print(f"create_order_repeat_http={second.status_code}")
        print(
            "create_order_repeat_same_order="
            f"{second_body.get('order_no') == order_no}"
        )
        assert second_body.get("order_no") == order_no, "幂等键未回放同一订单"

        prepay = await client.post(
            f"/api/v1/payments/orders/{order_no}/prepay",
            headers={"Authorization": f"Bearer {token}"},
        )
        print(f"prepay_http={prepay.status_code}")
        print(f"prepay_body={prepay.text}")
        if prepay.status_code != 200:
            _write_private(
                STATE_PATH,
                {
                    "run_id": run_id,
                    "user_id": USER_ID,
                    "session_id": session_id,
                    "order_no": order_no,
                    "prepay_http": prepay.status_code,
                    "prepay_body": prepay.json(),
                },
            )
            return 1
        params = prepay.json()

    package = str(params.get("package") or "")
    prepay_id = package.removeprefix("prepay_id=")
    print(f"prepay_id_prefix={prepay_id[:6]}... len={len(prepay_id)}")
    print(f"pay_sign_len={len(str(params.get('pay_sign') or ''))}")
    print(f"sign_type={params.get('sign_type')}")

    _write_private(
        STATE_PATH,
        {
            "run_id": run_id,
            "user_id": USER_ID,
            "session_id": session_id,
            "order_no": order_no,
            "amount": order.get("amount"),
            "pay_type": order.get("pay_type"),
            "prepay_http": 200,
            "prepay_id_prefix": prepay_id[:6],
            "prepay_id_len": len(prepay_id),
            "sign_type": params.get("sign_type"),
        },
    )
    print(f"order_no={order_no}")
    print(f"amount={order.get('amount')} status={order.get('status')} pay_type={order.get('pay_type')}")
    print(f"state_file={STATE_PATH}")
    return 0


def _build_test_crypto() -> tuple[WechatPayCrypto, rsa.RSAPrivateKey]:
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
    config = WechatPayConfig(
        mch_id=settings.wechat_pay_mch_id,
        app_id=settings.wechat_pay_app_id,
        serial_no="SMOKE-MCH-SERIAL",
        private_key_pem=private_pem,
        api_v3_key=TEST_API_V3_KEY,
        notify_url="https://xhztest.xyz/api/v1/payments/wechat/notify",
        public_key_id=TEST_PUBLIC_KEY_ID,
        public_key_pem=public_pem,
    )
    return WechatPayCrypto(config), private_key


def _encrypt_resource(plaintext: dict) -> dict:
    nonce = secrets.token_hex(6)  # 12 个 ASCII 字符，与解密侧 encode("utf-8") 对齐
    associated_data = "transaction"
    ciphertext = AESGCM(TEST_API_V3_KEY.encode()).encrypt(
        nonce.encode(),
        json.dumps(plaintext, ensure_ascii=False, separators=(",", ":")).encode(),
        associated_data.encode(),
    )
    return {
        "original_type": "transaction",
        "algorithm": "AEAD_AES_256_GCM",
        "ciphertext": base64.b64encode(ciphertext).decode(),
        "associated_data": associated_data,
        "nonce": nonce,
    }


def _signed_notify(
    private_key: rsa.RSAPrivateKey, state: dict
) -> tuple[bytes, dict[str, str]]:
    notify_id = f"SMOKE-NOTIFY-{state['run_id']}"
    transaction_id = f"SMOKE-TXN-{state['run_id']}"
    plaintext = {
        "appid": settings.wechat_pay_app_id,
        "mchid": settings.wechat_pay_mch_id,
        "out_trade_no": state["order_no"],
        "transaction_id": transaction_id,
        "trade_type": "JSAPI",
        "trade_state": "SUCCESS",
        "trade_state_desc": "支付成功",
        "bank_type": "OTHERS",
        "attach": "smoke-0.01",
        "success_time": _now_iso(),
        "payer": {"openid": "smoke-openid"},
        "amount": {
            "total": 1,
            "payer_total": 1,
            "currency": "CNY",
            "payer_currency": "CNY",
        },
    }
    body = json.dumps(
        {
            "id": notify_id,
            "create_time": _now_iso(),
            "resource_type": "encrypt-resource",
            "event_type": "TRANSACTION.SUCCESS",
            "summary": "支付成功",
            "resource": _encrypt_resource(plaintext),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(8)
    message = f"{timestamp}\n{nonce}\n{body.decode()}\n".encode()
    signature = base64.b64encode(
        private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    headers = {
        "Wechatpay-Timestamp": timestamp,
        "Wechatpay-Nonce": nonce,
        "Wechatpay-Signature": signature,
        "Wechatpay-Serial": TEST_PUBLIC_KEY_ID,
        "Content-Type": "application/json",
    }
    state["notify_id"] = notify_id
    state["transaction_id"] = transaction_id
    return body, headers


async def _order_snapshot(order_no: str) -> dict:
    async with session_factory() as db:
        row = (
            await db.execute(
                text(
                    """SELECT status, amount, paid_amount, transaction_id, pay_type,
                              fulfillment_status
                       FROM payment_order WHERE order_no=:order_no"""
                ),
                {"order_no": order_no},
            )
        ).mappings().first()
        log = (
            await db.execute(
                text(
                    """SELECT COUNT(*) AS n, MAX(processed) AS processed
                       FROM payment_notify_log WHERE out_trade_no=:order_no"""
                ),
                {"order_no": order_no},
            )
        ).mappings().first()
        jobs = (
            await db.execute(
                text(
                    """SELECT COUNT(*) AS n FROM payment_fulfillment_job
                       WHERE order_no=:order_no"""
                ),
                {"order_no": order_no},
            )
        ).scalar()
    return {
        "order": dict(row) if row else None,
        "notify_log_count": int(log["n"]),
        "notify_log_processed": int(log["processed"] or 0),
        "fulfillment_job_count": int(jobs or 0),
    }


async def _notify() -> int:
    import httpx

    from app.api.routes import payments as payment_routes
    from app.main import app

    _guard()
    state = _read_state()
    if "order_no" not in state:
        raise SystemExit("状态文件缺少 order_no")

    crypto, private_key = _build_test_crypto()
    payment_routes.build_payment_crypto = lambda: crypto
    body, headers = _signed_notify(private_key, state)
    _write_private(STATE_PATH, state)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as client:
        first = await client.post(
            "/api/v1/payments/wechat/notify", content=body, headers=headers
        )
        print(f"notify_first_http={first.status_code} body={first.text}")
        after_first = await _order_snapshot(state["order_no"])

        second = await client.post(
            "/api/v1/payments/wechat/notify", content=body, headers=headers
        )
        print(f"notify_second_http={second.status_code} body={second.text}")
        after_second = await _order_snapshot(state["order_no"])

    print(f"after_first={json.dumps(after_first, ensure_ascii=False, default=str)}")
    print(f"after_second={json.dumps(after_second, ensure_ascii=False, default=str)}")

    first_order = after_first["order"] or {}
    second_order = after_second["order"] or {}
    paid = int(first_order.get("status", -1)) == 1
    idempotent = (
        first_order.get("status") == second_order.get("status")
        and str(first_order.get("amount")) == str(second_order.get("amount"))
        and first_order.get("transaction_id") == second_order.get("transaction_id")
        and after_first["notify_log_count"] == 1
        and after_second["notify_log_count"] == 1
        and after_second["fulfillment_job_count"] == 1
    )
    evidence = {
        **state,
        "paid": paid,
        "idempotent": idempotent,
        "notify_first_http": first.status_code,
        "notify_second_http": second.status_code,
        "after_first": after_first,
        "after_second": after_second,
    }
    _write_private(EVIDENCE_PATH, evidence)
    print(f"paid={paid} idempotent={idempotent}")
    print(f"evidence_file={EVIDENCE_PATH}")
    return 0 if paid and idempotent else 1


async def _cleanup() -> int:
    import httpx

    _guard()
    state = _read_state()
    order_no = state.get("order_no")

    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE config_membership_package SET is_active=0 "
                "WHERE code=:code AND name LIKE '【测试】%'"
            ),
            {"code": PACKAGE_CODE},
        )
        await db.commit()
        if order_no:
            await db.execute(
                text("DELETE FROM user_membership WHERE order_no=:order_no"),
                {"order_no": order_no},
            )
            await db.execute(
                text("DELETE FROM payment_fulfillment_job WHERE order_no=:order_no"),
                {"order_no": order_no},
            )
            await db.execute(
                text("DELETE FROM payment_notify_log WHERE out_trade_no=:order_no"),
                {"order_no": order_no},
            )
            await db.execute(
                text("DELETE FROM payment_order WHERE order_no=:order_no"),
                {"order_no": order_no},
            )
        await db.execute(
            text("DELETE FROM user_session WHERE device_id='smoke-0.01'")
        )
        await db.execute(
            text(
                "DELETE FROM config_membership_package "
                "WHERE code=:code AND name LIKE '【测试】%'"
            ),
            {"code": PACKAGE_CODE},
        )
        await db.commit()

        remaining = (
            await db.execute(
                text(
                    """SELECT
                         (SELECT COUNT(*) FROM config_membership_package WHERE code=:code) AS package_rows,
                         (SELECT COUNT(*) FROM payment_order WHERE order_no=:order_no) AS order_rows,
                         (SELECT COUNT(*) FROM payment_fulfillment_job WHERE order_no=:order_no) AS job_rows,
                         (SELECT COUNT(*) FROM user_membership WHERE order_no=:order_no) AS membership_rows"""
                ),
                {"code": PACKAGE_CODE, "order_no": order_no or ""},
            )
        ).mappings().first()
        log_rows = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM payment_notify_log WHERE out_trade_no=:order_no"
                ),
                {"order_no": order_no or ""},
            )
        ).scalar()
        session_rows = (
            await db.execute(
                text("SELECT COUNT(*) FROM user_session WHERE device_id='smoke-0.01'")
            )
        ).scalar()

    print(f"cleanup_remaining={json.dumps(dict(remaining), ensure_ascii=False)}")
    print(f"notify_log_rows={int(log_rows or 0)} session_rows={int(session_rows or 0)}")

    if order_no:
        try:
            from app.services.payment.wechat_v3 import build_payment_runtime

            runtime = build_payment_runtime()
            try:
                await runtime.gateway.close_order(order_no)
                print("wechat_close_order=ok")
            finally:
                await runtime.gateway.aclose()
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响本地回滚
            print(f"wechat_close_order=skipped error={type(exc).__name__}")

    # 直连本机 8000，避免测试服内部 DNS 偶发失败影响清理判定。
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=15) as client:
        packages = await client.get("/api/v1/membership/packages")
        visible = any(
            item.get("code") == PACKAGE_CODE for item in packages.json()
        ) if packages.status_code == 200 else None
    print(f"local_packages_http={packages.status_code} test_product_visible={visible}")

    ok = (
        all(int(v or 0) == 0 for v in dict(remaining).values())
        and int(log_rows or 0) == 0
        and int(session_rows or 0) == 0
        and visible is False
    )
    print(f"cleanup_ok={ok}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="0.01 元微信支付冒烟（仅测试环境）")
    parser.add_argument("phase", choices=["seed", "prepay", "notify", "cleanup"])
    args = parser.parse_args()
    return asyncio.run(
        {
            "seed": _seed,
            "prepay": _prepay,
            "notify": _notify,
            "cleanup": _cleanup,
        }[args.phase]()
    )


if __name__ == "__main__":
    raise SystemExit(main())
