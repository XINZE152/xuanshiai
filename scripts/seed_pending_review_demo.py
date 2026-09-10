"""Seed rich local demo data for the matchmaker pending-review workspace.

Idempotent: every record carries the ``local-demo-workspace`` marker so reruns
update in place instead of duplicating.  Development/testing only.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MARK = "local-demo-workspace"
TARGET_PHONE = "19730552884"
TARGET_DISPLAY_NAME = "红娘测试账号"
TARGET_ORG_CODE = "dev-lead-workbench-org"

NEW_MEMBERS = [
    dict(key="member-09", nickname="苏晚棠（演示）", gender=2, birthday="1998-03-14", hometown="武汉", residence="武汉",
         education="本科", job="儿科护士", married=0, height=165, realname=2, review="PENDING",
         phone="13900000041", completion=88, owner_key="danxi"),
    dict(key="member-10", nickname="陆则言（演示）", gender=1, birthday="1992-11-02", hometown="长沙", residence="武汉",
         education="硕士", job="结构工程师", married=2, height=180, realname=2, review="PASSED",
         phone="13900000042", completion=95, owner_key="danxi"),
    dict(key="member-11", nickname="姜疏影（演示）", gender=2, birthday="1995-06-25", hometown="宜昌", residence="武汉",
         education="大专", job="花艺师", married=0, height=160, realname=0, review="REJECTED",
         phone="13900000043", completion=62, owner_key="yiyi"),
]

FOLLOW_UPS = [
    ("member-01", "PHONE", "初次电话沟通顺畅，确认目前单身且认真择偶，下周发送推荐名单。", 3, 2),
    ("member-01", "WECHAT", "微信推送 2 位候选人资料，等待反馈。", 1, None),
    ("member-05", "VISIT", "到店面谈 40 分钟，细化择偶偏好：优先本城、接受 3 岁内差距。", 4, 1),
    ("member-03", "OTHER", "提醒补充学历证明材料，资料齐全后进入优先推荐池。", 2, None),
    ("member-07", "PHONE", "弃海前电话回访，会员表示暂缓三个月再考虑。", 6, None),
    ("member-09", "PHONE", "新会员建档电话，性格开朗，期望另一半有稳定工作。", 1, 0),
    ("member-09", "OTHER", "已收集生活照 3 张，待审核通过后完善档案。", 0, None),
]

NOTES = [
    ("member-01", "性格沉稳有条理，对家庭分工期待清晰，建议匹配同城稳定型男生。"),
    ("member-03", "审美在线、表达细腻，补齐学历证明后可优先推荐。"),
]

# (from_key, to_key, tag, status, days_ago, responded_days_ago | None)
APPLIES = [
    ("member-02", "member-01", "42-41-pending", 0, 1, None),
    ("member-01", "member-04", "41-44-agreed", 1, 6, 5),
    ("member-04", "member-01", "44-41-rejected", 2, 4, 3),
    ("member-06", "member-05", "46-45-agreed", 1, 2, 1),
    ("member-08", "member-03", "48-43-pending", 0, 0, None),
    ("member-10", "member-05", "56-45-closed", 3, 9, 8),
    ("member-09", "member-02", "55-42-pending", 0, 0, None),
    ("member-02", "member-09", "42-55-agreed", 1, 3, 2),
    ("member-06", "member-11", "46-57-rejected", 2, 5, 4),
]

PHONE_BASE = {"member-01": "13900000044", "member-02": "13900000045", "member-03": "13900000046",
              "member-04": "13900000047", "member-05": "13900000048", "member-06": "13900000049",
              "member-07": "13900000050", "member-08": "13900000051"}


def seed_pending_review_demo() -> dict[str, int]:
    env = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if env not in {"", "development", "testing"}:
        raise RuntimeError("资料待审演示数据只允许在 development/testing 环境写入")

    import pymysql
    from app.core.security import hash_password
    from database_setup_marriage import get_db_config

    connection = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    cur = connection.cursor()
    now = datetime.utcnow()

    def uid_by_openid(key: str) -> int | None:
        cur.execute("SELECT id FROM users WHERE openid=%s LIMIT 1", (f"{MARK}-{key}",))
        row = cur.fetchone()
        return int(row["id"]) if row else None

    cur.execute("SELECT id FROM users WHERE phone=%s LIMIT 1", (TARGET_PHONE,))
    target_row = cur.fetchone()
    if target_row is None:
        raise RuntimeError(f"找不到目标红娘账号：{TARGET_PHONE}")
    target_id = int(target_row["id"])
    cur.execute("SELECT id FROM organization WHERE code=%s AND status=1 LIMIT 1", (TARGET_ORG_CODE,))
    org_row = cur.fetchone()
    if org_row is None:
        raise RuntimeError(f"找不到开发组织：{TARGET_ORG_CODE}")
    organization_id = int(org_row["id"])

    # Resolve the service-matchmaker owner ids dynamically so the script works
    # in any environment (the ids of 蛋希/依依 differ per database).
    def service_matchmaker_id(suffix: str) -> int:
        cur.execute("SELECT id FROM users WHERE openid=%s LIMIT 1", (f"dev-lead-workbench-{suffix}",))
        owner_row = cur.fetchone()
        if owner_row is None:
            raise RuntimeError(f"找不到服务红娘 dev-lead-workbench-{suffix}，请先运行 seed_lead_workbench_demo.py")
        return int(owner_row["id"])

    owner_ids = {"danxi": service_matchmaker_id("danxi"), "yiyi": service_matchmaker_id("yiyi")}
    cur.execute(
        """INSERT INTO matchmaker_workspace_profile
        (user_id, display_name, level, organization_id, status)
        VALUES (%s,%s,'SUPER',%s,1)
        ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), level='SUPER',
        organization_id=VALUES(organization_id), status=1, updated_at=UTC_TIMESTAMP()""",
        (target_id, TARGET_DISPLAY_NAME, organization_id),
    )

    old_ids = {f"member-{i:02d}": uid_by_openid(f"member-{i:02d}") for i in range(1, 9)}
    old_ids = {key: value for key, value in old_ids.items() if value is not None}

    new_ids: dict[str, int] = {}
    for item in NEW_MEMBERS:
        uid = uid_by_openid(item["key"])
        if uid is None:
            cur.execute(
                """INSERT INTO users (openid, phone, phone_verified_at, nickname, gender, birthday, is_married,
                status, data_complete_rate, register_ip, password)
                VALUES (%s,%s,UTC_TIMESTAMP(),%s,%s,%s,%s,1,%s,'127.0.0.1',%s)""",
                (f"{MARK}-{item['key']}", item["phone"], item["nickname"], item["gender"], item["birthday"],
                 item["married"], item["completion"], hash_password("password123")),
            )
            uid = int(cur.lastrowid)
        else:
            cur.execute(
                """UPDATE users SET phone=%s, nickname=%s, is_married=%s, birthday=%s, gender=%s, status=1,
                password=COALESCE(password,%s) WHERE id=%s""",
                (item["phone"], item["nickname"], item["married"], item["birthday"], item["gender"],
                 hash_password("password123"), uid),
            )
        cur.execute(
            """INSERT INTO user_profile (user_id, height, hometown, residence) VALUES (%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE height=VALUES(height), hometown=VALUES(hometown), residence=VALUES(residence)""",
            (uid, item["height"], item["hometown"], item["residence"]),
        )
        cur.execute(
            """INSERT INTO user_auth (user_id, education, job, realname_status)
            VALUES (%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE education=VALUES(education), job=VALUES(job),
            realname_status=VALUES(realname_status)""",
            (uid, item["education"], item["job"], item["realname"]),
        )
        cur.execute("SELECT 1 FROM user_profile_completion WHERE user_id=%s LIMIT 1", (uid,))
        if cur.fetchone():
            cur.execute("UPDATE user_profile_completion SET score=%s WHERE user_id=%s", (item["completion"], uid))
        else:
            cur.execute("INSERT INTO user_profile_completion (user_id, score) VALUES (%s,%s)", (uid, item["completion"]))
        reason = "本地演示：照片模糊，请重新上传生活近照" if item["review"] == "REJECTED" else None
        reviewed_at = now if item["review"] != "PENDING" else None
        cur.execute(
            """INSERT INTO matchmaker_member_review (user_id, status, reason, reviewed_by, reviewed_at)
            VALUES (%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE status=VALUES(status), reason=VALUES(reason), reviewed_at=VALUES(reviewed_at)""",
            (uid, item["review"], reason, target_id if reviewed_at else None, reviewed_at),
        )
        cur.execute(
            "UPDATE resource_assignment SET status=2, ended_at=UTC_TIMESTAMP(), end_reason='reseed' WHERE user_id=%s AND status=1",
            (uid,),
        )
        cur.execute(
            """INSERT INTO resource_assignment (user_id, organization_id, matchmaker_id, source, status, assigned_by)
            VALUES (%s,%s,%s,'manual',1,%s)""",
            (uid, organization_id, owner_ids[item["owner_key"]], target_id),
        )
        pref = (26, 36, 172, 188) if item["gender"] == 2 else (24, 34, 156, 170)
        cur.execute(
            """INSERT INTO user_partner_preference (user_id, age_min, age_max, height_min, height_max,
            accept_long_distance, accept_cross_province) VALUES (%s,%s,%s,%s,%s,1,1)
            ON DUPLICATE KEY UPDATE age_min=VALUES(age_min), age_max=VALUES(age_max),
            height_min=VALUES(height_min), height_max=VALUES(height_max)""",
            (uid, pref[0], pref[1], pref[2], pref[3]),
        )
        new_ids[item["key"]] = uid

    for key, phone in PHONE_BASE.items():
        uid = old_ids.get(key)
        if uid is not None:
            cur.execute(
                "UPDATE users SET phone=%s, phone_verified_at=UTC_TIMESTAMP() WHERE id=%s AND (phone IS NULL OR phone LIKE %s)",
                (phone, uid, "139000000%"),
            )
    for key in ("member-02", "member-04", "member-08"):
        if key in old_ids:
            cur.execute("UPDATE user_auth SET realname_status=2 WHERE user_id=%s", (old_ids[key],))
    if "member-07" in old_ids:
        cur.execute("UPDATE users SET is_married=2 WHERE id=%s", (old_ids["member-07"],))

    for frm, to, tag, status, days_ago, responded_days in APPLIES:
        from_id = new_ids.get(frm, old_ids.get(frm))
        to_id = new_ids.get(to, old_ids.get(to))
        if from_id is None or to_id is None:
            continue
        message = f"{MARK}:apply:{tag}"
        created = now - timedelta(days=days_ago)
        responded = now - timedelta(days=responded_days) if responded_days is not None else None
        cur.execute("SELECT id FROM match_apply WHERE message=%s LIMIT 1", (message,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE match_apply SET from_user_id=%s, to_user_id=%s, status=%s, created_at=%s, responded_at=%s WHERE id=%s",
                (from_id, to_id, status, created, responded, int(row["id"])),
            )
        else:
            cur.execute(
                """INSERT INTO match_apply (from_user_id, to_user_id, message, status, responded_at, expire_at, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (from_id, to_id, message, status, responded, created + timedelta(hours=72), created),
            )

    for key, method, content, days_ago, next_days in FOLLOW_UPS:
        uid = new_ids.get(key, old_ids.get(key))
        if uid is None:
            continue
        marked = f"{MARK}:{content}"
        cur.execute("SELECT id FROM member_follow_up WHERE content=%s LIMIT 1", (marked,))
        if cur.fetchone():
            continue
        cur.execute(
            """INSERT INTO member_follow_up (user_id, method, content, next_follow_at, created_by, created_at)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (uid, method, marked, now + timedelta(days=next_days) if next_days is not None else None,
             target_id, now - timedelta(days=days_ago)),
        )

    for key, note in NOTES:
        uid = new_ids.get(key, old_ids.get(key))
        if uid is None:
            continue
        cur.execute(
            """INSERT INTO matchmaker_admin_member_note (user_id, note, updated_by) VALUES (%s,%s,%s)
            ON DUPLICATE KEY UPDATE note=VALUES(note), updated_by=VALUES(updated_by)""",
            (uid, note, target_id),
        )

    connection.commit()
    connection.close()
    return {"new_members": new_ids, "existing_members": old_ids}


if __name__ == "__main__":
    result = seed_pending_review_demo()
    print("seeded:", result)
