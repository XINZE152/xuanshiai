"""continuous_v2 确认稿到 Memory 的事务内转发。

这个适配层只处理已经通过整份预览确认的冻结字段，不参与候选生成，也不
把 AI 候选直接当作 confirmed Claim。所有写入仍经 ``MemoryService``，而
``MemoryLedger`` 会在同一调用方事务中物化视图并登记既有 derivation
outbox；本模块不 commit、不启动 worker。

调用约定（由 continuous confirm 事务调用）：

``forward_continuous_confirmation_to_memory(
    db, draft, fields,
    revision_id=revision.revision_id,
    revision_no=revision.revision_no,
    source_revision=published_vector,
    consent_snapshot=consent,
    idempotency_key=f"continuous-confirm:{preview_id}",
)```

``fields`` 必须是服务端冻结的、最终要发布的 confirmed 字段；``draft.fields``
中的 ``deleted``/``rejected`` 字段用于计算删除墓碑。未确认字段会被拒绝，
不会被 shadow candidate 或本适配层升级为记忆确认。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.candidates import bucket_for_dimension, compute_candidate_content_hash
from app.services.ai.memory.policy import MemoryPolicy
from app.services.ai.memory.service import MemoryService

__all__ = [
    "ContinuousMemoryForwardError",
    "ContinuousMemoryForwardResult",
    "forward_continuous_confirmation_to_memory",
]


class ContinuousMemoryForwardError(ValueError):
    """冻结稿不满足 Memory 转发边界。"""


class _FieldLike(Protocol):
    field_key: str
    subject: str
    field_kind: str
    category: str | None
    content: str | None
    value: Any
    source_turn_ids: tuple[str, ...]
    source_span: str | None
    confidence: float
    consent_scope: str | None
    confirmation_status: str
    content_hash: str | None
    replaces_field_key: str | None
    profile_dimension: str | None


class _DraftLike(Protocol):
    draft_id: str
    owner_user_id: int
    subject: str
    schema_version: str
    fields: tuple[_FieldLike, ...]


@dataclass(frozen=True)
class ContinuousMemoryForwardResult:
    """本次确认稿在 Memory 内核产生的动作摘要（不含用户原文）。"""

    owner_user_id: int
    subject: str
    draft_id: str
    revision_id: int | None
    revision_no: int | None
    proposed: int = 0
    confirmed: int = 0
    corrected: int = 0
    suppressed: int = 0
    skipped: int = 0
    event_ids: tuple[str, ...] = ()

    @property
    def changed(self) -> int:
        return self.proposed + self.confirmed + self.corrected + self.suppressed


def _attr(field: Any, name: str, default: Any = None) -> Any:
    if isinstance(field, Mapping):
        return field.get(name, default)
    return getattr(field, name, default)


def _revision_dict(source_revision: Any) -> dict[str, Any]:
    if source_revision is None:
        return {}
    if hasattr(source_revision, "as_dict"):
        value = source_revision.as_dict()
    elif isinstance(source_revision, Mapping):
        value = dict(source_revision)
    else:
        raise ContinuousMemoryForwardError("source_revision must be a mapping or RevisionVector")
    return {str(key): value[key] for key in sorted(value)}


def _validate_consent(consent_snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not consent_snapshot:
        raise ContinuousMemoryForwardError("continuous confirmation requires consent snapshot")
    snapshot = dict(consent_snapshot)
    if str(snapshot.get("scope") or "") != "profile_text_extract":
        raise ContinuousMemoryForwardError("continuous memory requires profile_text_extract consent")
    for key in ("version", "policy_revision", "granted_at"):
        if not str(snapshot.get(key) or ""):
            raise ContinuousMemoryForwardError(f"consent snapshot missing {key}")
    snapshot["scope"] = "profile_text_extract"
    return snapshot


def _field_value(field: Any) -> Any:
    return _attr(field, "content") if _attr(field, "field_kind") == "entry" else _attr(field, "value")


def _field_identity(field: Any) -> str:
    subject = str(_attr(field, "subject") or "")
    field_kind = str(_attr(field, "field_kind") or "structured")
    field_key = _attr(field, "field_key")
    category = _attr(field, "category")
    if field_kind == "entry":
        content = str(_attr(field, "content") or "").strip()
        content_hash = _attr(field, "content_hash") or compute_candidate_content_hash(
            subject, "entry", None, category, None, content
        )
        return MemoryPolicy.candidate_identity("entry", None, category, content_hash)
    return MemoryPolicy.candidate_identity("structured", str(field_key), None, None)


def _field_dimension(field: Any) -> str | None:
    dimension = _attr(field, "profile_dimension")
    if dimension:
        return str(dimension)
    return bucket_for_dimension(
        str(_attr(field, "field_kind") or "structured"),
        str(_attr(field, "field_key") or "")
        if str(_attr(field, "field_kind") or "structured") == "structured"
        else None,
        _attr(field, "category"),
        _attr(field, "content"),
    )


def _canonical_key(field: Any) -> str:
    return MemoryPolicy.canonical_key(
        str(_attr(field, "subject") or ""),
        _field_dimension(field),
        _field_identity(field),
    )


def _source_quote(field: Any) -> str | None:
    value = str(_attr(field, "source_span") or "").strip()
    return value[:512] or None


def _source_turn_id(field: Any) -> str | None:
    ids = _attr(field, "source_turn_ids") or ()
    return str(ids[0]) if ids else None


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _same_value(row: Mapping[str, Any], field: Any) -> bool:
    return _json_value(row.get("value_json")) == _field_value(field)


def _action_key(base: str, field_key: str, action: str) -> str:
    digest = hashlib.sha256(f"{base}:{field_key}:{action}".encode("utf-8")).hexdigest()[:40]
    return f"continuous-memory:{action}:{digest}"


def _source_ref(
    draft: _DraftLike,
    *,
    revision_id: int | None,
    revision_no: int | None,
    source_revision: Mapping[str, Any],
) -> str:
    revision_part = str(revision_id if revision_id is not None else revision_no or "unknown")
    vector_digest = hashlib.sha256(
        json.dumps(dict(source_revision), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"continuous-v2:revision:{revision_part}:{draft.subject}:{vector_digest}"[:256]


def _ensure_field_contract(
    draft: _DraftLike,
    fields: tuple[Any, ...],
    *,
    require_confirmed: bool = True,
) -> None:
    if int(draft.owner_user_id) <= 0:
        raise ContinuousMemoryForwardError("draft owner is invalid")
    if draft.subject not in {"personal", "ideal_partner"}:
        raise ContinuousMemoryForwardError("unsupported continuous profile subject")
    if draft.schema_version != "profile-continuous-v2":
        raise ContinuousMemoryForwardError("memory forwarding requires continuous_v2 draft")
    seen: set[str] = set()
    for field in fields:
        subject = str(_attr(field, "subject") or "")
        field_key = str(_attr(field, "field_key") or "")
        if subject != draft.subject:
            raise ContinuousMemoryForwardError("continuous memory field crosses subject boundary")
        if field_key in seen:
            raise ContinuousMemoryForwardError("continuous memory contains duplicate field")
        seen.add(field_key)
        if require_confirmed and _attr(field, "confirmation_status") != "confirmed":
            raise ContinuousMemoryForwardError("only reviewed confirmed fields enter memory")
        if _attr(field, "consent_scope") not in (None, "profile_text_extract"):
            raise ContinuousMemoryForwardError("field consent scope is not profile_text_extract")
        kind = str(_attr(field, "field_kind") or "structured")
        if kind not in {"structured", "entry"}:
            raise ContinuousMemoryForwardError("unsupported continuous memory field kind")
        if kind == "entry" and not str(_attr(field, "content") or "").strip():
            raise ContinuousMemoryForwardError("confirmed entry is empty")


async def _read_claim_for_field(
    service: Any,
    *,
    owner_user_id: int,
    field: Any,
    canonical_key: str,
) -> Mapping[str, Any] | None:
    """Use the identity lookup shared by legacy Shadow Write when available.

    Existing claims may have been written with a dimension that differs from a
    freshly-derived draft dimension. Identity is the stable compatibility
    boundary; exact canonical lookup remains the fallback for test doubles or
    alternate MemoryService implementations.
    """
    finder = getattr(service, "find_claim_by_field_identity", None)
    if finder is not None:
        found = await finder(
            owner_user_id=owner_user_id,
            subject=str(_attr(field, "subject")),
            identity=_field_identity(field),
        )
        if found is not None:
            return found
    return await service.read_claim_by_canonical(
        owner_user_id=owner_user_id,
        subject=str(_attr(field, "subject")),
        namespace="moxiang",
        canonical_key=canonical_key,
    )


async def forward_continuous_confirmation_to_memory(
    db: AsyncSession,
    draft: _DraftLike,
    fields: tuple[_FieldLike, ...] | list[_FieldLike],
    *,
    revision_id: int | None = None,
    revision_no: int | None = None,
    source_revision: Any = None,
    consent_snapshot: Mapping[str, Any] | None = None,
    idempotency_key: str,
    memory_service: MemoryService | Any | None = None,
    previous_fields: tuple[_FieldLike, ...] | list[_FieldLike] | None = None,
) -> ContinuousMemoryForwardResult:
    """把 continuous_v2 冻结确认稿转发到 MemoryService。

    新字段使用 ``user_confirmed`` 来源先 propose、再 confirm；已有 proposed
    Claim 只在本次冻结确认边界内 confirm。基线继承且同值的 confirmed Claim
    幂等跳过；重新提供并审阅的同值事实也写新确认，不能沿用撤权前事件。
    变化调用既有 correct_claim。明确 deleted/rejected 字段调用 suppress_claim；
    基线存在但最终快照里已消失的字段（未确认稿删除后在 refresh 合并中随墓碑行
    被剔除）同样 suppress——否则正式稿已不含该条，旧记忆仍会被投影消费者使用。
    ``previous_fields`` 可传入基线正式 revision 的字段（对象或 mapping）；
    entry 内容修改会为旧 identity 建立删除墓碑，避免旧条目留在下游投影。

    本函数不 commit。调用方应在创建正式 revision、发布 outbox、summary 与
    本函数之间保持同一事务，并传入服务端刚校验过的 revision/consent 快照。
    """

    final_fields = tuple(fields)
    prior_fields = tuple(previous_fields or ())
    _ensure_field_contract(draft, final_fields)
    # Formal revision rows are confirmed by definition and may not carry the
    # draft-only confirmation_status column. Explicitly non-confirmed rows are
    # not eligible as a replacement baseline.
    prior_fields = tuple(
        field
        for field in prior_fields
        if _attr(field, "confirmation_status") in (None, "confirmed")
    )
    _ensure_field_contract(draft, prior_fields, require_confirmed=False)
    snapshot = _validate_consent(consent_snapshot)
    revision_vector = _revision_dict(source_revision)
    service = memory_service or MemoryService(db)
    base_key = f"{idempotency_key}:{draft.draft_id}:{revision_id or revision_no or 'na'}"
    source_ref = _source_ref(
        draft,
        revision_id=revision_id,
        revision_no=revision_no,
        source_revision=revision_vector,
    )

    active_by_key = {str(_attr(field, "field_key")): field for field in final_fields}
    prior_by_key = {str(_attr(field, "field_key")): field for field in prior_fields}
    inherited_identities = {_field_identity(field) for field in prior_fields}
    deleted_fields = tuple(
        field
        for field in draft.fields
        if _attr(field, "confirmation_status") in {"deleted", "rejected"}
        and str(_attr(field, "field_key")) not in active_by_key
    )
    replaced_fields: list[Any] = []
    for field in final_fields:
        if _attr(field, "field_kind") != "entry":
            continue
        old_key = _attr(field, "replaces_field_key")
        old = prior_by_key.get(str(old_key)) if old_key else prior_by_key.get(str(_attr(field, "field_key")))
        if old is not None and _field_identity(old) != _field_identity(field):
            replaced_fields.append(old)
    # 第三类：基线里有、最终快照里已经没有的字段。删除发生在未确认稿上（refresh
    # 合并时墓碑行被剔出新快照），确认时草稿已看不到 deleted/rejected 行，只靠
    # deleted_fields 会漏掉——正式稿不再含该条，旧 Memory 却会继续被投影消费。
    active_keys = set(active_by_key)
    tombstoned_keys = {
        str(_attr(field, "field_key")) for field in deleted_fields
    }
    replaced_keys = {
        str(_attr(field, "field_key")) for field in replaced_fields
    }
    # 与显式墓碑、被替换项按 field_key 去重：同一字段只发一次 suppress，
    # 幂等键 ``_action_key(base, key, "suppress")`` 也随之稳定。
    missing_fields = tuple(
        field
        for key, field in prior_by_key.items()
        if key not in active_keys
        and key not in replaced_keys
        and key not in tombstoned_keys
    )
    actions: list[tuple[str, Any, str]] = [
        ("deleted", field, "replace") for field in replaced_fields
    ] + [("active", field, "confirm") for field in final_fields] + [
        ("deleted", field, "suppress") for field in deleted_fields
    ] + [
        ("deleted", field, "suppress") for field in missing_fields
    ]

    proposed = confirmed = corrected = suppressed = skipped = 0
    event_ids: list[str] = []
    for kind, field, requested_action in actions:
        canonical_key = _canonical_key(field)
        claim = await _read_claim_for_field(
            service,
            owner_user_id=draft.owner_user_id,
            field=field,
            canonical_key=canonical_key,
        )
        if kind == "deleted":
            if claim is None:
                skipped += 1
                continue
            record = await service.suppress_claim(
                owner_user_id=draft.owner_user_id,
                claim_id=str(claim["claim_id"]),
                reason="continuous_v2 user deleted field",
                idempotency_key=_action_key(base_key, str(_attr(field, "field_key")), requested_action),
            )
            event_ids.append(str(record.event_id))
            suppressed += 1
            continue

        if claim is None:
            records = await service.propose(
                owner_user_id=draft.owner_user_id,
                subject=str(_attr(field, "subject")),
                canonical_key=canonical_key,
                dimension=_field_dimension(field),
                value=_field_value(field),
                confidence=max(0.0, min(1.0, float(_attr(field, "confidence") or 0.0))),
                source_kind="user_confirmed",
                fact_kind=(
                    "about_user"
                    if str(_attr(field, "subject")) == "personal"
                    else "partner_preference"
                ),
                source_quote=_source_quote(field),
                source_turn_id=_source_turn_id(field),
                source_ref=source_ref,
                idempotency_key=_action_key(base_key, str(_attr(field, "field_key")), "propose"),
            )
            proposed += 1
            event_ids.extend(str(record.event_id) for record in records)
            claim = await _read_claim_for_field(
                service,
                owner_user_id=draft.owner_user_id,
                field=field,
                canonical_key=canonical_key,
            )
            if claim is None:
                raise ContinuousMemoryForwardError("memory claim missing after confirmed propose")

        claim_id = str(claim["claim_id"])
        status = str(claim["status"])
        inherited_unchanged = _same_value(claim, field) and _field_identity(field) in inherited_identities
        if status == "confirmed" and inherited_unchanged:
            skipped += 1
            continue
        if status == "confirmed" or (status == "proposed" and not _same_value(claim, field)):
            record = await service.correct_claim(
                owner_user_id=draft.owner_user_id,
                claim_id=claim_id,
                expected_revision=int(claim["last_event_seq"]),
                value=_field_value(field),
                source_quote=_source_quote(field),
                idempotency_key=_action_key(base_key, str(_attr(field, "field_key")), "correct"),
            )
            event_ids.append(str(record.event_id))
            corrected += 1
            claim = await _read_claim_for_field(
                service, owner_user_id=draft.owner_user_id, field=field, canonical_key=canonical_key,
            )
            if claim is None:
                raise ContinuousMemoryForwardError("memory claim missing after reviewed correction")
            status = str(claim["status"])
        if status in {"proposed", "user_corrected"}:
            record = await service.confirm_claim(
                owner_user_id=draft.owner_user_id,
                claim_id=claim_id,
                expected_revision=int(claim["last_event_seq"]),
                importance=float(claim.get("importance") or 0.5),
                constraint_type=claim.get("constraint_type"),
                reviewed_revision_ref=source_ref,
                reviewed_value=_field_value(field),
                idempotency_key=_action_key(base_key, str(_attr(field, "field_key")), "confirm"),
            )
            event_ids.append(str(record.event_id))
            confirmed += 1
        else:
            # 删除/过期等终态不能由画像自动解除。
            skipped += 1

    _ = snapshot
    return ContinuousMemoryForwardResult(
        owner_user_id=draft.owner_user_id,
        subject=draft.subject,
        draft_id=draft.draft_id,
        revision_id=revision_id,
        revision_no=revision_no,
        proposed=proposed,
        confirmed=confirmed,
        corrected=corrected,
        suppressed=suppressed,
        skipped=skipped,
        event_ids=tuple(event_ids),
    )
