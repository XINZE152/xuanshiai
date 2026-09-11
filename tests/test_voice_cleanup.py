"""语音临时音频统一过期清理单测（Task 1）。

覆盖（对应 task-1-brief 验收项）：
- 过期文件在 ``voice/tts`` 与 ``tts`` 两个扫描目录都被删除；
- 未过期文件保留；
- 目录缺失 / 空目录 / upload_dir 缺失时安全空转；
- 越界符号链接被拒绝（目标文件不被删除）；symlink 不可用的环境自动 skip；
- 路径穿越拒绝（containment guard 直接断言）；
- 扫描目录内的子目录条目被跳过（只清理顶层文件）；
- 重复执行幂等（第二轮 deleted=0 且无错误）；
- 单个文件删除失败记错误并继续处理其他文件；
- 每轮统计日志可观测（scanned/deleted/failed/skipped）；
- worker 定时任务接入（``_run_voice_audio_cleanup_round`` + ``_run_forever`` 节流）。

所有文件操作都基于 ``tmp_path`` 真实文件系统，不 mock 被测函数本身。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import Any

import pytest

from app.core.config import settings
from app.services.voice import cleanup as voice_cleanup
from app.services.voice.cleanup import (
    SCAN_SUBDIRS,
    TTS_SUBDIR,
    VOICE_TTS_SUBDIR,
    _is_within,
    cleanup_expired_voice_audio,
)

RETENTION_HOURS = 24
_EXPIRED_AGE_SECONDS = (RETENTION_HOURS + 1) * 3600

_WORKER_LOGGER = "app.workers.ai_worker"
_CLEANUP_LOGGER = "app.services.voice.cleanup"


def _make_audio(path, *, age_seconds: float = 0.0) -> Any:
    """在真实文件系统创建一个音频文件；``age_seconds``>0 时把 mtime 拨到过去。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ID3fake-audio-bytes")
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return path


@pytest.fixture()
def voice_upload_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """构造标准 upload_dir（含 tts/ 与 voice/tts/ 两个空目录）并接管 settings。"""
    upload = tmp_path / "uploads"
    (upload / TTS_SUBDIR).mkdir(parents=True)
    (upload / VOICE_TTS_SUBDIR).mkdir(parents=True)
    monkeypatch.setattr(settings, "upload_dir", str(upload))
    monkeypatch.setattr(settings, "ai_voice_audio_retention_hours", RETENTION_HOURS)
    return upload


# ======================================================================
# 扫描目录契约
# ======================================================================


def test_scan_subdirs_are_exactly_the_two_configured_dirs() -> None:
    """统一清理函数固定扫描 upload_dir/voice/tts 与 upload_dir/tts。"""
    assert VOICE_TTS_SUBDIR == "voice/tts"
    assert TTS_SUBDIR == "tts"
    assert SCAN_SUBDIRS == ("voice/tts", "tts")


# ======================================================================
# 过期 / 不过期
# ======================================================================


@pytest.mark.asyncio
async def test_expired_audio_deleted_in_both_scan_dirs(voice_upload_dir) -> None:
    """两个扫描目录内的过期音频都被删除。"""
    expired_tts = _make_audio(
        voice_upload_dir / TTS_SUBDIR / "1000-old.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )
    expired_voice_tts = _make_audio(
        voice_upload_dir / VOICE_TTS_SUBDIR / "1001-old.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )

    stats = await cleanup_expired_voice_audio()

    assert not expired_tts.exists()
    assert not expired_voice_tts.exists()
    assert (stats.scanned, stats.deleted, stats.failed, stats.skipped) == (2, 2, 0, 0)


@pytest.mark.asyncio
async def test_recent_audio_not_deleted(voice_upload_dir) -> None:
    """保留期内（未过期）的音频文件原样保留。"""
    fresh_tts = _make_audio(voice_upload_dir / TTS_SUBDIR / "2000-new.mp3")
    fresh_voice_tts = _make_audio(
        voice_upload_dir / VOICE_TTS_SUBDIR / "2001-new.mp3"
    )

    stats = await cleanup_expired_voice_audio()

    assert fresh_tts.exists()
    assert fresh_voice_tts.exists()
    assert stats.deleted == 0
    assert stats.failed == 0
    assert stats.scanned == 2
    assert stats.skipped == 2


@pytest.mark.asyncio
async def test_expiry_boundary_uses_file_mtime(voice_upload_dir) -> None:
    """过期判定基于文件 mtime：恰好超过保留期即删除，临界内保留。"""
    just_expired = _make_audio(
        voice_upload_dir / TTS_SUBDIR / "3000-boundary.mp3",
        age_seconds=RETENTION_HOURS * 3600,
    )
    still_fresh = _make_audio(
        voice_upload_dir / TTS_SUBDIR / "3001-within.mp3",
        age_seconds=RETENTION_HOURS * 3600 - 60,
    )

    stats = await cleanup_expired_voice_audio()

    assert not just_expired.exists()
    assert still_fresh.exists()
    assert (stats.deleted, stats.skipped) == (1, 1)


# ======================================================================
# 目录缺失 / 空目录
# ======================================================================


@pytest.mark.asyncio
async def test_empty_dirs_are_safe(voice_upload_dir) -> None:
    """空目录：零扫描、零删除、不报错。"""
    stats = await cleanup_expired_voice_audio()
    assert (stats.scanned, stats.deleted, stats.failed, stats.skipped) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_missing_scan_dirs_are_safe(tmp_path, monkeypatch) -> None:
    """扫描目录不存在（首次部署/已清空）时安全空转，不报错。"""
    upload = tmp_path / "no-such-uploads"
    monkeypatch.setattr(settings, "upload_dir", str(upload))

    stats = await cleanup_expired_voice_audio()

    assert (stats.scanned, stats.deleted, stats.failed, stats.skipped) == (0, 0, 0, 0)


# ======================================================================
# 路径穿越 / 越界符号链接
# ======================================================================


def test_containment_guard_rejects_escape_paths(tmp_path) -> None:
    """containment guard：``..`` 穿越与目录外路径被拒绝，目录内路径通过。"""
    root = (tmp_path / "uploads").resolve()
    inside = (root / TTS_SUBDIR / "a.mp3").resolve()
    escaped = (root / TTS_SUBDIR / ".." / ".." / "evil.mp3").resolve()

    assert _is_within(root, inside) is True
    assert _is_within(root, escaped) is False
    assert _is_within(root, root) is True
    assert _is_within(root, tmp_path.resolve()) is False


@pytest.mark.asyncio
async def test_out_of_bounds_symlink_is_refused(voice_upload_dir, tmp_path) -> None:
    """指向扫描目录之外的符号链接被拒绝：目标文件不被删除，链接本身保留。"""
    outside_secret = tmp_path / "outside" / "secret.mp3"
    _make_audio(outside_secret, age_seconds=_EXPIRED_AGE_SECONDS)
    link = voice_upload_dir / TTS_SUBDIR / "escape-link.mp3"
    try:
        os.symlink(outside_secret, link)
    except (NotImplementedError, OSError):
        pytest.skip("当前环境不支持创建符号链接（Windows 需管理员/开发者模式）")

    stats = await cleanup_expired_voice_audio()

    # 越界目标绝不能被删除；链接本身也只跳过不删除。
    assert outside_secret.exists()
    assert link.exists()
    assert stats.deleted == 0
    assert stats.failed == 0
    assert stats.skipped >= 1


@pytest.mark.asyncio
async def test_symlink_inside_upload_dir_follows_target_expiry(
    voice_upload_dir, tmp_path
) -> None:
    """指向 upload_dir 内（另一扫描目录）的符号链接：resolve 后仍在配置目录
    内，按目标文件过期时间删除链接本身；目标文件同轮也被独立清理（两个
    目录条目各计一次删除）。"""
    target = _make_audio(
        voice_upload_dir / VOICE_TTS_SUBDIR / "target-old.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )
    link = voice_upload_dir / TTS_SUBDIR / "inside-link.mp3"
    try:
        os.symlink(target, link)
    except (NotImplementedError, OSError):
        pytest.skip("当前环境不支持创建符号链接（Windows 需管理员/开发者模式）")

    stats = await cleanup_expired_voice_audio()

    assert not link.exists()  # 链接被删除（unlink 只移除链接本身）
    assert not target.exists()  # 目标文件同轮被清理
    assert stats.deleted == 2
    assert stats.failed == 0


# ======================================================================
# 非常规条目（子目录 / 嵌套路径）
# ======================================================================


@pytest.mark.asyncio
async def test_subdirectory_entry_is_skipped(voice_upload_dir) -> None:
    """扫描目录内的子目录（含其中嵌套的过期文件）不递归、不删除。"""
    nested_dir = voice_upload_dir / TTS_SUBDIR / "batch-20260101"
    nested_expired = _make_audio(
        nested_dir / "nested-old.mp3", age_seconds=_EXPIRED_AGE_SECONDS
    )

    stats = await cleanup_expired_voice_audio()

    assert nested_expired.exists()
    assert nested_dir.is_dir()
    assert stats.deleted == 0
    assert stats.skipped >= 1


# ======================================================================
# 幂等
# ======================================================================


@pytest.mark.asyncio
async def test_second_run_is_idempotent(voice_upload_dir) -> None:
    """重复执行：过期文件首轮删除后，第二轮零删除且不报错。"""
    expired = _make_audio(
        voice_upload_dir / TTS_SUBDIR / "4000-old.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )
    fresh = _make_audio(voice_upload_dir / VOICE_TTS_SUBDIR / "4001-new.mp3")

    first = await cleanup_expired_voice_audio()
    second = await cleanup_expired_voice_audio()

    assert first.deleted == 1
    assert (second.deleted, second.failed) == (0, 0)
    assert second.scanned == 1  # 只剩未过期文件
    assert not expired.exists()
    assert fresh.exists()


# ======================================================================
# 单文件失败隔离
# ======================================================================


@pytest.mark.asyncio
async def test_single_file_failure_continues_and_counts(
    voice_upload_dir, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """单个文件删除失败：记错误日志并继续处理其余文件，统计 failed 计数。"""
    broken = _make_audio(
        voice_upload_dir / TTS_SUBDIR / "5000-broken.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )
    healthy = _make_audio(
        voice_upload_dir / TTS_SUBDIR / "5001-healthy.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )
    healthy_voice = _make_audio(
        voice_upload_dir / VOICE_TTS_SUBDIR / "5002-healthy.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )

    real_unlink = os.unlink

    def failing_unlink(path, *args: Any, **kwargs: Any):
        if str(path).endswith("5000-broken.mp3"):
            raise PermissionError(13, "模拟删除失败")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(voice_cleanup, "_unlink", failing_unlink)

    with caplog.at_level(logging.ERROR, logger=_CLEANUP_LOGGER):
        stats = await cleanup_expired_voice_audio()

    assert not healthy.exists()
    assert not healthy_voice.exists()
    assert broken.exists()  # 失败文件保留待下轮重试
    assert (stats.deleted, stats.failed) == (2, 1)
    assert "5000-broken.mp3" in caplog.text


@pytest.mark.asyncio
async def test_vanished_file_is_counted_skipped_not_failed(
    voice_upload_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删除瞬间文件已消失（并发清理）：按 skipped 处理，不算失败、不报错。"""
    vanished = _make_audio(
        voice_upload_dir / TTS_SUBDIR / "6000-vanished.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )

    real_unlink = os.unlink

    def racing_unlink(path, *args: Any, **kwargs: Any):
        if str(path).endswith("6000-vanished.mp3"):
            real_unlink(path)
            raise FileNotFoundError(2, "模拟并发删除")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(voice_cleanup, "_unlink", racing_unlink)

    stats = await cleanup_expired_voice_audio()

    assert not vanished.exists()
    assert (stats.deleted, stats.failed, stats.skipped) == (0, 0, 1)


# ======================================================================
# 可观测性
# ======================================================================


@pytest.mark.asyncio
async def test_cleanup_logs_scanned_deleted_failed_counts(
    voice_upload_dir, caplog
) -> None:
    """每轮清理输出 scanned/deleted/failed/skipped 统计日志。"""
    _make_audio(
        voice_upload_dir / TTS_SUBDIR / "7000-old.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )
    _make_audio(voice_upload_dir / VOICE_TTS_SUBDIR / "7001-new.mp3")

    with caplog.at_level(logging.INFO, logger=_CLEANUP_LOGGER):
        await cleanup_expired_voice_audio()

    assert "voice_audio_cleanup" in caplog.text
    assert "scanned=2" in caplog.text
    assert "deleted=1" in caplog.text
    assert "failed=0" in caplog.text
    assert "skipped=1" in caplog.text


# ======================================================================
# worker 定时任务接入
# ======================================================================


@pytest.mark.asyncio
async def test_worker_cleanup_round_deletes_expired_audio(
    voice_upload_dir, caplog
) -> None:
    """worker 清理轮次调用统一清理函数：过期文件被删除并输出统计日志。"""
    from app.workers import ai_worker

    expired = _make_audio(
        voice_upload_dir / TTS_SUBDIR / "8000-old.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )

    with caplog.at_level(logging.INFO, logger=_WORKER_LOGGER):
        payload = await ai_worker._run_voice_audio_cleanup_round()

    assert not expired.exists()
    assert payload["deleted"] == 1
    assert payload["failed"] == 0
    assert "voice_audio_cleanup_round" in caplog.text
    assert "deleted=1" in caplog.text


@pytest.mark.asyncio
async def test_worker_forever_loop_schedules_cleanup(
    voice_upload_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_run_forever`` 主循环作为定时任务机制触发语音音频清理（首轮即执行）。

    ``_run_round`` 被替换为空转，避免测试触碰数据库；循环随后被取消。
    """
    from app.workers import ai_worker

    expired = _make_audio(
        voice_upload_dir / VOICE_TTS_SUBDIR / "9000-old.mp3",
        age_seconds=_EXPIRED_AGE_SECONDS,
    )

    async def fake_round(worker_id: str, batch_size: int):
        return 0, 0, 0

    monkeypatch.setattr(ai_worker, "_run_round", fake_round)
    monkeypatch.setattr(
        settings, "ai_voice_audio_cleanup_interval_seconds", 3600
    )

    task = asyncio.create_task(ai_worker._run_forever("worker-test", 1, 0.01))
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and expired.exists():
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert not expired.exists()
