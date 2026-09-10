"""Seed development-only customer-lead data for the redesigned lead workbench.

Creates one organization, two service matchmakers inside it (蛋希老师 / 依依),
and a full-status set of customer leads with follow-up records so every UI
state (状态 sheet、分派跟进、跟进记录、10 天未跟进提醒) can be verified.

Every lead carries the ``dev-lead-workbench`` remark marker and every demo
user a ``dev-lead-workbench-`` openid, so re-running refreshes rather than
duplicates, and production records are never touched.
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

MARKER = "dev-lead-workbench"
ORG_CODE = "dev-lead-workbench-org"
ORG_NAME = "宣誓爱红娘馆（开发）"

# (key, display_name, phone, openid_suffix, level)
MATCHMAKERS = (
    ("danxi", "蛋希老师", "13800000031", "danxi", "SUPER"),
    ("yiyi", "依依", "13800000032", "yiyi", "NORMAL"),
)

# (key, name, phone, wechat, source, intention_level, status, owner_key,
#  gender, age, education, expectation, remark, days_since_created,
#  follow-ups: list[(method, content, days_ago)])
LEADS = (
    (
        "lead-01", "Lemon", "13911110001", "wx_lemon_dev", "朋友推荐", 3, "NEW", "danxi",
        2, 26, "本科", "希望找同龄、有稳定工作的对象", "首次咨询，尚未开始接触。",
        1,
        [],
    ),
    (
        "lead-02", "王小雅", "13911110002", "wx_wangxia_dev", "活动咨询", 2, "CONTACTED", "danxi",
        2, 28, "硕士", "希望对方有上进心、尊重女性", "已电话初聊，对平台接受度较高。",
        6,
        [("PHONE", "电话沟通 20 分钟，介绍了服务流程。", 3)],
    ),
    (
        "lead-03", "陈志远", "13911110003", "wx_chenzhiyuan_dev", "社群报名", 3, "INTENDED", "yiyi",
        1, 30, "本科", "希望两年内结婚", "已见过面，明确表达付费意向。",
        12,
        [
            ("WECHAT", "微信上发了资料包，回复积极。", 9),
            ("VISIT", "到店面谈，确定意向并讨论择偶标准。", 4),
        ],
    ),
    (
        "lead-04", "刘小雨", "13911110004", "wx_liuxiaoyu_dev", "朋友推荐", 1, "CONVERTED", "yiyi",
        2, 25, "本科", "看缘分，不强求", "已一键入库，等待公开资料审核。",
        20,
        [("PHONE", "电话确认了入库意愿。", 15)],
    ),
    (
        "lead-05", "赵倩", "13911110005", "wx_zhaoqian_dev", "线上表单", 2, "CLOSED", "danxi",
        2, 33, "硕士", "希望对方成熟稳重", "已在其他平台结婚，正常关闭。",
        60,
        [("OTHER", "回访确认已在别处领证，礼貌结束服务。", 30)],
    ),
    (
        "lead-06", "孙浩", "13911110006", "wx_sunhao_dev", "活动咨询", 1, "LOST", "danxi",
        1, 27, "大专", "希望对方顾家", "多次联系不上，放入弃海。",
        45,
        [],
    ),
    (
        "lead-07", "周敏", "13911110007", "wx_zhoumin_dev", "社群报名", 2, "CONTACTED", "danxi",
        2, 29, "本科", "希望对方有房有车", "超过 10 天未跟进，用于校验提醒标记。",
        18,
        [("WECHAT", "微信简单聊了基本情况。", 14)],
    ),
)


def _row_id(row: Any) -> int:
    return int(row["id"]) if row else 0


def _ensure_organization(cursor: Any, now: datetime) -> int:
    cursor.execute("SELECT id FROM organization WHERE code=%s LIMIT 1", (ORG_CODE,))
    row = cursor.fetchone()
    if row:
        cursor.execute(
            "UPDATE organization SET name=%s, org_type='store', status=1, updated_at=%s WHERE id=%s",
            (ORG_NAME, now, _row_id(row)),
        )
        return _row_id(row)
    cursor.execute(
        """INSERT INTO organization (org_type, code, name, status, created_at)
        VALUES ('store', %s, %s, 1, %s)""",
        (ORG_CODE, ORG_NAME, now),
    )
    return int(cursor.lastrowid)


def _ensure_matchmaker(cursor: Any, item: tuple[str, str, str, str, str], org_id: int, now: datetime) -> int:
    key, display_name, phone, openid_suffix, level = item
    openid = f"{MARKER}-{openid_suffix}"
    cursor.execute("SELECT id FROM users WHERE openid=%s LIMIT 1", (openid,))
    row = cursor.fetchone()
    if row:
        user_id = _row_id(row)
    else:
        cursor.execute(
            """INSERT INTO users
            (openid, phone, phone_verified_at, nickname, status, data_complete_rate, register_ip)
            VALUES (%s, %s, %s, %s, 1, 100, '127.0.0.1')""",
            (openid, phone, now, display_name),
        )
        user_id = int(cursor.lastrowid)
    cursor.execute("SELECT id FROM user_role WHERE user_id=%s AND role_code='service_matchmaker' LIMIT 1", (user_id,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO user_role (user_id, role_code, status, granted_at) VALUES (%s, 'service_matchmaker', 1, %s)",
            (user_id, now),
        )
    cursor.execute(
        """INSERT INTO matchmaker_workspace_profile (user_id, display_name, level, organization_id, status)
        VALUES (%s, %s, %s, %s, 1)
        ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), level=VALUES(level),
        organization_id=VALUES(organization_id), status=1, updated_at=VALUES(updated_at)""",
        (user_id, display_name, level, org_id),
    )
    return user_id


def _ensure_lead(
    cursor: Any,
    item: tuple[str, str, str, str, str, int, str, str, int, int, str, str, str, int],
    owners: dict[str, int],
    org_id: int,
    now: datetime,
) -> int:
    (
        key, name, phone, wechat, source, intention, status, owner_key,
        _gender, _age, _education, _expectation, remark, days_since,
        follow_ups,
    ) = item
    marker = f"{MARKER}:{key}"
    # 查询条件必须与存储值完全一致（remark 尾部带标记），否则重跑会重复插入线索。
    tagged_remark = remark + f"【{marker}】"
    owner_id = owners[owner_key]
    created_at = now - timedelta(days=days_since)
    cursor.execute("SELECT id FROM customer_lead WHERE remark=%s LIMIT 1", (tagged_remark,))
    row = cursor.fetchone()
    next_follow_at = None
    if follow_ups:
        last = follow_ups[-1]
        next_follow_at = now - timedelta(days=last[2]) + timedelta(days=10)
    if row:
        lead_id = _row_id(row)
        cursor.execute(
            """UPDATE customer_lead SET name=%s, phone=%s, wechat=%s, source=%s,
            intention_level=%s, status=%s, matchmaker_id=%s, organization_id=%s,
            next_follow_at=%s, remark=%s, created_at=%s, updated_at=%s WHERE id=%s""",
            (name, phone, wechat, source, intention, status, owner_id, org_id,
             next_follow_at, tagged_remark, created_at, now, lead_id),
        )
    else:
        cursor.execute(
            """INSERT INTO customer_lead
            (name, phone, wechat, source, intention_level, status, matchmaker_id,
             organization_id, next_follow_at, remark, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (name, phone, wechat, source, intention, status, owner_id,
             org_id, next_follow_at, tagged_remark, owner_id, created_at),
        )
        lead_id = int(cursor.lastrowid)
    cursor.execute("DELETE FROM customer_lead_follow_up WHERE lead_id=%s", (lead_id,))
    for method, content, days_ago in follow_ups:
        cursor.execute(
            """INSERT INTO customer_lead_follow_up
            (lead_id, method, content, intention_level, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (lead_id, method, content, intention, owner_id, now - timedelta(days=days_ago)),
        )
    # 弃海线索补一条未恢复的弃海记录，保证「弃海客源」页签可测恢复。
    cursor.execute("SELECT id FROM customer_lead_abandonment WHERE lead_id=%s LIMIT 1", (lead_id,))
    has_abandonment = cursor.fetchone()
    if status == "LOST" and not has_abandonment:
        cursor.execute(
            """INSERT INTO customer_lead_abandonment
            (lead_id, reason, abandoned_by, abandoned_at)
            VALUES (%s, '多次联系不上，放入弃海（开发数据）', %s, %s)""",
            (lead_id, owner_id, now - timedelta(days=days_since)),
        )
    elif status != "LOST" and has_abandonment:
        cursor.execute(
            """UPDATE customer_lead_abandonment SET restored_by=abandoned_by,
            restored_at=%s, restore_reason='开发数据重置：线索已恢复' WHERE lead_id=%s""",
            (now, lead_id),
        )
    print(f"  线索 {key}: {name} ({status}) -> lead_id={lead_id}")
    return lead_id


def seed_lead_workbench_demo(connection: Any = None, *, now: datetime | None = None) -> dict[str, int]:
    env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "development").lower()
    if env not in {"development", "testing"}:
        raise RuntimeError("客源线索开发数据只允许在 development/testing 环境写入")

    owned_connection = connection is None
    if owned_connection:
        import pymysql
        from database_setup_marriage import get_db_config

        connection = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor, autocommit=False)

    current_time = (now or datetime.now(UTC)).replace(tzinfo=None)
    cursor = connection.cursor()
    try:
        org_id = _ensure_organization(cursor, current_time)
        owners = {
            key: _ensure_matchmaker(cursor, item, org_id, current_time)
            for key, item in ((m[0], m) for m in MATCHMAKERS)
        }
        lead_ids = [
            _ensure_lead(cursor, item, owners, org_id, current_time)
            for item in LEADS
        ]
        connection.commit()
        return {
            "organization_id": org_id,
            "matchmakers": len(owners),
            "leads": len(lead_ids),
            "matchmaker_user_ids": json.dumps(owners),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        if owned_connection:
            connection.close()


if __name__ == "__main__":
    result = seed_lead_workbench_demo()
    print("客源线索开发数据已写入：" + json.dumps(result, ensure_ascii=False, default=str))
