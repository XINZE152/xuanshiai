"""一期订单、分成规则、账本和提现服务。"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser
from app.core.config import settings
from app.schemas.finance import (
    AccountBalanceResponse,
    CommissionEntryResponse,
    CommissionEntryDetailItem,
    CommissionEntryDetailOptions,
    CommissionEntryDetailPage,
    CommissionRuleCreate,
    CommissionRuleResponse,
    CreditGrantRequest,
    CreditGrantResult,
    EventOption,
    FinanceOrderCreate,
    FinanceReportRow,
    FinanceRefundRequest,
    FinanceDailyRow,
    MatchmakerOption,
    PaymentOrderResponse,
    ProductCommissionConfigCreate,
    ProductCommissionConfigResponse,
    WithdrawalCreate,
    WithdrawalResponse,
    WithdrawalReview,
    LedgerEntryPage,
    LedgerEntryResponse,
    PaymentOrderAdminPage,
    WithdrawalAdminPage,
    StoreCommissionEntryItem,
    StoreCommissionEntryPage,
    StoreCommissionOptions,
    StoreCommissionSummary,
    StoreOption,
)
from app.services.matchmaker import activate_paid_service_order
from app.services.runtime_config import withdrawal_policy


CENT = Decimal("0.01")


def _dt(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _order(row: Any) -> PaymentOrderResponse:
    return PaymentOrderResponse(**{**dict(row), "pay_time": _dt(row["pay_time"]) if row["pay_time"] else None, "created_at": _dt(row["created_at"])})


def _withdrawal(row: Any) -> WithdrawalResponse:
    return WithdrawalResponse(**{**dict(row), "created_at": _dt(row["created_at"]), "updated_at": _dt(row["updated_at"])})


def _rule(row: Any) -> CommissionRuleResponse:
    return CommissionRuleResponse(**{**dict(row), "created_at": _dt(row["created_at"])})


def _product_commission(row: Any) -> ProductCommissionConfigResponse:
    return ProductCommissionConfigResponse(**{**dict(row), "created_at": _dt(row["created_at"])})


async def create_product_commission_config(
    db: AsyncSession, admin: CurrentUser, product_id: int, request: ProductCommissionConfigCreate
) -> ProductCommissionConfigResponse:
    product = await db.execute(text("SELECT id FROM matchmaker_service_product WHERE id = :id"), {"id": product_id})
    if not product.scalar():
        raise HTTPException(404, detail="红娘服务商品不存在")
    version = int((await db.execute(text("""SELECT COALESCE(MAX(version), 0) + 1
        FROM product_commission_config WHERE product_id = :product_id AND beneficiary_type = :kind"""), {
            "product_id": product_id, "kind": request.beneficiary_type,
        })).scalar() or 1)
    await db.execute(text("""UPDATE product_commission_config SET status = 2
        WHERE product_id = :product_id AND beneficiary_type = :kind AND status = 1"""), {
        "product_id": product_id, "kind": request.beneficiary_type,
    })
    result = await db.execute(text("""INSERT INTO product_commission_config
        (product_id, beneficiary_type, mode, fixed_amount, rate_percent, version, created_by)
        VALUES (:product_id, :kind, :mode, :fixed_amount, :rate_percent, :version, :admin_id)"""), {
        "product_id": product_id, "kind": request.beneficiary_type, "mode": request.mode,
        "fixed_amount": request.fixed_amount, "rate_percent": request.rate_percent,
        "version": version, "admin_id": admin.id,
    })
    await db.commit()
    row = (await db.execute(text("""SELECT id, product_id, beneficiary_type, mode,
        fixed_amount, rate_percent, version, status, created_at
        FROM product_commission_config WHERE id = :id"""), {"id": result.lastrowid})).mappings().one()
    return _product_commission(row)


async def create_rule(db: AsyncSession, admin: CurrentUser, request: CommissionRuleCreate) -> CommissionRuleResponse:
    version_result = await db.execute(text("SELECT COALESCE(MAX(version), 0) + 1 FROM commission_rule WHERE beneficiary_type = :kind"), {"kind": request.beneficiary_type})
    version = int(version_result.scalar() or 1)
    result = await db.execute(text("""INSERT INTO commission_rule
        (beneficiary_type, name, mode, fixed_amount, rate_percent, priority, version, created_by)
        VALUES (:kind, :name, :mode, :fixed_amount, :rate_percent, :priority, :version, :admin_id)"""), {
        "kind": request.beneficiary_type, "name": request.name, "mode": request.mode,
        "fixed_amount": request.fixed_amount, "rate_percent": request.rate_percent,
        "priority": request.priority, "version": version, "admin_id": admin.id,
    })
    rule_id = int(result.lastrowid)
    await db.commit()
    result = await db.execute(text("""SELECT id, beneficiary_type, name, mode, fixed_amount,
        rate_percent, priority, version, status, created_at FROM commission_rule WHERE id = :id"""), {"id": rule_id})
    return _rule(result.mappings().one())


async def create_order(db: AsyncSession, current: CurrentUser, request: FinanceOrderCreate) -> PaymentOrderResponse:
    import secrets

    order_no = f"XS{datetime.utcnow():%Y%m%d%H%M%S}{current.id:08d}{secrets.token_hex(4)}"
    result = await db.execute(text("""INSERT INTO payment_order
        (user_id, order_no, type, product_type, product_name, amount, status, expire_at)
        VALUES (:user_id, :order_no, :type, :product_type, :name, :amount, 0, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 30 MINUTE))"""), {
        "user_id": current.id, "order_no": order_no, "type": request.product_type,
        "product_type": request.product_type, "name": request.product_name, "amount": request.amount,
    })
    order_id = int(result.lastrowid)
    await db.commit()
    result = await db.execute(text("""SELECT id, order_no, user_id, product_type, product_name,
        amount, status, pay_time, created_at FROM payment_order WHERE id = :id"""), {"id": order_id})
    return _order(result.mappings().one())


async def list_rules(db: AsyncSession) -> list[CommissionRuleResponse]:
    result = await db.execute(text("""SELECT id, beneficiary_type, name, mode, fixed_amount,
        rate_percent, priority, version, status, created_at FROM commission_rule
        WHERE status = 1 ORDER BY beneficiary_type, priority DESC, id DESC"""))
    return [_rule(row) for row in result.mappings().all()]


async def mark_order_paid_and_settle(db: AsyncSession, admin: CurrentUser, order_id: int) -> list[CommissionEntryResponse]:
    if not settings.is_test_mode:
        raise HTTPException(503, detail="支付成功状态必须由真实支付回调确认")
    result = await db.execute(text("""SELECT id, user_id, amount, status, service_product_id,
        matchmaker_id FROM payment_order
        WHERE id = :id FOR UPDATE"""), {"id": order_id})
    order = result.mappings().first()
    if not order:
        raise HTTPException(404, detail="支付订单不存在")
    if order["status"] == 3:
        raise HTTPException(409, detail="已退款订单不能结算")
    if order["status"] == 0:
        await db.execute(text("""UPDATE payment_order SET status = 1, pay_time = UTC_TIMESTAMP(),
            transaction_id = CONCAT('sandbox-', order_no) WHERE id = :id"""), {"id": order_id})
        order = {**dict(order), "status": 1}
    if order.get("service_product_id"):
        await activate_paid_service_order(db, order_id)
    base = Decimal(str(order["amount"])).quantize(CENT)
    beneficiaries: list[tuple[str, int]] = []
    assignment = await db.execute(text("""SELECT organization_id, matchmaker_id FROM resource_assignment
        WHERE user_id = :user_id AND status = 1 ORDER BY effective_at DESC LIMIT 1"""), {"user_id": order["user_id"]})
    assigned = assignment.mappings().first()
    if assigned and assigned["organization_id"]:
        beneficiaries.append(("store", int(assigned["organization_id"])))
    if order.get("matchmaker_id"):
        beneficiaries.append(("service_matchmaker", int(order["matchmaker_id"])))
    elif assigned and assigned["matchmaker_id"]:
        beneficiaries.append(("service_matchmaker", int(assigned["matchmaker_id"])))
    promotion = await db.execute(text("""SELECT promoter_id FROM promotion_attribution
        WHERE user_id = :user_id AND status = 1 LIMIT 1"""), {"user_id": order["user_id"]})
    promoter_id = promotion.scalar()
    if promoter_id:
        beneficiaries.append(("promoter", int(promoter_id)))
        partner = await db.execute(text("""SELECT pm.team_id, pt.owner_user_id FROM partner_membership pm
            JOIN partner_team pt ON pt.id = pm.team_id AND pt.status = 1
            WHERE pm.promoter_id = :promoter_id AND pm.status = 1 LIMIT 1"""), {"promoter_id": promoter_id})
        team = partner.mappings().first()
        if team:
            beneficiaries.append(("partner", int(team["owner_user_id"])))
    total = Decimal("0.00")
    entries: list[CommissionEntryResponse] = []
    for kind, beneficiary_id in beneficiaries:
        rule_table = "product_commission_config" if order.get("service_product_id") else "commission_rule"
        rule_result = await db.execute(text(f"""SELECT id, mode, fixed_amount, rate_percent, version
            FROM {rule_table} WHERE beneficiary_type = :kind AND status = 1
            {"AND product_id = :product_id" if rule_table == "product_commission_config" else ""}
            ORDER BY version DESC, id DESC LIMIT 1"""), {
                "kind": kind, "product_id": order.get("service_product_id"),
            })
        rule = rule_result.mappings().first()
        if not rule:
            continue
        amount = Decimal(str(rule["fixed_amount"])) if rule["mode"] == "fixed" else base * Decimal(str(rule["rate_percent"])) / Decimal("100")
        amount = amount.quantize(CENT, rounding=ROUND_DOWN)
        if amount <= 0:
            continue
        total += amount
        if total > base:
            await db.rollback()
            raise HTTPException(409, detail="当前分成规则总额超过订单可分成金额")
        key = f"commission:{order_id}:{kind}:{beneficiary_id}"
        await db.execute(text("""INSERT INTO commission_entry
            (order_id, beneficiary_type, beneficiary_id, rule_id, rule_version, base_amount, amount, idempotency_key)
            VALUES (:order_id, :kind, :beneficiary_id, :rule_id, :version, :base, :amount, :key)
            ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)"""), {
            "order_id": order_id, "kind": kind, "beneficiary_id": beneficiary_id,
            "rule_id": rule["id"], "version": rule["version"], "base": base, "amount": amount, "key": key,
        })
        entry_id = int((await db.execute(text("SELECT LAST_INSERT_ID()")).scalar()) or 0)
        await db.execute(text("""INSERT INTO account_ledger
            (account_type, account_id, direction, amount, state, source_type, source_id, idempotency_key)
            VALUES (:account_type, :account_id, 'CREDIT', :amount, 'PENDING', 'commission', :source_id, :key)
            ON DUPLICATE KEY UPDATE id = id"""), {
            "account_type": "store" if kind == "store" else "user", "account_id": beneficiary_id,
            "amount": amount, "source_id": entry_id, "key": f"ledger:commission:{entry_id}",
        })
        entry_result = await db.execute(text("""SELECT id, order_id, beneficiary_type, beneficiary_id,
            base_amount, amount, status, created_at FROM commission_entry WHERE id = :id"""), {"id": entry_id})
        entry = entry_result.mappings().one()
        entries.append(CommissionEntryResponse(**{**dict(entry), "created_at": _dt(entry["created_at"]) }))
    await db.commit()
    return entries


async def list_user_commissions(db: AsyncSession, current: CurrentUser) -> list[CommissionEntryResponse]:
    result = await db.execute(text("""SELECT id, order_id, beneficiary_type, beneficiary_id,
        base_amount, amount, status, created_at FROM commission_entry
        WHERE beneficiary_id = :user_id AND beneficiary_type IN ('service_matchmaker', 'promoter', 'partner')
        ORDER BY created_at DESC, id DESC LIMIT 200"""), {"user_id": current.id})
    return [CommissionEntryResponse(**{**dict(row), "created_at": _dt(row["created_at"])}) for row in result.mappings().all()]


async def admin_finance_report(db: AsyncSession) -> list[FinanceReportRow]:
    result = await db.execute(text("""SELECT ce.beneficiary_type, ce.beneficiary_id,
        COUNT(DISTINCT ce.order_id) AS order_count, COALESCE(SUM(ce.amount), 0) AS total_amount,
        COALESCE(SUM(CASE WHEN ce.status = 'PENDING' THEN ce.amount ELSE 0 END), 0) AS pending_amount,
        COALESCE(SUM(CASE WHEN ce.status = 'AVAILABLE' THEN ce.amount ELSE 0 END), 0) AS available_amount
        FROM commission_entry ce GROUP BY ce.beneficiary_type, ce.beneficiary_id
        ORDER BY total_amount DESC"""))
    return [FinanceReportRow(**dict(row)) for row in result.mappings().all()]


async def release_commission(db: AsyncSession, admin: CurrentUser, entry_id: int) -> CommissionEntryResponse:
    result = await db.execute(text("""SELECT id, order_id, beneficiary_type, beneficiary_id,
        base_amount, amount, status, created_at FROM commission_entry WHERE id = :id FOR UPDATE"""), {"id": entry_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="分成明细不存在")
    if row["status"] != "PENDING":
        raise HTTPException(409, detail="当前分成明细不能转为可用")
    await db.execute(text("UPDATE commission_entry SET status = 'AVAILABLE' WHERE id = :id"), {"id": entry_id})
    await db.execute(text("""UPDATE account_ledger SET state = 'AVAILABLE'
        WHERE source_type = 'commission' AND source_id = :entry_id AND direction = 'CREDIT'"""), {"entry_id": entry_id})
    await db.commit()
    result = await db.execute(text("""SELECT id, order_id, beneficiary_type, beneficiary_id,
        base_amount, amount, status, created_at FROM commission_entry WHERE id = :id"""), {"id": entry_id})
    row = result.mappings().one()
    return CommissionEntryResponse(**{**dict(row), "created_at": _dt(row["created_at"])})


async def refund_order(db: AsyncSession, admin: CurrentUser, order_id: int, request: FinanceRefundRequest) -> None:
    result = await db.execute(text("""SELECT id, status, service_request_id
        FROM payment_order WHERE id = :id FOR UPDATE"""), {"id": order_id})
    order = result.mappings().first()
    if not order:
        raise HTTPException(404, detail="支付订单不存在")
    if order["status"] != 1:
        raise HTTPException(409, detail="只有支付成功订单才能退款")
    entries = await db.execute(text("""SELECT id, beneficiary_type, beneficiary_id, amount, status
        FROM commission_entry WHERE order_id = :order_id AND status <> 'REVERSED' FOR UPDATE"""), {"order_id": order_id})
    for entry in entries.mappings().all():
        await db.execute(text("UPDATE commission_entry SET status = 'REVERSED' WHERE id = :id"), {"id": entry["id"]})
        state = "AVAILABLE" if entry["status"] == "AVAILABLE" else "PENDING"
        account_type = "store" if entry["beneficiary_type"] == "store" else "user"
        await db.execute(text("""INSERT INTO account_ledger
            (account_type, account_id, direction, amount, state, source_type, source_id, idempotency_key)
            VALUES (:account_type, :account_id, 'DEBIT', :amount, :state, 'commission_refund', :source_id, :key)
            ON DUPLICATE KEY UPDATE id = id"""), {
            "account_type": account_type, "account_id": entry["beneficiary_id"], "amount": entry["amount"],
            "state": state, "source_id": entry["id"], "key": f"ledger:commission-refund:{entry['id']}",
        })
    service_result = await db.execute(text("""SELECT id FROM matchmaker_service
        WHERE order_id = :order_id FOR UPDATE"""), {"order_id": order_id})
    for service_row in service_result.mappings().all():
        service_id = int(service_row["id"])
        await db.execute(text("""UPDATE matchmaker_service SET status = 3,
            feedback = CONCAT(COALESCE(feedback, ''), '\n退款关闭：', :reason),
            end_at = COALESCE(end_at, UTC_TIMESTAMP()), updated_at = UTC_TIMESTAMP()
            WHERE id = :service_id AND status <> 3"""), {"service_id": service_id, "reason": request.reason})
        await db.execute(text("""UPDATE matchmaker_contact_exchange SET status = 'HIDDEN',
            hidden_at = UTC_TIMESTAMP(), hidden_reason = '订单退款', updated_at = UTC_TIMESTAMP()
            WHERE service_id = :service_id AND status NOT IN ('REVOKED', 'HIDDEN')"""), {"service_id": service_id})
        await db.execute(text("""UPDATE meeting_request SET status = 'CLOSED', updated_at = UTC_TIMESTAMP()
            WHERE service_id = :service_id AND status IN ('SUBMITTED', 'CONTACTED', 'ACCEPTED')"""), {"service_id": service_id})
        await db.execute(text("""UPDATE meeting_record mr JOIN meeting_request rq ON rq.id = mr.request_id
            SET mr.status = 'CANCELLED', mr.cancel_reason = '关联红娘服务已退款', mr.updated_at = UTC_TIMESTAMP()
            WHERE rq.service_id = :service_id AND mr.status IN ('SCHEDULED', 'REMINDED')"""), {"service_id": service_id})
        await db.execute(text("""INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, reason)
            VALUES (:admin_id, 'matchmaker_service.refund_close', 'matchmaker_service', :service_id, :reason)"""), {
            "admin_id": admin.id, "service_id": service_id, "reason": request.reason,
        })
    await db.execute(text("UPDATE payment_order SET status = 3, refund_time = UTC_TIMESTAMP() WHERE id = :id"), {"id": order_id})
    await db.commit()


async def get_balance(db: AsyncSession, account_type: str, account_id: int) -> AccountBalanceResponse:
    result = await db.execute(text("""SELECT
        COALESCE(SUM(CASE WHEN state = 'PENDING' AND direction = 'CREDIT' THEN amount WHEN state = 'PENDING' AND direction = 'DEBIT' THEN -amount ELSE 0 END), 0) AS pending_amount,
        COALESCE(SUM(CASE WHEN state = 'AVAILABLE' AND direction = 'CREDIT' THEN amount WHEN state = 'AVAILABLE' AND direction = 'DEBIT' THEN -amount ELSE 0 END), 0) AS available_amount
        FROM account_ledger WHERE account_type = :account_type AND account_id = :account_id"""), {"account_type": account_type, "account_id": account_id})
    row = result.mappings().one()
    return AccountBalanceResponse(account_type=account_type, account_id=account_id, pending_amount=row["pending_amount"], available_amount=row["available_amount"])


async def request_withdrawal(db: AsyncSession, current: CurrentUser, request: WithdrawalCreate) -> WithdrawalResponse:
    # 财务配置：提现开关与单笔最低金额（后台「系统配置」页实时可调）
    policy = await withdrawal_policy(db)
    if not policy["enabled"]:
        raise HTTPException(403, detail="提现功能暂未开放，请联系平台客服")
    min_amount = Decimal(str(policy["min_amount"] or "0"))
    if request.amount < min_amount:
        raise HTTPException(422, detail=f"单笔提现金额不能低于 {min_amount:.2f} 元")
    # Serialize balance checks with other withdrawals for the same account.
    await db.execute(text("""SELECT id FROM account_ledger
        WHERE account_type = 'user' AND account_id = :user_id FOR UPDATE"""), {"user_id": current.id})
    balance = await get_balance(db, "user", current.id)
    if Decimal(str(balance.available_amount)) < request.amount:
        raise HTTPException(409, detail="可提现余额不足")
    result = await db.execute(text("""INSERT INTO withdrawal_request
        (account_type, account_id, amount, payee_masked) VALUES ('user', :user_id, :amount, :payee)"""), {
        "user_id": current.id, "amount": request.amount, "payee": request.payee_masked,
    })
    withdrawal_id = int(result.lastrowid)
    await db.execute(text("""INSERT INTO account_ledger
        (account_type, account_id, direction, amount, state, source_type, source_id, idempotency_key)
        VALUES ('user', :user_id, 'DEBIT', :amount, 'AVAILABLE', 'withdrawal', :source_id, :key)"""), {
        "user_id": current.id, "amount": request.amount, "source_id": withdrawal_id, "key": f"ledger:withdrawal:{withdrawal_id}",
    })
    await db.commit()
    result = await db.execute(text("""SELECT id, account_type, account_id, amount, status,
        payee_masked, failure_reason, created_at, updated_at FROM withdrawal_request WHERE id = :id"""), {"id": withdrawal_id})
    return _withdrawal(result.mappings().one())


async def review_withdrawal(db: AsyncSession, admin: CurrentUser, withdrawal_id: int, request: WithdrawalReview) -> WithdrawalResponse:
    result = await db.execute(text("""SELECT id, account_type, account_id, amount, status,
        payee_masked, failure_reason, created_at, updated_at FROM withdrawal_request WHERE id = :id FOR UPDATE"""), {"id": withdrawal_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, detail="提现申请不存在")
    allowed = {
        "PENDING_REVIEW": {"APPROVED", "REJECTED"},
        "APPROVED": {"PROCESSING", "FAILED", "REJECTED"},
        "PROCESSING": {"SUCCEEDED", "FAILED"},
    }
    if request.status not in allowed.get(row["status"], set()):
        raise HTTPException(409, detail="提现申请已经结束")
    if request.status in ("REJECTED", "FAILED"):
        await db.execute(text("""INSERT INTO account_ledger
            (account_type, account_id, direction, amount, state, source_type, source_id, idempotency_key)
            VALUES (:account_type, :account_id, 'CREDIT', :amount, 'AVAILABLE', 'withdrawal_reversal', :source_id, :key)
            ON DUPLICATE KEY UPDATE id = id"""), {
            "account_type": row["account_type"], "account_id": row["account_id"], "amount": row["amount"],
            "source_id": withdrawal_id, "key": f"ledger:withdrawal-reversal:{withdrawal_id}",
        })
    await db.execute(text("""UPDATE withdrawal_request SET status = :status,
        failure_reason = :reason, reviewed_by = :admin_id, reviewed_at = UTC_TIMESTAMP(), updated_at = UTC_TIMESTAMP()
        WHERE id = :id"""), {"status": request.status, "reason": request.failure_reason, "admin_id": admin.id, "id": withdrawal_id})
    await db.commit()
    result = await db.execute(text("""SELECT id, account_type, account_id, amount, status,
        payee_masked, failure_reason, created_at, updated_at FROM withdrawal_request WHERE id = :id"""), {"id": withdrawal_id})
    return _withdrawal(result.mappings().one())


async def admin_list_orders(db: AsyncSession, page: int, page_size: int, status: int | None = None, user_id: int | None = None, order_no: str | None = None, start_time: str | None = None, end_time: str | None = None) -> PaymentOrderAdminPage:
    where = ["1 = 1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status is not None:
        where.append("po.status = :status")
        params["status"] = status
    if user_id is not None:
        where.append("po.user_id = :user_id")
        params["user_id"] = user_id
    if order_no:
        where.append("po.order_no = :order_no")
        params["order_no"] = order_no
    if start_time:
        where.append("po.created_at >= CONCAT(:start_time, ' 00:00:00')")
        params["start_time"] = start_time
    if end_time:
        where.append("po.created_at < DATE_ADD(CONCAT(:end_time, ' 00:00:00'), INTERVAL 1 DAY)")
        params["end_time"] = end_time
    clause = " AND ".join(where)
    rows = await db.execute(text(f"""SELECT po.id, po.order_no, po.user_id,
        po.product_type, po.product_name, po.amount, po.status, po.pay_time, po.created_at
        FROM payment_order po WHERE {clause}
        ORDER BY po.id DESC LIMIT :limit OFFSET :offset"""), params)
    count = await db.execute(text(f"SELECT COUNT(*) FROM payment_order po WHERE {clause}"),
        {key: value for key, value in params.items() if key not in ("limit", "offset")})
    total = int(count.scalar() or 0)
    return PaymentOrderAdminPage(items=[_order(row) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def admin_list_withdrawals(db: AsyncSession, page: int, page_size: int, status: str | None = None, account_id: int | None = None, start_time: str | None = None, end_time: str | None = None) -> WithdrawalAdminPage:
    where = ["1 = 1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if status:
        where.append("status = :status")
        params["status"] = status
    if account_id is not None:
        where.append("account_id = :account_id")
        params["account_id"] = account_id
    if start_time:
        where.append("created_at >= CONCAT(:start_time, ' 00:00:00')")
        params["start_time"] = start_time
    if end_time:
        where.append("created_at < DATE_ADD(CONCAT(:end_time, ' 00:00:00'), INTERVAL 1 DAY)")
        params["end_time"] = end_time
    clause = " AND ".join(where)
    rows = await db.execute(text(f"""SELECT id, account_type, account_id, amount, status,
        payee_masked, failure_reason, created_at, updated_at FROM withdrawal_request
        WHERE {clause} ORDER BY id DESC LIMIT :limit OFFSET :offset"""), params)
    count = await db.execute(text(f"SELECT COUNT(*) FROM withdrawal_request WHERE {clause}"),
        {key: value for key, value in params.items() if key not in ("limit", "offset")})
    total = int(count.scalar() or 0)
    return WithdrawalAdminPage(items=[_withdrawal(row) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def admin_revenue_daily_report(
    db: AsyncSession, start_date: str | None = None, end_date: str | None = None
) -> list[FinanceDailyRow]:
    """后台统计报表：按支付日期聚合订单收入与退款（status=1 已支付 / status=3 已退款）。"""
    where = ["po.pay_time IS NOT NULL"]
    params: dict[str, object] = {}
    if start_date:
        where.append("DATE(po.pay_time) >= :start_date")
        params["start_date"] = start_date
    if end_date:
        where.append("DATE(po.pay_time) <= :end_date")
        params["end_date"] = end_date
    clause = " AND ".join(where)
    rows = (await db.execute(text(f"""SELECT DATE_FORMAT(po.pay_time, '%Y-%m-%d') AS date,
        COUNT(CASE WHEN po.status = 1 THEN 1 END) AS pay_count,
        COALESCE(SUM(CASE WHEN po.status = 1 THEN po.amount ELSE 0 END), 0) AS income_amount,
        COUNT(CASE WHEN po.status = 3 THEN 1 END) AS refund_count,
        COALESCE(SUM(CASE WHEN po.status = 3 THEN po.amount ELSE 0 END), 0) AS refund_amount
        FROM payment_order po WHERE {clause}
        GROUP BY date ORDER BY date DESC"""), params)).mappings().all()
    return [FinanceDailyRow(
        date=str(row["date"]),
        pay_count=int(row["pay_count"] or 0),
        income_amount=Decimal(str(row["income_amount"] or 0)),
        refund_count=int(row["refund_count"] or 0),
        refund_amount=Decimal(str(row["refund_amount"] or 0)),
    ) for row in rows]


async def admin_list_ledger(db: AsyncSession, page: int, page_size: int, account_type: str | None = None, account_id: int | None = None, start_time: str | None = None, end_time: str | None = None) -> LedgerEntryPage:
    where = ["1 = 1"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if account_type:
        where.append("account_type = :account_type")
        params["account_type"] = account_type
    if account_id is not None:
        where.append("account_id = :account_id")
        params["account_id"] = account_id
    if start_time:
        where.append("created_at >= CONCAT(:start_time, ' 00:00:00')")
        params["start_time"] = start_time
    if end_time:
        where.append("created_at < DATE_ADD(CONCAT(:end_time, ' 00:00:00'), INTERVAL 1 DAY)")
        params["end_time"] = end_time
    clause = " AND ".join(where)
    rows = await db.execute(text(f"""SELECT id, account_type, account_id, direction, amount,
        state, source_type, source_id, idempotency_key, created_at
        FROM account_ledger WHERE {clause} ORDER BY id DESC LIMIT :limit OFFSET :offset"""), params)
    count = await db.execute(text(f"SELECT COUNT(*) FROM account_ledger WHERE {clause}"),
        {key: value for key, value in params.items() if key not in ("limit", "offset")})
    total = int(count.scalar() or 0)
    return LedgerEntryPage(items=[LedgerEntryResponse(**dict(row)) for row in rows.mappings().all()], page=page, page_size=page_size, total=total, has_more=page * page_size < total)


# ============================================
# M9-A 后台「积分明细-发放积分」service
# ============================================
# 目标解析：
#   all              → users.status=1 全部账号
#   member           → user_ids（入参校验非空）
#   verified         → users JOIN user_auth WHERE ua.realname_status=2
#   matchmaker_team  → user_role.role_code IN ('service_matchmaker','promoter') status=1
# 行为：
#   - 分批 500 写 account_ledger(CREDIT, AVAILABLE, source_type='admin_grant')
#   - 单条 idempotency_key = 'credit-grant:<admin.id>:<user_id>:<amount>:<reason>' 防重
#   - 单条 business_audit_log(actor_user_id, action='finance.credit_grant',
#     resource_type='finance', reason, after_json)
#   - 返回：granted_count / total_amount / sample_ledger_ids / target_user_ids
# 注意：
#   - 积分（amount）字段是整数（积分名称/比例由 finance 配置域决定，本表只记积分）。
#   - AVAILABLE 状态：直接到账，不进 PENDING 队列（区别于 commission）。
#   - 不动 payment_order、不写 commission_entry、不触发分账。


async def _resolve_credit_targets(db: AsyncSession, target_type: str, user_ids: list[int] | None) -> list[int]:
    if target_type == "member":
        return list({int(uid) for uid in (user_ids or []) if int(uid) > 0})
    if target_type == "all":
        rows = await db.execute(text("SELECT id FROM users WHERE status = 1 ORDER BY id"))
        return [int(r[0]) for r in rows.all()]
    if target_type == "verified":
        rows = await db.execute(
            text(
                """SELECT u.id FROM users u
                   JOIN user_auth ua ON ua.user_id = u.id
                   WHERE u.status = 1 AND ua.realname_status = 2
                   ORDER BY u.id"""
            )
        )
        return [int(r[0]) for r in rows.all()]
    if target_type == "matchmaker_team":
        rows = await db.execute(
            text(
                """SELECT DISTINCT ur.user_id FROM user_role ur
                   WHERE ur.role_code IN ('service_matchmaker', 'promoter') AND ur.status = 1
                   ORDER BY ur.user_id"""
            )
        )
        return [int(r[0]) for r in rows.all()]
    raise HTTPException(422, detail=f"不支持的发放目标类型：{target_type}")


async def admin_grant_credits(db: AsyncSession, admin: CurrentUser, request: CreditGrantRequest) -> CreditGrantResult:
    targets = await _resolve_credit_targets(db, request.target_type, request.user_ids)
    if not targets:
        raise HTTPException(404, detail="发放目标为空，请检查 target_type 或 user_ids")
    batch_size = 500
    ledger_ids: list[int] = []
    for offset in range(0, len(targets), batch_size):
        batch = targets[offset : offset + batch_size]
        for user_id in batch:
            result = await db.execute(
                text(
                    """INSERT INTO account_ledger
                       (account_type, account_id, direction, amount, state, source_type, source_id, idempotency_key)
                       VALUES ('user', :user_id, 'CREDIT', :amount, 'AVAILABLE', 'admin_grant', :source_id, :key)"""
                ),
                {
                    "user_id": user_id,
                    "amount": request.amount,
                    "source_id": admin.id,
                    "key": f"credit-grant:{admin.id}:{user_id}:{request.amount}:{request.reason}",
                },
            )
            ledger_ids.append(int(result.lastrowid or 0))
    # 单条 audit_log（前端/财务对账只需要一次总览）
    await db.execute(
        text(
            """INSERT INTO business_audit_log
               (actor_user_id, action, resource_type, resource_id, reason, after_json)
               VALUES (:admin_id, 'finance.credit_grant', 'finance', NULL, :reason, :after_json)"""
        ),
        {
            "admin_id": admin.id,
            "reason": request.reason,
            "after_json": json.dumps(
                {
                    "target_type": request.target_type,
                    "granted_count": len(targets),
                    "amount_per_user": request.amount,
                    "total_amount": len(targets) * request.amount,
                    "ledger_ids": ledger_ids[:10],
                },
                ensure_ascii=False,
            ),
        },
    )
    await db.commit()
    return CreditGrantResult(
        granted_count=len(targets),
        total_amount=len(targets) * request.amount,
        sample_ledger_ids=ledger_ids[:10],
        target_user_ids=targets,
    )


# ============================================
# 后台「红娘线上分成明细」页 service
# ============================================
# 字段策略：
# - store_name：业务上「总店红娘」统一为「总店」
# - 红娘：用 beneficiaries 表的 beneficiary_id JOIN users.nickname
# - 消费会员：用 payment_order.user_id JOIN users.nickname/phone/avatar
# - 事件名：commission_rule.name 优先，无则回退 payment_order.product_name
# - consumer_amount：commission_entry.base_amount（业务上等于订单可分成基数）
# - 时间区间：created_at 区间，end_date < DATE_ADD(end, 1 DAY) 保证整天可达


_DETAIL_BENEFICIARY_TYPE = "service_matchmaker"


def _detail_item(row: dict) -> CommissionEntryDetailItem:
    payload = dict(row)
    payload["created_at"] = _dt(row["created_at"])
    payload["store_name"] = "总店"
    payload["matchmaker_id"] = int(row["beneficiary_id"])
    payload["matchmaker_name"] = row.get("matchmaker_name") or f"红娘#{row['beneficiary_id']}"
    payload["matchmaker_avatar"] = row.get("matchmaker_avatar")
    payload["consumer_id"] = int(row["consumer_id"]) if row.get("consumer_id") else 0
    payload["consumer_name"] = row.get("consumer_name") or (f"用户#{payload['consumer_id']}" if payload["consumer_id"] else "未知会员")
    payload["consumer_phone"] = row.get("consumer_phone")
    payload["consumer_avatar"] = row.get("consumer_avatar")
    payload["event_name"] = row.get("event_name") or row.get("product_name") or "其他事件"
    payload["order_id"] = int(row["order_id"])
    payload["order_no"] = row.get("order_no")
    payload["consumer_amount"] = Decimal(str(row["base_amount"]))
    payload["commission_amount"] = Decimal(str(row["amount"]))
    return CommissionEntryDetailItem(**payload)


async def admin_list_commission_entries(
    db: AsyncSession,
    page: int,
    page_size: int,
    matchmaker_id: int | None = None,
    rule_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> CommissionEntryDetailPage:
    where = [f"ce.beneficiary_type = '{_DETAIL_BENEFICIARY_TYPE}'"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if matchmaker_id is not None:
        where.append("ce.beneficiary_id = :matchmaker_id")
        params["matchmaker_id"] = matchmaker_id
    if rule_id is not None:
        where.append("ce.rule_id = :rule_id")
        params["rule_id"] = rule_id
    if start_date:
        where.append("ce.created_at >= :start_date")
        params["start_date"] = f"{start_date} 00:00:00"
    if end_date:
        where.append("ce.created_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")
        params["end_date"] = f"{end_date} 00:00:00"
    clause = " AND ".join(where)

    list_sql = f"""SELECT ce.id, ce.created_at, ce.beneficiary_id, ce.beneficiary_type,
        ce.order_id, ce.base_amount, ce.amount, ce.status, ce.rule_id,
        m.nickname AS matchmaker_name, m.avatar AS matchmaker_avatar,
        po.order_no, po.user_id AS consumer_id, po.product_name,
        c.nickname AS consumer_name, c.phone AS consumer_phone, c.avatar AS consumer_avatar,
        COALESCE(rule.name, po.product_name) AS event_name
        FROM commission_entry ce
        LEFT JOIN users m ON m.id = ce.beneficiary_id
        LEFT JOIN payment_order po ON po.id = ce.order_id
        LEFT JOIN users c ON c.id = po.user_id
        LEFT JOIN commission_rule rule ON rule.id = ce.rule_id
        WHERE {clause}
        ORDER BY ce.created_at DESC, ce.id DESC
        LIMIT :limit OFFSET :offset"""
    rows = (await db.execute(text(list_sql), params)).mappings().all()

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = int(
        (
            await db.execute(
                text(f"SELECT COUNT(*) FROM commission_entry ce WHERE {clause}"),
                count_params,
            )
        ).scalar()
        or 0
    )

    return CommissionEntryDetailPage(
        items=[_detail_item(dict(row)) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def admin_list_commission_options(db: AsyncSession) -> CommissionEntryDetailOptions:
    """一次性返回筛选下拉：当前产生过分成的总店红娘列表 + 启用的分成规则列表"""
    matchmaker_rows = (
        await db.execute(
            text(
                """SELECT DISTINCT u.id, COALESCE(u.nickname, CONCAT('红娘#', u.id)) AS name, u.avatar
                   FROM commission_entry ce
                   JOIN users u ON u.id = ce.beneficiary_id
                   WHERE ce.beneficiary_type = :kind
                   ORDER BY u.id DESC"""
            ),
            {"kind": _DETAIL_BENEFICIARY_TYPE},
        )
    ).mappings().all()
    event_rows = (
        await db.execute(
            text(
                """SELECT id, name, beneficiary_type
                   FROM commission_rule
                   WHERE status = 1
                   ORDER BY beneficiary_type, priority DESC, id DESC"""
            )
        )
    ).mappings().all()
    return CommissionEntryDetailOptions(
        matchmakers=[MatchmakerOption(**dict(row)) for row in matchmaker_rows],
        events=[EventOption(**dict(row)) for row in event_rows],
    )


# ------------------------- M5 分店分成明细 -------------------------
_STORE_BENEFICIARY_TYPE = "store"


def _store_entry_item(row: dict) -> StoreCommissionEntryItem:
    payload = dict(row)
    payload["created_at"] = _dt(row["created_at"])
    payload["store_id"] = int(row["store_id"])
    payload["store_name"] = row.get("store_name") or f"分店#{payload['store_id']}"
    payload["matchmaker_id"] = int(row["matchmaker_id"]) if row.get("matchmaker_id") else None
    payload["matchmaker_name"] = row.get("matchmaker_name")
    payload["consumer_id"] = int(row["consumer_id"]) if row.get("consumer_id") else None
    payload["consumer_name"] = row.get("consumer_name")
    payload["event_name"] = row.get("event_name") or row.get("product_name") or "其他事件"
    payload["order_id"] = int(row["order_id"])
    payload["order_no"] = row.get("order_no")
    payload["consumer_amount"] = Decimal(str(row["base_amount"]))
    payload["commission_amount"] = Decimal(str(row["amount"]))
    return StoreCommissionEntryItem(**payload)


async def admin_list_store_commission_entries(
    db: AsyncSession,
    page: int,
    page_size: int,
    store_id: int | None = None,
    matchmaker_id: int | None = None,
    rule_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> StoreCommissionEntryPage:
    """分店线上分成明细：beneficiary_type='store'，beneficiary_id=organization.id。"""
    where = [f"ce.beneficiary_type = '{_STORE_BENEFICIARY_TYPE}'"]
    params: dict[str, object] = {"limit": page_size, "offset": (page - 1) * page_size}
    if store_id is not None:
        where.append("ce.beneficiary_id = :store_id")
        params["store_id"] = store_id
    if matchmaker_id is not None:
        # 通过订单所属会员的生效归属反查红娘（分店成员）
        where.append(
            "EXISTS (SELECT 1 FROM resource_assignment ra WHERE ra.user_id = po.user_id "
            "AND ra.status = 1 AND ra.matchmaker_id = :matchmaker_id)"
        )
        params["matchmaker_id"] = matchmaker_id
    if rule_id is not None:
        where.append("ce.rule_id = :rule_id")
        params["rule_id"] = rule_id
    if start_date:
        where.append("ce.created_at >= :start_date")
        params["start_date"] = f"{start_date} 00:00:00"
    if end_date:
        where.append("ce.created_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")
        params["end_date"] = f"{end_date} 00:00:00"
    clause = " AND ".join(where)

    rows = (
        await db.execute(
            text(f"""SELECT ce.id, ce.created_at, ce.beneficiary_id store_id,
                ce.order_id, ce.base_amount, ce.amount, ce.status, ce.rule_id,
                COALESCE(o.display_name, o.name) store_name,
                ra.matchmaker_id, m.nickname matchmaker_name,
                po.order_no, po.user_id AS consumer_id, po.product_name,
                c.nickname AS consumer_name,
                COALESCE(rule.name, po.product_name) AS event_name
                FROM commission_entry ce
                LEFT JOIN organization o ON o.id = ce.beneficiary_id AND o.org_type = 'store'
                LEFT JOIN payment_order po ON po.id = ce.order_id
                LEFT JOIN users c ON c.id = po.user_id
                LEFT JOIN resource_assignment ra ON ra.user_id = po.user_id AND ra.status = 1
                LEFT JOIN users m ON m.id = ra.matchmaker_id
                LEFT JOIN commission_rule rule ON rule.id = ce.rule_id
                WHERE {clause}
                ORDER BY ce.created_at DESC, ce.id DESC
                LIMIT :limit OFFSET :offset"""),
            params,
        )
    ).mappings().all()

    total = int(
        (
            await db.execute(
                text(f"""SELECT COUNT(*) FROM commission_entry ce
                    LEFT JOIN payment_order po ON po.id = ce.order_id WHERE {clause}"""),
                {key: value for key, value in params.items() if key not in ("limit", "offset")},
            )
        ).scalar()
        or 0
    )
    return StoreCommissionEntryPage(
        items=[_store_entry_item(dict(row)) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def admin_store_commission_options(db: AsyncSession) -> StoreCommissionOptions:
    """分店分成明细筛选下拉：门店列表 + 产生过分成的红娘 + 启用分成规则。"""
    store_rows = (
        await db.execute(
            text("""SELECT id, COALESCE(display_name, name) name, status
                FROM organization WHERE org_type = 'store' ORDER BY sort_order DESC, id DESC""")
        )
    ).mappings().all()
    matchmaker_rows = (
        await db.execute(
            text(
                """SELECT DISTINCT m.id, COALESCE(m.nickname, CONCAT('红娘#', m.id)) AS name, m.avatar
                   FROM commission_entry ce
                   JOIN payment_order po ON po.id = ce.order_id
                   JOIN resource_assignment ra ON ra.user_id = po.user_id AND ra.status = 1
                   JOIN users m ON m.id = ra.matchmaker_id
                   WHERE ce.beneficiary_type = :kind
                   ORDER BY m.id DESC"""
            ),
            {"kind": _STORE_BENEFICIARY_TYPE},
        )
    ).mappings().all()
    event_rows = (
        await db.execute(
            text("""SELECT id, name, beneficiary_type FROM commission_rule
                WHERE status = 1 ORDER BY beneficiary_type, priority DESC, id DESC""")
        )
    ).mappings().all()
    return StoreCommissionOptions(
        stores=[StoreOption(**dict(row)) for row in store_rows],
        matchmakers=[MatchmakerOption(**dict(row)) for row in matchmaker_rows],
        events=[EventOption(**dict(row)) for row in event_rows],
    )


async def admin_store_commission_summary(db: AsyncSession, store_id: int | None = None) -> StoreCommissionSummary:
    """分店分成 4 张统计卡：累计分得 / 本月分成 / 上月分成 / 待结算余额。"""
    where = ["ce.beneficiary_type = :kind", "ce.status <> 'REVERSED'"]
    params: dict[str, object] = {"kind": _STORE_BENEFICIARY_TYPE}
    if store_id is not None:
        where.append("ce.beneficiary_id = :store_id")
        params["store_id"] = store_id
    clause = " AND ".join(where)
    row = (
        await db.execute(
            text(f"""SELECT
                COALESCE(SUM(ce.amount), 0) total_amount,
                COALESCE(SUM(CASE WHEN ce.created_at >= DATE_FORMAT(UTC_TIMESTAMP(), '%%Y-%%m-01')
                    THEN ce.amount ELSE 0 END), 0) current_month_amount,
                COALESCE(SUM(CASE WHEN ce.created_at >= DATE_FORMAT(DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 MONTH), '%%Y-%%m-01')
                    AND ce.created_at < DATE_FORMAT(UTC_TIMESTAMP(), '%%Y-%%m-01')
                    THEN ce.amount ELSE 0 END), 0) previous_month_amount,
                COALESCE(SUM(CASE WHEN ce.status = 'PENDING' THEN ce.amount ELSE 0 END), 0) pending_amount
                FROM commission_entry ce WHERE {clause}"""),
            params,
        )
    ).mappings().one()
    return StoreCommissionSummary(
        total_amount=Decimal(str(row["total_amount"])),
        current_month_amount=Decimal(str(row["current_month_amount"])),
        previous_month_amount=Decimal(str(row["previous_month_amount"])),
        pending_amount=Decimal(str(row["pending_amount"])),
    )


_STORE_ENTRY_EXPORT_HEADERS = [
    "ID", "时间", "分店名称", "红娘", "消费会员", "分成/奖励事件", "消费金额", "分成金额", "状态",
]


async def build_store_commission_export(
    db: AsyncSession,
    store_id: int | None = None,
    matchmaker_id: int | None = None,
    rule_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> bytes:
    """导出分店线上分成明细为 .xlsx（当前筛选条件的全量数据）。"""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    where = [f"ce.beneficiary_type = '{_STORE_BENEFICIARY_TYPE}'"]
    params: dict[str, object] = {}
    if store_id is not None:
        where.append("ce.beneficiary_id = :store_id")
        params["store_id"] = store_id
    if matchmaker_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM resource_assignment ra WHERE ra.user_id = po.user_id "
            "AND ra.status = 1 AND ra.matchmaker_id = :matchmaker_id)"
        )
        params["matchmaker_id"] = matchmaker_id
    if rule_id is not None:
        where.append("ce.rule_id = :rule_id")
        params["rule_id"] = rule_id
    if start_date:
        where.append("ce.created_at >= :start_date")
        params["start_date"] = f"{start_date} 00:00:00"
    if end_date:
        where.append("ce.created_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")
        params["end_date"] = f"{end_date} 00:00:00"
    clause = " AND ".join(where)
    rows = (
        await db.execute(
            text(f"""SELECT ce.id, ce.created_at, ce.beneficiary_id store_id,
                ce.order_id, ce.base_amount, ce.amount, ce.status,
                COALESCE(o.display_name, o.name) store_name,
                m.nickname matchmaker_name, c.nickname consumer_name, po.product_name,
                COALESCE(rule.name, po.product_name) AS event_name
                FROM commission_entry ce
                LEFT JOIN organization o ON o.id = ce.beneficiary_id AND o.org_type = 'store'
                LEFT JOIN payment_order po ON po.id = ce.order_id
                LEFT JOIN users c ON c.id = po.user_id
                LEFT JOIN resource_assignment ra ON ra.user_id = po.user_id AND ra.status = 1
                LEFT JOIN users m ON m.id = ra.matchmaker_id
                LEFT JOIN commission_rule rule ON rule.id = ce.rule_id
                WHERE {clause}
                ORDER BY ce.created_at DESC, ce.id DESC"""),
            params,
        )
    ).mappings().all()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "分店分成明细"
    sheet.append(_STORE_ENTRY_EXPORT_HEADERS)
    for row in rows:
        sheet.append([
            int(row["id"]),
            _dt(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S"),
            row.get("store_name") or f"分店#{row['store_id']}",
            row.get("matchmaker_name") or "",
            row.get("consumer_name") or "",
            row.get("event_name") or row.get("product_name") or "其他事件",
            float(Decimal(str(row["base_amount"]))),
            float(Decimal(str(row["amount"]))),
            row.get("status") or "",
        ])
    for index, width in enumerate((10, 20, 20, 16, 16, 24, 14, 14, 12), start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()

