"""AI-CORE audit and observability: redaction, generation audit and metrics.

Task 12 keeps three hard promises from the unified plan §6.5/§6.6 and the
execution plan:

1. Sensitive values never cross the audit/log boundary.  ``redact_ai_log``
   drops keys from :data:`SENSITIVE_KEYS` (case-insensitive, recursively), the
   same key-allowlist discipline the Gateway already applies to provider
   messages.

2. ``record_generation_audit`` writes a minimal, replayable row into
   ``ai_generation_audit`` — request id, task id, scene, provider/model,
   prompt/schema version, source revisions, policy revision, status, error
   code, usage/cost presence, display eligibility and timestamps.  Raw prompts,
   original answers and raw provider responses never reach the row.  The
   frozen Task 5 table has no dedicated columns for ``status``/
   ``policy_revision``/``display_eligible``, so those controlled fields are
   persisted inside ``safety_result_json`` under an ``audit_meta`` block (the
   column is free-form JSON and the table comment only forbids raw
   prompt/response).  A failed audit write must never block business — it is
   caught and recorded as a local warning.

3. ``emit_ai_metric`` sinks the plan's operational metrics (queue age, lease
   reclaim, retry rate, schema invalid, provider 429/5xx, stale rate, fallback
   rate, deletion propagation, outbox/purge backlog) into an in-process
   registry plus a structured log line; backlog metrics over the configured
   threshold also raise a local warning.  Metric failures never raise.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# 脱敏（统一方案 §3.2 / §12.1 安全日志；与 gateway._PROVIDER_MESSAGE_SENSITIVE_KEYS
# 同一 key-allowlist 纪律）
# ----------------------------------------------------------------------

SENSITIVE_KEYS = frozenset({
    "prompt",
    "raw_response",
    "phone",
    "id_card",
    "precise_location",
    "raw_ip",
})


def _redact_value(key: str, value: Any) -> Any:
    """Recursively strip sensitive keys from nested mappings and lists."""
    if isinstance(value, Mapping):
        return {
            k: _redact_value(k, v)
            for k, v in value.items()
            if k.lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_redact_value("", item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value("", item) for item in value)
    return value


def redact_ai_log(fields: Mapping[str, object]) -> dict[str, object]:
    """Return a log-safe copy of ``fields`` with sensitive keys removed.

    The brief contract is verbatim: ``prompt``/``phone``/``id_card`` (and any
    other key in :data:`SENSITIVE_KEYS`) are dropped while non-sensitive keys
    such as ``task_id``/``request_id`` survive.  Nested mappings are scrubbed
    recursively with the same key-allowlist rule.
    """
    return {
        key: _redact_value(key, value)
        for key, value in fields.items()
        if key.lower() not in SENSITIVE_KEYS
    }


# ----------------------------------------------------------------------
# Generation audit（统一方案 §6.5，ai_generation_audit 表）
# ----------------------------------------------------------------------

#: Frozen Task 5 columns of ``ai_generation_audit``; the writer only ever
#: touches these columns so no schema migration is introduced.
_AUDIT_TABLE_COLUMNS = (
    "request_id",
    "task_id",
    "scene",
    "provider",
    "model",
    "prompt_version",
    "schema_version",
    "input_revision_json",
    "duration_ms",
    "token_usage_json",
    "cost",
    "safety_result_json",
    "error_code",
)


@dataclass(frozen=True)
class GenerationAuditEvent:
    """Minimal replayable record of one provider generation (Task 12).

    Only controlled metadata; never the prompt, original answers or the raw
    provider response.  ``usage_cost`` carries token/cost presence when the
    provider reports it (phase-1 mock reports none).
    """

    request_id: str
    task_id: str | None
    scene: str
    provider: str
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    input_revision: Mapping[str, int] = field(default_factory=dict)
    policy_revision: str | None = None
    status: str | None = None
    error_code: str | None = None
    usage_cost: Mapping[str, Any] | None = None
    display_eligible: bool = False
    safety_result: Mapping[str, Any] | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None


def _db_connect_params() -> dict[str, Any] | None:
    """Translate ``settings.database_url`` into synchronous pymysql params."""
    from urllib.parse import unquote, urlsplit

    url = settings.database_url.replace("mysql+aiomysql://", "mysql://", 1)
    parsed = urlsplit(url)
    if not (
        parsed.scheme == "mysql"
        and parsed.hostname
        and parsed.username
        and parsed.password is not None
        and parsed.port
    ):
        return None
    database = parsed.path.lstrip("/")
    if not database:
        return None
    return {
        "host": parsed.hostname,
        "port": parsed.port,
        "user": unquote(parsed.username),
        "password": unquote(parsed.password),
        "database": database,
        "charset": "utf8mb4",
    }


def _audit_row(event: GenerationAuditEvent) -> tuple[str, tuple[Any, ...]]:
    """Build the INSERT statement and its bound parameters for one event."""
    usage = event.usage_cost or {}
    safety_meta: dict[str, Any] = {
        "status": event.status,
        "policy_revision": event.policy_revision,
        "display_eligible": bool(event.display_eligible),
        "safety": event.safety_result,
    }
    values = (
        event.request_id,
        event.task_id,
        event.scene,
        event.provider,
        event.model,
        event.prompt_version,
        event.schema_version,
        json.dumps(dict(event.input_revision), ensure_ascii=False, sort_keys=True)
        if event.input_revision
        else None,
        event.duration_ms,
        json.dumps(usage, ensure_ascii=False, sort_keys=True) if usage else None,
        usage.get("cost"),
        json.dumps(safety_meta, ensure_ascii=False, sort_keys=True),
        event.error_code,
    )
    placeholders = ", ".join("%s" for _ in values)
    statement = (
        f"INSERT IGNORE INTO ai_generation_audit ({', '.join(_AUDIT_TABLE_COLUMNS)}) "
        f"VALUES ({placeholders})"
    )
    return statement, values


def _persist_audit_row_sync(event: GenerationAuditEvent) -> None:
    """Best-effort synchronous write; never raises.

    Runs inside the audit flusher thread (via :func:`asyncio.to_thread` from
    :func:`_audit_flusher_loop`) so the blocking ``pymysql`` calls never stall
    the event loop.  The per-thread connection is cached and ping-refreshed;
    on any failure it is discarded (next write reconnects) and the event is
    counted as lost — audit is best-effort and never blocks the business path.
    Raw prompts, original answers and raw provider responses are never part
    of the row.
    """
    global _audit_lost_count
    params = _db_connect_params()
    if params is None:
        logger.debug("ai_audit_skip_unparseable_db_url request_id=%s", event.request_id)
        return
    try:
        import pymysql

        statement, values = _audit_row(event)
        conn = _thread_local_connection(params)
        try:
            # 缓存连接可能被服务端 wait_timeout 掐断：先 ping 探活，失败
            # 就弃置重建（pymysql 已弃用 ping(reconnect=True) 参数）。
            conn.ping()
        except Exception:  # noqa: BLE001
            _discard_thread_connection()
            conn = _thread_local_connection(params)
        with conn.cursor() as cur:
            cur.execute(statement, values)
        conn.commit()
    except Exception:
        _discard_thread_connection()
        logger.warning(
            "ai_audit_write_failed request_id=%s error_code=%s",
            event.request_id,
            event.error_code,
            exc_info=True,
        )
        global _audit_lost_count
        _audit_lost_count += 1
        emit_ai_metric("audit_lost", 1, {"reason": "write_failed"})


_AUDIT_THREAD_LOCAL = threading.local()


def _thread_local_connection(params: dict[str, Any]):
    """Return (and cache) a per-thread pymysql connection."""
    import pymysql

    conn = getattr(_AUDIT_THREAD_LOCAL, "conn", None)
    if conn is None:
        conn = pymysql.connect(**params)
        _AUDIT_THREAD_LOCAL.conn = conn
    return conn


def _discard_thread_connection() -> None:
    conn = getattr(_AUDIT_THREAD_LOCAL, "conn", None)
    _AUDIT_THREAD_LOCAL.conn = None
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------
# Task 16：有界异步审计队列 + 后台 flusher
# ----------------------------------------------------------------------

#: 队列上限。满时丢最旧事件（保新），并计 audit_lost 指标——审计是
#: best-effort 旁路，绝不反压阻塞业务主链路。
_AUDIT_QUEUE_MAX = 2048
_AUDIT_QUEUE: "queue.SimpleQueue[GenerationAuditEvent]" = queue.SimpleQueue()
_AUDIT_QUEUE_LOCK = threading.Lock()
_AUDIT_QUEUE_SIZE = 0
#: flusher 批量上限与空队列等待时长。
_AUDIT_FLUSH_BATCH = 64
_AUDIT_FLUSH_IDLE_SECONDS = 2.0
_flusher_task: asyncio.Task[None] | None = None
_flusher_loop: asyncio.AbstractEventLoop | None = None
_audit_lost_count = 0


def _enqueue_audit_event(event: GenerationAuditEvent) -> bool:
    """Best-effort enqueue; drops the OLDEST queued event when full."""
    global _AUDIT_QUEUE_SIZE, _audit_lost_count
    with _AUDIT_QUEUE_LOCK:
        if _AUDIT_QUEUE_SIZE >= _AUDIT_QUEUE_MAX:
            # 丢最旧（SimpleQueue 无出队窥视，用 get_nowait 腾位）。
            try:
                _AUDIT_QUEUE.get_nowait()
            except queue.Empty:
                pass
            else:
                _AUDIT_QUEUE_SIZE -= 1
                _audit_lost_count += 1
                emit_ai_metric("audit_lost", 1, {"reason": "queue_full"})
        _AUDIT_QUEUE.put_nowait(event)
        _AUDIT_QUEUE_SIZE += 1
    return True


def _drain_audit_batch() -> list[GenerationAuditEvent]:
    """Pop up to _AUDIT_FLUSH_BATCH events; non-blocking."""
    global _AUDIT_QUEUE_SIZE
    batch: list[GenerationAuditEvent] = []
    while len(batch) < _AUDIT_FLUSH_BATCH:
        try:
            batch.append(_AUDIT_QUEUE.get_nowait())
        except queue.Empty:
            break
    with _AUDIT_QUEUE_LOCK:
        _AUDIT_QUEUE_SIZE = max(0, _AUDIT_QUEUE_SIZE - len(batch))
    return batch


async def _audit_flusher_loop() -> None:
    """Background flusher: batch-drain the queue and write via to_thread.

    取消时立即退出；剩余事件的排空由 :func:`shutdown_audit_flusher` 负责
    （在取消处理器内继续 await 会被外层取消二次打断，不可靠）。
    """
    while True:
        await asyncio.sleep(0.05 if not _AUDIT_QUEUE.empty() else _AUDIT_FLUSH_IDLE_SECONDS)
        batch = _drain_audit_batch()
        if not batch:
            continue
        for event in batch:
            try:
                await asyncio.to_thread(_persist_audit_row_sync, event)
            except Exception:  # noqa: BLE001 - persist never raises, belt & braces
                logger.warning(
                    "ai_audit_flusher_unhandled request_id=%s",
                    event.request_id,
                    exc_info=True,
                )
                global _audit_lost_count
                _audit_lost_count += 1
                emit_ai_metric("audit_lost", 1, {"reason": "flusher_unhandled"})


def _ensure_audit_flusher() -> None:
    """Start the flusher task on the running loop (idempotent, loop-aware)."""
    global _flusher_task, _flusher_loop
    if _flusher_task is not None and not _flusher_task.done():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if _flusher_loop is loop:
            return
        # 旧 loop 的残留任务（测试环境多 loop）：弃用并在新 loop 重启。
        _flusher_task = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _flusher_loop = loop
    _flusher_task = loop.create_task(_audit_flusher_loop())


async def shutdown_audit_flusher(timeout: float = 5.0) -> None:
    """Stop the flusher and drain remaining events (worker/API shutdown).

    取消 flusher 后在当前协程里把剩余队列写完（限时）。单条失败即放弃
    该条（计入丢失路径由 _persist_audit_row_sync 自己处理）。
    """
    global _flusher_task
    task = _flusher_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
    _flusher_task = None

    async def _drain_all() -> None:
        while True:
            batch = _drain_audit_batch()
            if not batch:
                return
            for event in batch:
                try:
                    await asyncio.to_thread(_persist_audit_row_sync, event)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - single-event failure is terminal
                    logger.warning(
                        "ai_audit_shutdown_drain_failed request_id=%s",
                        event.request_id,
                        exc_info=True,
                    )

    try:
        await asyncio.wait_for(_drain_all(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "ai_audit_shutdown_drain_timeout remaining=%d",
            audit_queue_depth(),
        )


def audit_queue_depth() -> int:
    """Current queued event count (metrics/runbook)."""
    with _AUDIT_QUEUE_LOCK:
        return _AUDIT_QUEUE_SIZE


def audit_lost_total() -> int:
    """Cumulative dropped/failed audit events since process start."""
    return _audit_lost_count


async def record_generation_audit(event: GenerationAuditEvent) -> None:
    """Record one generation audit event without blocking the event loop.

    Always logs the minimal non-sensitive metadata; then best-effort persists a
    row into ``ai_generation_audit`` via a bounded async queue drained by a
    background flusher task (Task 16).  The synchronous DB write is offloaded
    to a worker thread via :func:`asyncio.to_thread` so the blocking
    ``pymysql.connect`` call never stalls the event loop.  Raw prompts,
    original answers and raw provider responses are never part of the row.
    Queue overflow drops the oldest event and counts ``audit_lost``; any write
    failure is caught and recorded as a local warning so business keeps
    running.  When no flusher can be started (e.g. no running loop) the event
    is written inline via :func:`asyncio.to_thread` as before.
    """
    if not settings.ai_audit_enabled:
        return
    logger.info(
        "ai_audit request_id=%s task_id=%s scene=%s provider=%s model=%s "
        "prompt_version=%s schema_version=%s policy_revision=%s status=%s "
        "error_code=%s usage_reported=%s display_eligible=%s",
        event.request_id,
        event.task_id,
        event.scene,
        event.provider,
        event.model,
        event.prompt_version,
        event.schema_version,
        event.policy_revision,
        event.status,
        event.error_code,
        bool(event.usage_cost),
        event.display_eligible,
    )
    _ensure_audit_flusher()
    if _flusher_task is not None and not _flusher_task.done():
        _enqueue_audit_event(event)
        return
    try:
        await asyncio.to_thread(_persist_audit_row_sync, event)
    except Exception:
        logger.warning(
            "ai_audit_persist_unhandled request_id=%s",
            event.request_id,
            exc_info=True,
        )


# ----------------------------------------------------------------------
# 指标（统一方案 §6.5 / §12.2 运行指标；执行计划 §7 审计/发布检查点）
# ----------------------------------------------------------------------

#: 指标至少覆盖：queue age、lease 回收、重试率、schema invalid、Provider
#: 429/5xx、stale rate、fallback rate、撤回传播延迟、outbox 积压和清理积压。
#: 批次3 #24 追加：task_retry（单次进入 retry_wait 计数）与
#: task_retry_backlog（retry_wait 积压量，按 task_type 维度）。
#: 批次1 Task 2 追加：memory_state_ttl_cleanup_{success,failed,skipped}
#: （Memory State TTL 定时清理的成功/失败/重复执行节流）。
KNOWN_METRICS = frozenset({
    "queue_age",
    "lease_reclaimed",
    "retry_rate",
    "task_retry",
    "task_retry_backlog",
    "schema_invalid",
    "provider_429",
    "provider_5xx",
    "stale_rate",
    "fallback_rate",
    "deletion_propagation_seconds",
    "outbox_backlog",
    "purge_backlog",
    "voice_audio_cleanup_failed",
    "memory_state_ttl_cleanup_success",
    "memory_state_ttl_cleanup_failed",
    "memory_state_ttl_cleanup_skipped",
    # 批次三 Task 11：Redis pub/sub 不可用时 WebSocket 退回轮询的计数。
    "websocket_fallback",
    # 批次四 Task 16：审计丢失（队列满丢弃 / 写入失败）计数。
    "audit_lost",
    # 批次四 Task 17：运行手册告警指标（清理计数/耗时、provider 超时、
    # quota 退款失败）。
    "voice_audio_cleanup_deleted",
    "voice_audio_cleanup_duration_seconds",
    "retention_cleanup_deleted",
    "provider_timeout",
    "quota_refund_failure",
})

#: 积压类指标超过阈值时打印本地告警（queue/backlog 告警语义）。
_BACKLOG_METRICS = frozenset(
    {"outbox_backlog", "purge_backlog", "task_retry_backlog"}
)

#: 单个指标序列保留的最大样本数。超过后丢弃最旧样本，保证注册表在
#: 长生命周期进程内不会无界增长。
_METRIC_MAX_SAMPLES = 10_000

#: 进程内指标注册表：name -> deque[(value, tags), ...]。本地可观测与测试快照
#: 用，生产仍以结构化日志为真实时序出口。每个序列上限为
#: :data:`_METRIC_MAX_SAMPLES`，超过后丢弃最旧样本。
_METRIC_REGISTRY: dict[str, deque[tuple[float, dict[str, str]]]] = defaultdict(
    lambda: deque(maxlen=_METRIC_MAX_SAMPLES)
)


def metric_snapshot() -> dict[str, list[tuple[float, dict[str, str]]]]:
    """Return a copy of the in-process metric registry (tests/ops)."""
    return {name: list(values) for name, values in _METRIC_REGISTRY.items()}


def emit_ai_metric(name: str, value: float, tags: Mapping[str, str] | None = None) -> None:
    """Emit one operational metric without ever blocking the caller.

    ``name`` should be one of :data:`KNOWN_METRICS`; unknown names are still
    recorded but logged as a warning.  Each per-name series is bounded by
    :data:`_METRIC_MAX_SAMPLES`; the oldest sample is dropped once the limit is
    reached, so the registry never grows unbounded.  Backlog metrics above
    ``settings.ai_metrics_backlog_warn_threshold`` raise a local warning.
    """
    try:
        tag_map = dict(tags or {})
        if name not in KNOWN_METRICS:
            logger.warning(
                "ai_metric_unknown name=%s value=%s", name, value
            )
        _METRIC_REGISTRY[name].append((float(value), tag_map))
        if name in _BACKLOG_METRICS and float(value) >= settings.ai_metrics_backlog_warn_threshold:
            logger.warning(
                "ai_metric_backlog_high name=%s value=%s threshold=%s",
                name,
                value,
                settings.ai_metrics_backlog_warn_threshold,
            )
        logger.info(
            "ai_metric name=%s value=%s tags=%s",
            name,
            value,
            json.dumps(tag_map, ensure_ascii=False, sort_keys=True),
        )
    except Exception:
        logger.warning("ai_metric_emit_failed name=%s", name, exc_info=True)
