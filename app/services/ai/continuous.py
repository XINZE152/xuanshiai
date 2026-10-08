"""continuous_v2：复用既有存储的双主体成稿与整份确认。

所有写服务由调用方提交事务。用户版本行串行化成稿/确认与撤权；预览的
content 存冻结 JSON 信封，HTTP 只暴露其中可审阅正文，不新增表或中间件。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.ai_schema import PROFILE_DIMENSIONS
from app.schemas.ai_moxiang import HIGH_CONFIDENCE_THRESHOLD
from app.services.ai.consents import _lock_privacy_revision
from app.services.ai.profile import (
    PROFILE_CONSENT_SCOPE, PROFILE_POLICY_REVISION, _load_revision_vector,
)
from app.services.ai.tasks import AiTaskRecord, TaskError, enqueue_task, fail_task
from app.services.idempotency import _payload_hash
from app.services.revisions import RevisionKind, RevisionVector, increment_revision_and_enqueue

FLOW_VERSION = "continuous_v2"
CONTINUOUS_DRAFT_SCHEMA_VERSION = "profile-continuous-v2"
CONTINUOUS_PREVIEW_TASK_TYPE = "profile_preview"
_TERMINAL_TASKS = {"failed", "cancelled", "superseded", "expired"}


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value is not None else None)


def _json(value: Any, default: Any = None) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return value if value is not None else default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def _consent(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    row = (await db.execute(text(
        "SELECT version,policy_revision,granted_at FROM ai_consent_grant "
        "WHERE user_id=:user_id AND scope=:scope AND revoked_at IS NULL "
        "ORDER BY granted_at DESC LIMIT 1"
    ), {"user_id": user_id, "scope": PROFILE_CONSENT_SCOPE})).mappings().first()
    if not row:
        return None
    return {"scope": PROFILE_CONSENT_SCOPE, "version": str(row["version"]),
            "consent_version": str(row["version"]), "granted_at": _iso(row["granted_at"]),
            "policy_revision": str(row.get("policy_revision") or PROFILE_POLICY_REVISION)}


async def _require_consent(db: AsyncSession, user_id: int) -> dict[str, Any]:
    consent = await _consent(db, user_id)
    if consent is None:
        raise PermissionError("AI_CONSENT_REQUIRED")
    return consent


async def _replay(db: AsyncSession, user_id: int, operation: str, key: str, payload: dict) -> dict | None:
    """调用方已持用户版本锁；复用通用幂等表，不在中途 commit 释放锁。"""
    row = (await db.execute(text(
        "SELECT payload_hash,state,response_json FROM api_idempotency_record "
        "WHERE user_id=:user_id AND operation=:operation AND BINARY idempotency_key=BINARY :key FOR UPDATE"
    ), {"user_id": user_id, "operation": operation, "key": key})).mappings().first()
    if not row:
        return None
    if row["payload_hash"] != _payload_hash(payload) or row["state"] != "completed":
        raise TaskError(code="TASK_IDEMPOTENCY_CONFLICT", message="幂等键已用于不同请求或请求处理中", status_code=409)
    return _json(row["response_json"], {})


async def _remember(db: AsyncSession, user_id: int, operation: str, key: str, payload: dict, response: dict) -> None:
    await db.execute(text(
        "INSERT INTO api_idempotency_record (user_id,operation,idempotency_key,payload_hash,state,owner_token,response_json,created_at,updated_at) "
        "VALUES (:user_id,:operation,:key,:digest,'completed',:token,:response,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))"
    ), {"user_id": user_id, "operation": operation, "key": key, "digest": _payload_hash(payload),
        "token": str(uuid.uuid4()), "response": _dump(response)})


async def _latest_revision(db: AsyncSession, user_id: int, subject: str) -> dict | None:
    consent = await _require_consent(db, user_id)
    row = (await db.execute(text(
        "SELECT r.id,r.revision_no,r.created_at,d.schema_version,d.consent_snapshot_json FROM ai_profile_revision r "
        "LEFT JOIN ai_profile_draft d ON d.draft_id=r.draft_id "
        "WHERE r.user_id=:user_id AND r.subject=:subject AND (d.status='published' OR r.draft_id IS NULL) "
        "ORDER BY r.revision_no DESC LIMIT 1"
    ), {"user_id": user_id, "subject": subject})).mappings().first()
    if not row:
        return None
    # 旧正式数据保留，但撤回后重新授权不能使旧资料自动复活。
    if row["schema_version"] == CONTINUOUS_DRAFT_SCHEMA_VERSION:
        meta = _json(row["consent_snapshot_json"], {})
        if any(meta.get(key) != consent.get(key) for key in ("version", "granted_at", "policy_revision")):
            return None
    elif (_iso(row.get("created_at")) or "") < consent["granted_at"]:
        return None
    return dict(row)


async def _revision_fields(db: AsyncSession, revision_id: int | None) -> list[dict]:
    if revision_id is None:
        return []
    rows = (await db.execute(text(
        "SELECT field_key,subject,field_kind,profile_dimension,category,content,replaces_field_key,value_json,"
        "display_value,source_type,source_turn_ids,source_span,confidence,content_hash "
        "FROM ai_profile_revision_field WHERE revision_id=:revision_id ORDER BY id"
    ), {"revision_id": revision_id})).mappings().all()
    return [dict(row) for row in rows]


async def _candidates(db: AsyncSession, user_id: int, subject: str, consent: dict) -> list[dict]:
    rows = (await db.execute(text(
        "SELECT c.* FROM ai_profile_candidate c JOIN ai_profile_session s ON s.session_id=c.session_id "
        "WHERE c.user_id=:user_id AND s.user_id=:user_id AND c.subject=:subject "
        "AND c.status IN ('active','promoted') AND s.status NOT IN ('deleted','cancelled') "
        "AND c.consent_version=:version AND c.policy_revision=:policy "
        "AND c.created_at>=:granted_at AND c.confidence>=:threshold ORDER BY c.updated_at,c.id"
    ), {"user_id": user_id, "subject": subject, "version": consent["version"],
        "policy": consent["policy_revision"], "granted_at": consent["granted_at"],
        "threshold": HIGH_CONFIDENCE_THRESHOLD})).mappings().all()
    sources = (await db.execute(text(
        "SELECT t.turn_id FROM ai_profile_turn t JOIN ai_profile_session s ON s.session_id=t.session_id "
        "WHERE t.user_id=:user_id AND s.user_id=:user_id AND t.role='user' AND t.status<>'deleted' "
        "AND s.status NOT IN ('deleted','cancelled') AND t.created_at>=:granted_at"
    ), {"user_id": user_id, "granted_at": consent["granted_at"]})).mappings().all()
    valid_ids = {str(row["turn_id"]) for row in sources}
    valid = []
    seen = set()
    for row in rows:
        ids = _json(row.get("source_turn_ids"), [])
        digest = str(row.get("content_hash") or "")
        if (not isinstance(ids, list) or not ids or not set(map(str, ids)).issubset(valid_ids)
                or not digest or digest in seen or row.get("profile_dimension") not in PROFILE_DIMENSIONS):
            continue
        seen.add(digest)
        valid.append(dict(row))
    return valid


def _versions(candidates: list[dict]) -> list[str]:
    return sorted({f"{row['candidate_id']}:{row['content_hash']}" for row in candidates})


def _is_snapshot_entry(row: dict | None) -> bool:
    """判断快照行是否为可被同 key 替换的 entry 行。

    正式 revision 行与草稿行都带 ``field_kind``；缺失该列的历史数据按
    structured 处理（保守：宁可不替换，也不误覆盖结构化字段）。
    """
    if not row:
        return False
    return str(row.get("field_kind") or "structured") == "entry"


def ready_to_build(candidates: list[dict], baseline: list[dict], has_published: bool) -> bool:
    """首次三维三证据；增量一次有效变化即可。冲突底线必须先澄清。"""
    previous = {str(row["content_hash"]) for row in baseline}
    changed = [row for row in candidates if str(row["content_hash"]) not in previous]
    if not changed:
        return False
    boundaries: dict[str, set[str]] = {}
    for row in candidates:
        if row.get("profile_dimension") == "relationship_boundaries":
            key = str(row.get("field_key") or row.get("category") or "boundary")
            boundaries.setdefault(key, set()).add(str(row["content_hash"]))
    if any(len(values) > 1 for values in boundaries.values()):
        return False
    return has_published or (len(candidates) >= 3 and len({r["profile_dimension"] for r in candidates}) >= 3)


def _empty_subject(subject: str) -> dict[str, Any]:
    return {"subject": subject, "status": "collecting", "overall_percent": 0.0,
            "dimensions": {key: {"percent": 0.0, "evidence_count": 0} for key in PROFILE_DIMENSIONS},
            "draft_id": None, "expected_revision": None, "preview_id": None, "task_id": None,
            "published_revision_id": None, "has_updates": False, "last_error": None}


async def _subject_state(db: AsyncSession, user_id: int, subject: str, consent: dict | None = None) -> dict[str, Any]:
    state = _empty_subject(subject)
    consent = consent or await _require_consent(db, user_id)
    candidates = await _candidates(db, user_id, subject, consent)
    for dimension in PROFILE_DIMENSIONS:
        count = sum(row["profile_dimension"] == dimension for row in candidates)
        state["dimensions"][dimension] = {"percent": min(100.0, count * 50.0), "evidence_count": count}
    state["overall_percent"] = sum(item["percent"] for item in state["dimensions"].values()) / len(PROFILE_DIMENSIONS)
    published = await _latest_revision(db, user_id, subject)
    if published:
        state["published_revision_id"] = int(published["id"])
        state["status"] = "confirmed"
    draft = (await db.execute(text(
        "SELECT draft_id,status,expected_revision,consent_snapshot_json FROM ai_profile_draft "
        "WHERE user_id=:user_id AND subject=:subject AND schema_version=:schema "
        "AND status IN ('draft','published','stale') ORDER BY id DESC LIMIT 1"
    ), {"user_id": user_id, "subject": subject, "schema": CONTINUOUS_DRAFT_SCHEMA_VERSION})).mappings().first()
    if not draft:
        baseline = await _revision_fields(db, int(published["id"]) if published else None)
        state["has_updates"] = any(r["content_hash"] not in {b["content_hash"] for b in baseline} for r in candidates)
        return state
    meta = _json(draft["consent_snapshot_json"], {}).get("continuous", {})
    grant = _json(draft["consent_snapshot_json"], {})
    if any(grant.get(key) != consent.get(key) for key in ("version", "granted_at", "policy_revision")):
        return state
    state.update(draft_id=str(draft["draft_id"]), expected_revision=int(draft["expected_revision"]))
    state["has_updates"] = bool(set(_versions(candidates)) - set(meta.get("candidate_versions", [])))
    row = (await db.execute(text(
        "SELECT p.preview_id,p.status,p.task_id,p.last_error,p.content,t.status AS task_status,t.error_code AS last_error_code "
        "FROM ai_profile_preview p LEFT JOIN ai_task t ON t.task_id=p.task_id "
        "WHERE p.draft_id=:draft_id AND p.expected_revision=:revision LIMIT 1"
    ), {"draft_id": draft["draft_id"], "revision": state["expected_revision"]})).mappings().first()
    # 发布会递增草稿版本，正式状态不依赖旧 preview 的版本号。
    if draft["status"] == "published":
        return state
    if not row:
        state["status"] = "stale" if draft["status"] == "stale" else "collecting"
        return state
    state.update(preview_id=str(row["preview_id"]), task_id=row["task_id"],
                 last_error=row.get("last_error") or row.get("last_error_code"))
    task_status = str(row.get("task_status") or "")
    if draft["status"] == "stale" or row["status"] == "stale":
        state["status"] = "stale"
    elif row["status"] == "failed" or task_status in _TERMINAL_TASKS:
        state["status"] = "failed"
    elif task_status == "succeeded" and _json(row.get("content"), {}).get("content"):
        state["status"] = "awaiting_confirmation"
    else:
        state["status"] = "generating"
    return state


async def build_state(db: AsyncSession, user_id: int) -> dict[str, Any]:
    consent = await _consent(db, user_id)
    if consent is None:
        return {"flow_version": FLOW_VERSION, "consent_granted": False, "session_id": None,
                "personal": _empty_subject("personal"), "ideal_partner": _empty_subject("ideal_partner")}
    session = (await db.execute(text(
        "SELECT session_id FROM ai_profile_session WHERE user_id=:user_id AND session_kind='master' "
        "AND active_status=1 AND status NOT IN ('deleted','cancelled') ORDER BY updated_at DESC LIMIT 1"
    ), {"user_id": user_id})).mappings().first()
    return {"flow_version": FLOW_VERSION, "consent_granted": True,
            "session_id": str(session["session_id"]) if session else None,
            "personal": await _subject_state(db, user_id, "personal", consent),
            "ideal_partner": await _subject_state(db, user_id, "ideal_partner", consent)}


async def list_continuous_turns(db: AsyncSession, user_id: int, limit: int, before_id: str | None = None) -> tuple[list[dict], str | None]:
    consent = await _require_consent(db, user_id)
    limit = max(1, min(100, int(limit)))
    params = {"user_id": user_id, "limit": limit + 1, "granted_at": consent["granted_at"]}
    cursor = ""
    if before_id:
        if not str(before_id).isdigit() or int(before_id) <= 0:
            raise ValueError("before_id must be a positive numeric turn id")
        params["before_id"] = int(before_id)
        cursor = " AND t.id<:before_id"
    rows = (await db.execute(text(
        "SELECT t.id,t.turn_id,t.turn_no,t.role,t.answer_text,t.client_turn_id,t.created_at "
        "FROM ai_profile_turn t JOIN ai_profile_session s ON s.session_id=t.session_id "
        "WHERE t.user_id=:user_id AND s.user_id=:user_id AND s.session_kind='master' "
        "AND s.status NOT IN ('deleted','cancelled') AND t.status<>'deleted' "
        "AND t.role IN ('user','assistant') AND t.created_at>=:granted_at "
        # 历史双写按来源去重，不按文本去重；无 client ID 的旧行按 turn_id 保留。
        "AND NOT EXISTS (SELECT 1 FROM ai_profile_turn newer WHERE newer.user_id=t.user_id "
        "AND newer.role=t.role AND newer.status<>'deleted' AND newer.id>t.id "
        "AND t.client_turn_id IS NOT NULL AND t.client_turn_id<>'' AND newer.client_turn_id=t.client_turn_id) "
        f"{cursor} ORDER BY t.id DESC LIMIT :limit"
    ), params)).mappings().all()
    next_id = str(rows[limit - 1]["id"]) if len(rows) > limit else None
    result = [{"turn_id": str(row["turn_id"]), "turn_no": int(row["turn_no"]), "role": str(row["role"]),
               "answer_text": str(row["answer_text"]), "client_turn_id": str(row.get("client_turn_id") or ""),
               "created_at": _iso(row["created_at"])} for row in rows[:limit]]
    return list(reversed(result)), next_id


async def build_continuous_draft(db: AsyncSession, user_id: int, subject: str, *, refresh: bool, idempotency_key: str) -> dict[str, Any]:
    if subject not in {"personal", "ideal_partner"}:
        raise ValueError("invalid subject")
    await _lock_privacy_revision(db, user_id)
    consent = await _require_consent(db, user_id)
    payload = {"subject": subject, "refresh": refresh}
    # 回放返回当前只读快照，不复建、不让过时快照掩盖已发生的撤权或删除。
    if await _replay(db, user_id, "continuous-build", idempotency_key, payload) is not None:
        return await build_state(db, user_id)
    existing = (await db.execute(text(
        "SELECT * FROM ai_profile_draft WHERE user_id=:user_id AND subject=:subject AND schema_version=:schema "
        "AND status='draft' ORDER BY id DESC LIMIT 1 FOR UPDATE"
    ), {"user_id": user_id, "subject": subject, "schema": CONTINUOUS_DRAFT_SCHEMA_VERSION})).mappings().first()
    if existing and not refresh:
        await ensure_continuous_preview(db, str(existing["draft_id"]), user_id, int(existing["expected_revision"]), idempotency_key)
        await _remember(db, user_id, "continuous-build", idempotency_key, payload, {"draft_id": existing["draft_id"]})
        return await build_state(db, user_id)
    latest = await _latest_revision(db, user_id, subject)
    baseline_id = int(latest["id"]) if latest else None
    baseline = await _revision_fields(db, baseline_id)
    candidates = await _candidates(db, user_id, subject, consent)
    consumed = set(_json(latest.get("consent_snapshot_json"), {}).get("continuous", {}).get("candidate_versions", [])) if latest else set()
    new_candidates = [r for r in candidates if f"{r['candidate_id']}:{r['content_hash']}" not in consumed]
    if not ready_to_build(new_candidates, baseline, latest is not None):
        raise LookupError("CONTINUOUS_BUILD_NOT_READY")
    vector = await _load_revision_vector(db, user_id)
    # 未确认稿显式 refresh：保留用户编辑/删除，只将尚未纳入的证据合并。
    snapshot = {str(row["field_key"]): row for row in baseline}
    previous_versions: set[str] = set(consumed)
    if existing:
        existing_meta = _json(existing["consent_snapshot_json"], {}).get("continuous", {})
        previous_versions.update(existing_meta.get("candidate_versions", []))
        fields = (await db.execute(text("SELECT * FROM ai_profile_draft_field WHERE draft_id=:id ORDER BY id"),
                                   {"id": existing["draft_id"]})).mappings().all()
        for row in fields:
            if row["confirmation_status"] in {"deleted", "rejected"}:
                snapshot.pop(str(row["field_key"]), None)
            else:
                snapshot[str(row["field_key"])] = dict(row)
    for row in candidates:
        if f"{row['candidate_id']}:{row['content_hash']}" not in previous_versions:
            # 结构化候选照旧按自身字段键落到快照（不涉及替换语义）；entry 候选
            # 只在带替换目标、且目标仍是当前快照里的 entry 行时按同 key 覆盖，
            # 成为该条目的新版本。目标已被用户删除/驳回、或目标 key 不属于
            # entry 行时一律回退新 key——绝不复活墓碑，也不误并到别的字段。
            target_key = str(row.get("field_key") or "")
            if not target_key:
                key = f"entry_{row['candidate_id']}"
            elif str(row.get("field_kind") or "structured") == "structured":
                key = target_key
            elif _is_snapshot_entry(snapshot.get(target_key)):
                key = target_key
            else:
                key = f"entry_{row['candidate_id']}"
            snapshot[key] = row
    if not snapshot:
        raise LookupError("CONTINUOUS_BUILD_NOT_READY")
    draft_id = uuid.uuid4().hex
    meta = {**consent, "continuous": {"baseline_revision_id": baseline_id,
            "source_revision": vector.as_dict(), "candidate_versions": _versions(candidates)}}
    await db.execute(text(
        "INSERT INTO ai_profile_draft (draft_id,user_id,subject,status,expected_revision,consent_snapshot_json,"
        "policy_revision,prompt_version,schema_version) VALUES (:id,:user,:subject,'draft',0,:meta,:policy,:prompt,:schema)"
    ), {"id": draft_id, "user": user_id, "subject": subject, "meta": _dump(meta), "policy": consent["policy_revision"],
        "prompt": "profile-continuous-prompt-v1", "schema": CONTINUOUS_DRAFT_SCHEMA_VERSION})
    for key, row in snapshot.items():
        value = _json(row.get("value_json"))
        await db.execute(text(
            "INSERT INTO ai_profile_draft_field (draft_id,field_key,subject,field_kind,profile_dimension,category,content,"
            "replaces_field_key,value_json,display_value,source_type,source_turn_ids,source_span,confidence,visibility,"
            "consent_scope,schema_version,prompt_version,content_hash,confirmation_status) "
            "VALUES (:id,:key,:subject,:kind,:dimension,:category,:content,:replaces,:value,:display,:source,:turns,:span,"
            ":confidence,:visibility,:scope,:schema,:prompt,:hash,'suggested')"
        ), {"id": draft_id, "key": key, "subject": subject, "kind": row.get("field_kind") or "entry",
            "dimension": row.get("profile_dimension"), "category": row.get("category"), "content": row.get("content"),
            "replaces": row.get("replaces_field_key"), "value": _dump(value) if value is not None else None,
            "display": row.get("display_value") or row.get("content") or (str(value) if value is not None else None),
            "source": row.get("source_type") or "moxiang_continuous", "turns": _dump(_json(row.get("source_turn_ids"), [])),
            "span": row.get("source_span"), "confidence": float(row.get("confidence") or 0),
            "visibility": row.get("visibility") or "self", "scope": PROFILE_CONSENT_SCOPE,
            "schema": "profile-extract-v1", "prompt": "profile-continuous-prompt-v1", "hash": row["content_hash"]})
    if existing:
        await db.execute(text("UPDATE ai_profile_draft SET status='stale' WHERE draft_id=:id"), {"id": existing["draft_id"]})
        await db.execute(text("UPDATE ai_profile_preview SET status='stale' WHERE draft_id=:id AND status='active'"), {"id": existing["draft_id"]})
    await ensure_continuous_preview(db, draft_id, user_id, 0, idempotency_key)
    await _remember(db, user_id, "continuous-build", idempotency_key, payload, {"draft_id": draft_id})
    return await build_state(db, user_id)


async def ensure_continuous_preview(db: AsyncSession, draft_id: str, user_id: int, expected_revision: int, idempotency_key: str) -> dict[str, Any]:
    await _lock_privacy_revision(db, user_id)
    consent = await _require_consent(db, user_id)
    draft = (await db.execute(text("SELECT * FROM ai_profile_draft WHERE draft_id=:id AND user_id=:user FOR UPDATE"),
                             {"id": draft_id, "user": user_id})).mappings().first()
    if not draft or draft["schema_version"] != CONTINUOUS_DRAFT_SCHEMA_VERSION:
        raise LookupError("PREVIEW_NOT_FOUND")
    if draft["status"] != "draft" or int(draft["expected_revision"]) != expected_revision:
        raise ValueError("DRAFT_VERSION_CONFLICT")
    meta = _json(draft["consent_snapshot_json"], {})
    if any(meta.get(key) != consent.get(key) for key in ("version", "granted_at", "policy_revision")):
        raise PermissionError("AI_CONSENT_REQUIRED")
    row = (await db.execute(text(
        "SELECT p.preview_id,p.status,t.status AS task_status FROM ai_profile_preview p "
        "LEFT JOIN ai_task t ON t.task_id=p.task_id WHERE p.draft_id=:id AND p.expected_revision=:revision"
    ), {"id": draft_id, "revision": expected_revision})).mappings().first()
    if row:
        if row["status"] == "active" and row["task_status"] not in _TERMINAL_TASKS:
            return await get_continuous_preview(db, str(row["preview_id"]), user_id)
        # 新 revision 避免复用失败任务/违反 (draft, revision) 唯一键；原稿仍可追溯。
        await db.execute(text("UPDATE ai_profile_preview SET status='stale' WHERE preview_id=:id"), {"id": row["preview_id"]})
        expected_revision += 1
        await db.execute(text("UPDATE ai_profile_draft SET expected_revision=:revision WHERE draft_id=:id"),
                         {"id": draft_id, "revision": expected_revision})
    vector = await _load_revision_vector(db, user_id)
    preview_id = uuid.uuid4().hex
    digest = _payload_hash({"draft_id": draft_id, "expected_revision": expected_revision})
    task = await enqueue_task(db, user_id, CONTINUOUS_PREVIEW_TASK_TYPE,
                              f"continuous-preview:{draft_id}:{expected_revision}", digest, revisions=vector, consent=consent)
    await db.execute(text("UPDATE ai_task SET payload_summary=:payload WHERE task_id=:id"),
                     {"id": task.task_id, "payload": _dump({"flow_version": FLOW_VERSION, "draft_id": draft_id, "subject": draft["subject"], "expected_revision": expected_revision})})
    await db.execute(text(
        "INSERT INTO ai_profile_preview (preview_id,draft_id,expected_revision,user_id,subject,content,status,task_id) "
        "VALUES (:preview,:draft,:revision,:user,:subject,'','active',:task)"
    ), {"preview": preview_id, "draft": draft_id, "revision": expected_revision,
        "user": user_id, "subject": draft["subject"], "task": task.task_id})
    return await get_continuous_preview(db, preview_id, user_id)


def preview_fields(fields: list[dict], baseline: list[dict]) -> list[dict]:
    previous = {str(row["field_key"]): row for row in baseline}
    result = []
    for row in fields:
        old = previous.get(str(row["field_key"]))
        value = _json(row.get("value_json"))
        unchanged = old and row.get("content") == old.get("content") and value == _json(old.get("value_json"))
        result.append({"field_key": str(row["field_key"]), "field_kind": str(row["field_kind"]),
                       "category": row.get("category"), "content": str(row.get("content") or row.get("display_value") or ""),
                       "display_value": row.get("display_value"), "value_json": value,
                       "change": "unchanged" if unchanged else ("changed" if old else "added"),
                       "previous_display_value": str(old.get("display_value") or old.get("content") or "") if old else None})
    return result


def _narrative_text(data: dict) -> str:
    # 所有正式展示字段都在确认前可读；正文不能只是标题与少数维度摘录。
    parts = [data.get("persona_title"), " / ".join(data.get("persona_tags") or []), data.get("insight")]
    parts += [f"{d['title']}：{d['summary']}" for d in data.get("dimensions", [])]
    parts += [f"{w['label']}：{w['percent']}%" for w in data.get("ideal_weights", [])]
    change = data.get("recent_change")
    if change:
        parts += [change.get("summary"), change.get("observation")]
    parts += [h.get("observation") for h in data.get("history_observations", [])]
    parts += [data.get("conclusion")]
    return "\n\n".join(str(p) for p in parts if p)


async def generate_continuous_preview_handler(db: AsyncSession, task: AiTaskRecord, worker_id: str) -> tuple[str, RevisionVector] | None:
    from app.services.ai.base import AITaskContext, NarrativeRequest
    from app.services.ai.gateway import AIGateway
    from app.services.ai.profile import NARRATIVE_PROMPT_VERSION, NARRATIVE_SCHEMA_VERSION
    from app.services.ai.prompts.profile_narrative import serialize_fields_for_prompt

    payload = task.payload_summary or {}
    draft_id = str(payload.get("draft_id") or "")
    # 不在模型调用期间持有用户锁；Worker 在完成写回前再核对授权/版本并可回滚 handler。
    draft = (await db.execute(text("SELECT * FROM ai_profile_draft WHERE draft_id=:id AND user_id=:user"),
                             {"id": draft_id, "user": task.owner_user_id})).mappings().first()
    if not draft or draft["status"] != "draft" or draft["schema_version"] != CONTINUOUS_DRAFT_SCHEMA_VERSION:
        return None
    expected = int(payload.get("expected_revision", -1))
    if int(draft["expected_revision"]) != expected:
        return None
    consent = await _require_consent(db, task.owner_user_id)
    meta = _json(draft["consent_snapshot_json"], {})
    if any(meta.get(key) != consent.get(key) for key in ("version", "granted_at", "policy_revision")):
        return None
    fields = [dict(r) for r in (await db.execute(text(
        "SELECT * FROM ai_profile_draft_field WHERE draft_id=:id AND confirmation_status NOT IN ('deleted','rejected') ORDER BY id"
    ), {"id": draft_id})).mappings().all()]
    baseline = await _revision_fields(db, meta.get("continuous", {}).get("baseline_revision_id"))
    context = AITaskContext(task_id=task.task_id, request_id=uuid.uuid4().hex, scene="profile_preview",
                            provider=settings.ai_provider_name, model=settings.ai_model_name,
                            prompt_version=NARRATIVE_PROMPT_VERSION, schema_version=NARRATIVE_SCHEMA_VERSION,
                            input_revision=task.source_revision_json or {}, policy_revision=consent["policy_revision"])
    request = NarrativeRequest(subject=str(draft["subject"]), current_fields=serialize_fields_for_prompt(fields),
                               previous_fields=serialize_fields_for_prompt(baseline), history_summaries=(),
                               consent_version=consent["version"], policy_revision=consent["policy_revision"])
    outcome = await AIGateway(timeout_seconds=settings.ai_gateway_timeout_seconds).generate_narrative(context, request)
    if outcome.result is None:
        await fail_task(db, task.task_id, worker_id, error_code=outcome.error_code or "AI_TEMPORARILY_UNAVAILABLE", retryable=outcome.retryable)
        return None
    narrative = outcome.result.model_dump(mode="json")
    if (narrative.get("schema_version") != NARRATIVE_SCHEMA_VERSION or narrative.get("prompt_version") != NARRATIVE_PROMPT_VERSION
            or not narrative.get("persona_title") or not narrative.get("insight") or not narrative.get("dimensions")
            or (draft["subject"] == "personal" and narrative.get("ideal_weights")) or not fields):
        await fail_task(db, task.task_id, worker_id, error_code="AI_INPUT_INVALID", retryable=False)
        return None
    rendered = preview_fields(fields, baseline)
    boundaries = {r["field_key"]: r.get("content_hash") for r in fields if r.get("profile_dimension") == "relationship_boundaries"}
    old_boundaries = {r["field_key"]: r.get("content_hash") for r in baseline if r.get("profile_dimension") == "relationship_boundaries"}
    envelope = {"flow_version": FLOW_VERSION, "content": _narrative_text(narrative), "narrative": narrative,
                "fields": rendered, "fields_digest": _payload_hash(rendered), "boundary_changed": boundaries != old_boundaries}
    # 编辑和生成串行写回；编辑抢先则本次结果不落入任何可确认预览。
    await _lock_privacy_revision(db, task.owner_user_id)
    live = (await db.execute(text("SELECT status,expected_revision FROM ai_profile_draft WHERE draft_id=:id FOR UPDATE"),
                            {"id": draft_id})).mappings().first()
    if not live or live["status"] != "draft" or int(live["expected_revision"]) != expected:
        return None
    await db.execute(text(
        "UPDATE ai_profile_preview SET content=:content,last_error=NULL WHERE task_id=:task AND status='active' AND expected_revision=:revision"
    ), {"content": _dump(envelope), "task": task.task_id, "revision": expected})
    return f"profile-preview:{draft_id}:{expected}", RevisionVector(**(task.source_revision_json or {}))


async def get_continuous_preview(db: AsyncSession, preview_id: str, user_id: int) -> dict[str, Any] | None:
    consent = await _require_consent(db, user_id)
    row = (await db.execute(text(
        "SELECT p.*,d.schema_version,d.consent_snapshot_json,d.status AS draft_status,d.expected_revision AS draft_revision,"
        "t.status AS task_status,t.error_code AS last_error_code FROM ai_profile_preview p "
        "JOIN ai_profile_draft d ON d.draft_id=p.draft_id AND d.user_id=p.user_id "
        "LEFT JOIN ai_task t ON t.task_id=p.task_id WHERE p.preview_id=:id AND p.user_id=:user"
    ), {"id": preview_id, "user": user_id})).mappings().first()
    if not row or row["schema_version"] != CONTINUOUS_DRAFT_SCHEMA_VERSION or row["draft_status"] in {"deleted", "cancelled"}:
        return None
    meta = _json(row.get("consent_snapshot_json"), {})
    if any(meta.get(key) != consent.get(key) for key in ("version", "granted_at", "policy_revision")):
        raise PermissionError("AI_CONSENT_REQUIRED")
    envelope = _json(row.get("content"), {})
    if not isinstance(envelope, dict):
        envelope = {}
    status = str(row["status"])
    if status != "confirmed" and (row["draft_status"] != "draft" or int(row["draft_revision"]) != int(row["expected_revision"])):
        status = "stale"
    generation = {"succeeded": "completed", "running": "processing", "leased": "processing"}.get(str(row["task_status"]), "queued")
    if row["task_status"] in _TERMINAL_TASKS:
        generation = "failed"
        if status == "active":
            status = "failed"
    visible = generation == "completed" and envelope.get("flow_version") == FLOW_VERSION
    return {"flow_version": FLOW_VERSION, "preview_id": str(row["preview_id"]), "draft_id": str(row["draft_id"]),
            "expected_revision": int(row["expected_revision"]), "subject": str(row["subject"]), "status": status,
            "task_id": row["task_id"], "generation_status": generation,
            "content": str(envelope.get("content") or "") if visible else "", "fields": envelope.get("fields", []) if visible else [],
            "boundary_changed": bool(envelope.get("boundary_changed")), "last_error": row.get("last_error") or row.get("last_error_code"),
            "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"])}


async def continuous_preview_fields(db: AsyncSession, preview_id: str, user_id: int) -> list[dict]:
    preview = await get_continuous_preview(db, preview_id, user_id)
    return preview["fields"] if preview else []


async def confirm_continuous_preview(db: AsyncSession, preview_id: str, user_id: int, expected_revision: int, idempotency_key: str) -> dict[str, Any]:
    from app.services.ai.profile import (
        ensure_draft_editable, load_owned_draft_for_update, insert_immutable_profile_revision,
        enqueue_cleanup_or_projection_task,
    )
    await _lock_privacy_revision(db, user_id)
    consent = await _require_consent(db, user_id)
    payload = {"preview_id": preview_id, "expected_revision": expected_revision}
    replay = await _replay(db, user_id, "continuous-confirm", idempotency_key, payload)
    row = (await db.execute(text(
        "SELECT p.*,t.status AS task_status FROM ai_profile_preview p LEFT JOIN ai_task t ON t.task_id=p.task_id "
        "WHERE p.preview_id=:id AND p.user_id=:user"
    ), {"id": preview_id, "user": user_id})).mappings().first()
    if not row:
        raise LookupError("PREVIEW_NOT_FOUND")
    draft = await load_owned_draft_for_update(db, str(row["draft_id"]), user_id)
    if draft.schema_version != CONTINUOUS_DRAFT_SCHEMA_VERSION or draft.status in {"deleted", "cancelled"}:
        raise LookupError("PREVIEW_NOT_FOUND")
    if int(row["expected_revision"]) != expected_revision:
        raise ValueError("DRAFT_VERSION_CONFLICT")
    meta = draft.consent_snapshot
    if any(meta.get(key) != consent.get(key) for key in ("version", "granted_at", "policy_revision")):
        raise PermissionError("AI_CONSENT_REQUIRED")
    if replay is not None:
        return {**replay, "replayed": True}
    previous = meta.get("continuous", {}).get("confirmation")
    if row["status"] == "confirmed" and previous:
        await _remember(db, user_id, "continuous-confirm", idempotency_key, payload, previous)
        return {**previous, "replayed": True}
    ensure_draft_editable(draft, "确认")
    if draft.revision != expected_revision:
        raise ValueError("DRAFT_VERSION_CONFLICT")
    envelope = _json(row["content"], {})
    if (row["status"] != "active" or row["task_status"] != "succeeded" or not envelope.get("content")
            or not envelope.get("narrative") or not envelope.get("fields")):
        raise LookupError("PREVIEW_NOT_READY")
    latest = await _latest_revision(db, user_id, draft.subject)
    baseline_id = meta.get("continuous", {}).get("baseline_revision_id")
    if (int(latest["id"]) if latest else None) != baseline_id:
        raise ValueError("DRAFT_VERSION_CONFLICT")
    vector = await _load_revision_vector(db, user_id)
    source = meta.get("continuous", {}).get("source_revision", {})
    if any(vector.as_dict()[key] != source.get(key) for key in ("privacy", "policy")):
        raise ValueError("DRAFT_VERSION_CONFLICT")
    fields_rows = [dict(r) for r in (await db.execute(text(
        "SELECT * FROM ai_profile_draft_field WHERE draft_id=:id AND confirmation_status NOT IN ('deleted','rejected') ORDER BY id"
    ), {"id": draft.draft_id})).mappings().all()]
    baseline = await _revision_fields(db, baseline_id)
    if _payload_hash(preview_fields(fields_rows, baseline)) != envelope.get("fields_digest"):
        raise ValueError("DRAFT_VERSION_CONFLICT")
    keys = {row["field_key"] for row in fields_rows}
    # 这是已校验冻结快照的整份确认，不调用旧逐条确认 API 或旧发布/再生成链。
    fields = tuple(replace(field, confirmation_status="confirmed") for field in draft.fields if field.field_key in keys)
    if not fields:
        raise LookupError("PREVIEW_NOT_READY")
    revision = await insert_immutable_profile_revision(db, user_id, replace(draft, session_id=None), fields,
                                                       "user_profile" if draft.subject == "personal" else "user_partner_preference")
    published = await increment_revision_and_enqueue(db, user_id,
        RevisionKind.PROFILE if draft.subject == "personal" else RevisionKind.PREFERENCE,
        revision.changed_field_keys, "ai_profile_published", priority=40,
        payload_extra={"published_revision_id": revision.revision_id, "subject": draft.subject, "consent_snapshot": consent})
    await db.execute(text("UPDATE ai_profile_revision SET source_revision_json=:source WHERE id=:id"),
                     {"source": _dump(published.as_dict()), "id": revision.revision_id})
    # 既有 revision writer 不复制六维；在同事务按字段键补齐，供后续增量覆盖度使用。
    await db.execute(text(
        "UPDATE ai_profile_revision_field r JOIN ai_profile_draft_field d ON d.field_key=r.field_key AND d.draft_id=:draft "
        "SET r.profile_dimension=d.profile_dimension WHERE r.revision_id=:revision"
    ), {"draft": draft.draft_id, "revision": revision.revision_id})
    summary = _dump(envelope["narrative"])
    await db.execute(text(
        "INSERT INTO ai_profile_summary (draft_id,revision_id,user_id,subject,summary_text,status,content_hash) "
        "VALUES (:draft,:revision,:user,:subject,:summary,'confirmed',:hash)"
    ), {"draft": draft.draft_id, "revision": revision.revision_id, "user": user_id, "subject": draft.subject,
        "summary": summary, "hash": hashlib.sha256(summary.encode()).hexdigest()})
    from app.services.ai.continuous_memory import forward_continuous_confirmation_to_memory
    await forward_continuous_confirmation_to_memory(
        db, draft, fields, revision_id=revision.revision_id, revision_no=revision.revision_no,
        source_revision=published, consent_snapshot=consent,
        idempotency_key=f"continuous-confirm:{preview_id}", previous_fields=baseline,
    )
    from app.schemas.ai_common import ProjectionKind
    from app.services.ai.features import invalidate_projection
    from app.services.ai.memory.projections import MemoryProjectionService
    from app.services.ai.projection_status import KIND_FOR_SUBJECT, SqlProjectionStatusRepository, mark_pending
    from app.services.derivation_outbox import _mark_derived_results_stale
    await MemoryProjectionService(db).invalidate_for_subject(user_id, draft.subject)
    status_repository = SqlProjectionStatusRepository(db)
    for kind in KIND_FOR_SUBJECT[draft.subject]:
        await invalidate_projection(db, user_id, "continuous_profile_confirmed", published, ProjectionKind(kind))
        await mark_pending(repo=status_repository, user_id=user_id, kind=kind)
    await _mark_derived_results_stale(db, user_id, strict=True)
    task = await enqueue_cleanup_or_projection_task(db, user_id, revision,
        f"continuous-confirm:{preview_id}", _payload_hash(payload), published, consent)
    response = {"task_id": task.task_id, "status": str(task.status.value if hasattr(task.status, "value") else task.status),
                "replayed": False, "revision_id": revision.revision_id, "revision_no": revision.revision_no,
                "subject": draft.subject, "field_count": len(fields), "narrative_task_id": None}
    updated_meta = {**meta, "continuous": {**meta["continuous"], "confirmation": response}}
    await db.execute(text("UPDATE ai_profile_draft SET consent_snapshot_json=:meta WHERE draft_id=:id"),
                     {"meta": _dump(updated_meta), "id": draft.draft_id})
    await db.execute(text("UPDATE ai_profile_draft_field SET confirmation_status='confirmed' WHERE draft_id=:id AND confirmation_status NOT IN ('deleted','rejected')"), {"id": draft.draft_id})
    await db.execute(text("UPDATE ai_profile_preview SET status='confirmed' WHERE preview_id=:id"), {"id": preview_id})
    await _remember(db, user_id, "continuous-confirm", idempotency_key, payload, response)
    return response
