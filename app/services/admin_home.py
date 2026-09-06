"""Read-only, data-scope-aware administrator home-page queries."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin
from app.schemas.admin_home import (
    AcademyCategory, AdminBootstrap, AdminDashboard, AnnouncementItem, AnnouncementPage,
    DailyTrend, DashboardGender, DashboardMetrics, DashboardPending, HomeAuthorization,
    HomeHeader, HomeOperator, IncomeRankItem, RechargeItem, SmsStatistics,
)


def _money(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _scope_condition(admin: CurrentMatchmakerAdmin, user_column: str) -> tuple[str, dict[str, object]]:
    """Return a parameterized resource-assignment predicate for an admin data scope."""
    account = admin.account
    if account.data_scope == "ALL":
        return "1 = 1", {}
    if account.data_scope == "SELF":
        # An unbound account is still used for CRM setup/testing. Keep it
        # consistent with the member list (which includes unassigned users)
        # until a concrete matchmaker owner is configured.
        if account.matchmaker_user_id is None:
            return "1 = 1", {}
        matchmaker_id = account.matchmaker_user_id
        return ("EXISTS (SELECT 1 FROM resource_assignment scope_assignment "
                f"WHERE scope_assignment.user_id = {user_column} "
                "AND scope_assignment.matchmaker_id = :scope_matchmaker_id "
                "AND scope_assignment.status = 1)"), {"scope_matchmaker_id": matchmaker_id}
    if account.organization_id is None:
        return "1 = 0", {}
    return ("EXISTS (SELECT 1 FROM resource_assignment scope_assignment "
            f"WHERE scope_assignment.user_id = {user_column} AND scope_assignment.status = 1 "
            "AND scope_assignment.organization_id IN (SELECT id FROM organization "
            "WHERE id = :scope_organization_id OR parent_id = :scope_organization_id))"), {
        "scope_organization_id": account.organization_id,
    }


def _lead_scope_condition(admin: CurrentMatchmakerAdmin) -> tuple[str, dict[str, object]]:
    account = admin.account
    if account.data_scope == "ALL":
        return "1 = 1", {}
    if account.data_scope == "SELF":
        if account.matchmaker_user_id is None:
            return "1 = 1", {}
        matchmaker_id = account.matchmaker_user_id
        return "customer_lead.matchmaker_id = :scope_matchmaker_id", {"scope_matchmaker_id": matchmaker_id}
    if account.organization_id is None:
        return "1 = 0", {}
    return ("customer_lead.organization_id IN (SELECT id FROM organization WHERE id = :scope_organization_id "
            "OR parent_id = :scope_organization_id)"), {"scope_organization_id": account.organization_id}


async def sms_statistics(db: AsyncSession) -> SmsStatistics:
    row = (await db.execute(text("""SELECT COALESCE(SUM(success_count), 0) success_count,
        COALESCE(SUM(failed_count), 0) failed_count, COALESCE(SUM(remaining_count), 0) remaining_count
        FROM admin_sms_statistics WHERE tenant_id = 1"""))).mappings().one()
    return SmsStatistics(**{key: int(row[key] or 0) for key in SmsStatistics.model_fields})


async def has_unread_feedback(db: AsyncSession, account_id: int) -> bool:
    result = await db.execute(text("""SELECT 1 FROM admin_feedback_message message
        LEFT JOIN admin_feedback_read read_state ON read_state.feedback_id = message.id
            AND read_state.account_id = :account_id
        WHERE message.tenant_id = 1 AND message.sender_type IN ('SERVICE', 'SYSTEM') AND read_state.id IS NULL
          AND message.id = (SELECT latest.id FROM admin_feedback_message latest
              WHERE latest.ticket_id = message.ticket_id ORDER BY latest.created_at DESC, latest.id DESC LIMIT 1)
        LIMIT 1"""), {"account_id": account_id})
    return result.scalar() is not None


async def _unread_announcements(db: AsyncSession, account_id: int) -> int:
    return int((await db.execute(text("""SELECT COUNT(*) FROM admin_announcement announcement
        LEFT JOIN admin_announcement_read read_state ON read_state.announcement_id = announcement.id
            AND read_state.account_id = :account_id
        WHERE announcement.published_at IS NOT NULL AND read_state.id IS NULL AND announcement.tenant_id = 1"""), {
        "account_id": account_id,
    })).scalar() or 0)


async def bootstrap(db: AsyncSession, admin: CurrentMatchmakerAdmin) -> AdminBootstrap:
    sms = await sms_statistics(db)
    return AdminBootstrap(
        operator=HomeOperator(id=admin.account.id, account=admin.account.username,
            name=admin.account.display_name, permissions=sorted(admin.permissions), locked=admin.account.status != 1),
        authorization=HomeAuthorization(sms_remaining_count=sms.remaining_count),
        header=HomeHeader(has_unread_feedback=await has_unread_feedback(db, admin.account.id),
            unread_announcement_count=await _unread_announcements(db, admin.account.id), sms=sms),
    )


async def dashboard(db: AsyncSession, admin: CurrentMatchmakerAdmin, from_date: date, to_date: date) -> AdminDashboard:
    if to_date < from_date or (to_date - from_date).days > 365:
        raise HTTPException(422, detail="统计日期范围必须为 1 至 366 天")
    user_scope, scope_params = _scope_condition(admin, "users.id")
    membership_scope, _ = _scope_condition(admin, "user_membership.user_id")
    membership_scope_alias = membership_scope.replace("user_membership.user_id", "membership.user_id")
    order_scope, _ = _scope_condition(admin, "payment_order.user_id")
    withdrawal_scope, _ = _scope_condition(admin, "withdrawal_request.account_id")
    matchmaker_scope, _ = _scope_condition(admin, "user_matchmaker_apply.user_id")
    lead_scope, _ = _lead_scope_condition(admin)
    row = (await db.execute(text(f"""SELECT
        (SELECT COUNT(*) FROM users WHERE status = 1 AND {user_scope}) member_count,
        (SELECT COUNT(*) FROM users WHERE status = 1 AND {user_scope}) platform_user_count,
        (SELECT COUNT(*) FROM users WHERE status = 1 AND openid IS NOT NULL AND openid <> '' AND {user_scope}) wechat_fan_count,
        (SELECT COUNT(DISTINCT DATE(last_login_at)) FROM users WHERE status = 1 AND last_login_at IS NOT NULL AND {user_scope}) online_days,
        (SELECT COUNT(*) FROM customer_lead WHERE {lead_scope}) lead_count,
        (SELECT COUNT(*) FROM customer_lead WHERE {lead_scope}) customer_lead_count,
        (SELECT COUNT(DISTINCT user_id) FROM user_membership WHERE status = 1 AND (end_at IS NULL OR end_at > UTC_TIMESTAMP()) AND {membership_scope}) vip_count,
        (SELECT COUNT(DISTINCT membership.user_id) FROM user_membership membership WHERE membership.status = 1 AND (membership.end_at IS NULL OR membership.end_at > UTC_TIMESTAMP()) AND NOT EXISTS (SELECT 1 FROM payment_order membership_order WHERE membership_order.order_no = membership.order_no AND membership_order.product_type = 'offline_vip') AND {membership_scope_alias}) online_vip_count,
        (SELECT COUNT(DISTINCT membership.user_id) FROM user_membership membership WHERE membership.status = 1 AND (membership.end_at IS NULL OR membership.end_at > UTC_TIMESTAMP()) AND EXISTS (SELECT 1 FROM payment_order membership_order WHERE membership_order.order_no = membership.order_no AND membership_order.product_type = 'offline_vip') AND {membership_scope_alias}) offline_vip_count,
        (SELECT COUNT(*) FROM user_matchmaker_apply WHERE application_type = 'service_matchmaker' AND status = 1 AND {matchmaker_scope}) matchmaker_count,
        (SELECT COUNT(*) FROM user_matchmaker_apply WHERE application_type = 'service_matchmaker' AND status = 1 AND {matchmaker_scope}) service_matchmaker_count,
        (SELECT COUNT(*) FROM user_matchmaker_apply WHERE application_type = 'promoter' AND status = 1 AND {matchmaker_scope}) promotion_matchmaker_count,
        (SELECT COUNT(*) FROM user_match matched_user WHERE matched_user.status IN (1, 2) AND matched_user.user_id < matched_user.target_user_id AND {user_scope.replace('users.id', 'matched_user.user_id')}) successful_match_count,
        (SELECT COUNT(*) FROM users WHERE status = 1 AND gender = 1 AND {user_scope}) male_member_count,
        (SELECT COUNT(*) FROM users WHERE status = 1 AND gender = 2 AND {user_scope}) female_member_count,
        (SELECT COUNT(*) FROM withdrawal_request WHERE status = 'PENDING_REVIEW' AND account_type = 'user' AND {withdrawal_scope}) pending_withdrawal_count,
        (SELECT COALESCE(SUM(amount), 0) FROM payment_order WHERE status = 1 AND (product_type IS NULL OR product_type <> 'offline_vip') AND {order_scope}) online_income,
        (SELECT COALESCE(SUM(amount), 0) FROM payment_order WHERE status = 1 AND product_type = 'offline_vip' AND {order_scope}) offline_income"""), scope_params)).mappings().one()
    metrics = DashboardMetrics(**{key: _money(row[key]) if "income" in key else int(row[key] or 0) for key in DashboardMetrics.model_fields})
    pending_row = (await db.execute(text(f"""SELECT
        (SELECT COUNT(*) FROM withdrawal_request WHERE status = 'PENDING_REVIEW' AND account_type = 'user' AND {withdrawal_scope}) withdrawal,
        (SELECT COUNT(*) FROM user_matchmaker_apply WHERE status = 0 AND {matchmaker_scope}) matchmaker_application,
        (SELECT COUNT(*) FROM matchmaker_service WHERE status = 0 AND {user_scope.replace('users.id', 'matchmaker_service.user_id')}) matchmaker_service,
        (SELECT COUNT(*) FROM match_apply WHERE status = 0 AND {user_scope.replace('users.id', 'match_apply.to_user_id')}) match_application,
        (SELECT COUNT(*) FROM user_report WHERE status = 0 AND {user_scope.replace('users.id', 'user_report.target_user_id')}) report"""), scope_params)).mappings().one()
    pending = DashboardPending(**{key: int(pending_row[key] or 0) for key in DashboardPending.model_fields})
    gender = DashboardGender(male=metrics.male_member_count, female=metrics.female_member_count,
        unspecified=max(0, metrics.member_count - metrics.male_member_count - metrics.female_member_count))
    rank_rows = (await db.execute(text(f"""SELECT COALESCE(NULLIF(product_type, ''), 'unknown') product_type,
        COALESCE(SUM(amount), 0) income FROM payment_order WHERE status = 1 AND {order_scope}
        GROUP BY COALESCE(NULLIF(product_type, ''), 'unknown') ORDER BY income DESC, product_type LIMIT 5"""), scope_params)).mappings().all()
    rank_total = sum((_money(item["income"]) for item in rank_rows), Decimal("0.00"))
    income_rank = [IncomeRankItem(product_type=str(item["product_type"]), income=_money(item["income"]),
        proportion=(_money(item["income"]) * Decimal("100") / rank_total).quantize(Decimal("0.01")) if rank_total else Decimal("0.00")) for item in rank_rows]
    start = datetime.combine(from_date, time.min)
    end = datetime.combine(to_date + timedelta(days=1), time.min)
    rows = (await db.execute(text(f"""SELECT DATE(d) date, SUM(member_count) member_count, SUM(lead_count) lead_count,
        SUM(paid_count) paid_count, SUM(refund_count) refund_count, SUM(paid_amount) paid_amount, SUM(online_paid_amount) online_paid_amount, SUM(offline_paid_amount) offline_paid_amount, SUM(refund_amount) refund_amount FROM (
          SELECT DATE(created_at) d, COUNT(*) member_count, 0 lead_count, 0 paid_count, 0 refund_count, 0 paid_amount, 0 online_paid_amount, 0 offline_paid_amount, 0 refund_amount FROM users WHERE created_at >= :start AND created_at < :end AND {user_scope} GROUP BY DATE(created_at)
          UNION ALL SELECT DATE(created_at) d, 0, COUNT(*), 0, 0, 0, 0, 0, 0 FROM customer_lead WHERE created_at >= :start AND created_at < :end AND {lead_scope} GROUP BY DATE(created_at)
          UNION ALL SELECT DATE(pay_time) d, 0, 0, COUNT(*), 0, COALESCE(SUM(amount), 0), COALESCE(SUM(CASE WHEN product_type IS NULL OR product_type <> 'offline_vip' THEN amount ELSE 0 END), 0), COALESCE(SUM(CASE WHEN product_type = 'offline_vip' THEN amount ELSE 0 END), 0), 0 FROM payment_order WHERE status IN (1, 3) AND pay_time >= :start AND pay_time < :end AND {order_scope} GROUP BY DATE(pay_time)
          UNION ALL SELECT DATE(refund_time) d, 0, 0, 0, COUNT(*), 0, 0, 0, COALESCE(SUM(amount), 0) FROM payment_order WHERE status = 3 AND refund_time >= :start AND refund_time < :end AND {order_scope} GROUP BY DATE(refund_time)
        ) daily GROUP BY DATE(d)"""), {**scope_params, "start": start, "end": end})).mappings().all()
    values = {row["date"]: row for row in rows}
    trends: list[DailyTrend] = []
    current = from_date
    while current <= to_date:
        item = values.get(current, {})
        paid, refunded = _money(item.get("paid_amount")), _money(item.get("refund_amount"))
        trends.append(DailyTrend(date=current, member_count=int(item.get("member_count") or 0), lead_count=int(item.get("lead_count") or 0), paid_count=int(item.get("paid_count") or 0), completed_refund_count=int(item.get("refund_count") or 0), paid_amount=paid, online_paid_amount=_money(item.get("online_paid_amount")), offline_paid_amount=_money(item.get("offline_paid_amount")), completed_refund_amount=refunded, net_amount=paid - refunded))
        current += timedelta(days=1)
    return AdminDashboard(from_date=from_date, to_date=to_date, metrics=metrics, pending=pending,
        member_gender=gender, income_rank=income_rank, trends=trends)


async def member_statistics(db: AsyncSession, admin: CurrentMatchmakerAdmin, from_date: date, to_date: date) -> dict:
    """Aggregate only persisted CRM data for the member-report visualizations."""
    if to_date < from_date or (to_date - from_date).days > 365:
        raise HTTPException(422, detail="统计日期范围必须为 1 至 366 天")
    user_scope, scope_params = _scope_condition(admin, "users.id")
    lead_scope, lead_scope_params = _lead_scope_condition(admin)
    start = datetime.combine(from_date, time.min)
    end = datetime.combine(to_date + timedelta(days=1), time.min)

    async def grouped(sql: str, params: dict[str, object] | None = None) -> list[dict]:
        result = await db.execute(text(sql), {**scope_params, **lead_scope_params, **(params or {})})
        return [{"label": str(row["label"]), "value": int(row["value"] or 0)} for row in result.mappings().all()]

    ordinary_user = "EXISTS (SELECT 1 FROM user_role ordinary_role WHERE ordinary_role.user_id = {column} AND ordinary_role.role_code = 'user' AND ordinary_role.status = 1)"
    ordinary_users = ordinary_user.format(column="users.id")
    async def users_group(expression: str, joins: str = "", order: str = "value DESC, label") -> list[dict]:
        return await grouped(f"""SELECT COALESCE(NULLIF(CAST({expression} AS CHAR), ''), '未填写') label, COUNT(*) value
            FROM users {joins} WHERE users.status = 1 AND {ordinary_users} AND {user_scope}
            GROUP BY label ORDER BY {order}""")
    gender = await grouped(f"""SELECT CASE users.gender WHEN 1 THEN '男' WHEN 2 THEN '女' ELSE '未填写' END label, COUNT(*) value
        FROM users WHERE users.status = 1 AND {ordinary_users} AND {user_scope}
        GROUP BY users.gender ORDER BY users.gender""")
    intention = await grouped(f"""SELECT CASE profile.intention_level WHEN 3 THEN '高意向' WHEN 2 THEN '中意向' WHEN 1 THEN '低意向' ELSE '未填写' END label, COUNT(*) value
        FROM users LEFT JOIN user_profile profile ON profile.user_id = users.id WHERE users.status = 1 AND {ordinary_users} AND {user_scope}
        GROUP BY profile.intention_level ORDER BY profile.intention_level DESC""")
    follow = await grouped(f"""SELECT CASE cl.status WHEN 1 THEN '跟进中' WHEN 2 THEN '已转化' WHEN 3 THEN '已放弃' ELSE '待跟进' END label, COUNT(*) value
        FROM customer_lead AS cl WHERE cl.created_at >= :start AND cl.created_at < :end AND {lead_scope.replace('customer_lead.', 'cl.')}
        GROUP BY cl.status ORDER BY cl.status""", {"start": start, "end": end})
    requirement = await grouped(f"""SELECT COALESCE(NULLIF(preference.dating_goal, ''), '未填写') label, COUNT(*) value
        FROM users LEFT JOIN user_partner_preference preference ON preference.user_id = users.id
        WHERE users.status = 1 AND {ordinary_users} AND {user_scope} GROUP BY COALESCE(NULLIF(preference.dating_goal, ''), '未填写') ORDER BY value DESC, label""")
    basic_groups = {
        "gender": gender,
        "marriage": await users_group("CASE users.is_married WHEN 0 THEN '未婚' WHEN 1 THEN '已婚' WHEN 2 THEN '离异' WHEN 3 THEN '丧偶' ELSE '未填写' END"),
        "age": await users_group("CASE WHEN users.birthday IS NULL THEN '未填写' WHEN TIMESTAMPDIFF(YEAR, users.birthday, CURDATE()) < 26 THEN '25岁以下' WHEN TIMESTAMPDIFF(YEAR, users.birthday, CURDATE()) <= 30 THEN '26岁-30岁' WHEN TIMESTAMPDIFF(YEAR, users.birthday, CURDATE()) <= 35 THEN '31岁-35岁' WHEN TIMESTAMPDIFF(YEAR, users.birthday, CURDATE()) <= 40 THEN '36岁-40岁' WHEN TIMESTAMPDIFF(YEAR, users.birthday, CURDATE()) <= 45 THEN '41岁-45岁' ELSE '46岁以上' END"),
        "education": await users_group("CASE profile.education_level WHEN 1 THEN '初中' WHEN 2 THEN '技校' WHEN 3 THEN '高中' WHEN 4 THEN '大专' WHEN 5 THEN '本科' WHEN 6 THEN '硕士' WHEN 7 THEN '博士' ELSE '不限' END", "LEFT JOIN user_profile profile ON profile.user_id = users.id"),
        "house": await users_group("COALESCE(NULLIF(profile.house, ''), '未填写')", "LEFT JOIN user_profile profile ON profile.user_id = users.id"),
        "car": await users_group("COALESCE(NULLIF(profile.car, ''), '未填写')", "LEFT JOIN user_profile profile ON profile.user_id = users.id"),
        "income": await users_group("CASE WHEN profile.income IS NULL THEN '不限' WHEN profile.income < 3000 THEN '3千元以下' WHEN profile.income < 5000 THEN '3-5千元' WHEN profile.income < 8000 THEN '5-8千元' WHEN profile.income < 10000 THEN '8千-1万元' ELSE '1万元以上' END", "LEFT JOIN user_profile profile ON profile.user_id = users.id"),
        "realname": await users_group("CASE WHEN users.is_real_name = 1 OR auth.realname_status = 1 THEN '已实名' ELSE '未实名' END", "LEFT JOIN user_auth auth ON auth.user_id = users.id"),
        "occupation": await users_group("COALESCE(NULLIF(profile.occupation, ''), '不限')", "LEFT JOIN user_profile profile ON profile.user_id = users.id"),
        "hometown": await users_group("COALESCE(NULLIF(profile.hometown, ''), '未填写')", "LEFT JOIN user_profile profile ON profile.user_id = users.id"),
        "residence": await users_group("COALESCE(NULLIF(profile.residence, ''), '未填写')", "LEFT JOIN user_profile profile ON profile.user_id = users.id"),
        "dating_status": await users_group("CASE users.status WHEN 1 THEN '公开相亲' ELSE '停止相亲' END"),
    }
    browse = await grouped(f"""SELECT CASE WHEN history.user_id = history.target_user_id THEN '查看自己' ELSE '查看会员资料' END label, COUNT(*) value
        FROM user_browse_history history JOIN users ON users.id = history.user_id
        WHERE history.created_at >= :start AND history.created_at < :end AND users.status = 1 AND {ordinary_user.format(column='users.id')} AND {user_scope}
        GROUP BY CASE WHEN history.user_id = history.target_user_id THEN '查看自己' ELSE '查看会员资料' END ORDER BY value DESC""", {"start": start, "end": end})
    popularity = await grouped(f"""SELECT COALESCE(NULLIF(target.nickname, ''), CONCAT('会员', target.id)) label, COUNT(*) value
        FROM user_browse_history history JOIN users viewer ON viewer.id = history.user_id JOIN users target ON target.id = history.target_user_id
        WHERE history.created_at >= :start AND history.created_at < :end AND viewer.status = 1 AND {ordinary_user.format(column='viewer.id')} AND target.status = 1 AND {ordinary_user.format(column='target.id')} AND {user_scope.replace('users.id', 'viewer.id')}
        GROUP BY target.id, target.nickname ORDER BY value DESC, target.id LIMIT 8""", {"start": start, "end": end})
    total_browse = sum(item["value"] for item in browse)
    browse_daily = await grouped(f"""SELECT DATE(history.created_at) label, COUNT(*) value
        FROM user_browse_history history JOIN users ON users.id = history.user_id
        WHERE history.created_at >= :start AND history.created_at < :end AND users.status = 1
          AND {ordinary_user.format(column='users.id')} AND {user_scope}
        GROUP BY DATE(history.created_at) ORDER BY DATE(history.created_at) DESC""", {"start": start, "end": end})
    popular_female = await grouped(f"""SELECT COALESCE(NULLIF(target.nickname, ''), CONCAT('会员', target.id)) label, COUNT(*) value
        FROM user_browse_history history JOIN users viewer ON viewer.id = history.user_id JOIN users target ON target.id = history.target_user_id
        WHERE history.created_at >= :start AND history.created_at < :end AND target.gender = 2 AND target.status = 1
          AND {ordinary_user.format(column='target.id')} AND {ordinary_user.format(column='viewer.id')} AND {user_scope.replace('users.id', 'viewer.id')}
        GROUP BY target.id, target.nickname ORDER BY value DESC, target.id LIMIT 10""", {"start": start, "end": end})
    popular_male = await grouped(f"""SELECT COALESCE(NULLIF(target.nickname, ''), CONCAT('会员', target.id)) label, COUNT(*) value
        FROM user_browse_history history JOIN users viewer ON viewer.id = history.user_id JOIN users target ON target.id = history.target_user_id
        WHERE history.created_at >= :start AND history.created_at < :end AND target.gender = 1 AND target.status = 1
          AND {ordinary_user.format(column='target.id')} AND {ordinary_user.format(column='viewer.id')} AND {user_scope.replace('users.id', 'viewer.id')}
        GROUP BY target.id, target.nickname ORDER BY value DESC, target.id LIMIT 10""", {"start": start, "end": end})
    apply_female = await grouped(f"""SELECT COALESCE(NULLIF(target.nickname, ''), CONCAT('会员', target.id)) label, COUNT(*) value
        FROM match_apply application JOIN users target ON target.id = application.to_user_id
        WHERE application.created_at >= :start AND application.created_at < :end AND target.gender = 2 AND target.status = 1
          AND application.status IN (0, 1) AND {ordinary_user.format(column='target.id')}
        GROUP BY target.id, target.nickname ORDER BY value DESC, target.id LIMIT 10""", {"start": start, "end": end})
    apply_male = await grouped(f"""SELECT COALESCE(NULLIF(target.nickname, ''), CONCAT('会员', target.id)) label, COUNT(*) value
        FROM match_apply application JOIN users target ON target.id = application.to_user_id
        WHERE application.created_at >= :start AND application.created_at < :end AND target.gender = 1 AND target.status = 1
          AND application.status IN (0, 1) AND {ordinary_user.format(column='target.id')}
        GROUP BY target.id, target.nickname ORDER BY value DESC, target.id LIMIT 10""", {"start": start, "end": end})

    # The source tables do not store a separate "home page view" event.  The
    # profile-view column below is therefore the complete persisted browse total.
    browse_rows = (await db.execute(text(f"""SELECT DATE(history.created_at) date,
            COUNT(*) profile_views,
            COALESCE(MAX(popular.nickname), '') popular_member
        FROM user_browse_history history
        JOIN users viewer ON viewer.id = history.user_id
        LEFT JOIN users popular ON popular.id = history.target_user_id
        WHERE history.created_at >= :start AND history.created_at < :end
          AND viewer.status = 1 AND {ordinary_user.format(column='viewer.id')}
          AND {user_scope.replace('users.id', 'viewer.id')}
        GROUP BY DATE(history.created_at) ORDER BY DATE(history.created_at) DESC"""), {
            **scope_params, **lead_scope_params, "start": start, "end": end,
        })).mappings().all()
    browse_report = [{
        "date": str(row["date"]), "home_views": 0,
        "profile_views": int(row["profile_views"] or 0),
        "popular_member": str(row["popular_member"] or "-"),
    } for row in browse_rows]

    growth_rows = (await db.execute(text(f"""SELECT calendar.date,
        COALESCE(registered.member_count, 0) member_count,
        COALESCE(vip.vip_count, 0) vip_count,
        COALESCE(applied.apply_count, 0) apply_count,
        COALESCE(applied.failed_count, 0) failed_count,
        COALESCE(applied.success_count, 0) success_count
        FROM (
          SELECT DATE(created_at) date FROM users WHERE created_at >= :start AND created_at < :end
          UNION SELECT DATE(start_at) date FROM user_membership WHERE start_at >= :start AND start_at < :end
          UNION SELECT DATE(created_at) date FROM match_apply WHERE created_at >= :start AND created_at < :end
        ) calendar
        LEFT JOIN (
          SELECT DATE(users.created_at) date, COUNT(*) member_count FROM users
          WHERE users.created_at >= :start AND users.created_at < :end AND users.status = 1
            AND {ordinary_users} AND {user_scope} GROUP BY DATE(users.created_at)
        ) registered ON registered.date = calendar.date
        LEFT JOIN (
          SELECT DATE(membership.start_at) date, COUNT(DISTINCT membership.user_id) vip_count
          FROM user_membership membership JOIN users ON users.id = membership.user_id
          WHERE membership.start_at >= :start AND membership.start_at < :end AND membership.status = 1
            AND {ordinary_users} AND {user_scope} GROUP BY DATE(membership.start_at)
        ) vip ON vip.date = calendar.date
        LEFT JOIN (
          SELECT DATE(application.created_at) date, COUNT(*) apply_count,
            SUM(application.status IN (2, 3)) failed_count,
            SUM(application.status = 1) success_count
          FROM match_apply application JOIN users ON users.id = application.from_user_id
          WHERE application.created_at >= :start AND application.created_at < :end AND users.status = 1
            AND {ordinary_users} AND {user_scope} GROUP BY DATE(application.created_at)
        ) applied ON applied.date = calendar.date
        ORDER BY calendar.date DESC"""), {**scope_params, "start": start, "end": end})).mappings().all()
    growth = [{
        "date": str(row["date"]), "member_count": int(row["member_count"] or 0),
        "vip_count": int(row["vip_count"] or 0), "apply_count": int(row["apply_count"] or 0),
        "failed_count": int(row["failed_count"] or 0), "success_count": int(row["success_count"] or 0),
    } for row in growth_rows]

    follow_rows = (await db.execute(text(f"""SELECT
        COALESCE(NULLIF(matchmaker.nickname, ''), CONCAT('红娘', assignment.matchmaker_id), '未分配') matchmaker,
        COUNT(DISTINCT users.id) member_count,
        SUM(followup.last_follow_at IS NULL) never_followed,
        SUM(followup.last_follow_at < UTC_TIMESTAMP() - INTERVAL 3 DAY) over_3_days,
        SUM(followup.last_follow_at < UTC_TIMESTAMP() - INTERVAL 7 DAY) over_7_days,
        SUM(followup.last_follow_at < UTC_TIMESTAMP() - INTERVAL 15 DAY) over_15_days,
        SUM(followup.last_follow_at < UTC_TIMESTAMP() - INTERVAL 30 DAY) over_30_days,
        COALESCE(SUM(followup.follow_count), 0) follow_count,
        COALESCE(SUM(followup.month_follow_count), 0) month_follow_count
        FROM users
        LEFT JOIN resource_assignment assignment ON assignment.user_id = users.id AND assignment.status = 1
        LEFT JOIN users matchmaker ON matchmaker.id = assignment.matchmaker_id
        LEFT JOIN (
          SELECT user_id, MAX(created_at) last_follow_at, COUNT(*) follow_count,
            SUM(created_at >= DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-01')) month_follow_count
          FROM member_follow_up GROUP BY user_id
        ) followup ON followup.user_id = users.id
        WHERE users.status = 1 AND {ordinary_users} AND {user_scope}
        GROUP BY assignment.matchmaker_id, matchmaker.nickname
        ORDER BY member_count DESC, matchmaker"""), scope_params)).mappings().all()
    follow_report = [{key: int(row[key] or 0) if key != "matchmaker" else str(row[key]) for key in row.keys()} for row in follow_rows]

    preference_labels = {
        "age": "年龄", "marriage": "婚况", "height": "身高", "education": "学历",
        "housing": "住房", "smoking": "抽烟", "drinking": "喝酒", "goal": "结婚要求",
    }
    async def preference_report(gender_value: int) -> dict[str, list[dict]]:
        conditions = f"users.status = 1 AND users.gender = {gender_value} AND {ordinary_users} AND {user_scope}"
        expressions = {
            "age": "CASE WHEN preference.age_min IS NULL AND preference.age_max IS NULL THEN '不限' WHEN preference.age_max IS NULL THEN CONCAT(preference.age_min, '岁以上') WHEN preference.age_min IS NULL THEN CONCAT(preference.age_max, '岁以下') ELSE CONCAT(preference.age_min, '-', preference.age_max, '岁') END",
            "marriage": "CASE preference.marriage_status WHEN 1 THEN '未婚' WHEN 2 THEN '离异' WHEN 3 THEN '丧偶' ELSE '不限' END",
            "height": "CASE WHEN preference.height_min IS NULL AND preference.height_max IS NULL THEN '不限' WHEN preference.height_max IS NULL THEN CONCAT(preference.height_min, 'cm以上') WHEN preference.height_min IS NULL THEN CONCAT(preference.height_max, 'cm以下') ELSE CONCAT(preference.height_min, '-', preference.height_max, 'cm') END",
            "education": "CASE preference.education_min WHEN 1 THEN '初中' WHEN 2 THEN '技校' WHEN 3 THEN '高中' WHEN 4 THEN '大专' WHEN 5 THEN '本科' WHEN 6 THEN '硕士' WHEN 7 THEN '博士' ELSE '不限' END",
            "housing": "CASE preference.housing_requirement WHEN 1 THEN '有房' WHEN 2 THEN '无房可接受' ELSE '不限' END",
            "smoking": "CASE preference.smoking_requirement WHEN 1 THEN '不抽烟' WHEN 2 THEN '可接受' ELSE '不限' END",
            "drinking": "CASE preference.drinking_requirement WHEN 1 THEN '不喝酒' WHEN 2 THEN '可接受' ELSE '不限' END",
            "goal": "COALESCE(NULLIF(preference.dating_goal, ''), '不限')",
        }
        return {key: await grouped(f"""SELECT {expression} label, COUNT(*) value
            FROM users LEFT JOIN user_partner_preference preference ON preference.user_id = users.id
            WHERE {conditions} GROUP BY {expression} ORDER BY value DESC, label""") for key, expression in expressions.items()}

    requirements = {"male": await preference_report(1), "female": await preference_report(2)}
    top_metrics = (await db.execute(text(f"""SELECT COUNT(*) total_members,
        COALESCE(SUM(DATE(users.created_at) = :to_date), 0) today_members
        FROM users WHERE users.status = 1 AND {ordinary_users} AND {user_scope}"""), {
            **scope_params, "to_date": to_date,
        })).mappings().one()
    return {
        "from_date": str(from_date), "to_date": str(to_date),
        "groups": {"follow": follow, "intention": intention, "basic": gender, "requirement": requirement, "browse": browse, "popularity": popularity,
                    "basic_groups": basic_groups, "browse_daily": browse_daily,
                    "popularity_female": popular_female, "popularity_male": popular_male,
                    "apply_female": apply_female, "apply_male": apply_male,
                    "growth": growth, "follow_report": follow_report,
                    "browse_report": browse_report, "requirements": requirements,
                    "preference_labels": preference_labels},
        "totals": {"follow": sum(item["value"] for item in follow), "intention": sum(item["value"] for item in intention), "basic": sum(item["value"] for item in gender), "requirement": sum(item["value"] for item in requirement), "browse": total_browse, "popularity": sum(item["value"] for item in popularity)},
        "metrics": {"total_members": int(top_metrics["total_members"] or 0), "today_members": int(top_metrics["today_members"] or 0)},
    }


async def announcements(db: AsyncSession, admin: CurrentMatchmakerAdmin, page: int, page_size: int, category: str | None, keyword: str | None) -> AnnouncementPage:
    where = ["announcement.published_at IS NOT NULL", "announcement.tenant_id = 1"]
    params: dict[str, object] = {"account_id": admin.account.id, "limit": page_size, "offset": (page - 1) * page_size}
    if category:
        where.append("announcement.category = :category")
        params["category"] = category
    if keyword:
        where.append("(announcement.title LIKE CONCAT('%', :keyword, '%') OR CAST(announcement.id AS CHAR) = :keyword)")
        params["keyword"] = keyword
    clause = " AND ".join(where)
    base = "FROM admin_announcement announcement LEFT JOIN admin_announcement_read read_state ON read_state.announcement_id = announcement.id AND read_state.account_id = :account_id"
    result = await db.execute(text(f"SELECT announcement.id, announcement.version_id, announcement.category, announcement.title, announcement.title_color, announcement.title_bold, announcement.top, announcement.sort_order, announcement.link_to, announcement.created_at, read_state.id read_id {base} WHERE {clause} ORDER BY announcement.top DESC, announcement.sort_order DESC, announcement.published_at DESC, announcement.id DESC LIMIT :limit OFFSET :offset"), params)
    count = await db.execute(text(f"SELECT COUNT(*) FROM admin_announcement announcement WHERE {clause}"), {key: value for key, value in params.items() if key not in {"account_id", "limit", "offset"}})
    items = [AnnouncementItem(**{**dict(row), "read": row["read_id"] is not None}) for row in result.mappings().all()]
    total = int(count.scalar() or 0)
    return AnnouncementPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)


async def academy_categories(db: AsyncSession, enabled: bool | None = True) -> list[AcademyCategory]:
    where = ["category_type = 'Guides'", "tenant_id = 1"]
    params: dict[str, object] = {}
    if enabled is not None:
        where.append("enabled = :enabled")
        params["enabled"] = enabled
    rows = (await db.execute(text(f"SELECT id, parent_id, name, description, image, category_type, sort, enabled, matchmaker_class_enabled FROM admin_academy_category WHERE {' AND '.join(where)} ORDER BY sort, id"), params)).mappings().all()
    nodes = {int(row["id"]): AcademyCategory(**dict(row), children=[]) for row in rows}
    roots: list[AcademyCategory] = []
    for node in nodes.values():
        if node.parent_id and node.parent_id in nodes:
            nodes[node.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


async def recharge_items(db: AsyncSession) -> list[RechargeItem]:
    rows = await db.execute(text("SELECT id, name, resource_type, quantity, price FROM admin_recharge_item WHERE enabled = 1 AND tenant_id = 1 ORDER BY sort, id"))
    return [RechargeItem(**dict(row)) for row in rows.mappings().all()]
