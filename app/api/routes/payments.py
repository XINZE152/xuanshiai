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
from app.schemas.payment import (
    JsapiPayParams,
    RefundCreateRequest,
    RefundResponse,
    VirtualPayParamsRequest,
    VirtualPayParamsResponse,
    XpayStatusResponse,
)
from app.schemas.payments import (
    BoostPackage,
    BoostStatus,
    CreateBoostOrderRequest,
    PaidOrderResponse,
    SpotlightPaymentRequest,
    SpotlightPriceResponse,
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
from app.services.payment.xpay import XpayService
from app.services.payment.xpay_notify import (
    extract_goods_deliver,
    parse_push_payload,
    verify_url_signature,
)
from app.services.payments import (
    create_boost_order,
    create_spotlight_order,
    get_boost_status,
    get_paid_order,
    get_spotlight_price,
    list_boost_packages,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/boost/packages", response_model=list[BoostPackage], summary="查询置顶套餐")
async def boost_packages(db: AsyncSession = Depends(get_db)) -> list[BoostPackage]:
    return await list_boost_packages(db)


@router.get("/users/me/boost/status", response_model=BoostStatus, summary="查询我的置顶状态")
async def boost_status(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> BoostStatus:
    return await get_boost_status(db, current.id)


@router.post("/boost/orders", response_model=PaidOrderResponse, status_code=201, summary="创建置顶订单")
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


@router.get("/spotlights/price", response_model=SpotlightPriceResponse, summary="查询爆灯单价")
async def spotlight_price(db: AsyncSession = Depends(get_db)) -> SpotlightPriceResponse:
    """公开读接口：C 端卡片按此展示单价，下单金额由服务端按同一配置计算。"""
    return await get_spotlight_price(db)


@router.post("/spotlights/payments", response_model=PaidOrderResponse, status_code=201, summary="创建爆灯订单")
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


@router.get(
    "/payments/xpay/status",
    response_model=XpayStatusResponse,
    summary="虚拟支付开关状态",
)
async def xpay_status() -> XpayStatusResponse:
    """前端据此选择 ``requestVirtualPayment`` 或回退 JSAPI。"""
    mode = settings.wechat_xpay_mode
    return XpayStatusResponse(
        enabled=mode in {"mock", "real"},
        mode=mode,  # type: ignore[arg-type]
        env=int(settings.wechat_xpay_env),  # type: ignore[arg-type]
        offer_id_configured=bool((settings.wechat_xpay_offer_id or "").strip()),
    )


@router.post(
    "/payments/orders/{order_no}/virtual-pay-params",
    response_model=VirtualPayParamsResponse,
    summary="虚拟支付签名参数",
)
async def virtual_pay_params(
    body: VirtualPayParamsRequest,
    order_no: str = Path(..., min_length=8, max_length=64),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VirtualPayParamsResponse:
    """返回 ``wx.requestVirtualPayment`` 所需 signData / 双签名。

    服务端不下单；真正下单发生在客户端拉起支付时。
    ``js_code`` 须为当次 ``uni.login`` 新鲜 code，用于换取 session_key。
    """
    try:
        params = await XpayService().build_virtual_pay_params(
            db, current.id, order_no, body.js_code
        )
    except PaymentConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PaymentValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PaymentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return VirtualPayParamsResponse(
        sign_data=params.sign_data,
        mode="short_series_goods",
        pay_sig=params.pay_sig,
        signature=params.signature,
        offer_id=params.offer_id,
        product_id=params.product_id,
        goods_price=params.goods_price,
        out_trade_no=params.out_trade_no,
        env=params.env,  # type: ignore[arg-type]
    )


@router.post(
    "/payments/orders/{order_no}/xpay-confirm",
    summary="虚拟支付查单兜底确认",
)
async def xpay_confirm_order(
    order_no: str = Path(..., min_length=8, max_length=64),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """前端支付 success 后若推送延迟，可调本接口查单并落库发货。"""
    try:
        order = await XpayService().confirm_by_query(db, current.id, order_no)
    except PaymentConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PaymentValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PaymentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "order_no": order.get("order_no"),
        "status": order.get("status"),
        "transaction_id": order.get("transaction_id"),
    }


@router.api_route(
    "/payments/xpay/notify",
    methods=["GET", "POST"],
    summary="虚拟支付发货推送",
)
async def xpay_notify(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """MP「发货推送」回调：GET 接入校验，POST 道具发货。

    成功响应 ``{"ErrCode":0,"ErrMsg":"success"}``（JSON）或等价空成功，
    否则微信会重试最多 15 次。
    """
    token = (settings.wechat_xpay_push_token or "").strip()

    if request.method == "GET":
        signature = request.query_params.get("signature", "")
        timestamp = request.query_params.get("timestamp", "")
        nonce = request.query_params.get("nonce", "")
        echostr = request.query_params.get("echostr", "")
        if not token or not verify_url_signature(
            token=token,
            timestamp=timestamp,
            nonce=nonce,
            echostr=echostr,
            signature=signature,
        ):
            return Response(content="invalid signature", status_code=403)
        return Response(content=echostr, media_type="text/plain")

    raw_body = await request.body()
    try:
        event = parse_push_payload(
            raw_body,
            content_type=request.headers.get("content-type"),
            msg_signature=request.query_params.get("msg_signature"),
            timestamp=request.query_params.get("timestamp"),
            nonce=request.query_params.get("nonce"),
        )
        event_name = str(event.get("Event") or event.get("event") or "")
        if event_name and event_name != "xpay_goods_deliver_notify":
            # 退款/投诉等事件先 ACK，避免微信无限重试；后续可再拆处理。
            logger.info("xpay notify ignore event=%s", event_name)
            return JSONResponse({"ErrCode": 0, "ErrMsg": "success"})

        deliver = extract_goods_deliver(event)
        async with db.begin():
            await XpayService().mark_paid_from_deliver(
                db,
                out_trade_no=str(deliver["out_trade_no"]),
                transaction_id=(
                    str(deliver["transaction_id"])
                    if deliver.get("transaction_id")
                    else None
                ),
                amount_cents=(
                    int(deliver["amount_cents"])
                    if deliver.get("amount_cents") is not None
                    else None
                ),
                payer_openid=(
                    str(deliver["openid"]) if deliver.get("openid") else None
                ),
                product_id=(
                    str(deliver["product_id"]) if deliver.get("product_id") else None
                ),
            )
    except PaymentSignatureError as exc:
        logger.warning("xpay notify signature failed: %s", exc)
        return JSONResponse({"ErrCode": 1, "ErrMsg": "签名验证失败"}, status_code=401)
    except PaymentDecryptError as exc:
        logger.warning("xpay notify decrypt failed: %s", exc)
        return JSONResponse({"ErrCode": 1, "ErrMsg": "解密失败"}, status_code=401)
    except PaymentValidationError as exc:
        logger.warning("xpay notify validation failed: %s", exc)
        return JSONResponse({"ErrCode": 1, "ErrMsg": str(exc)}, status_code=409)
    except Exception:
        logger.exception("xpay notify processing failed")
        return JSONResponse({"ErrCode": 1, "ErrMsg": "处理失败"}, status_code=409)

    return JSONResponse({"ErrCode": 0, "ErrMsg": "success"})


@router.post(
    "/payments/orders/{order_no}/prepay",
    response_model=JsapiPayParams,
    deprecated=True,
    summary="【已下线】旧版微信支付预下单（虚拟商品请使用虚拟支付）",
)
async def prepay_order(
    order_no: str = Path(..., min_length=8, max_length=64),
    current: CurrentUser = Depends(get_current_user),
) -> JsapiPayParams:
    """旧版 JSAPI 预下单，已整体下线。

    会员/置顶/爆灯/积分均为微信定义的**虚拟商品**，只能通过小程序虚拟支付
    （``POST /payments/orders/{order_no}/virtual-pay-params`` + 前端
    ``wx.requestVirtualPayment``）完成。保留该路径而非直接删除，是为了让
    存量客户端与脚本拿到明确的 410，而不是含糊的 404。

    仍需登录态，避免未授权探测。
    """
    raise HTTPException(
        status_code=410,
        detail="旧版微信支付通道已下线，虚拟商品请使用小程序虚拟支付",
    )


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
