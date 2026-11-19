"""Meeting ownership follows the applicant; old unassigned requests remain visible."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def meeting_owner_sql(alias: str, column: str = "matchmaker_id") -> str:
    # alias/column are constants supplied by service code, never request input.
    return (
        f"COALESCE(NULLIF({alias}.{column}, 0), (SELECT ra.{column} FROM resource_assignment ra "
        f"WHERE ra.user_id = {alias}.user_id AND ra.status = 1 ORDER BY ra.id DESC LIMIT 1))"
    )


async def applicant_assignment(db: AsyncSession, user_id: int) -> dict:
    row = (await db.execute(text("""SELECT matchmaker_id, organization_id FROM resource_assignment
        WHERE user_id = :user_id AND status = 1 ORDER BY id DESC LIMIT 1"""),
        {"user_id": user_id})).mappings().first()
    return dict(row) if row else {}
