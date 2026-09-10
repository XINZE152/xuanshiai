"""Seed the local-only debug matchmaker account (登录页"账号一").

Creates or refreshes user 19730552884 (红娘测试账号) with the debug password
``password123`` and the business roles needed by every 红娘工作台 entry, then
removes the placeholder user 13800138001 together with every row that
references it.  Development/testing only: the script refuses to run outside
development/testing so it can never mutate a deployed environment.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TARGET_PHONE = "19730552884"
MARKER = "local-demo-account-one"
TARGET_OPENID = MARKER
TARGET_NICKNAME = "红娘测试账号"
TARGET_PASSWORD = "password123"
PLACEHOLDER_PHONE = "13800138001"
ROLES = ("service_matchmaker", "promoter", "partner")


def seed_local_debug_account() -> dict[str, object]:
    env = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if env not in {"", "development", "testing"}:
        raise RuntimeError("本地调试账号只允许在 development/testing 环境写入")

    import pymysql
    from app.core.security import hash_password
    from database_setup_marriage import get_db_config

    connection = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    cur = connection.cursor()
    result: dict[str, object] = {}

    # 1. Drop the placeholder user and every child row that references it.
    cur.execute("SELECT id FROM users WHERE phone=%s LIMIT 1", (PLACEHOLDER_PHONE,))
    row = cur.fetchone()
    if row:
        placeholder_id = int(row["id"])
        cur.execute(
            """SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE
            WHERE REFERENCED_TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME = 'users'"""
        )
        references = cur.fetchall()
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            for ref in references:
                table = str(ref["TABLE_NAME"])
                column = str(ref["COLUMN_NAME"])
                cur.execute(f"DELETE FROM `{table}` WHERE `{column}`=%s", (placeholder_id,))
                result[f"deleted:{table}"] = cur.rowcount
            cur.execute("DELETE FROM users WHERE id=%s", (placeholder_id,))
            result["deleted:users"] = cur.rowcount
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS=1")

    # 2. Upsert the debug matchmaker account.
    cur.execute("SELECT id FROM users WHERE phone=%s OR openid=%s LIMIT 1", (TARGET_PHONE, TARGET_OPENID))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            """INSERT INTO users (openid, phone, phone_verified_at, nickname, gender, status,
            data_complete_rate, register_ip, password)
            VALUES (%s,%s,UTC_TIMESTAMP(),%s,1,1,100,'127.0.0.1',%s)""",
            (TARGET_OPENID, TARGET_PHONE, TARGET_NICKNAME, hash_password(TARGET_PASSWORD)),
        )
        target_id = int(cur.lastrowid)
        result["created"] = TARGET_PHONE
    else:
        target_id = int(row["id"])
        cur.execute(
            """UPDATE users SET phone=%s, openid=COALESCE(NULLIF(openid,''),%s), nickname=%s,
            status=1, phone_verified_at=COALESCE(phone_verified_at, UTC_TIMESTAMP()),
            password=COALESCE(password,%s) WHERE id=%s""",
            (TARGET_PHONE, TARGET_OPENID, TARGET_NICKNAME, hash_password(TARGET_PASSWORD), target_id),
        )
        result["updated"] = TARGET_PHONE
    result["user_id"] = target_id

    # 3. Grant every business role the workbench entry points check.
    for role_code in ROLES:
        cur.execute(
            """INSERT INTO user_role (user_id, role_code, status)
            VALUES (%s,%s,1)
            ON DUPLICATE KEY UPDATE status=1, revoked_at=NULL, revoke_reason=NULL""",
            (target_id, role_code),
        )
    result["roles"] = list(ROLES)

    # 4. Wire the account into the local demo workspaces so every tab has data.
    cur.execute("SELECT id FROM organization WHERE code=%s AND status=1 LIMIT 1", ("dev-lead-workbench-org",))
    org_row = cur.fetchone()
    if org_row is None:
        raise RuntimeError("找不到开发组织 dev-lead-workbench-org，请先运行 seed_lead_workbench_demo.py")
    organization_id = int(org_row["id"])

    cur.execute(
        """INSERT INTO matchmaker_workspace_profile
        (user_id, display_name, level, organization_id, status)
        VALUES (%s,%s,'SUPER',%s,1)
        ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), level='SUPER',
        organization_id=VALUES(organization_id), status=1, updated_at=UTC_TIMESTAMP()""",
        (target_id, TARGET_NICKNAME, organization_id),
    )

    # Members assigned under local-demo assignments carry no organization_id,
    # which hides them from an ORGANIZATION-scope actor.  Backfill it.
    cur.execute(
        """UPDATE resource_assignment SET organization_id=%s
        WHERE organization_id IS NULL AND status=1
        AND matchmaker_id IN (SELECT user_id FROM matchmaker_workspace_profile WHERE status=1)""",
        (organization_id,),
    )
    result["assignments_org_backfilled"] = cur.rowcount

    # Promotion code + attributions so the promoter center has content.
    cur.execute("SELECT id FROM promotion_touch WHERE code=%s LIMIT 1", ("LOCAL-DEBUG-PROMOTER",))
    touch_row = cur.fetchone()
    if touch_row is None:
        cur.execute(
            "INSERT INTO promotion_touch (code, promoter_id, created_at) VALUES (%s,%s,UTC_TIMESTAMP())",
            ("LOCAL-DEBUG-PROMOTER", target_id),
        )
        touch_id = int(cur.lastrowid)
    else:
        touch_id = int(touch_row["id"])
        cur.execute("UPDATE promotion_touch SET promoter_id=%s WHERE id=%s", (target_id, touch_id))
    cur.execute(
        """SELECT id FROM users WHERE openid LIKE %s AND id <> %s ORDER BY id DESC LIMIT 3""",
        ("local-demo-workspace-member-%", target_id),
    )
    member_rows = cur.fetchall()
    attributed = 0
    for member_row in member_rows:
        member_id = int(member_row["id"])
        cur.execute("SELECT 1 FROM promotion_attribution WHERE user_id=%s AND status=1 LIMIT 1", (member_id,))
        if cur.fetchone():
            cur.execute(
                "UPDATE promotion_attribution SET promoter_id=%s, status=1, ended_at=NULL, end_reason=NULL WHERE user_id=%s AND status=1",
                (target_id, member_id),
            )
        else:
            cur.execute(
                """INSERT INTO promotion_attribution (user_id, promoter_id, organization_id, touch_id, status)
                VALUES (%s,%s,%s,%s,1)""",
                (member_id, target_id, organization_id, touch_id),
            )
        attributed += 1
    result["promoter_attributions"] = attributed

    # A couple of customer leads owned by the account.
    for lead in (
        {"name": "测试客源一", "phone": "13922220001", "wechat": "wx_debug_lead_01",
         "source": "朋友推荐", "intention_level": 3, "status": "NEW",
         "remark": f"{MARKER}: 本地调试账号客源一"},
        {"name": "测试客源二", "phone": "13922220002", "wechat": "wx_debug_lead_02",
         "source": "活动咨询", "intention_level": 2, "status": "CONTACTED",
         "remark": f"{MARKER}: 本地调试账号客源二"},
    ):
        cur.execute("SELECT id FROM customer_lead WHERE remark=%s LIMIT 1", (lead["remark"],))
        row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE customer_lead SET name=%s, phone=%s, wechat=%s, source=%s,
                intention_level=%s, status=%s, created_by=%s WHERE id=%s""",
                (lead["name"], lead["phone"], lead["wechat"], lead["source"],
                 lead["intention_level"], lead["status"], target_id, int(row["id"])),
            )
        else:
            cur.execute(
                """INSERT INTO customer_lead
                (name, phone, wechat, source, intention_level, status, remark, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (lead["name"], lead["phone"], lead["wechat"], lead["source"],
                 lead["intention_level"], lead["status"], lead["remark"], target_id),
            )
    result["customer_leads"] = 2

    connection.commit()
    connection.close()
    return result


if __name__ == "__main__":
    print("seeded:", seed_local_debug_account())
