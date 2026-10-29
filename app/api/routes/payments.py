"""Paid discovery products and WeChat Pay APIv3 entry points."""

import logging

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CurrentMatchmakerAdmin,
    CurrentUser,
    get_current_matchmaker_admin,
    get_current_user,
    require_sensitive_operation,
)
from app.core.config import settings
from app.db.session import get_db
from app.schemas.payment import JsapiPayParams, RefundCreateRequest, RefundResponse
from app.schemas.payments import (
    BoostPackage,
    BoostStatus,
    CreateBoostOrderRequest,
    PaidOrderResponse,
    SpotlightPaymentRequest,
)
from app.services.payment.base import (
    PaymentConfigError,
    PaymentDecryptError,
    PaymentError,
    PaymentNotification,
    PaymentSignatureError,
    PaymentUnknownSerialError,
    PaymentValidationError,
)
from app.services.payment.notify import PaymentNotifyLogService
from app.services.payment.orders import PaymentOrderService
from app.services.payment.refunds import RefundService
from app.services.payment.wechat_v3 import (
    WechatPayV3Client,
    build_payment_crypto,
    build_payment_runtime,
    parse_wechat_notification,
)
from app.services.payments import (
    create_boost_order,
    create_spotlight_order,
    get_boost_status,
    get_paid_order,
    list_boost_packages,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/boost/packages", response_model=list[BoostPackage], summary="查询置顶套餐")
async def boost_packages() -> list[BoostPackage]:
    return await list_boost_packages()


@router.get("/users/me/boost/status", response_model=BoostStatus, summary="查询我的置顶状态")
async def boost_status(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> BoostStatus:
    return await get_boost_status(db, current.id)


@router.post("/boost/orders", response_model=PaidOrderResponse, status_code=201, summary="创建置顶测试支付订单")
async def boost_order(
    body: CreateBoostOrderRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaidOrderResponse:
    return await create_boost_order(db, current.id, body, idempotency_key)


@router.get("/boost/orders/{order_no}", response_model=PaidOrderResponse, summary="查询置顶订单")
async def boost_order_detail(order_no: str = Path(..., min_length=8, max_length=64), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PaidOrderResponse:
    return await get_paid_order(db, current.id, order_no, 4)


@router.post("/spotlights/payments", response_model=PaidOrderResponse, status_code=201, summary="创建爆灯测试支付订单")
async def spotlight_order(
    body: SpotlightPaymentRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaidOrderResponse:
    return await create_spotlight_order(db, current.id, body, idempotency_key)


@router.get("/spotlights/payments/{order_no}", response_model=PaidOrderResponse, summary="查询爆灯支付订单")
async def spotlight_order_detail(order_no: str = Path(..., min_length=8, max_length=64), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PaidOrderResponse:
    return await get_paid_order(db, current.id, order_no, 2)


@router.post(
    "/payments/orders/{order_no}/prepay",
    response_model=JsapiPayParams,
    summary="微信支付预下单",
)
async def prepay_order(
    order_no: str = Path(..., min_length=8, max_length=64),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JsapiPayParams:
    """为当前用户的待支付订单创建微信预支付单。

    新链路独立于旧测试支付；``WECHAT_PAY_MODE != real`` 时返回 503。
    """
    try:
        runtime = build_payment_runtime()
    except PaymentConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        service = PaymentOrderService(runtime.gateway, runtime.crypto)
        return await service.prepay(db, current.id, order_no)
    except PaymentValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PaymentError as exc:
        # 微信 4xx/5xx、网络超时、配置错误统一映射为 503；订单状态错误由上面的
        # HTTPException 直接透传。
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        await runtime.gateway.aclose()


@router.post(
    "/payments/wechat/notify",
    status_code=204,
    summary="微信支付 APIv3 回调",
)
async def wechat_notify(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """接收微信支付 APIv3 通知。

    处理顺序：
    1. 读取原始 body；
    2. 验签 + 解密；
    3. 写 ``payment_notify_log`` 去重；
    4. ``TRANSACTION.SUCCESS`` 调 ``PaymentOrderService.mark_paid``；
    5. 成功返回 204；签名/解密失败 401；业务校验失败 409。
    """
    raw_body = await request.body()
    signature_serial = request.headers.get("Wechatpay-Serial")

    try:
        crypto = build_payment_crypto()
        try:
            notification = parse_wechat_notification(crypto, request.headers, raw_body)
        except PaymentUnknownSerialError:
            # 平台证书轮换：刷新一次后重试解析。该 HTTP 调用发生在任何
            # 数据库事务之前，不违反「外部调用不进事务」约束。
            gateway = WechatPayV3Client(crypto.config, crypto)
            try:
                await gateway.refresh_platform_certs()
            finally:
                await gateway.aclose()
            notification = parse_wechat_notification(crypto, request.headers, raw_body)
    except PaymentSignatureError as exc:
        logger.warning("wechat notify signature failed: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": "签名验证失败"},
            status_code=401,
        )
    except PaymentDecryptError as exc:
        logger.warning("wechat notify decrypt failed: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": "通知解密失败"},
            status_code=401,
        )
    except PaymentValidationError as exc:
        logger.warning("wechat notify payload invalid: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": str(exc)},
            status_code=409,
        )
    except PaymentConfigError as exc:
        logger.error("wechat notify config error: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": "支付配置未完成"},
            status_code=409,
        )
    except PaymentError as exc:
        logger.error("wechat notify parse error: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": str(exc)},
            status_code=409,
        )

    notify_log = PaymentNotifyLogService()
    try:
        async with db.begin():
            # 同一 notify_id 只处理一次；上次失败（processed=0）允许重试。
            should_process = await notify_log.record_once(
                db,
                notification=notification,
                raw_body=raw_body,
                signature_serial=signature_serial,
            )
            if not should_process:
                return Response(status_code=204)

            if notification.event_type == "TRANSACTION.SUCCESS":
                # 本轮只落订单支付状态，不写会员/爆灯/服务单履约逻辑；
                # 第三轮由 FulfillmentDispatcher 消费 payment_fulfillment_job。
                await PaymentOrderService().mark_paid(db, notification)

            await notify_log.mark_processed(db, notification.notify_id)
    except PaymentValidationError as exc:
        logger.warning("wechat notify business validation failed: %s", exc)
        await _record_notify_failure(
            db, notify_log, notification, raw_body, signature_serial, str(exc)
        )
        return JSONResponse(
            {"code": "FAIL", "message": str(exc)},
            status_code=409,
        )
    except PaymentError as exc:
        logger.error("wechat notify business error: %s", exc)
        await _record_notify_failure(
            db, notify_log, notification, raw_body, signature_serial, str(exc)
        )
        return JSONResponse(
            {"code": "FAIL", "message": str(exc)},
            status_code=409,
        )
    except Exception:
        logger.exception("wechat notify processing failed")
        await _record_notify_failure(
            db, notify_log, notification, raw_body, signature_serial, "回调处理失败"
        )
        return JSONResponse(
            {"code": "FAIL", "message": "回调处理失败"},
            status_code=409,
        )

    return Response(status_code=204)


@router.post(
    "/admin/finance/orders/{order_id}/refunds",
    response_model=RefundResponse,
    status_code=201,
    summary="后台发起微信退款",
)
async def create_refund_order(
    order_id: int = Path(..., ge=1),
    body: RefundCreateRequest = Body(...),
    idempotency_key: str = Depends(require_sensitive_operation),
    admin: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> RefundResponse:
    """后台发起真实微信退款。

    与旧 ``/admin/finance/orders/{id}/refund``（本地记账）并存：
    本接口只处理 ``WECHAT_PAY_MODE=real`` 的真实微信退款。
    """
    admin.require("finance.write")
    try:
        runtime = build_payment_runtime()
    except PaymentConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        service = RefundService(
            runtime.gateway,
            settings.wechat_pay_refund_notify_url,
        )
        result = await service.create_refund(
            db,
            order_id=order_id,
            amount=body.amount,
            reason=body.reason,
            idempotency_key=idempotency_key,
            operator_id=admin.account.id,
        )
        return RefundResponse(
            refund_no=result.refund_no,
            order_no=result.order_no or "",
            amount=result.amount if result.amount is not None else body.amount,
            refund_type="FULL" if result.refund_type == "FULL" else "PARTIAL",
            status=result.status,
            wechat_refund_id=result.wechat_refund_id,
        )
    except PaymentValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PaymentConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PaymentError as exc:
        # 微信网络/5xx：退款单已保留 CREATED，可走 query_refund 补偿。
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await runtime.gateway.aclose()


@router.post(
    "/payments/wechat/refund-notify",
    status_code=204,
    summary="微信退款 APIv3 回调",
)
async def wechat_refund_notify(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """接收微信退款通知，验签/解密/防重放后更新退款单。"""
    raw_body = await request.body()
    signature_serial = request.headers.get("Wechatpay-Serial")

    try:
        crypto = build_payment_crypto()
        try:
            notification = parse_wechat_notification(crypto, request.headers, raw_body)
        except PaymentUnknownSerialError:
            gateway = WechatPayV3Client(crypto.config, crypto)
            try:
                await gateway.refresh_platform_certs()
            finally:
                await gateway.aclose()
            notification = parse_wechat_notification(crypto, request.headers, raw_body)
    except PaymentSignatureError as exc:
        logger.warning("wechat refund notify signature failed: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": "签名验证失败"},
            status_code=401,
        )
    except PaymentDecryptError as exc:
        logger.warning("wechat refund notify decrypt failed: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": "通知解密失败"},
            status_code=401,
        )
    except PaymentValidationError as exc:
        logger.warning("wechat refund notify payload invalid: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": str(exc)},
            status_code=409,
        )
    except PaymentConfigError as exc:
        logger.error("wechat refund notify config error: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": "支付配置未完成"},
            status_code=409,
        )
    except PaymentError as exc:
        logger.error("wechat refund notify parse error: %s", exc)
        return JSONResponse(
            {"code": "FAIL", "message": str(exc)},
            status_code=409,
        )

    notify_log = PaymentNotifyLogService()
    try:
        async with db.begin():
            should_process = await notify_log.record_once(
                db,
                notification=notification,
                raw_body=raw_body,
                signature_serial=signature_serial,
            )
            if not should_process:
                return Response(status_code=204)

            if notification.event_type.startswith("REFUND."):
                await RefundService().apply_notify(db, notification)

            await notify_log.mark_processed(db, notification.notify_id)
    except PaymentValidationError as exc:
        logger.warning("wechat refund notify validation failed: %s", exc)
        await _record_notify_failure(
            db, notify_log, notification, raw_body, signature_serial, str(exc)
        )
        return JSONResponse(
            {"code": "FAIL", "message": str(exc)},
            status_code=409,
        )
    except PaymentError as exc:
        logger.error("wechat refund notify business error: %s", exc)
        await _record_notify_failure(
            db, notify_log, notification, raw_body, signature_serial, str(exc)
        )
        return JSONResponse(
            {"code": "FAIL", "message": str(exc)},
            status_code=409,
        )
    except Exception:
        logger.exception("wechat refund notify processing failed")
        await _record_notify_failure(
            db, notify_log, notification, raw_body, signature_serial, "回调处理失败"
        )
        return JSONResponse(
            {"code": "FAIL", "message": "回调处理失败"},
            status_code=409,
        )

    return Response(status_code=204)


async def _record_notify_failure(
    db: AsyncSession,
    service: PaymentNotifyLogService,
    notification: PaymentNotification,
    raw_body: bytes,
    signature_serial: str | None,
    error: str,
) -> None:
    """在独立事务中记录回调失败，避免失败信息随业务回滚一起丢失。"""
    try:
        await db.rollback()
        async with db.begin():
            await service.record_failed(
                db,
                notification=notification,
                raw_body=raw_body,
                signature_serial=signature_serial,
                error=error,
            )
    except Exception:
        logger.exception("failed to persist wechat notify failure")
