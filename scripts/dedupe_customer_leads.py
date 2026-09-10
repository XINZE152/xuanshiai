"""客源线索存量重复清理脚本（development/testing 专用）。

按产品唯一性口径处理 customer_lead 存量数据：
- 同手机号或同微信号（去除首尾空白后非空）的"有效"线索（状态非 LOST/CLOSED）
  只保留 id 最小的一条作为有效线索，其余线索的跟进记录与弃海记录改挂到保留线索，
  然后删除重复线索行；
- 携带相同演示标记（local-demo-workspace / dev-lead-workbench）的行视为同一条
  演示线索的历史重跑残留，无论状态一律保留 id 最小的一条；
- 弃海（LOST）与已关闭（CLOSED）线索不占用联系方式，不参与联系方式合并；
- 顺带归一化全部 phone/wechat（去首尾空白，空串置 NULL）。

默认 dry-run 只打印计划；加 --apply 才会写库。清理后可重跑
database_setup_marriage 以补齐 active_phone/active_wechat 唯一索引。
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CHILD_TABLES = (
    "customer_lead_follow_up",
    "customer_lead_abandonment",
    "customer_lead_review",
    "customer_lead_call_note",
)


def _clean_contact(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def dedupe_customer_leads(connection: Any, *, apply: bool) -> dict[str, object]:
    cursor = connection.cursor()
    cursor.execute(
        """SELECT id, name, phone, wechat, status, remark, created_by, created_at
        FROM customer_lead ORDER BY id"""
    )
    rows = cursor.fetchall()

    # 归一化联系方式：trim，空串置 NULL。
    normalized: dict[int, tuple[str | None, str | None]] = {}
    for row in rows:
        phone, wechat = _clean_contact(row["phone"]), _clean_contact(row["wechat"])
        if (phone, wechat) != (row["phone"], row["wechat"]):
            normalized[int(row["id"])] = (phone, wechat)

    # 有效线索才占用联系方式；LOST/CLOSED 可与任何行共存。
    active_rows = [row for row in rows if row["status"] not in ("LOST", "CLOSED")]
    keep_ids: set[int] = set()
    drop_ids: set[int] = set()
    drop_plan: list[dict[str, object]] = []

    # 演示标记行：相同标记即同一条演示线索的重跑残留，按标记保留 id 最小的一条。
    demo_markers = ("local-demo-workspace:", "dev-lead-workbench:")
    demo_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        remark = _clean_contact(row.get("remark"))
        if remark and any(marker in remark for marker in demo_markers):
            demo_groups.setdefault(remark, []).append(row)
    for remark, members in demo_groups.items():
        if len(members) < 2:
            continue
        keeper = members[0]
        keep_ids.add(int(keeper["id"]))
        for duplicate in members[1:]:
            duplicate_id = int(duplicate["id"])
            if duplicate_id in drop_ids:
                continue
            drop_ids.add(duplicate_id)
            drop_plan.append(
                {
                    "field": "demo_remark",
                    "contact": remark,
                    "keep_id": int(keeper["id"]),
                    "keep_name": keeper["name"],
                    "drop_id": duplicate_id,
                    "drop_name": duplicate["name"],
                    "drop_status": duplicate["status"],
                }
            )

    for key in ("phone", "wechat"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in active_rows:
            contact = _clean_contact(row[key])
            if contact:
                groups.setdefault(contact, []).append(row)
        for contact, members in groups.items():
            if len(members) < 2:
                continue
            keeper = members[0]
            for duplicate in members[1:]:
                if int(duplicate["id"]) in keep_ids or int(duplicate["id"]) in drop_ids:
                    continue
                keep_ids.add(int(keeper["id"]))
                drop_ids.add(int(duplicate["id"]))
                drop_plan.append(
                    {
                        "field": key,
                        "contact": contact,
                        "keep_id": int(keeper["id"]),
                        "keep_name": keeper["name"],
                        "drop_id": int(duplicate["id"]),
                        "drop_name": duplicate["name"],
                        "drop_status": duplicate["status"],
                    }
                )

    report: dict[str, object] = {
        "total_leads": len(rows),
        "normalized_contacts": len(normalized),
        "duplicates_removed": len(drop_ids),
        "plan": drop_plan,
    }

    if not apply:
        cursor.close()
        return report

    now = datetime.now(UTC).replace(tzinfo=None)
    for lead_id, (phone, wechat) in normalized.items():
        cursor.execute(
            "UPDATE customer_lead SET phone=%s, wechat=%s, updated_at=%s WHERE id=%s",
            (phone, wechat, now, lead_id),
        )
    for lead_id in drop_ids:
        # 重复线索的子记录（跟进/弃海/审核/小记/标签）一并删除，保留线索自身的记录不受影响。
        for table in CHILD_TABLES:
            cursor.execute(f"DELETE FROM {table} WHERE lead_id=%s", (lead_id,))
        cursor.execute("DELETE FROM customer_lead_tag_relation WHERE lead_id=%s", (lead_id,))
        cursor.execute("DELETE FROM customer_lead WHERE id=%s", (lead_id,))
    connection.commit()
    cursor.close()
    return report


def main(*, apply: bool) -> None:
    env = (os.getenv("ENV") or os.getenv("ENVIRONMENT") or "development").lower()
    if env not in {"development", "testing"}:
        raise RuntimeError("客源线索清理只允许在 development/testing 环境执行")

    import pymysql
    from database_setup_marriage import get_db_config

    connection = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    try:
        report = dedupe_customer_leads(connection, apply=apply)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    mode = "已清理" if apply else "dry-run 预览（加 --apply 执行）"
    print(f"客源线索去重{mode}：" + json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
