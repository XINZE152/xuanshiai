"""users 表可选列探测。

补列迁移与代码发布不一定同批到达：登录、换绑、后台编辑等写路径不能因为
``Unknown column`` 整体失败。列名只来自代码常量，不接受外部输入。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def users_has_column(db: AsyncSession, column: str) -> bool:
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE()"
                " AND TABLE_NAME = 'users' AND COLUMN_NAME = :column"
            ),
            {"column": column},
        )
    ).first()
    return row is not None


async def should_reset_phone_region(db: AsyncSession, *, phone_changed: bool) -> bool:
    """号码确实变化且 ``phone_region`` 列存在时，才需要把属地置空。"""
    return bool(phone_changed and await users_has_column(db, "phone_region"))
