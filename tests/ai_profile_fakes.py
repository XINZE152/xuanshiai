"""画像/墨相师测试共用的内存 fake store（自 test_ai_profile_sessions.py 迁出）。

原对话式画像问答测试已于 2026-09-11 删除；``FakeProfileSession``/``ProfileStore``
等 fake 仍被 test_ai_profile_publish（发布链）、test_master_session（墨相师
master 会话）与 test_ai_profile_entries（草稿条目）使用，故迁入本模块。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.services.ai.tasks import AiTaskRecord
from app.services.ai.profile import (
    ProfileSessionNotFound,
    ProfileSessionStale,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _to_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return value


# ----------------------------------------------------------------------
# 内存结果辅助
# ----------------------------------------------------------------------


class _MappingResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _MappingResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar(self) -> Any:
        if not self._rows:
            return None
        return next(iter(self._rows[0].values()))


class _WriteResult:
    def __init__(self, *, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class TaskStore:
    """Minimal in-memory ai_task fact store (mirrors Task 6 contract)."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self._next_id = 1

    def find_by_idempotency(
        self, owner_user_id: int, task_type: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        for row in self.tasks.values():
            if (
                row["owner_user_id"] == owner_user_id
                and row["task_type"] == task_type
                and row["idempotency_key"] == idempotency_key
            ):
                return row
        return None

    def insert(self, params: dict[str, Any]) -> bool:
        existing = self.find_by_idempotency(
            int(params["owner_user_id"]),
            str(params["task_type"]),
            str(params["idempotency_key"]),
        )
        if existing is not None:
            raise IntegrityError(
                "INSERT INTO ai_task", params, Exception("Duplicate entry")
            )
        now = _now()
        task_id = str(params["task_id"])
        self.tasks[task_id] = {
            "id": self._next_id,
            "task_id": task_id,
            "owner_user_id": int(params["owner_user_id"]),
            "task_type": str(params["task_type"]),
            "scene": str(params.get("scene") or params["task_type"]),
            "idempotency_key": str(params["idempotency_key"]),
            "request_digest": params.get("request_digest"),
            "status": "queued",
            "stage": None,
            "attempt_count": 0,
            "max_attempts": int(params.get("max_attempts") or settings.ai_max_attempts),
            "next_run_at": None,
            "lease_owner": None,
            "lease_until": None,
            "consent_snapshot_json": params.get("consent_snapshot_json"),
            "source_revision_json": params.get("source_revision_json"),
            "payload_summary": None,
            "error_code": None,
            "error_message": None,
            "result_ref": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
        }
        self._next_id += 1
        return True

    def apply_update(self, sql: str, params: dict[str, Any]) -> bool:
        row = self.tasks.get(params.get("task_id"))
        if row is None:
            return False
        if "SET status = 'leased'" in sql:
            row["status"] = "leased"
            row["lease_owner"] = params.get("worker_id")
            row["lease_until"] = params.get("lease_until")
        elif "SET status = 'running'" in sql:
            if row["status"] != "leased" or row["lease_owner"] != params.get("worker_id"):
                return False
            row["status"] = "running"
            if row["started_at"] is None:
                row["started_at"] = params.get("now")
        elif "SET status = 'succeeded'" in sql:
            row["status"] = "succeeded"
            row["result_ref"] = params.get("result_ref")
            row["finished_at"] = params.get("now")
        elif "SET status = 'retry_wait'" in sql:
            row["status"] = "retry_wait"
            row["attempt_count"] = int(params.get("attempt_count") or 0)
            row["next_run_at"] = params.get("next_run_at")
            row["error_code"] = params.get("error_code")
            row["error_message"] = params.get("error_message")
            row["lease_owner"] = None
            row["lease_until"] = None
        elif "SET status = 'failed'" in sql:
            row["status"] = "failed"
            row["error_code"] = params.get("error_code")
            row["error_message"] = params.get("error_message")
            row["finished_at"] = params.get("now")
            row["lease_owner"] = None
            row["lease_until"] = None
        elif "SET status = 'superseded'" in sql:
            # ``_supersede``：完成门禁（consent/版本复查）发现任务已被新状态
            # 取代时，把 running 任务移到 superseded 终态，清空租约与 payload。
            row["status"] = "superseded"
            row["finished_at"] = params.get("now")
            row["lease_owner"] = None
            row["lease_until"] = None
            row["consent_snapshot_json"] = None
            row["source_revision_json"] = None
            row["payload_summary"] = None
            row["result_ref"] = None
        elif sql.startswith("UPDATE ai_task SET lease_until"):
            if row["status"] not in ("running", "leased") or row["lease_owner"] != params.get(
                "worker_id"
            ):
                return False
            row["lease_until"] = params.get("lease_until")
        elif "SET payload_summary" in sql:
            row["payload_summary"] = params.get("payload_summary")
            if "source_revision_json" in params:
                row["source_revision_json"] = params.get("source_revision_json")
            if "consent_snapshot_json" in params:
                row["consent_snapshot_json"] = params.get("consent_snapshot_json")
        elif "SET stage = :stage" in sql:
            row["stage"] = params.get("stage")
        else:
            raise AssertionError(f"unhandled task update: {sql}")
        row["updated_at"] = _now()
        return True

    async def seed(self, **kwargs: Any) -> AiTaskRecord:
        task_id = kwargs.pop("task_id", None) or uuid.uuid4().hex
        now = _now()
        row: dict[str, Any] = {
            "id": self._next_id,
            "task_id": task_id,
            "owner_user_id": int(kwargs.pop("owner_user_id", 10)),
            "task_type": str(kwargs.pop("task_type", "moxiang_candidate_extract")),
            "scene": str(kwargs.pop("scene", "profile_text_extract")),
            "idempotency_key": str(kwargs.pop("idempotency_key", "")),
            "request_digest": kwargs.pop("request_digest", None),
            "status": str(kwargs.pop("status", "queued")),
            "stage": kwargs.pop("stage", None),
            "attempt_count": int(kwargs.pop("attempt_count", 0)),
            "max_attempts": int(kwargs.pop("max_attempts", settings.ai_max_attempts)),
            "next_run_at": _to_dt(kwargs.pop("next_run_at", None)),
            "lease_owner": kwargs.pop("lease_owner", None),
            "lease_until": _to_dt(kwargs.pop("lease_until", None)),
            "consent_snapshot_json": kwargs.pop("consent_snapshot_json", None),
            "source_revision_json": kwargs.pop("source_revision_json", None),
            "payload_summary": kwargs.pop("payload_summary", None),
            "error_code": kwargs.pop("error_code", None),
            "error_message": kwargs.pop("error_message", None),
            "result_ref": kwargs.pop("result_ref", None),
            "created_at": _to_dt(kwargs.pop("created_at", now)),
            "updated_at": _to_dt(kwargs.pop("updated_at", now)),
            "started_at": _to_dt(kwargs.pop("started_at", None)),
            "finished_at": _to_dt(kwargs.pop("finished_at", None)),
        }
        self.tasks[task_id] = row
        self._next_id += 1
        return AiTaskRecord.from_row(row)

    async def get(self, task_id: str) -> dict[str, Any] | None:
        return self.tasks.get(task_id)


class FakeProfileSession:
    """Routes service SQL by substring onto one ProfileStore.

    ``commit()`` 记录当前内存态快照作为已提交基线；``rollback()`` 还原到最近
    一次 commit 的快照，撤销快照之后的所有「插入/更新」副作用——与真实 DB 的
    「未提交写入在回滚时撤销」语义一致（此前 rollback 只计数不还原，掩盖了
    stale 标记不落库的缺陷）。尚无任何 commit 时（例如并发竞态测试），rollback
    为无操作：共享 session 无法区分各「请求」的写入归属，且败方失败语句本就没
    有产生副作用，还原基线反而会误删赢家的数据。
    """

    def __init__(self, store: ProfileStore) -> None:
        self._store = store
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self._committed_snapshot: dict[str, Any] | None = None

    def _snapshot_store(self) -> dict[str, Any]:
        return {
            "sessions": {sid: dict(row) for sid, row in self._store.sessions.items()},
            "turns": [dict(row) for row in self._store.turns],
            "drafts": [dict(row) for row in self._store.drafts],
            "draft_fields": [dict(row) for row in self._store.draft_fields],
            "consents": [dict(row) for row in self._store.consents],
            "revision_rows": {
                uid: dict(row) for uid, row in self._store.revision_rows.items()
            },
            "tasks": {
                tid: dict(row) for tid, row in self._store.task_store.tasks.items()
            },
            "task_next_id": self._store.task_store._next_id,
        }

    def _restore_store(self, snapshot: dict[str, Any]) -> None:
        self._store.sessions = {sid: dict(row) for sid, row in snapshot["sessions"].items()}
        self._store.turns = [dict(row) for row in snapshot["turns"]]
        self._store.drafts = [dict(row) for row in snapshot["drafts"]]
        self._store.draft_fields = [dict(row) for row in snapshot["draft_fields"]]
        self._store.consents = [dict(row) for row in snapshot["consents"]]
        self._store.revision_rows = {
            uid: dict(row) for uid, row in snapshot["revision_rows"].items()
        }
        self._store.task_store.tasks = {
            tid: dict(row) for tid, row in snapshot["tasks"].items()
        }
        self._store.task_store._next_id = snapshot["task_next_id"]

    async def flush(self) -> None:
        self.flushes += 1

    async def execute(
        self, statement: object, params: dict[str, Any] | None = None
    ) -> _MappingResult | _WriteResult:
        sql = str(statement)
        values = dict(params or {})
        self.calls.append((sql, values))
        # ---- ai_task (Task 6 contract, same semantics) ----
        if "INSERT INTO ai_task" in sql:
            return _WriteResult(rowcount=1 if self._store.task_store.insert(values) else 0)
        if "UPDATE ai_task" in sql and "payload_summary" in sql:
            self._store.task_store.apply_update(sql, values)
            return _WriteResult(rowcount=1)
        if "FROM ai_task" in sql and "status IN ('queued', 'retry_wait')" in sql:
            eligible = [
                row
                for row in self._store.task_store.tasks.values()
                if row["status"] in ("queued", "retry_wait")
                and (row["next_run_at"] is None or row["next_run_at"] <= values["now"])
                and (
                    row["lease_owner"] is None
                    or row["lease_until"] is None
                    or row["lease_until"] < values["now"]
                )
            ]
            eligible.sort(key=lambda row: row["created_at"])
            return _MappingResult(eligible[: int(values["limit"])])
        if "FROM ai_task" in sql and "status IN ('leased', 'running')" in sql:
            eligible = [
                row
                for row in self._store.task_store.tasks.values()
                if row["status"] in ("leased", "running")
                and row["lease_until"] is not None
                and row["lease_until"] < values["now"]
            ]
            eligible.sort(key=lambda row: row["lease_until"])
            return _MappingResult(eligible[: int(values["limit"])])
        if "FROM ai_task" in sql and "WHERE task_id = :task_id" in sql:
            row = self._store.task_store.tasks.get(values["task_id"])
            return _MappingResult([row] if row else [])
        if "FROM ai_task" in sql and "owner_user_id = :owner_user_id" in sql:
            row = self._store.task_store.find_by_idempotency(
                int(values["owner_user_id"]),
                str(values["task_type"]),
                str(values["idempotency_key"]),
            )
            return _MappingResult([row] if row else [])
        if sql.startswith("UPDATE ai_task"):
            applied = self._store.task_store.apply_update(sql, values)
            return _WriteResult(rowcount=1 if applied else 0)
        # ---- profile tables ----
        if "INSERT INTO ai_profile_session" in sql:
            # build/master 创建把 session_kind 写成 SQL 字面量（绑定参数中无
            # session_kind），假库按真实 DB 语义从语句补齐；update 走 :session_kind
            # 绑定参数，无需补齐。
            if "'master'" in sql:
                values = {**values, "session_kind": "master"}
            elif "'build'" in sql:
                values = {**values, "session_kind": "build"}
            self._store.insert_session(values)
            return _WriteResult(rowcount=1)
        if "INSERT INTO ai_profile_turn" in sql:
            if "'assistant'" in sql:
                # ``_insert_assistant_turn`` 以 SQL 字面量写 role='assistant'
                # （绑定参数中无 role），假库按真实 DB 语义记录 assistant 行，
                # 而非落回 role 缺省的 'user'。
                values = {**values, "role": "assistant"}
            self._store.insert_turn(values)
            return _WriteResult(rowcount=1)
        if "INSERT INTO ai_profile_draft_field" in sql:
            self._store.insert_draft_field(values)
            return _WriteResult(rowcount=1)
        if "INSERT INTO ai_profile_draft" in sql:
            self._store.insert_draft(values)
            return _WriteResult(rowcount=1)
        if "UPDATE ai_profile_session" in sql:
            self._store.apply_session_update(sql, values)
            return _WriteResult(rowcount=1)
        # ``_insert_turn`` 修复后改用 ``COALESCE(MAX(turn_no), 0)+1`` 取序号，
        # 不再走 ``COUNT(*)``。此处匹配新 SQL 并返回当前最大 turn_no + 1。
        if (
            "FROM ai_profile_turn" in sql
            and "COALESCE(MAX(turn_no)" in sql
        ):
            max_no = max(
                (int(r["turn_no"]) for r in self._store.turns if r["session_id"] == values["session_id"]),
                default=0,
            )
            return _MappingResult([{"next_no": max_no + 1}])
        if "FROM ai_profile_turn" in sql and "COUNT(*)" in sql:
            return _MappingResult(
                [{"COUNT(*)": self._store.count_turns(values["session_id"])}]
            )
        if "FROM ai_profile_turn" in sql:
            row = self._store.find_turn(values["session_id"], values["client_turn_id"])
            return _MappingResult([row] if row else [])
        # ``_revoke_consent`` 与 ``_load_latest_consent`` 的 ``FOR UPDATE`` 锁行 /
        # 按 user_id+scope 取最新 grant：这些 SQL 不带 ``version`` 参数，需与
        # ``_load_consent_grant``（按 user_id+scope+version 精确匹配）区分。
        if "FROM ai_consent_grant" in sql:
            user_id = int(values["user_id"])
            scope = str(values["scope"])
            if "version" in values:
                row = self._store.find_consent(user_id, scope, str(values["version"]))
            else:
                # 取该 user+scope 下最新一条（granted_at 最大）未撤销 grant。
                candidates = [
                    c for c in self._store.consents
                    if c["user_id"] == user_id and c["scope"] == scope
                ]
                row = candidates[-1] if candidates else None
            return _MappingResult([row] if row else [])
        if "FROM user_revision_state" in sql:
            row = self._store.revision_rows.get(int(values["user_id"]))
            return _MappingResult([row] if row else [])
        if "FROM ai_profile_session" in sql and "active_status = 1" in sql:
            row = self._store.find_active(int(values["user_id"]), str(values["subject"]))
            return _MappingResult([row] if row else [])
        if "FROM ai_profile_session" in sql:
            row = self._store.sessions.get(str(values["session_id"]))
            return _MappingResult([row] if row else [])
        if "FROM ai_profile_draft " in sql or "FROM ai_profile_draft\n" in sql:
            session_id = str(values["session_id"])
            editable = {"draft", "extracting", "awaiting_confirmation", "paused"}
            candidates = [
                d for d in self._store.drafts
                if d["session_id"] == session_id and d["status"] in editable
            ]
            if candidates:
                candidates.sort(key=lambda d: d["updated_at"], reverse=True)
                return _MappingResult([{"draft_id": candidates[0]["draft_id"]}])
            return _MappingResult([])
        if "FROM ai_profile_draft_field" in sql and "WHERE draft_id = :draft_id" in sql:
            rows = []
            for field in self._store.fields_for_draft(str(values["draft_id"])):
                row = dict(field)
                if "value_json" not in row:
                    row["value_json"] = json.dumps(field.get("value"), ensure_ascii=False)
                rows.append(row)
            return _MappingResult(rows)
        if "FROM ai_profile_draft_field" in sql:
            return _MappingResult(self._store.field_keys(str(values["session_id"])))
        # ---- 敏感词库（Task 9 turn 前置审核）----
        # 内存假库没有 config_sensitive_word 表:词库按"空"处理,moderate_text
        # 本地规则放行——与 load_active_sensitive_words 对不可用词库的优雅
        # 降级语义一致;需要验证 reject/replace 语义的测试直接 patch
        # app.services.ai.profile.moderate_text。
        if "FROM config_sensitive_word" in sql:
            return _MappingResult([])
        # ---- Phase 4 P4-01: ai_profile_projection_status ----
        # 假库只关心删除路径(走 mark_deleted):记录 status=deleted 即视为
        # 准入位已不可读;active/invalidated/pending 假库不模拟,默认视为"可读"。
        if "INSERT INTO ai_profile_projection_status" in sql:
            kind = str(values.get("kind") or "")
            status = str(values.get("status") or "")
            if status == "deleted":
                self._store.projection_status[(int(values["user_id"]), kind)] = {
                    "status": status, "reason": values.get("last_error")
                }
            # 其余状态假库不模拟(测试目标只关心删除)
            return _MappingResult([])
        raise AssertionError(f"unhandled sql: {sql}")

    async def commit(self) -> None:
        self.commits += 1
        self._committed_snapshot = self._snapshot_store()

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self._committed_snapshot is not None:
            # 还原到最近一次 commit 的快照，撤销其后所有未提交的插入/更新。
            self._restore_store(self._committed_snapshot)


class ProfileStore:
    """In-memory profile store with the Task 7 fixture surface."""

    NotFound = ProfileSessionNotFound
    Stale = ProfileSessionStale

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.turns: list[dict[str, Any]] = []
        self.drafts: list[dict[str, Any]] = []
        self.draft_fields: list[dict[str, Any]] = []
        self.consents: list[dict[str, Any]] = []
        self.revision_rows: dict[int, dict[str, Any]] = {}
        # Phase 4 P4-01: ai_profile_projection_status 假存储
        self.projection_status: dict[tuple[int, str], dict[str, Any]] = {}
        self.task_store = TaskStore()
        self.session = FakeProfileSession(self)
        self.db = self.session
        # 预置 user 10 的 profile_text_extract 授权与初始 revision 状态，
        # 与 create_profile_session 的前置条件一致。
        self.seed_consent(10, "profile-text-v1")
        self.revision_rows.setdefault(
            10,
            {
                "profile_revision": 0,
                "preference_revision": 0,
                "privacy_revision": 0,
                "relationship_revision": 0,
                "policy_revision": 0,
            },
        )

    # ---- seed helpers ---------------------------------------------------

    def seed_consent(self, user_id: int, version: str) -> None:
        self.consents.append(
            {
                "user_id": int(user_id),
                "scope": "profile_text_extract",
                "version": version,
                "policy_revision": "ai-policy-2026-08-07-v1",
                "granted_at": _now() - timedelta(days=1),
            }
        )

    async def seed_session(
        self,
        owner_user_id: int = 10,
        subject: str = "personal",
        status: str = "draft",
        session_id: str | None = None,
        consent_version: str = "profile-text-v1",
        profile_revision: int = 1,
        preference_revision: int = 0,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = _now()
        sid = session_id or f"ps_{uuid.uuid4().hex[:12]}"
        row = {
            "session_id": sid,
            "user_id": int(owner_user_id),
            "subject": subject,
            "input_mode": "text",
            "status": status,
            "active_status": 1,
            "consent_version": consent_version,
            "policy_revision": "ai-policy-2026-08-07-v1",
            "current_question_id": None,
            "skipped_field_keys": None,
            "profile_revision": int(profile_revision),
            "preference_revision": int(preference_revision),
            "expires_at": expires_at or now + timedelta(days=7),
            "ended_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self.sessions[sid] = row
        self.revision_rows[int(owner_user_id)] = {
            "profile_revision": int(profile_revision),
            "preference_revision": int(preference_revision),
            "privacy_revision": 0,
            "relationship_revision": 0,
            "policy_revision": 0,
        }
        if not self.find_consent(int(owner_user_id), "profile_text_extract", consent_version):
            self.seed_consent(int(owner_user_id), consent_version)
        return row


    def insert_session(self, params: dict[str, Any]) -> dict[str, Any]:
        # 模拟 uk_ai_profile_session_active(user_id, subject, active_status)
        # 唯一约束：同 user+subject 只允许一个活动会话。
        existing_active = self.find_active(
            int(params["user_id"]), str(params["subject"])
        )
        if existing_active is not None:
            raise IntegrityError(
                "INSERT INTO ai_profile_session", params, Exception("Duplicate entry")
            )
        now = _now()
        row = {
            "session_id": str(params["session_id"]),
            "user_id": int(params["user_id"]),
            "subject": str(params["subject"]),
            "input_mode": "text",
            "session_kind": str(params.get("session_kind") or "build"),
            "status": "draft",
            "active_status": 1,
            "consent_version": str(params["consent_version"]),
            "policy_revision": str(params["policy_revision"]),
            "current_question_id": None,
            "skipped_field_keys": params.get("skipped_field_keys"),
            "profile_revision": int(params["profile_revision"]),
            "preference_revision": int(params["preference_revision"]),
            "expires_at": params.get("expires_at"),
            "ended_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self.sessions[row["session_id"]] = row
        self.revision_rows.setdefault(
            int(params["user_id"]),
            {
                "profile_revision": int(params["profile_revision"]),
                "preference_revision": int(params["preference_revision"]),
                "privacy_revision": 0,
                "relationship_revision": 0,
                "policy_revision": 0,
            },
        )
        return row

    def insert_turn(self, params: dict[str, Any]) -> dict[str, Any]:
        # 模拟 uk_ai_profile_turn_session_client(session_id, client_turn_id)
        # 唯一约束：同会话同 client_turn_id 只允许一条 turn。
        if self.find_turn(
            str(params["session_id"]), str(params["client_turn_id"])
        ) is not None:
            raise IntegrityError(
                "INSERT INTO ai_profile_turn", params, Exception("Duplicate entry")
            )
        row = {
            "turn_id": str(params["turn_id"]),
            "session_id": str(params["session_id"]),
            "client_turn_id": str(params["client_turn_id"]),
            "user_id": int(params["user_id"]),
            "turn_no": int(params["turn_no"]),
            "role": str(params.get("role") or "user"),
            "answer_text": str(params["answer_text"]),
            "status": "saved",
            "source_type": "user_answer",
            "created_at": _now(),
        }
        self.turns.append(row)
        return row

    def insert_draft(self, params: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        row = {
            "draft_id": str(params["draft_id"]),
            "user_id": int(params["user_id"]),
            "subject": str(params["subject"]),
            "session_id": str(params["session_id"]),
            "status": "draft",
            "expected_revision": 0,
            "consent_snapshot_json": params.get("consent_snapshot_json"),
            "policy_revision": str(params["policy_revision"]),
            "prompt_version": str(params["prompt_version"]),
            "schema_version": str(params["schema_version"]),
            "expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self.drafts.append(row)
        return row

    def insert_draft_field(self, params: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        row = {
            "draft_id": str(params["draft_id"]),
            "field_key": str(params["field_key"]),
            "subject": str(params["subject"]),
            "value": (
                json.loads(params["value_json"]) if params.get("value_json") else None
            ),
            "display_value": params.get("display_value"),
            "source_type": str(params.get("source_type") or "user_answer"),
            "source_turn_ids": params.get("source_turn_ids"),
            "source_span": params.get("source_span"),
            "confidence": float(params.get("confidence") or 0.0),
            "visibility": params.get("visibility"),
            "consent_scope": params.get("consent_scope"),
            "schema_version": str(params.get("schema_version") or "profile-extract-v1"),
            "prompt_version": params.get("prompt_version"),
            "content_hash": params.get("content_hash"),
            "confirmation_status": str(params.get("confirmation_status") or "suggested"),
            "created_at": now,
            "updated_at": now,
        }
        self.draft_fields.append(row)
        return row

    def apply_session_update(self, sql: str, params: dict[str, Any]) -> bool:
        row = self.sessions.get(params.get("session_id"))
        if row is None:
            return False
        if "status = 'stale'" in sql:
            row["status"] = "stale"
            row["active_status"] = 0
            row["ended_at"] = _now()
        elif "status = 'cancelled'" in sql:
            row["status"] = "cancelled"
            row["active_status"] = 0
            row["ended_at"] = _now()
        elif "status = 'failed'" in sql:
            # ``_fail_extract_session`` 的终态失败自提交写入：字面量 SQL 带
            # ``WHERE ... AND status = 'extracting'`` 幂等守卫——非 extracting
            # 的会话不误伤（返回 rowcount=0 的 no-op）。
            if "status = 'extracting'" in sql and row["status"] != "extracting":
                return False
            row["status"] = "failed"
            row["active_status"] = 0
            row["ended_at"] = _now()
        elif "SET status = :status" in sql:
            row["status"] = str(params["status"])
        elif "skipped_field_keys = :skipped_field_keys" in sql:
            row["skipped_field_keys"] = params.get("skipped_field_keys")
        elif "input_mode = :input_mode" in sql:
            row["input_mode"] = str(params["input_mode"])
        else:
            raise AssertionError(f"unhandled session update: {sql}")
        row["updated_at"] = _now()
        return True

    # ---- query helpers ---------------------------------------------------

    def find_consent(self, user_id: int, scope: str, version: str) -> dict[str, Any] | None:
        for row in self.consents:
            if (
                row["user_id"] == user_id
                and row["scope"] == scope
                and row["version"] == version
            ):
                return row
        return None

    def find_active(self, user_id: int, subject: str) -> dict[str, Any] | None:
        for row in self.sessions.values():
            if row["user_id"] == user_id and row["subject"] == subject and row["active_status"] == 1:
                return row
        return None

    def find_turn(self, session_id: str, client_turn_id: str) -> dict[str, Any] | None:
        for row in self.turns:
            if row["session_id"] == session_id and row["client_turn_id"] == client_turn_id:
                return row
        return None

    def count_turns(self, session_id: str) -> int:
        return sum(1 for row in self.turns if row["session_id"] == session_id)

    def field_keys(self, session_id: str) -> list[dict[str, Any]]:
        draft_ids = {d["draft_id"] for d in self.drafts if d["session_id"] == session_id}
        return [
            {"field_key": f["field_key"], "confirmation_status": f["confirmation_status"]}
            for f in self.draft_fields
            if f["draft_id"] in draft_ids and f["confirmation_status"] != "deleted"
        ]

    def fields_for_draft(self, draft_id: str) -> list[dict[str, Any]]:
        return [dict(f) for f in self.draft_fields if f["draft_id"] == draft_id]

    # ---- Task 7 fixture surface (brief semantics) -----------------------


    async def count_tasks(self, turn_id: str) -> int:
        def _turn_id(payload: Any) -> Any:
            if isinstance(payload, dict):
                return payload.get("turn_id")
            if isinstance(payload, str):
                try:
                    return json.loads(payload).get("turn_id")
                except ValueError:
                    return None
            return None

        return sum(
            1
            for row in self.task_store.tasks.values()
            if row["payload_summary"] and _turn_id(row["payload_summary"]) == turn_id
        )

    async def read_session(self, session_id: str, owner_user_id: int) -> dict[str, Any]:
        row = self.sessions.get(session_id)
        if row is None or int(row["user_id"]) != int(owner_user_id):
            raise self.NotFound()
        return row

    async def get(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)
