"""会员 CRM 跟进全览与历史跟进批量导入服务。

对应前端页面：会员CRM → 跟进全览（/love-user-follow-up）与其二级页「导入历史跟进」。
数据源：member_follow_up（会员跟进记录）、users、user_profile、user_auth、matchmaker_admin_account。
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.member_follow_up_admin import (
    MemberFollowUpImportResult,
    MemberFollowUpListPage,
    MemberFollowUpRow,
    MemberFollowUpSummary,
)

# 跟进方式库内枚举 -> 中文
_METHOD_LABELS: dict[str, str] = {
    "PHONE": "电话",
    "WECHAT": "微信",
    "VISIT": "面谈",
    "OTHER": "其他",
}

# 导入模板表头（顺序即模板列顺序）
_TEMPLATE_HEADERS: list[str] = ["会员手机号", "跟进内容", "跟进人", "跟进时间", "跟进方式"]

# 模板示例行
_TEMPLATE_SAMPLE: list[str] = [
    "13800000000",
    "示例：电话沟通，客户反馈本周有空到店，已约定周六下午",
    "admin",
    "2026-01-01 10:00:00",
    "电话",
]

# 跟进方式中文 -> 库内枚举
_METHOD_ALIASES: dict[str, str] = {
    "电话": "PHONE",
    "phone": "PHONE",
    "微信": "WECHAT",
    "wechat": "WECHAT",
    "vx": "WECHAT",
    "面谈": "VISIT",
    "拜访": "VISIT",
    "到店": "VISIT",
    "visit": "VISIT",
    "其他": "OTHER",
    "other": "OTHER",
}

# 跟进时间可接受的文本格式
_TIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d",
)

# 单次导入行数上限，避免超大文件打爆请求
_MAX_IMPORT_ROWS = 2000

_LIST_BODY = """
SELECT f.id, f.user_id, f.method, f.content, f.created_at,
       u.nickname, u.avatar,
       CONCAT('G', LPAD(u.id, 6, '0')) AS member_code,
       p.intention_level,
       COALESCE(acc.display_name, mk.nickname, 'admin') AS matchmaker_name
FROM member_follow_up f
JOIN users u ON u.id = f.user_id
LEFT JOIN user_profile p ON p.user_id = u.id
LEFT JOIN user_auth au ON au.user_id = u.id
LEFT JOIN matchmaker_admin_account acc ON acc.id = f.created_by
LEFT JOIN users mk ON mk.id = f.created_by
"""

# 计数用（不需要展示列，避免多算 join）
_COUNT_BODY = """
FROM member_follow_up f
JOIN users u ON u.id = f.user_id
LEFT JOIN user_profile p ON p.user_id = u.id
LEFT JOIN user_auth au ON au.user_id = u.id
"""

# 8 个时间维度计数：与前端「跟进全览」统计卡顺序一致
# 本周/上周按自然周（周一为起点）计算，避免跨年时 YEARWEEK 边界问题
_SUMMARY_SQL = """
SELECT
    COUNT(*) AS all_count,
    COALESCE(SUM(DATE(created_at) = CURDATE()), 0) AS today_count,
    COALESCE(SUM(DATE(created_at) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)), 0) AS yesterday_count,
    COALESCE(SUM(DATE(created_at) >= DATE_SUB(CURDATE(), INTERVAL 2 DAY)), 0) AS three_days_count,
    COALESCE(SUM(DATE(created_at) >= DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY)), 0)
        AS this_week_count,
    COALESCE(SUM(DATE(created_at) >= DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) + 7 DAY)
        AND DATE(created_at) < DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY)), 0)
        AS last_week_count,
    COALESCE(SUM(DATE_FORMAT(created_at, '%Y-%m') = DATE_FORMAT(CURDATE(), '%Y-%m')), 0)
        AS this_month_count,
    COALESCE(SUM(DATE_FORMAT(created_at, '%Y-%m')
        = DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m')), 0)
        AS last_month_count
FROM member_follow_up
"""


def _to_int(value: Any) -> int:
    return int(value or 0)


def _method_label(method: str | None) -> str:
    return _METHOD_LABELS.get(str(method or "").upper(), "其他")


def _row(payload: dict[str, Any]) -> MemberFollowUpRow:
    """数据库行 -> 列表行。"""
    method = str(payload.get("method") or "OTHER")
    intention = payload.get("intention_level")
    return MemberFollowUpRow(
        id=int(payload["id"]),
        user_id=int(payload["user_id"]),
        member_code=payload.get("member_code") or f"G{int(payload['user_id']):06d}",
        nickname=payload.get("nickname"),
        avatar=payload.get("avatar"),
        matchmaker_name=payload.get("matchmaker_name") or "admin",
        method=method,
        method_label=_method_label(method),
        content=payload.get("content") or "",
        # member_follow_up 无「是否系统自动生成」标记列，数据源不支持，恒为 null
        note=None,
        intention_level=int(intention) if intention is not None else None,
        created_at=payload.get("created_at"),
    )


async def list_follow_ups(
    db: AsyncSession,
    page: int,
    page_size: int,
    keyword: str | None = None,
    intention_level: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> MemberFollowUpListPage:
    """跟进全览分页列表。keyword 匹配昵称/手机号/实名/会员编号。"""
    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
    if keyword:
        conditions.append(
            "(u.nickname LIKE CONCAT('%', :keyword, '%')"
            " OR u.phone LIKE CONCAT('%', :keyword, '%')"
            " OR au.real_name LIKE CONCAT('%', :keyword, '%')"
            " OR CONCAT('G', LPAD(u.id, 6, '0')) LIKE CONCAT('%', :keyword, '%'))"
        )
        params["keyword"] = keyword
    if intention_level is not None:
        conditions.append("p.intention_level = :intention_level")
        params["intention_level"] = intention_level
    if start_date:
        conditions.append("f.created_at >= :start_date")
        params["start_date"] = start_date
    if end_date:
        conditions.append("f.created_at < DATE_ADD(:end_date, INTERVAL 1 DAY)")
        params["end_date"] = end_date
    where = " AND ".join(conditions)
    rows = await db.execute(
        text(
            f"{_LIST_BODY} WHERE {where}"
            " ORDER BY f.created_at DESC, f.id DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = _to_int(
        (await db.scalar(text(f"SELECT COUNT(*) {_COUNT_BODY} WHERE {where}"), count_params))
    )
    return MemberFollowUpListPage(
        items=[_row(dict(row)) for row in rows.mappings().all()],
        page=page,
        page_size=page_size,
        total=total,
        has_more=page * page_size < total,
    )


async def follow_up_summary(db: AsyncSession) -> MemberFollowUpSummary:
    """跟进全览 8 个时间统计卡计数。"""
    row = (await db.execute(text(_SUMMARY_SQL))).mappings().one()
    return MemberFollowUpSummary(
        all=_to_int(row["all_count"]),
        today=_to_int(row["today_count"]),
        yesterday=_to_int(row["yesterday_count"]),
        three_days=_to_int(row["three_days_count"]),
        this_week=_to_int(row["this_week_count"]),
        last_week=_to_int(row["last_week_count"]),
        this_month=_to_int(row["this_month_count"]),
        last_month=_to_int(row["last_month_count"]),
    )


def build_import_template() -> bytes:
    """生成「历史跟进」导入 Excel 模板（.xlsx）。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "历史跟进"
    sheet.append(_TEMPLATE_HEADERS)
    sheet.append(_TEMPLATE_SAMPLE)
    for index, width in enumerate((18, 46, 16, 22, 12), start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    normalized = str(value).strip()
    return "" if normalized.lower() in {"nan", "none", "null"} else normalized


def _parse_datetime(value: Any) -> datetime | None:
    """解析跟进时间；Excel 原生日期时间单元格直接取用，文本按多种格式尝试。"""
    if isinstance(value, datetime):
        return value
    raw = _cell_text(value)
    if not raw:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_method(value: Any) -> str:
    """解析跟进方式，无法识别统一按 OTHER。"""
    raw = _cell_text(value)
    if not raw:
        return "OTHER"
    return _METHOD_ALIASES.get(raw, _METHOD_ALIASES.get(raw.lower(), "OTHER"))


def _find_column(headers: list[str], *keywords: str) -> int | None:
    """按关键字定位模板列下标（表头可能被用户微调，故用包含匹配）。"""
    for index, header in enumerate(headers):
        for keyword in keywords:
            if keyword in header:
                return index
    return None


def _read_sheet(data: bytes, filename: str) -> tuple[list[str], list[tuple[int, list[Any]]]]:
    """解析上传文件，返回 (表头列表, [(Excel 行号, 行值列表)])。仅支持 .xlsx。"""
    lowered = (filename or "").lower()
    if lowered.endswith(".xls"):
        raise HTTPException(400, detail="暂不支持 .xls 旧格式，请用 Excel 另存为 .xlsx 后重新上传")
    if not lowered.endswith(".xlsx"):
        raise HTTPException(400, detail="仅支持 .xlsx 格式的 Excel 文件")
    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # 非 Excel / 文件损坏
        raise HTTPException(400, detail=f"Excel 解析失败：{exc}") from exc
    try:
        sheet = workbook.active
        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        headers = [_cell_text(cell) for cell in (header_row or ())]
        if _find_column(headers, "手机", "电话", "phone") is None:
            raise HTTPException(400, detail="模板表头缺少「会员手机号」列，请使用下载的模板填写")
        if _find_column(headers, "跟进内容", "内容", "content") is None:
            raise HTTPException(400, detail="模板表头缺少「跟进内容」列，请使用下载的模板填写")
        rows: list[tuple[int, list[Any]]] = []
        for index, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            if len(rows) >= _MAX_IMPORT_ROWS:
                raise HTTPException(400, detail=f"单次最多导入 {_MAX_IMPORT_ROWS} 行，请拆分文件后重试")
            rows.append((index, list(values or ())))
        return headers, rows
    finally:
        workbook.close()


async def _default_operator_id(db: AsyncSession, account_id: int) -> int:
    """导入时「跟进人」取不到时的默认账号：优先平台 admin 账号，回退当前操作人。"""
    admin_id = await db.scalar(
        text("SELECT id FROM matchmaker_admin_account WHERE username = 'admin' LIMIT 1")
    )
    return int(admin_id or account_id)


async def _resolve_operator_id(
    db: AsyncSession, name: str, cache: dict[str, int], fallback: int
) -> int:
    """把模板中的「跟进人」称呼映射到后台账号 id；匹配不到（或为空）回退默认账号。"""
    key = name.strip()
    if not key:
        return fallback
    if key in cache:
        return cache[key]
    operator_id = await db.scalar(
        text(
            "SELECT id FROM matchmaker_admin_account"
            " WHERE display_name = :name AND status = 1 LIMIT 1"
        ),
        {"name": key},
    )
    if not operator_id:
        # 服务红娘可能只有 users.nickname，通过账号绑定的 matchmaker_user_id 反查
        operator_id = await db.scalar(
            text(
                "SELECT acc.id FROM users u"
                " JOIN matchmaker_admin_account acc ON acc.matchmaker_user_id = u.id"
                " WHERE u.nickname = :name AND acc.status = 1 LIMIT 1"
            ),
            {"name": key},
        )
    resolved = int(operator_id or fallback)
    cache[key] = resolved
    return resolved


async def import_follow_ups(
    db: AsyncSession,
    account_id: int,
    data: bytes,
    filename: str,
) -> MemberFollowUpImportResult:
    """解析上传的 Excel，按手机号匹配会员后批量写入历史跟进记录。

    规则（与页面「须知」一致）：
    - 按「会员手机号」识别会员，会员CRM 中不存在该手机号的行无法导入；
    - 「跟进人」与后台账号称呼无法对应或为空时默认 admin；
    - 「跟进时间」缺失或格式无法匹配时使用导入时间；
    - 「跟进方式」无法识别时按 OTHER。
    """
    if not data:
        raise HTTPException(400, detail="上传文件为空")

    headers, rows_only = _read_sheet(data, filename)
    if not rows_only:
        # 表头存在但无数据行，视为空文件
        return MemberFollowUpImportResult(created=0, skipped=0, failed=0, errors=[])

    phone_col = _find_column(headers, "手机", "电话", "phone")
    content_col = _find_column(headers, "跟进内容", "内容", "content")
    matchmaker_col = _find_column(headers, "跟进人", "红娘", "matchmaker")
    time_col = _find_column(headers, "跟进时间", "时间", "time")
    method_col = _find_column(headers, "跟进方式", "方式", "method")

    fallback_operator = await _default_operator_id(db, account_id)
    operator_cache: dict[str, int] = {}

    created = skipped = failed = 0
    errors: list[str] = []
    for excel_row, values in rows_only:

        def cell(column: int | None) -> Any:
            return values[column] if column is not None and column < len(values) else None

        phone = _cell_text(cell(phone_col))
        content = _cell_text(cell(content_col))
        if not phone and not content:
            skipped += 1
            continue
        if not phone:
            failed += 1
            errors.append(f"第 {excel_row} 行：缺少会员手机号")
            continue
        if not content:
            failed += 1
            errors.append(f"第 {excel_row} 行：缺少跟进内容")
            continue

        user_id = await db.scalar(
            text("SELECT id FROM users WHERE phone = :phone AND status = 1 LIMIT 1"),
            {"phone": phone},
        )
        if not user_id:
            failed += 1
            errors.append(f"第 {excel_row} 行：手机号 {phone} 在会员CRM中不存在，无法导入")
            continue

        method = _parse_method(cell(method_col))
        followed_at = _parse_datetime(cell(time_col))
        operator_id = await _resolve_operator_id(
            db, _cell_text(cell(matchmaker_col)), operator_cache, fallback_operator
        )
        try:
            if followed_at is None:
                result = await db.execute(
                    text(
                        "INSERT INTO member_follow_up (user_id, method, content, created_by)"
                        " VALUES (:user_id, :method, :content, :created_by)"
                    ),
                    {
                        "user_id": int(user_id),
                        "method": method,
                        "content": content,
                        "created_by": operator_id,
                    },
                )
            else:
                result = await db.execute(
                    text(
                        "INSERT INTO member_follow_up (user_id, method, content, created_by, created_at)"
                        " VALUES (:user_id, :method, :content, :created_by, :created_at)"
                    ),
                    {
                        "user_id": int(user_id),
                        "method": method,
                        "content": content,
                        "created_by": operator_id,
                        "created_at": followed_at,
                    },
                )
            follow_id = int(result.lastrowid)
            await db.execute(
                text(
                    "INSERT INTO business_audit_log (actor_user_id, action, resource_type, resource_id)"
                    " VALUES (:actor, 'member.follow_up.import', 'member_follow_up', :id)"
                ),
                {"actor": account_id, "id": follow_id},
            )
            created += 1
        except HTTPException:
            raise
        except Exception as exc:  # 单行失败不阻断整批
            failed += 1
            errors.append(f"第 {excel_row} 行：{exc}")
    await db.commit()
    return MemberFollowUpImportResult(
        created=created, skipped=skipped, failed=failed, errors=errors[:50]
    )
