"""Backfill legacy admin applications into the shared introduction table."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.matchmaker_workspace import _match_record_follow_up_owner


async def backfill_admin_introductions(db: AsyncSession, *, apply: bool) -> dict[str, int]:
    """Keep existing introduction IDs/audits and link legacy application IDs by note.

    The caller owns the transaction and must prevent concurrent application writes.
    Existing linked rows are never overwritten; conflicts abort the migration.
    """
    rows = (await db.execute(text("""SELECT id, from_user_id, to_user_id, status,
        created_at, updated_at, responded_at FROM match_apply ORDER BY id FOR UPDATE"""))).mappings().all()
    summary = {"applications": len(rows), "linked": 0, "missing": 0, "deleted": 0}
    statuses = {0: "PENDING", 1: "SUCCEEDED", 2: "FAILED", 3: "CLOSED"}
    for row in rows:
        record_id = int(row["id"])
        deleted = await db.scalar(text("""SELECT 1 FROM business_audit_log
            WHERE resource_type = 'match_apply' AND resource_id = :id
            AND action = 'match_record.unified_deleted' LIMIT 1"""), {"id": record_id})
        if deleted:
            summary["deleted"] += 1
            continue
        status = statuses.get(row["status"])
        if status is None:
            raise ValueError(f"Unsupported application status for record {record_id}")
        notes = {"admin": f"admin-entry:{record_id}", "discovery": f"discovery-apply:{record_id}"}
        linked = (await db.execute(text("""SELECT id, from_user_id, to_user_id, status
            FROM matchmaker_introduction WHERE note IN (:admin, :discovery) FOR UPDATE"""), notes)).mappings().all()
        if len(linked) > 1:
            raise ValueError(f"Multiple introduction links for application {record_id}")
        if linked:
            current = linked[0]
            if int(current["from_user_id"]) != int(row["from_user_id"]) or int(current["to_user_id"]) != int(row["to_user_id"]):
                raise ValueError(f"Introduction participants do not match application {record_id}")
            if status in ("SUCCEEDED", "FAILED", "CLOSED") and current["status"] != status:
                raise ValueError(f"Introduction status conflicts with application {record_id}")
            summary["linked"] += 1
            continue
        summary["missing"] += 1
        if not apply:
            continue
        owner = await _match_record_follow_up_owner(db, int(row["from_user_id"]), int(row["to_user_id"]))
        matchmaker_id, organization_id = owner if owner is not None else (0, None)
        reason = None
        if status in ("FAILED", "CLOSED"):
            reason = await db.scalar(text("""SELECT reason FROM business_audit_log
                WHERE resource_type = 'match_apply' AND resource_id = :id
                AND action IN ('match_record.status', 'match_record.timeout_fail')
                ORDER BY id DESC LIMIT 1"""), {"id": record_id})
        inserted = await db.execute(text("""INSERT INTO matchmaker_introduction
            (from_user_id, to_user_id, matchmaker_id, organization_id, status, failure_reason, note, created_at, updated_at)
            VALUES (:from_user_id, :to_user_id, :matchmaker_id, :organization_id, :status, :reason,
                    :note, COALESCE(:created_at, UTC_TIMESTAMP()),
                    COALESCE(:responded_at, :updated_at, :created_at, UTC_TIMESTAMP()))"""), {
            "from_user_id": row["from_user_id"], "to_user_id": row["to_user_id"],
            "matchmaker_id": matchmaker_id, "organization_id": organization_id, "status": status,
            "reason": str(reason)[:500] if reason else None,
            "note": notes["admin"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "responded_at": row["responded_at"],
        })
        await db.execute(text("""INSERT INTO business_audit_log
            (actor_user_id, action, resource_type, resource_id, reason)
            VALUES (NULL, 'match_record.migrated', 'matchmaker_introduction', :new_id, :reason)"""),
            {"new_id": int(inserted.lastrowid), "reason": f"原 match_apply 记录 ID: {record_id}"})
    return summary
