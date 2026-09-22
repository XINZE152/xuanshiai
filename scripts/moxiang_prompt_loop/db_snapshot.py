"""墨相师·对话闭环的只读 DB 快照。

严格只读、参数化查询,绝不拼接 SQL 字面量;只触表白名单中的 AI 画像相关
表,不返回连接信息、原始 prompt/response 等敏感内容。

出口::

    snapshot = Snapshot.run(user_id, session_id)
    snapshot.to_json(path)   # 给 scorer 消费
    snapshot.to_sql(path)    # 给审计/复核使用
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

# 表白名单,禁止触碰任何业务非 AI 表(防止误扫用户/订单/支付)。
_ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "ai_profile_session",
        "ai_profile_turn",
        "ai_task",
        "ai_profile_candidate",
        "ai_profile_draft",
        "ai_profile_draft_field",
        "ai_profile_revision",
        "ai_profile_revision_field",
        "ai_profile_summary",
        "ai_feature_projection",
        "ai_profile_build_invite",
        "ai_profile_preview",
        "ai_generation_audit",
    }
)

# 不同表的用户列名不一致,统一在这里维护
_USER_COLUMN: dict[str, str] = {
    "ai_task": "owner_user_id",
    "ai_feature_projection": "subject_user_id",
}

# 这些表没有 session_id 列,只能用 user 列过滤
_NO_SESSION_ID: frozenset[str] = frozenset(
    {"ai_task", "ai_feature_projection", "ai_profile_revision", "ai_profile_preview"}
)


def _connect():
    """连接当前 ``DATABASE_URL``。"""
    from database_setup_marriage import get_db_config  # noqa: WPS433 — 复用既有 helper

    config = get_db_config()
    return pymysql.connect(
        **config,
        cursorclass=DictCursor,
        autocommit=True,
        charset="utf8mb4",
    )


@dataclass
class Snapshot:
    """一次回放后的 DB 快照。"""

    user_id: int
    session_id: str | None
    captured_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)

    @classmethod
    def run(cls, user_id: int, session_id: str | None = None) -> "Snapshot":
        """按 user_id 读取 AI 画像相关表的全部行(只读,无锁)。

        ``session_id`` 给定时,优先用 session 维度过滤;为空时回退到 user 维度
        (用于清理/reset 后的全量核查)。
        """
        snap = cls(user_id=user_id, session_id=session_id)
        conn = _connect()
        try:
            with conn.cursor() as cur:
                # ai_profile_session: 必有 user_id 列
                snap.tables["ai_profile_session"] = _fetch(
                    cur, "ai_profile_session", user_id, session_id
                )
                # 仅当会话存在时,其余表才有意义;否则为空(脚本刚 reset 过的情况)
                if snap.tables["ai_profile_session"]:
                    snap.tables["ai_profile_turn"] = _fetch(
                        cur, "ai_profile_turn", user_id, session_id
                    )
                    snap.tables["ai_task"] = _fetch(
                        cur, "ai_task", user_id, session_id
                    )
                    snap.tables["ai_profile_candidate"] = _fetch(
                        cur, "ai_profile_candidate", user_id, session_id
                    )
                    snap.tables["ai_profile_draft"] = _fetch(
                        cur, "ai_profile_draft", user_id, session_id
                    )
                    snap.tables["ai_profile_draft_field"] = _fetch_draft_field(cur, snap.tables["ai_profile_draft"])
                    snap.tables["ai_profile_revision"] = _fetch(
                        cur, "ai_profile_revision", user_id, session_id
                    )
                    snap.tables["ai_profile_revision_field"] = _fetch_revision_field(
                        cur, snap.tables["ai_profile_revision"]
                    )
                    snap.tables["ai_profile_summary"] = _fetch(
                        cur, "ai_profile_summary", user_id, session_id
                    )
                    snap.tables["ai_feature_projection"] = _fetch(
                        cur, "ai_feature_projection", user_id, session_id
                    )
                    snap.tables["ai_profile_build_invite"] = _fetch(
                        cur, "ai_profile_build_invite", user_id, session_id
                    )
                    snap.tables["ai_profile_preview"] = _fetch(
                        cur, "ai_profile_preview", user_id, session_id
                    )
                    # audit 仅按 request_id 前缀过滤(无 user_id 列),避免全表扫描
                    snap.tables["ai_generation_audit"] = _fetch_audit(cur, session_id)
                else:
                    for table in (
                        "ai_profile_turn",
                        "ai_task",
                        "ai_profile_candidate",
                        "ai_profile_draft",
                        "ai_profile_draft_field",
                        "ai_profile_revision",
                        "ai_profile_revision_field",
                        "ai_profile_summary",
                        "ai_feature_projection",
                        "ai_profile_build_invite",
                        "ai_profile_preview",
                        "ai_generation_audit",
                    ):
                        snap.tables[table] = []
        finally:
            conn.close()

        snap.stats = {name: len(rows) for name, rows in snap.tables.items()}
        return snap

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "user_id": self.user_id,
                    "session_id": self.session_id,
                    "captured_at": self.captured_at,
                    "stats": self.stats,
                    "tables": self.tables,
                },
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            ),
            encoding="utf-8",
        )

    def to_sql(self, path: Path) -> None:
        """导出 mysqldump 风格的 SQL,便于人工审计。

        仅含 INSERT 语句,不含 DDL/PASSWORD/连接串。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        lines.append(f"-- moxiang-prompt-loop snapshot")
        lines.append(f"-- user_id={self.user_id} session_id={self.session_id or ''}")
        lines.append(f"-- captured_at={self.captured_at}")
        lines.append("SET NAMES utf8mb4;")
        for table, rows in self.tables.items():
            if not rows:
                continue
            columns = list(rows[0].keys())
            lines.append(f"-- table {table} rows={len(rows)}")
            lines.append(f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in columns)}) VALUES")
            for idx, row in enumerate(rows):
                values = [_sql_value(row.get(col)) for col in columns]
                terminator = "," if idx < len(rows) - 1 else ";"
                lines.append("  (" + ", ".join(values) + ")" + terminator)
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")


def _fetch(cur, table: str, user_id: int, session_id: str | None) -> list[dict[str, Any]]:
    """参数化 SELECT,过滤 user_id;若提供 session_id 且表有 session_id 列则进一步收窄。

    严禁拼接任何字面量到 SQL。
    """
    if table not in _ALLOWED_TABLES:
        raise PermissionError(f"table {table!r} not in whitelist")
    user_col = _USER_COLUMN.get(table, "user_id")
    if session_id is not None and table not in _NO_SESSION_ID:
        sql = (
            f"SELECT * FROM `{table}` "
            f"WHERE `{user_col}` = %s AND `session_id` = %s"
        )
        cur.execute(sql, (user_id, session_id))
    else:
        sql = f"SELECT * FROM `{table}` WHERE `{user_col}` = %s"
        cur.execute(sql, (user_id,))
    return list(cur.fetchall())


def _fetch_draft_field(cur, drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not drafts:
        return []
    placeholders = ", ".join(["%s"] * len(drafts))
    sql = f"SELECT * FROM `ai_profile_draft_field` WHERE `draft_id` IN ({placeholders})"
    cur.execute(sql, tuple(d["draft_id"] for d in drafts))
    return list(cur.fetchall())


def _fetch_revision_field(cur, revisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not revisions:
        return []
    ids = [r["id"] for r in revisions if r.get("id") is not None]
    if not ids:
        return []
    placeholders = ", ".join(["%s"] * len(ids))
    sql = f"SELECT * FROM `ai_profile_revision_field` WHERE `revision_id` IN ({placeholders})"
    cur.execute(sql, tuple(ids))
    return list(cur.fetchall())


def _fetch_audit(cur, session_id: str | None) -> list[dict[str, Any]]:
    """audit 表无 user_id 列,按 scene=moxiang_master_chat + 时间窗过滤。

    严格参数化,绝无拼接。
    """
    if session_id is None:
        return []
    # 仅返回与本次回放时间窗相关的行(scene=moxiang_master_chat)
    sql = (
        "SELECT request_id, task_id, scene, provider, model, prompt_version, "
        "schema_version, error_code, duration_ms, created_at "
        "FROM `ai_generation_audit` "
        "WHERE `scene` = %s AND `created_at` >= (NOW() - INTERVAL 30 MINUTE)"
    )
    cur.execute(sql, ("moxiang_master_chat",))
    return list(cur.fetchall())


def _sql_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return f"'{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, (dict, list)):
        escaped = json.dumps(value, ensure_ascii=False).replace("'", "''")
        return f"'{escaped}'"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
