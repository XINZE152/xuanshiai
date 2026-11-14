"""红娘工作台活动报名：与后台同源，按会员归属限制读取。"""

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.matchmaker_workspace import (
    WorkspaceActivityOption, WorkspaceActivityPage, WorkspaceActivitySignup,
    WorkspaceActivityStatistics,
)
from app.services.matchmaker_workspace import WorkspaceActor, _member_scope_predicate, _money_text


async def list_workspace_activity_signups(
    db: AsyncSession, actor: WorkspaceActor, activity_id: int | None,
    page: int, page_size: int, keyword: str | None, filter_key: str = 'all',
) -> WorkspaceActivityPage:
    options = (await db.execute(text("""SELECT id, title FROM offline_activity
        WHERE COALESCE(online, 1) = 1 AND COALESCE(audit_status, 'approved') = 'approved'
        ORDER BY id DESC LIMIT 500"""))).mappings().all()
    activities = [WorkspaceActivityOption(id=int(row['id']), title=str(row['title'])) for row in options]
    if activity_id is not None and not any(item.id == activity_id for item in activities):
        raise HTTPException(404, detail='活动不存在或不可访问')
    selected = activity_id if activity_id is not None else (activities[0].id if activities else None)
    if selected is None:
        return WorkspaceActivityPage(activities=[], selected_activity_id=None,
            statistics=WorkspaceActivityStatistics(), items=[], page=page, page_size=page_size,
            total=0, has_more=False)

    params: dict[str, object] = {'activity_id': selected}
    scope = _member_scope_predicate(actor, params)
    base = f"""FROM activity_signup s JOIN users u ON u.id = s.user_id
        WHERE s.activity_id = :activity_id AND s.deleted_at IS NULL AND ({scope})"""
    stats = (await db.execute(text(f"""SELECT COUNT(*) AS total,
        SUM(CASE WHEN s.status = 0 THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN s.status = 1 THEN 1 ELSE 0 END) AS approved,
        SUM(CASE WHEN s.status = 3 THEN 1 ELSE 0 END) AS rejected,
        SUM(CASE WHEN COALESCE(s.checked_in, 0) = 1 THEN 1 ELSE 0 END) AS checked_in,
        SUM(CASE WHEN COALESCE(s.checked_in, 0) = 0 THEN 1 ELSE 0 END) AS not_checked_in {base}"""), params)).mappings().one()
    statistics = WorkspaceActivityStatistics(**{key: int(stats[key] or 0) for key in
        ('total', 'pending', 'approved', 'rejected', 'checked_in', 'not_checked_in')})
    filters = {'pending': 's.status = 0', 'approved': 's.status = 1',
        'rejected': 's.status = 3', 'checked_in': 'COALESCE(s.checked_in, 0) = 1',
        'not_checked_in': 'COALESCE(s.checked_in, 0) = 0'}
    if filter_key in filters:
        base += ' AND ' + filters[filter_key]
    if keyword:
        base += " AND u.nickname LIKE :keyword"
        params['keyword'] = '%' + keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    total = int(await db.scalar(text('SELECT COUNT(*) ' + base), params) or 0)
    rows = (await db.execute(text("""SELECT s.id, s.user_id, s.created_at, s.status,
        s.checked_in, s.pay_status, s.pay_amount, u.nickname, u.avatar """ + base +
        " ORDER BY s.id DESC LIMIT :limit OFFSET :offset"),
        {**params, 'limit': page_size, 'offset': (page - 1) * page_size})).mappings().all()
    items = [WorkspaceActivitySignup(id=int(row['id']), user_id=int(row['user_id']),
        nickname=str(row['nickname'] or '未命名会员'), avatar=row['avatar'],
        status=int(row['status'] or 0), checked_in=bool(row['checked_in']),
        created_at=row['created_at'], pay_status=str(row['pay_status'] or 'free'),
        pay_amount=_money_text(row['pay_amount'])) for row in rows]
    return WorkspaceActivityPage(activities=activities, selected_activity_id=selected,
        statistics=statistics, items=items, page=page, page_size=page_size,
        total=total, has_more=page * page_size < total)
