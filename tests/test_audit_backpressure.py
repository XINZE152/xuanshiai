"""Task 16：审计写入有界队列与背压（批次四）。

计划要求（docs/superpowers/plans/2026-09-09-ai-backend-four-batches.md Task 16）：
- 有界异步审计队列 + 专用写入线程（选型 B：不新增独立连接池）。
- 队列满时的处理：丢最旧、计 audit_lost，绝不阻塞业务主链路。
- 审计故障不阻塞主链路：写入失败只记日志 + audit_lost 指标。
- worker 关闭时排空：shutdown_audit_flusher cancel 后 drain。
- 丢失审计可观测：audit_queue_depth / audit_lost_total / audit_lost 指标。
- 连接复用与重连：thread-local 连接缓存 + ping(reconnect=True)。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.config import settings
from app.services.ai import audit as audit_mod
from app.services.ai.audit import GenerationAuditEvent

pytestmark = pytest.mark.asyncio


def _event(request_id: str = "audit-1", **kwargs: Any) -> GenerationAuditEvent:
    base: dict[str, Any] = {
        "request_id": request_id,
        "task_id": "task-1",
        "scene": "profile_extract",
        "provider": "mock",
        "model": "mock-v1",
        "prompt_version": "p1",
        "schema_version": "s1",
    }
    base.update(kwargs)
    return GenerationAuditEvent(**base)


@pytest.fixture(autouse=True)
def _reset_audit_state():
    """每个用例重置队列/计数/flusher，隔离测试。"""
    # 先取消上一用例遗留的 flusher（绑定已关闭的 loop），再清状态。
    # 遗留任务的事件循环可能已被前序用例关闭：cancel() 会因 loop closed
    # 抛 RuntimeError，此时任务已随循环消亡，无需（也无法）取消。
    stale = audit_mod._flusher_task
    if stale is not None and not stale.done():
        try:
            stale.cancel()
        except RuntimeError:  # noqa: BLE001 - flusher 的 loop 已关闭
            pass
    audit_mod._flusher_task = None
    audit_mod._AUDIT_QUEUE_LOCK.acquire()
    while not audit_mod._AUDIT_QUEUE.empty():
        try:
            audit_mod._AUDIT_QUEUE.get_nowait()
        except Exception:  # noqa: BLE001
            break
    audit_mod._AUDIT_QUEUE_SIZE = 0
    audit_mod._AUDIT_QUEUE_LOCK.release()
    audit_mod._audit_lost_count = 0
    yield
    task = audit_mod._flusher_task
    if task is not None and not task.done():
        try:
            task.cancel()
        except RuntimeError:  # noqa: BLE001 - flusher 的 loop 已关闭
            pass
    audit_mod._flusher_task = None


async def test_record_enqueues_without_blocking(monkeypatch) -> None:
    """开启队列路径：record 只入队，写入发生在后台 flusher。"""
    monkeypatch.setattr(settings, "ai_audit_enabled", True)
    writes: list[str] = []

    async def fake_persist(event: GenerationAuditEvent) -> None:
        await asyncio.sleep(0)
        writes.append(event.request_id)

    monkeypatch.setattr(audit_mod, "_persist_audit_row_sync", None, raising=False)
    # 直接测入队原语（flusher 里的 to_thread 绑定的是模块真名）。
    assert audit_mod._enqueue_audit_event(_event("q-1")) is True
    assert audit_mod.audit_queue_depth() == 1
    batch = audit_mod._drain_audit_batch()
    assert [e.request_id for e in batch] == ["q-1"]
    assert audit_mod.audit_queue_depth() == 0


async def test_queue_overflow_drops_oldest_and_counts_lost(monkeypatch) -> None:
    """队列满：丢最旧、保最新、audit_lost 计数递增。"""
    monkeypatch.setattr(audit_mod, "_AUDIT_QUEUE_MAX", 3)
    for i in range(5):
        audit_mod._enqueue_audit_event(_event(f"overflow-{i}"))
    assert audit_mod.audit_queue_depth() == 3
    assert audit_mod.audit_lost_total() == 2
    # 队列里应是最新的三条。
    remaining = audit_mod._drain_audit_batch()
    assert [e.request_id for e in remaining] == ["overflow-2", "overflow-3", "overflow-4"]


async def test_drain_batch_respects_batch_limit() -> None:
    """单批排水上限 _AUDIT_FLUSH_BATCH。"""
    for i in range(audit_mod._AUDIT_FLUSH_BATCH + 10):
        audit_mod._enqueue_audit_event(_event(f"batch-{i}"))
    first = audit_mod._drain_audit_batch()
    assert len(first) == audit_mod._AUDIT_FLUSH_BATCH
    second = audit_mod._drain_audit_batch()
    assert len(second) == 10
    assert audit_mod.audit_queue_depth() == 0


async def test_flusher_drains_queue_and_survives_write_failure(monkeypatch) -> None:
    """flusher 后台排水；写入失败不终止 flusher、计入 audit_lost。"""
    monkeypatch.setattr(settings, "ai_audit_enabled", True)
    attempts: list[str] = []

    def flaky_sync(event: GenerationAuditEvent) -> None:
        attempts.append(event.request_id)
        raise RuntimeError("db down")

    monkeypatch.setattr(audit_mod, "_persist_audit_row_sync", flaky_sync)
    audit_mod._ensure_audit_flusher()
    assert audit_mod._flusher_task is not None
    await audit_mod.record_generation_audit(_event("f-1"))
    await audit_mod.record_generation_audit(_event("f-2"))
    # 等 flusher 排干（idle 间隔最长 2s）。
    for _ in range(60):
        if audit_mod.audit_queue_depth() == 0 and len(attempts) >= 2:
            break
        await asyncio.sleep(0.05)
    assert audit_mod.audit_queue_depth() == 0
    assert sorted(attempts) == ["f-1", "f-2"]
    # 写入失败计 audit_lost（flusher 兜底路径），且 flusher 仍活着。
    assert audit_mod.audit_lost_total() == 2
    assert not audit_mod._flusher_task.done()
    # 同 loop 内干净关闭，避免 teardown 报 loop closed。
    await audit_mod.shutdown_audit_flusher(timeout=5.0)


async def test_disabled_audit_records_nothing(monkeypatch) -> None:
    """ai_audit_enabled=False：不入队、不写。"""
    monkeypatch.setattr(settings, "ai_audit_enabled", False)
    await audit_mod.record_generation_audit(_event("off-1"))
    assert audit_mod.audit_queue_depth() == 0


async def test_shutdown_drains_remaining_events(monkeypatch) -> None:
    """关闭排空：cancel 后 flusher 把剩余队列写完再退出。"""
    monkeypatch.setattr(settings, "ai_audit_enabled", True)
    written: list[str] = []

    def ok_sync(event: GenerationAuditEvent) -> None:
        written.append(event.request_id)

    monkeypatch.setattr(audit_mod, "_persist_audit_row_sync", ok_sync)
    audit_mod._ensure_audit_flusher()
    for i in range(5):
        await audit_mod.record_generation_audit(_event(f"drain-{i}"))
    await audit_mod.shutdown_audit_flusher(timeout=5.0)
    assert audit_mod._flusher_task is None
    assert audit_mod.audit_queue_depth() == 0
    assert len(written) == 5


async def test_persist_write_failure_discards_connection_and_never_raises(
    monkeypatch,
) -> None:
    """同步写入失败：不抛出、丢弃缓存连接、计 audit_lost。"""

    class _BrokenConn:
        def ping(self, reconnect: bool = False) -> None:
            raise RuntimeError("gone away")

        def cursor(self) -> Any:
            raise RuntimeError("no cursor")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        audit_mod,
        "_db_connect_params",
        lambda: {"host": "db", "port": 3306, "user": "u", "password": "p", "database": "d"},
    )
    monkeypatch.setattr(
        audit_mod, "_thread_local_connection", lambda params: _BrokenConn()
    )
    discarded: list[None] = []
    monkeypatch.setattr(
        audit_mod, "_discard_thread_connection", lambda: discarded.append(None)
    )
    before = audit_mod.audit_lost_total()
    # 不抛出即通过。
    audit_mod._persist_audit_row_sync(_event("fail-1"))
    assert discarded, "失败必须丢弃缓存连接"
    assert audit_mod.audit_lost_total() == before + 1


async def test_persist_skips_unparseable_db_url(monkeypatch) -> None:
    """database_url 无法解析为 pymysql 参数：静默跳过（原行为保留）。"""
    monkeypatch.setattr(
        audit_mod, "_db_connect_params", lambda: None
    )
    audit_mod._persist_audit_row_sync(_event("skip-1"))
    assert audit_mod.audit_lost_total() == 0, "跳过不计丢失（未尝试写）"
