"""Seed repeatable, fictional local data for the three matchmaker workspaces.

The script is deliberately restricted to development/testing.  Every record it
owns carries the ``local-demo-workspace`` marker or a ``local-demo-workspace-``
openid, so it never repurposes an existing user or business record.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


MARKER = "local-demo-workspace"
ACTOR_OPENID = f"{MARKER}-operator"
ACTOR_PHONE = "13900000001"
ACTOR_NAME = "本地演示运营"

MEMBERS = (
    {
        "key": "member-01",
        "nickname": "林知夏（演示）",
        "gender": 2,
        "birthday": "1996-05-18",
        "hometown": "南京",
        "residence": "南京",
        "education": "本科",
        "job": "内容策划",
        "review": "PASSED",
        "completion": 96,
    },
    {
        "key": "member-02",
        "nickname": "周予安（演示）",
        "gender": 1,
        "birthday": "1994-09-12",
        "hometown": "杭州",
        "residence": "上海",
        "education": "硕士",
        "job": "建筑设计师",
        "review": "PASSED",
        "completion": 94,
    },
    {
        "key": "member-03",
        "nickname": "顾言澄（演示）",
        "gender": 2,
        "birthday": "1997-02-26",
        "hometown": "苏州",
        "residence": "南京",
        "education": "本科",
        "job": "教育服务专员",
        "review": "PENDING",
        "completion": 78,
    },
    {
        "key": "member-04",
        "nickname": "许闻洲（演示）",
        "gender": 1,
        "birthday": "1993-07-21",
        "hometown": "合肥",
        "residence": "杭州",
        "education": "博士",
        "job": "高校教师",
        "review": "PASSED",
        "completion": 98,
    },
    {
        "key": "member-05",
        "nickname": "唐妤宁（演示）",
        "gender": 2,
        "birthday": "1995-08-09",
        "hometown": "杭州",
        "residence": "杭州",
        "education": "本科",
        "job": "品牌运营",
        "review": "PENDING",
        "completion": 70,
    },
    {
        "key": "member-06",
        "nickname": "程野（演示）",
        "gender": 1,
        "birthday": "1997-12-15",
        "hometown": "南通",
        "residence": "南京",
        "education": "本科",
        "job": "公益项目专员",
        "review": "PASSED",
        "completion": 92,
    },
    {
        "key": "member-07",
        "nickname": "沈知意（演示）",
        "gender": 2,
        "birthday": "1998-11-03",
        "hometown": "无锡",
        "residence": "上海",
        "education": "本科",
        "job": "产品经理",
        "review": "REJECTED",
        "completion": 62,
    },
    {
        "key": "member-08",
        "nickname": "陆景舟（演示）",
        "gender": 1,
        "birthday": "1995-03-28",
        "hometown": "宁波",
        "residence": "上海",
        "education": "硕士",
        "job": "数据分析师",
        "review": "PASSED",
        "completion": 89,
    },
)

TEAM_PROMOTERS = (
    ("promoter-01", "陈若晴（演示）"),
    ("promoter-02", "苏念安（演示）"),
)

LEADS = (
    ("lead-01", "韩女士（演示线索）", "139****0101", "local_demo_wx_01", "活动咨询", 3, "CONTACTED"),
    ("lead-02", "魏先生（演示线索）", "139****0102", "local_demo_wx_02", "朋友推荐", 2, "INTENDED"),
    ("lead-03", "宋女士（演示线索）", "139****0103", "local_demo_wx_03", "社群报名", 1, "CONVERTED"),
)

# 线索联系方式全局唯一（有效状态），因此每条演示线索只挂在一个工作台下，
# 其余工作台通过从表读取共享展示，不再按红娘复制同一条线索。

INTRODUCTIONS = (
    ("intro-01", "member-01", "member-02", "PENDING", None),
    ("intro-02", "member-03", "member-04", "PENDING", None),
    ("intro-03", "member-05", "member-06", "IN_PROGRESS", None),
    ("intro-04", "member-07", "member-08", "IN_PROGRESS", None),
    ("intro-05", "member-01", "member-04", "SUCCEEDED", None),
    ("intro-06", "member-05", "member-08", "SUCCEEDED", None),
    ("intro-07", "member-03", "member-06", "FAILED", "本地演示：双方期待不一致，已结束本次牵线"),
    ("intro-08", "member-07", "member-02", "FAILED", "本地演示：沟通节奏不同，暂不继续推进"),
)

MEETING_REQUESTS = (
    ("meeting-01", "member-01", "member-02", "SUBMITTED", None, None, 0, ""),
    ("meeting-02", "member-03", "member-04", "SUBMITTED", None, None, 0, ""),
    ("meeting-03", "member-05", "member-06", "CONTACTED", None, None, 0, ""),
    ("meeting-04", "member-07", "member-08", "ACCEPTED", None, None, 0, ""),
    ("meeting-05", "member-01", "member-04", "ACCEPTED", "SCHEDULED", None, 3, "本地演示会面室 A"),
    ("meeting-06", "member-05", "member-08", "ACCEPTED", "REMINDED", None, 6, "本地演示会面室 B"),
    ("meeting-07", "member-07", "member-02", "ACCEPTED", "COMPLETED", None, -6, "本地演示会面室 C"),
    ("meeting-08", "member-03", "member-06", "ACCEPTED", "CANCELLED", "本地演示：双方时间无法协调", -2, "本地演示会面室 D"),
)


def _row_id(row: Any) -> int:
    return int(row["id"] if isinstance(row, dict) else row[0])


def _user_id_by_openid(cursor: Any, openid: str) -> int | None:
    cursor.execute("SELECT id FROM users WHERE openid=%s LIMIT 1", (openid,))
    row = cursor.fetchone()
    return _row_id(row) if row else None


def _ensure_actor(cursor: Any, now: datetime) -> int:
    """Create the sole login-capable local demo operator without touching others."""
    cursor.execute("SELECT id, openid FROM users WHERE phone=%s LIMIT 1", (ACTOR_PHONE,))
    phone_owner = cursor.fetchone()
    if phone_owner and phone_owner["openid"] != ACTOR_OPENID:
        raise RuntimeError(f"演示手机号 {ACTOR_PHONE} 已属于其他账号，已停止写入")

    user_id = _user_id_by_openid(cursor, ACTOR_OPENID)
    if user_id is None:
        cursor.execute(
            """INSERT INTO users
            (openid, phone, phone_verified_at, nickname, status, data_complete_rate, register_ip)
            VALUES (%s,%s,%s,%s,1,100,'127.0.0.1')""",
            (ACTOR_OPENID, ACTOR_PHONE, now, ACTOR_NAME),
        )
        return int(cursor.lastrowid)
    cursor.execute(
        """UPDATE users SET phone=%s, phone_verified_at=%s, nickname=%s, status=1,
        data_complete_rate=100, updated_at=%s WHERE id=%s""",
        (ACTOR_PHONE, now, ACTOR_NAME, now, user_id),
    )
    return user_id


def _ensure_member(cursor: Any, item: dict[str, Any], now: datetime) -> int:
    openid = f"{MARKER}-{item['key']}"
    user_id = _user_id_by_openid(cursor, openid)
    if user_id is None:
        cursor.execute(
            """INSERT INTO users
            (openid, nickname, gender, birthday, status, is_married, is_single_pledge,
             data_complete_rate, register_ip, created_at)
            VALUES (%s,%s,%s,%s,1,1,1,%s,'127.0.0.1',%s)""",
            (openid, item["nickname"], item["gender"], item["birthday"], item["completion"], now),
        )
        user_id = int(cursor.lastrowid)
    else:
        cursor.execute(
            """UPDATE users SET nickname=%s, gender=%s, birthday=%s, status=1,
            is_married=1, is_single_pledge=1, data_complete_rate=%s, updated_at=%s WHERE id=%s""",
            (item["nickname"], item["gender"], item["birthday"], item["completion"], now, user_id),
        )
    cursor.execute(
        """INSERT INTO user_profile
        (user_id, hometown, residence, self_intro, love_view, hobbies, interest_tags,
         personality_tags, online_status, last_active_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s)
        ON DUPLICATE KEY UPDATE hometown=VALUES(hometown), residence=VALUES(residence),
        self_intro=VALUES(self_intro), love_view=VALUES(love_view), hobbies=VALUES(hobbies),
        interest_tags=VALUES(interest_tags), personality_tags=VALUES(personality_tags),
        online_status=1, last_active_at=VALUES(last_active_at)""",
        (
            user_id,
            item["hometown"],
            item["residence"],
            "此资料为本地演示账号生成的虚构信息，仅用于界面验收。",
            "尊重边界、坦诚沟通，循序渐进地了解彼此。",
            "阅读、城市漫步、运动",
            json.dumps(["本地演示", "认真沟通"], ensure_ascii=False),
            json.dumps(["真诚", "稳定"], ensure_ascii=False),
            now,
        ),
    )
    cursor.execute(
        """INSERT INTO user_auth (user_id, education, job, realname_status, auth_status, auth_step)
        VALUES (%s,%s,%s,0,0,0)
        ON DUPLICATE KEY UPDATE education=VALUES(education), job=VALUES(job),
        realname_status=0, auth_status=0, auth_step=0""",
        (user_id, item["education"], item["job"]),
    )
    cursor.execute(
        """INSERT INTO user_profile_completion
        (user_id, gender_completed, birthday_completed, location_completed, marriage_completed,
         occupation_completed, education_completed, avatar_completed, intro_completed,
         interest_completed, score, calculated_at)
        VALUES (%s,1,1,1,1,1,1,1,1,1,%s,%s)
        ON DUPLICATE KEY UPDATE score=VALUES(score), calculated_at=VALUES(calculated_at)""",
        (user_id, item["completion"], now),
    )
    return user_id


def _ensure_promoter(cursor: Any, key: str, nickname: str, now: datetime) -> int:
    openid = f"{MARKER}-{key}"
    user_id = _user_id_by_openid(cursor, openid)
    if user_id is None:
        cursor.execute(
            "INSERT INTO users (openid, nickname, status, register_ip, created_at) VALUES (%s,%s,1,'127.0.0.1',%s)",
            (openid, nickname, now),
        )
        user_id = int(cursor.lastrowid)
    else:
        cursor.execute("UPDATE users SET nickname=%s, status=1, updated_at=%s WHERE id=%s", (nickname, now, user_id))
    _ensure_role(cursor, user_id, "promoter", now)
    return user_id


def _ensure_role(cursor: Any, user_id: int, role_code: str, now: datetime) -> None:
    cursor.execute("SELECT id FROM user_role WHERE user_id=%s AND role_code=%s ORDER BY id DESC LIMIT 1", (user_id, role_code))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE user_role SET status=1, revoked_at=NULL, revoke_reason=NULL WHERE id=%s", (_row_id(row),))
    else:
        cursor.execute("INSERT INTO user_role (user_id, role_code, status, granted_at) VALUES (%s,%s,1,%s)", (user_id, role_code, now))


def _active_role_ids(cursor: Any, role_code: str) -> list[int]:
    """Return local role holders without reading or changing their profile data."""
    cursor.execute("SELECT DISTINCT user_id FROM user_role WHERE role_code=%s AND status=1", (role_code,))
    return [int(row["user_id"]) for row in cursor.fetchall()]


def _ensure_member_assignment(cursor: Any, member_id: int, matchmaker_id: int, now: datetime) -> None:
    """Attach only this script's fictional member record to a local workbench."""
    cursor.execute(
        "SELECT id FROM resource_assignment WHERE user_id=%s AND matchmaker_id=%s AND source='local_demo' LIMIT 1",
        (member_id, matchmaker_id),
    )
    assignment = cursor.fetchone()
    if assignment:
        cursor.execute("UPDATE resource_assignment SET status=1, ended_at=NULL, end_reason=NULL WHERE id=%s", (_row_id(assignment),))
        return
    cursor.execute(
        """INSERT INTO resource_assignment (user_id, matchmaker_id, source, status, assigned_by, effective_at)
        VALUES (%s,%s,'local_demo',1,%s,%s)""",
        (member_id, matchmaker_id, matchmaker_id, now),
    )


def _ensure_touch(cursor: Any, code: str, *, promoter_id: int | None, team_id: int | None, now: datetime) -> int:
    cursor.execute("SELECT id, promoter_id, partner_team_id FROM promotion_touch WHERE code=%s LIMIT 1", (code,))
    row = cursor.fetchone()
    if row and (row["promoter_id"] != promoter_id or row["partner_team_id"] != team_id):
        raise RuntimeError(f"演示推广码 {code} 已被其他记录占用，已停止写入")
    if row:
        touch_id = _row_id(row)
        cursor.execute("UPDATE promotion_touch SET expires_at=NULL WHERE id=%s", (touch_id,))
        return touch_id
    cursor.execute(
        "INSERT INTO promotion_touch (code, promoter_id, partner_team_id, created_at) VALUES (%s,%s,%s,%s)",
        (code, promoter_id, team_id, now),
    )
    return int(cursor.lastrowid)


def _ensure_lead(cursor: Any, actor_id: int, item: tuple[str, str, str, str, str, int, str], now: datetime) -> None:
    key, name, phone, wechat, source, intention, status = item
    marker = f"{MARKER}:{key}"
    cursor.execute("SELECT id FROM customer_lead WHERE remark=%s LIMIT 1", (marker,))
    row = cursor.fetchone()
    params = (name, phone, wechat, source, intention, status, now + timedelta(days=intention), actor_id, marker)
    if row:
        cursor.execute(
            """UPDATE customer_lead SET name=%s, phone=%s, wechat=%s, source=%s,
            intention_level=%s, status=%s, next_follow_at=%s, matchmaker_id=%s,
            updated_at=%s WHERE id=%s""",
            params[:-1] + (now, _row_id(row)),
        )
    else:
        cursor.execute(
            """INSERT INTO customer_lead
            (name, phone, wechat, source, intention_level, status, next_follow_at,
             matchmaker_id, created_by, remark, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            params[:-1] + (actor_id, marker, now),
        )


def _ensure_match_apply(
    cursor: Any, *, from_user_id: int, to_user_id: int, message: str, status: int, now: datetime
) -> None:
    cursor.execute("SELECT id FROM match_apply WHERE message=%s LIMIT 1", (message,))
    row = cursor.fetchone()
    if row:
        cursor.execute(
            "UPDATE match_apply SET from_user_id=%s, to_user_id=%s, status=%s, responded_at=%s, expire_at=%s WHERE id=%s",
            (from_user_id, to_user_id, status, now if status == 1 else None, now + timedelta(days=3), _row_id(row)),
        )
        return
    cursor.execute(
        """INSERT INTO match_apply (from_user_id, to_user_id, message, status, responded_at, expire_at, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (from_user_id, to_user_id, message, status, now if status == 1 else None, now + timedelta(days=3), now),
    )


def _ensure_introduction(
    cursor: Any,
    *,
    matchmaker_id: int,
    from_user_id: int,
    to_user_id: int,
    key: str,
    status: str,
    failure_reason: str | None,
    now: datetime,
) -> None:
    note = f"{MARKER}:{key}"
    cursor.execute(
        "SELECT id FROM matchmaker_introduction WHERE matchmaker_id=%s AND note=%s LIMIT 1",
        (matchmaker_id, note),
    )
    row = cursor.fetchone()
    params = (from_user_id, to_user_id, status, failure_reason, now)
    if row:
        cursor.execute(
            """UPDATE matchmaker_introduction SET from_user_id=%s, to_user_id=%s, status=%s,
            failure_reason=%s, updated_at=%s WHERE id=%s""",
            params + (_row_id(row),),
        )
        return
    cursor.execute(
        """INSERT INTO matchmaker_introduction
        (from_user_id, to_user_id, matchmaker_id, status, failure_reason, note, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (from_user_id, to_user_id, matchmaker_id, status, failure_reason, note, now),
    )


def _ensure_meeting_request(
    cursor: Any,
    *,
    matchmaker_id: int,
    user_id: int,
    target_user_id: int,
    key: str,
    status: str,
    meeting_status: str | None,
    cancel_reason: str | None,
    scheduled_offset_days: int,
    location: str,
    now: datetime,
) -> None:
    note = f"{MARKER}: 本地演示约见申请 {key}"
    cursor.execute("SELECT id FROM meeting_request WHERE matchmaker_id=%s AND note=%s LIMIT 1", (matchmaker_id, note))
    row = cursor.fetchone()
    if not row and key == "meeting-01":
        cursor.execute(
            "SELECT id FROM meeting_request WHERE matchmaker_id=%s AND note=%s LIMIT 1",
            (matchmaker_id, f"{MARKER}: 本地演示约见申请"),
        )
        row = cursor.fetchone()
    if row:
        request_id = _row_id(row)
        cursor.execute(
            "UPDATE meeting_request SET user_id=%s, target_user_id=%s, status=%s, note=%s, updated_at=%s WHERE id=%s",
            (user_id, target_user_id, status, note, now, request_id),
        )
    else:
        cursor.execute(
            """INSERT INTO meeting_request (user_id, target_user_id, matchmaker_id, status, note, created_at)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (user_id, target_user_id, matchmaker_id, status, note, now),
        )
        request_id = int(cursor.lastrowid)
    if meeting_status is None:
        return
    cursor.execute(
        "SELECT id FROM meeting_record WHERE request_id=%s AND location=%s LIMIT 1",
        (request_id, location),
    )
    meeting = cursor.fetchone()
    scheduled_at = now + timedelta(days=scheduled_offset_days)
    if meeting:
        cursor.execute(
            """UPDATE meeting_record SET organizer_id=%s, scheduled_at=%s, status=%s,
            cancel_reason=%s, updated_at=%s WHERE id=%s""",
            (matchmaker_id, scheduled_at, meeting_status, cancel_reason, now, _row_id(meeting)),
        )
        return
    cursor.execute(
        """INSERT INTO meeting_record
        (request_id, organizer_id, scheduled_at, location, status, cancel_reason, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (request_id, matchmaker_id, scheduled_at, location, meeting_status, cancel_reason, now),
    )


def seed_matchmaker_workspace_demo(
    connection: Any = None, *, environment: str | None = None, now: datetime | None = None
) -> dict[str, int]:
    """Populate one local account with realistic-looking, entirely fictional workspace data."""
    env = (environment or os.getenv("ENV") or os.getenv("ENVIRONMENT") or "development").lower()
    if env not in {"development", "testing"}:
        raise RuntimeError("红娘工作台演示数据只允许在 development/testing 环境写入")

    owned_connection = connection is None
    if owned_connection:
        import pymysql
        from database_setup_marriage import get_db_config

        connection = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor, autocommit=False)

    current_time = (now or datetime.now(UTC)).replace(tzinfo=None)
    cursor = connection.cursor()
    try:
        actor_id = _ensure_actor(cursor, current_time)
        for role in ("user", "service_matchmaker", "partner", "promoter"):
            _ensure_role(cursor, actor_id, role, current_time)
        cursor.execute(
            """INSERT INTO matchmaker_workspace_profile (user_id, display_name, level, organization_id, status)
            VALUES (%s,%s,'NORMAL',NULL,1)
            ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), level='NORMAL',
            organization_id=NULL, status=1, updated_at=VALUES(updated_at)""",
            (actor_id, ACTOR_NAME),
        )

        member_ids = {item["key"]: _ensure_member(cursor, item, current_time) for item in MEMBERS}
        workspace_actor_ids = _active_role_ids(cursor, "service_matchmaker")
        if actor_id not in workspace_actor_ids:
            workspace_actor_ids.append(actor_id)
        for item in MEMBERS:
            member_id = member_ids[item["key"]]
            for workspace_actor_id in workspace_actor_ids:
                _ensure_member_assignment(cursor, member_id, workspace_actor_id, current_time)
            reason = "本地演示：补充职业信息后可重新提交" if item["review"] == "REJECTED" else None
            reviewed_at = current_time if item["review"] != "PENDING" else None
            cursor.execute(
                """INSERT INTO matchmaker_member_review (user_id, status, reason, reviewed_by, reviewed_at)
                VALUES (%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE status=VALUES(status), reason=VALUES(reason),
                reviewed_by=VALUES(reviewed_by), reviewed_at=VALUES(reviewed_at)""",
                (member_id, item["review"], reason, actor_id if reviewed_at else None, reviewed_at),
            )

        # 线索联系方式全局唯一，演示线索只创建一份并挂在演示操作员名下；
        # 其它服务红娘工作台的线索演示由 seed_lead_workbench_demo 提供。
        for lead in LEADS:
            _ensure_lead(cursor, actor_id, lead, current_time)
        for workspace_actor_id in workspace_actor_ids:
            for key, from_key, to_key, status, failure_reason in INTRODUCTIONS:
                _ensure_introduction(
                    cursor,
                    matchmaker_id=workspace_actor_id,
                    from_user_id=member_ids[from_key],
                    to_user_id=member_ids[to_key],
                    key=key,
                    status=status,
                    failure_reason=failure_reason,
                    now=current_time,
                )

        cursor.execute("SELECT id FROM partner_team WHERE owner_user_id=%s LIMIT 1", (actor_id,))
        team = cursor.fetchone()
        if team:
            team_id = _row_id(team)
            cursor.execute("UPDATE partner_team SET name=%s, status=1, open_mode='manual' WHERE id=%s", ("本地演示合伙人团队", team_id))
        else:
            cursor.execute(
                "INSERT INTO partner_team (owner_user_id, name, status, open_mode, created_at) VALUES (%s,%s,1,'manual',%s)",
                (actor_id, "本地演示合伙人团队", current_time),
            )
            team_id = int(cursor.lastrowid)

        _ensure_touch(cursor, "LOCAL-DEMO-TEAM", promoter_id=None, team_id=team_id, now=current_time)
        own_touch_id = _ensure_touch(cursor, "LOCAL-DEMO-PROMOTER", promoter_id=actor_id, team_id=None, now=current_time)
        promoter_ids = [actor_id]
        for key, nickname in TEAM_PROMOTERS:
            promoter_ids.append(_ensure_promoter(cursor, key, nickname, current_time))
        for promoter_id in promoter_ids:
            cursor.execute("SELECT id FROM partner_membership WHERE promoter_id=%s AND status=1 LIMIT 1", (promoter_id,))
            membership = cursor.fetchone()
            if membership:
                cursor.execute("UPDATE partner_membership SET team_id=%s, joined_at=%s WHERE id=%s", (team_id, current_time, _row_id(membership)))
            else:
                cursor.execute(
                    "INSERT INTO partner_membership (team_id, promoter_id, status, joined_at, changed_by) VALUES (%s,%s,1,%s,%s)",
                    (team_id, promoter_id, current_time, actor_id),
                )

        for index, item in enumerate(MEMBERS):
            member_id = member_ids[item["key"]]
            promoter_id = promoter_ids[index % len(promoter_ids)]
            cursor.execute(
                """INSERT INTO promotion_attribution (user_id, promoter_id, touch_id, status, effective_at)
                VALUES (%s,%s,%s,1,%s)
                ON DUPLICATE KEY UPDATE promoter_id=VALUES(promoter_id), touch_id=VALUES(touch_id),
                ended_at=NULL, end_reason=NULL""",
                (member_id, promoter_id, own_touch_id if promoter_id == actor_id else None, current_time),
            )

        _ensure_match_apply(
            cursor,
            from_user_id=member_ids["member-02"],
            to_user_id=member_ids["member-01"],
            message=f"{MARKER}: 待处理牵线申请",
            status=0,
            now=current_time,
        )
        _ensure_match_apply(
            cursor,
            from_user_id=member_ids["member-06"],
            to_user_id=member_ids["member-05"],
            message=f"{MARKER}: 已成功牵线申请",
            status=1,
            now=current_time,
        )
        for workspace_actor_id in workspace_actor_ids:
            for key, user_key, target_key, status, meeting_status, cancel_reason, scheduled_offset_days, location in MEETING_REQUESTS:
                _ensure_meeting_request(
                    cursor,
                    matchmaker_id=workspace_actor_id,
                    user_id=member_ids[user_key],
                    target_user_id=member_ids[target_key],
                    key=key,
                    status=status,
                    meeting_status=meeting_status,
                    cancel_reason=cancel_reason,
                    scheduled_offset_days=scheduled_offset_days,
                    location=location,
                    now=current_time,
                )

        connection.commit()
        return {
            "operator_user_id": actor_id,
            "members": len(member_ids),
            "workspace_leads": len(LEADS),
            "workspace_introductions": len(INTRODUCTIONS),
            "workspace_meeting_requests": len(MEETING_REQUESTS),
            "service_matchmaker_workbenches": len(workspace_actor_ids),
            "team_promoters": len(promoter_ids),
            "promotion_attributions": len(member_ids),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        if owned_connection:
            connection.close()


if __name__ == "__main__":
    result = seed_matchmaker_workspace_demo()
    print("红娘工作台本地演示数据已写入：" + json.dumps(result, ensure_ascii=False))
