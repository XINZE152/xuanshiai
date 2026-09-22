"""成稿反哺资料卡开放文本草稿。

旁挂在 personal 已发布 revision + confirmed narrative 之后。生成走通用
``ai_task``（task_type=profile_card_summarize），确认写入走资料 PATCH 空补。
不改抽取、发布门槛或墨相 WS。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.profile_tags import ALL_TAG_OPTIONS, MAX_PERSONAL_TAGS
from app.schemas.ai_profile import ProfileSubject
from app.schemas.ai_profile_card import (
    ProfileCardApplyRequest,
    ProfileCardDraftFields,
    ProfileCardDraftRead,
)
from app.schemas.auth import ProfileUpdateRequest, QA_QUESTION_TEXTS
from app.services.ai.audit import emit_ai_metric
from app.services.ai.base import AITaskContext
from app.services.ai.gateway import AIGateway
from app.services.ai.profile import (
    AIConsentRequired,
    AIInputError,
    PROFILE_CONSENT_SCOPE,
    PROFILE_POLICY_REVISION,
    _consent_snapshot,
    _load_latest_consent,
    _load_revision_fields,
    _load_revision_vector,
    load_published_narrative,
)
from app.services.ai.prompts.profile_card_summarize import (
    PROFILE_CARD_PROMPT_VERSION,
    PROFILE_CARD_SCHEMA_VERSION,
    looks_like_contact,
)
from app.services.ai.prompts.profile_narrative import serialize_fields_for_prompt
from app.services.ai.tasks import AiTaskRecord, TaskError, enqueue_task, fail_task
from app.services.content_filter import moderate_text
from app.services.profile import get_profile, update_profile
from app.services.revisions import RevisionVector

logger = logging.getLogger(__name__)

PROFILE_CARD_SUMMARIZE_TASK_TYPE = "profile_card_summarize"
PROFILE_CARD_DAILY_LIMIT = 5
_READABLE_STATUSES = frozenset({"queued", "running", "ready", "partial"})
# applied 也可再 apply：采用后用户仍可对同一草稿发起 replace_existing 覆盖，
# 重复并发由 expected_revision（每次 apply 后 +1）与幂等键回放守卫。
_APPLYABLE_STATUSES = frozenset({"ready", "partial", "applied"})
_FACT_KEYS = frozenset(
    {
        "height",
        "education",
        "income",
        "occupation",
        "city",
        "education_level",
        "city_code",
        "weight",
        "birthday",
        "age",
    }
)


class ProfileCardDraftNotFound(Exception):
    code = "PROFILE_CARD_DRAFT_NOT_FOUND"
    status_code = 404
    retryable = False

    def __init__(self) -> None:
        super().__init__("资料卡草稿不存在")
        self.message = "资料卡草稿不存在"


class ProfileCardQuotaExceeded(Exception):
    code = "AI_QUOTA_EXCEEDED"
    status_code = 429
    retryable = True

    def __init__(self) -> None:
        super().__init__("今日资料草稿生成次数已达上限")
        self.message = "今日资料草稿生成次数已达上限"


class ProfileCardVersionConflict(Exception):
    code = "DRAFT_VERSION_CONFLICT"
    status_code = 409
    retryable = False

    def __init__(self) -> None:
        super().__init__("草稿版本已变化，请刷新后重试")
        self.message = "草稿版本已变化，请刷新后重试"


@dataclass(frozen=True)
class ProfileCardTaskSubmission:
    task: AiTaskRecord
    replayed: bool = False


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _maybe_json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _hash_summarize_request(user_id: int, revision_id: int, force: bool) -> str:
    payload = json.dumps(
        {
            "user_id": user_id,
            "revision_id": revision_id,
            "force": bool(force),
            "type": PROFILE_CARD_SUMMARIZE_TASK_TYPE,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _hash_apply_request(user_id: int, body: ProfileCardApplyRequest) -> str:
    payload = json.dumps(
        body.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{user_id}:{payload}".encode()).hexdigest()


async def _first_row(result: Any) -> dict[str, Any] | None:
    mappings = result.mappings()
    return mappings.first()


def _empty_fields() -> dict[str, Any]:
    return ProfileCardDraftFields().model_dump()


def _draft_read(row: dict[str, Any]) -> ProfileCardDraftRead:
    raw_fields = _maybe_json(row.get("fields_json")) or {}
    if not isinstance(raw_fields, dict):
        raw_fields = {}
    return ProfileCardDraftRead(
        draft_id=str(row["draft_id"]),
        status=str(row.get("status") or "queued"),
        expected_revision=int(row.get("expected_revision") or 1),
        source_revision_id=(
            int(row["source_revision_id"]) if row.get("source_revision_id") else None
        ),
        prompt_version=str(row["prompt_version"]) if row.get("prompt_version") else None,
        schema_version=str(row["schema_version"]) if row.get("schema_version") else None,
        fields=ProfileCardDraftFields.model_validate(raw_fields),
        task_id=str(row["task_id"]) if row.get("task_id") else None,
        generated_at=row.get("generated_at"),
        applied_at=row.get("applied_at"),
        applied_meta=_maybe_json(row.get("applied_meta")),
    )


async def _require_consent(db: AsyncSession, user_id: int) -> dict[str, Any]:
    consent = await _load_latest_consent(db, user_id, PROFILE_CONSENT_SCOPE)
    if consent is None:
        raise AIConsentRequired()
    return _consent_snapshot(consent)


async def _load_confirmed_personal_source(
    db: AsyncSession, user_id: int
) -> tuple[int, dict[str, Any], tuple[dict[str, Any], ...], str | None]:
    revision_result = await db.execute(
        text(
            "SELECT id FROM ai_profile_revision "
            "WHERE user_id = :user_id AND subject = :subject "
            "ORDER BY revision_no DESC, id DESC LIMIT 1"
        ),
        {"user_id": user_id, "subject": ProfileSubject.PERSONAL.value},
    )
    revision_row = await _first_row(revision_result)
    if revision_row is None:
        raise AIInputError("请先确认画像成稿")
    revision_id = int(revision_row["id"])
    narrative = await load_published_narrative(
        db, user_id, ProfileSubject.PERSONAL.value
    )
    status = str((narrative or {}).get("status") or "")
    data = (narrative or {}).get("data") if narrative else None
    if status != "confirmed" or not isinstance(data, dict):
        raise AIInputError("请先确认画像成稿")
    field_rows = await _load_revision_fields(db, revision_id)
    if not field_rows:
        raise AIInputError("请先确认画像成稿")
    partner = await load_published_narrative(
        db, user_id, ProfileSubject.IDEAL_PARTNER.value
    )
    partner_summary = None
    if partner and str(partner.get("status") or "") in {"confirmed", "published"}:
        pdata = partner.get("data") or {}
        if isinstance(pdata, dict):
            partner_summary = str(pdata.get("insight") or pdata.get("conclusion") or "").strip() or None
    return revision_id, data, serialize_fields_for_prompt(field_rows), partner_summary


async def _count_daily_tasks(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        text(
            "SELECT COUNT(*) AS n FROM ai_task "
            "WHERE owner_user_id = :user_id AND task_type = :task_type "
            "AND created_at > UTC_TIMESTAMP() - INTERVAL 24 HOUR"
        ),
        {"user_id": user_id, "task_type": PROFILE_CARD_SUMMARIZE_TASK_TYPE},
    )
    row = await _first_row(result)
    return int((row or {}).get("n") or 0)


async def _latest_card_draft(
    db: AsyncSession, user_id: int, statuses: tuple[str, ...]
) -> dict[str, Any] | None:
    placeholders = ", ".join(f":st{i}" for i in range(len(statuses)))
    params: dict[str, Any] = {"user_id": user_id}
    for index, status in enumerate(statuses):
        params[f"st{index}"] = status
    result = await db.execute(
        text(
            "SELECT draft_id, user_id, task_id, source_revision_id, status, "
            "expected_revision, fields_json, prompt_version, schema_version, "
            "applied_meta, last_operation_idempotency_key, "
            "last_operation_request_digest, last_operation_response_json, "
            "generated_at, applied_at, created_at, updated_at "
            "FROM ai_profile_card_draft "
            f"WHERE user_id = :user_id AND status IN ({placeholders}) "
            "ORDER BY updated_at DESC LIMIT 1"
        ),
        params,
    )
    return await _first_row(result)


async def _latest_open_draft(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    return await _latest_card_draft(
        db, user_id, ("queued", "running", "ready", "partial")
    )


async def _latest_readable_draft(
    db: AsyncSession, user_id: int
) -> dict[str, Any] | None:
    return await _latest_card_draft(
        db, user_id, ("queued", "running", "ready", "partial", "applied")
    )


async def request_profile_card_summarize(
    db: AsyncSession,
    user_id: int,
    *,
    force: bool,
    idempotency_key: str,
) -> ProfileCardTaskSubmission:
    consent = await _require_consent(db, user_id)
    revision_id, _narrative, _fields, _partner = await _load_confirmed_personal_source(
        db, user_id
    )
    request_hash = _hash_summarize_request(user_id, revision_id, force)
    existing = await db.execute(
        text(
            "SELECT id, task_id, owner_user_id, task_type, scene, idempotency_key, "
            "request_digest, status, stage, progress_percent, attempt_count, "
            "max_attempts, next_run_at, lease_owner, lease_until, "
            "consent_snapshot_json, source_revision_json, payload_summary, "
            "error_code, error_message, result_ref, created_at, updated_at, "
            "started_at, finished_at "
            "FROM ai_task WHERE owner_user_id = :user_id AND task_type = :task_type "
            "AND idempotency_key = :idempotency_key LIMIT 1"
        ),
        {
            "user_id": user_id,
            "task_type": PROFILE_CARD_SUMMARIZE_TASK_TYPE,
            "idempotency_key": idempotency_key,
        },
    )
    existing_row = await _first_row(existing)
    if existing_row is not None:
        record = AiTaskRecord.from_row(existing_row)
        if record.request_digest != request_hash:
            raise TaskError(
                code="TASK_IDEMPOTENCY_CONFLICT",
                message="Idempotency-Key 已用于不同请求内容",
                status_code=409,
            )
        return ProfileCardTaskSubmission(task=record, replayed=True)

    ready = await _latest_open_draft(db, user_id)
    if (
        not force
        and ready is not None
        and str(ready.get("status")) in {"ready", "partial"}
        and int(ready.get("source_revision_id") or 0) == revision_id
        and ready.get("task_id")
    ):
        task_row = await db.execute(
            text(
                "SELECT id, task_id, owner_user_id, task_type, scene, idempotency_key, "
                "request_digest, status, stage, progress_percent, attempt_count, "
                "max_attempts, next_run_at, lease_owner, lease_until, "
                "consent_snapshot_json, source_revision_json, payload_summary, "
                "error_code, error_message, result_ref, created_at, updated_at, "
                "started_at, finished_at FROM ai_task WHERE task_id = :task_id LIMIT 1"
            ),
            {"task_id": str(ready["task_id"])},
        )
        replay_row = await _first_row(task_row)
        if replay_row is not None:
            emit_ai_metric("success", 1, {"scene": PROFILE_CARD_SUMMARIZE_TASK_TYPE, "reason": "replay_ready"})
            return ProfileCardTaskSubmission(
                task=AiTaskRecord.from_row(replay_row), replayed=True
            )

    if await _count_daily_tasks(db, user_id) >= PROFILE_CARD_DAILY_LIMIT:
        emit_ai_metric("quota", 1, {"scene": PROFILE_CARD_SUMMARIZE_TASK_TYPE})
        raise ProfileCardQuotaExceeded()

    vector = await _load_revision_vector(db, user_id)
    task = await enqueue_task(
        db=db,
        owner_user_id=user_id,
        task_type=PROFILE_CARD_SUMMARIZE_TASK_TYPE,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        revisions=vector,
        consent=consent,
    )
    draft_id = uuid.uuid4().hex
    await db.execute(
        text(
            "INSERT INTO ai_profile_card_draft "
            "(draft_id, user_id, task_id, source_revision_id, status, "
            " expected_revision, fields_json, prompt_version, schema_version, "
            " created_at, updated_at) "
            "VALUES (:draft_id, :user_id, :task_id, :source_revision_id, 'queued', "
            " 1, :fields_json, :prompt_version, :schema_version, "
            " UTC_TIMESTAMP(), UTC_TIMESTAMP())"
        ),
        {
            "draft_id": draft_id,
            "user_id": user_id,
            "task_id": task.task_id,
            "source_revision_id": revision_id,
            "fields_json": json.dumps(_empty_fields(), ensure_ascii=False),
            "prompt_version": PROFILE_CARD_PROMPT_VERSION,
            "schema_version": PROFILE_CARD_SCHEMA_VERSION,
        },
    )
    await db.execute(
        text(
            "UPDATE ai_task SET payload_summary = :payload_summary, "
            "source_revision_json = :source_revision_json, "
            "consent_snapshot_json = :consent_snapshot_json, "
            "updated_at = UTC_TIMESTAMP() WHERE task_id = :task_id"
        ),
        {
            "payload_summary": json.dumps(
                {
                    "user_id": user_id,
                    "draft_id": draft_id,
                    "published_revision_id": revision_id,
                    "subject": ProfileSubject.PERSONAL.value,
                    "force": bool(force),
                },
                ensure_ascii=False,
            ),
            "source_revision_json": json.dumps(vector.as_dict(), ensure_ascii=False),
            "consent_snapshot_json": json.dumps(consent, ensure_ascii=False),
            "task_id": task.task_id,
        },
    )
    await db.flush()
    emit_ai_metric("success", 1, {"scene": PROFILE_CARD_SUMMARIZE_TASK_TYPE, "reason": "enqueued"})
    return ProfileCardTaskSubmission(task=task, replayed=False)


async def load_profile_card_draft(
    db: AsyncSession, user_id: int
) -> ProfileCardDraftRead:
    await _require_consent(db, user_id)
    row = await _latest_open_draft(db, user_id)
    if row is None:
        raise ProfileCardDraftNotFound()
    return _draft_read(row)


def _clip(value: str, limit: int) -> str:
    text_value = str(value or "").strip()
    if looks_like_contact(text_value):
        return ""
    return text_value[:limit]


def _intersect_tags(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw]
    elif isinstance(raw, str) and raw.strip():
        values = [raw.strip()]
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        if item in ALL_TAG_OPTIONS and item not in seen:
            seen.add(item)
            result.append(item)
        if len(result) >= MAX_PERSONAL_TAGS:
            break
    return result


def _field_payload(raw: Any, *, as_candidates: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    value = _clip(str(raw.get("value") or ""), 500 if not as_candidates else 300)
    candidates = _intersect_tags(raw.get("candidates") or [])
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    source_ref = str(raw.get("source_ref") or "").strip() or None
    if as_candidates:
        return {
            "value": "",
            "candidates": candidates,
            "confidence": confidence if candidates else 0.0,
            "source_ref": source_ref,
        }
    if not value:
        confidence = 0.0
    return {
        "value": value,
        "candidates": [],
        "confidence": confidence,
        "source_ref": source_ref,
    }


async def _moderate_blob(db: AsyncSession, blob: str) -> str | None:
    if not blob.strip():
        return blob
    decision = await moderate_text(db, blob, field="资料卡草稿")
    if decision.action == "reject":
        return None
    if decision.action == "replace":
        return decision.display_content
    return blob


async def generate_profile_card_summarize_handler(
    db: AsyncSession, task: AiTaskRecord, worker_id: str
) -> tuple[str, RevisionVector] | None:
    payload = task.payload_summary or {}
    user_id = payload.get("user_id")
    draft_id = payload.get("draft_id")
    published_revision_id = payload.get("published_revision_id")
    if not user_id or not draft_id or not published_revision_id:
        await fail_task(
            db, task.task_id, worker_id, error_code="AI_INPUT_INVALID", retryable=False
        )
        return None
    user_id_int = int(user_id)
    revision_id_int = int(published_revision_id)
    await db.execute(
        text(
            "UPDATE ai_profile_card_draft SET status = 'running', "
            "updated_at = UTC_TIMESTAMP() "
            "WHERE draft_id = :draft_id AND user_id = :user_id"
        ),
        {"draft_id": str(draft_id), "user_id": user_id_int},
    )
    try:
        consent = await _require_consent(db, user_id_int)
        _rev, narrative, fields, partner_summary = await _load_confirmed_personal_source(
            db, user_id_int
        )
    except (AIConsentRequired, AIInputError):
        await db.execute(
            text(
                "UPDATE ai_profile_card_draft SET status = 'failed', "
                "updated_at = UTC_TIMESTAMP() WHERE draft_id = :draft_id"
            ),
            {"draft_id": str(draft_id)},
        )
        await fail_task(
            db, task.task_id, worker_id, error_code="AI_INPUT_INVALID", retryable=False
        )
        return None

    input_blob = "\n".join(
        str(item.get("display_value") or "") for item in fields
    ) + "\n" + json.dumps(narrative, ensure_ascii=False)
    moderated_input = await _moderate_blob(db, input_blob)
    if moderated_input is None:
        emit_ai_metric("moderation", 1, {"scene": PROFILE_CARD_SUMMARIZE_TASK_TYPE, "stage": "input"})
        await db.execute(
            text(
                "UPDATE ai_profile_card_draft SET status = 'failed', "
                "updated_at = UTC_TIMESTAMP() WHERE draft_id = :draft_id"
            ),
            {"draft_id": str(draft_id)},
        )
        await fail_task(
            db, task.task_id, worker_id, error_code="AI_POLICY_DENIED", retryable=False
        )
        return None

    source_revision = task.source_revision_json or {}
    context = AITaskContext(
        task_id=task.task_id,
        request_id=uuid.uuid4().hex,
        scene=PROFILE_CARD_SUMMARIZE_TASK_TYPE,
        provider=settings.ai_provider_name,
        model=settings.ai_model_name,
        prompt_version=PROFILE_CARD_PROMPT_VERSION,
        schema_version=PROFILE_CARD_SCHEMA_VERSION,
        input_revision=source_revision if isinstance(source_revision, dict) else {},
        policy_revision=consent.get("policy_revision") or PROFILE_POLICY_REVISION,
    )
    from app.services.ai.base import ProfileCardSummarizeRequest

    request = ProfileCardSummarizeRequest(
        personal_fields=fields,
        narrative=narrative,
        ideal_partner_summary=partner_summary,
        consent_version=str(consent.get("version") or ""),
        policy_revision=str(consent.get("policy_revision") or PROFILE_POLICY_REVISION),
    )
    gateway = AIGateway(timeout_seconds=settings.ai_gateway_timeout_seconds)
    outcome = await gateway.generate_profile_card_draft(context, request)
    if outcome.result is None:
        await db.execute(
            text(
                "UPDATE ai_profile_card_draft SET status = 'failed', "
                "updated_at = UTC_TIMESTAMP() WHERE draft_id = :draft_id"
            ),
            {"draft_id": str(draft_id)},
        )
        await fail_task(
            db,
            task.task_id,
            worker_id,
            error_code=outcome.error_code or "AI_TEMPORARILY_UNAVAILABLE",
            retryable=outcome.retryable,
        )
        emit_ai_metric("fail", 1, {"scene": PROFILE_CARD_SUMMARIZE_TASK_TYPE})
        return None

    raw = outcome.result.model_dump()
    fields_json = {
        "self_intro": _field_payload(raw.get("self_intro")),
        "qa_1_partner": _field_payload(raw.get("qa_1_partner")),
        "qa_3_love": _field_payload(raw.get("qa_3_love")),
        "qa_2_sports_candidates": _field_payload(
            raw.get("qa_2_sports_candidates"), as_candidates=True
        ),
        "interest_tag_candidates": _field_payload(
            raw.get("interest_tag_candidates"), as_candidates=True
        ),
    }
    output_blob = json.dumps(fields_json, ensure_ascii=False)
    moderated_output = await _moderate_blob(db, output_blob)
    if moderated_output is None:
        emit_ai_metric("moderation", 1, {"scene": PROFILE_CARD_SUMMARIZE_TASK_TYPE, "stage": "output"})
        await db.execute(
            text(
                "UPDATE ai_profile_card_draft SET status = 'failed', "
                "fields_json = :fields_json, updated_at = UTC_TIMESTAMP() "
                "WHERE draft_id = :draft_id"
            ),
            {
                "draft_id": str(draft_id),
                "fields_json": json.dumps(_empty_fields(), ensure_ascii=False),
            },
        )
        await fail_task(
            db, task.task_id, worker_id, error_code="AI_POLICY_DENIED", retryable=False
        )
        return None
    has_any = any(
        (item.get("value") or item.get("candidates"))
        for item in fields_json.values()
    )
    status = "ready" if has_any else "failed"
    await db.execute(
        text(
            "UPDATE ai_profile_card_draft SET status = :status, "
            "fields_json = :fields_json, prompt_version = :prompt_version, "
            "schema_version = :schema_version, model_name = :model_name, "
            "generated_at = UTC_TIMESTAMP(), updated_at = UTC_TIMESTAMP() "
            "WHERE draft_id = :draft_id AND user_id = :user_id"
        ),
        {
            "status": status,
            "fields_json": json.dumps(fields_json, ensure_ascii=False),
            "prompt_version": PROFILE_CARD_PROMPT_VERSION,
            "schema_version": PROFILE_CARD_SCHEMA_VERSION,
            "model_name": settings.ai_model_name,
            "draft_id": str(draft_id),
            "user_id": user_id_int,
        },
    )
    if status == "failed":
        await fail_task(
            db, task.task_id, worker_id, error_code="AI_INPUT_INVALID", retryable=False
        )
        return None
    revisions = (
        RevisionVector(**source_revision)
        if isinstance(source_revision, dict)
        else RevisionVector()
    )
    emit_ai_metric("success", 1, {"scene": PROFILE_CARD_SUMMARIZE_TASK_TYPE, "reason": "ready"})
    return f"profile-card:{user_id_int}:{revision_id_int}", revisions


async def apply_profile_card_draft(
    db: AsyncSession,
    user_id: int,
    body: ProfileCardApplyRequest,
    idempotency_key: str,
) -> dict[str, Any]:
    await _require_consent(db, user_id)
    row = await _latest_readable_draft(db, user_id)
    if row is None:
        raise ProfileCardDraftNotFound()
    request_hash = _hash_apply_request(user_id, body)
    if (
        str(row.get("last_operation_idempotency_key") or "") == idempotency_key
        and str(row.get("last_operation_request_digest") or "") == request_hash
        and str(row.get("status")) == "applied"
    ):
        replay = _maybe_json(row.get("last_operation_response_json")) or {}
        replay["replayed"] = True
        return replay
    if str(row.get("status")) not in _APPLYABLE_STATUSES:
        raise AIInputError("当前草稿不可写入资料卡")
    if int(row.get("expected_revision") or 0) != int(body.expected_revision):
        raise ProfileCardVersionConflict()

    accepted = body.accepted
    written: list[str] = []
    skipped: list[str] = []
    patch: dict[str, Any] = {}
    current = await get_profile(db, user_id)

    if accepted.self_intro is not None:
        intro = accepted.self_intro.strip()
        if intro:
            decision = await moderate_text(db, intro, field="自我介绍")
            if decision.action == "reject":
                raise AIInputError("自我介绍内容不适合公开展示，请修改后重试")
            if decision.action == "replace":
                intro = decision.display_content
            existing_intro = str(current.get("self_intro") or "").strip()
            if existing_intro and not body.replace_existing.self_intro:
                skipped.append("self_intro")
            else:
                patch["self_intro"] = intro
                written.append("self_intro")
        else:
            skipped.append("self_intro")

    if accepted.qa_answers:
        moderated_qa: list[dict[str, Any]] = []
        existing_qa = {
            int(item["question_id"]): item
            for item in (current.get("qa_answers") or [])
            if isinstance(item, dict) and item.get("question_id") is not None
        }
        for item in accepted.qa_answers:
            answer = item.answer.strip()
            if not answer:
                continue
            decision = await moderate_text(db, answer, field="关于我问答")
            if decision.action == "reject":
                raise AIInputError("关于我问答内容不适合公开展示，请修改后重试")
            if decision.action == "replace":
                answer = decision.display_content
            moderated_qa.append(
                {
                    "question_id": int(item.question_id),
                    "question": item.question or QA_QUESTION_TEXTS[int(item.question_id)],
                    "answer": answer[:300],
                }
            )
        merged = dict(existing_qa)
        for item in moderated_qa:
            qid = int(item["question_id"])
            existing_answer = str((merged.get(qid) or {}).get("answer") or "").strip()
            if existing_answer and qid != 2:
                skipped.append(f"qa_{qid}")
                continue
            merged[qid] = item
            written.append(f"qa_{qid}")
        patch["qa_answers"] = [merged[key] for key in sorted(merged)]

    if accepted.personal_tags is not None:
        incoming = [tag for tag in accepted.personal_tags if tag in ALL_TAG_OPTIONS]
        existing_tags = list(current.get("personal_tags") or [])
        merged_tags: list[str] = []
        for tag in existing_tags + incoming:
            if tag not in merged_tags:
                merged_tags.append(tag)
            if len(merged_tags) >= MAX_PERSONAL_TAGS:
                break
        patch["personal_tags"] = merged_tags
        written.append("personal_tags")

    discarded = [key for key in body.rejected if key in _FACT_KEYS]
    if discarded:
        skipped.extend(discarded)

    applied_meta = {
        "ai_generated": [key for key in written],
        "user_edited": True,
        "label": "AI 生成后经用户修改",
    }
    response = {
        "status": "applied",
        "replayed": False,
        "written_fields": written,
        "skipped_fields": skipped,
        "profile": None,
    }
    if patch:
        updated = await update_profile(db, user_id, ProfileUpdateRequest(**patch))
        response["profile"] = updated
    else:
        response["profile"] = current
        response["profile"] = current

    await db.execute(
        text(
            "UPDATE ai_profile_card_draft SET status = 'applied', "
            "applied_at = UTC_TIMESTAMP(), applied_meta = :applied_meta, "
            "last_operation_idempotency_key = :idempotency_key, "
            "last_operation_request_digest = :request_digest, "
            "last_operation_response_json = :response_json, "
            "expected_revision = expected_revision + 1, "
            "updated_at = UTC_TIMESTAMP() "
            "WHERE draft_id = :draft_id AND user_id = :user_id"
        ),
        {
            "applied_meta": json.dumps(applied_meta, ensure_ascii=False),
            "idempotency_key": idempotency_key,
            "request_digest": request_hash,
            "response_json": json.dumps(response, ensure_ascii=False, default=str),
            "draft_id": str(row["draft_id"]),
            "user_id": user_id,
        },
    )
    await db.flush()
    return response
