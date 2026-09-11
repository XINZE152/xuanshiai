"""语音临时音频统一过期清理（Task 1）。

语音栈在两个目录落盘 TTS 音频（P-04 REST 路径 ``tts/`` 与 P-04b 实时对话
路径 ``voice/tts/``），此前没有任何过期清理：合规要求转写完成后短期保留
即删除原始音频（``PROJECT_RULES.md` §3.2.1），不得长期留存。

本模块提供唯一的统一清理函数 :func:`cleanup_expired_voice_audio`：

- 固定扫描 ``upload_dir/voice/tts`` 与 ``upload_dir/tts`` 两个目录（只清理
  顶层文件，不递归子目录——批次目录等留给运维手工处理）。
- 过期判定基于文件 mtime 与 ``ai_voice_audio_retention_hours``（音频 retention
  与 ``voice_transcript`` retention 分开配置，禁止混用）。
- 越界防护：最终路径（``Path.resolve()``，含符号链接展开）必须仍位于配置的
  ``upload_dir`` 内，``..`` 穿越、指向目录外的符号链接一律拒绝并跳过。
- 单文件删除失败只记录错误并继续处理其余文件，failed 计数进入统计与日志，
  便于下一轮重试；文件消失（并发清理）按 skipped 处理，不算失败。
- 每轮输出 ``voice_audio_cleanup`` 统计日志（scanned/deleted/failed/skipped），
  可观测、可重试；重复执行幂等（第二轮 deleted=0）。

worker 侧通过 :func:`app.workers.ai_worker._run_voice_audio_cleanup_round`
按 ``ai_voice_audio_cleanup_interval_seconds`` 节流接入现有 ``_run_forever``
主循环，不引入新的调度器。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# 统一清理的两个扫描目录（相对 upload_dir）。与写入侧路径契约一致：
# - providers._AliyunVoiceClient.synthesize_speech → ``tts/<ts>-<rand>.<fmt>``
# - conversation._write_tts_file                   → ``voice/tts/<ts>-<rand>.mp3``
VOICE_TTS_SUBDIR = "voice/tts"
TTS_SUBDIR = "tts"
SCAN_SUBDIRS: tuple[str, ...] = (VOICE_TTS_SUBDIR, TTS_SUBDIR)


@dataclass
class VoiceCleanupStats:
    """单轮清理统计（进入日志与 worker 指标，可观测/可重试）。"""

    scanned: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0


def _unlink(path: Path) -> None:
    """删除单个文件（模块级注入点，测试可替换以模拟删除失败）。"""
    os.unlink(path)


def _is_within(root: Path, candidate: Path) -> bool:
    """Containment guard：``candidate`` 解析后必须仍在 ``root`` 之内。

    双方都先 ``resolve()``（展开符号链接与 ``..``），再做前缀比较；``root``
    自身视为在内。symlink 越界时 ``candidate`` 会解析到 ``root`` 之外而被
    拒绝（拒绝路径穿越与越界符号链接）。
    """
    try:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
    except OSError:
        # 解析失败（父目录不可达等）：宁可漏删也不越界删除。
        return False
    if resolved_candidate == resolved_root:
        return True
    return str(resolved_candidate).startswith(str(resolved_root) + os.sep)


def _expired_entry(
    entry: Path, *, now: float, retention_hours: int
) -> bool:
    """按 mtime 判断扫描目录内的单个条目是否过期。"""
    try:
        mtime = entry.stat().st_mtime
    except OSError:
        return False
    return (now - mtime) >= retention_hours * 3600


def _cleanup_expired_voice_audio_sync(
    *,
    upload_dir: str | None = None,
    retention_hours: int | None = None,
    now: float | None = None,
) -> VoiceCleanupStats:
    """同步实现：所有文件系统 I/O 都在这里，供 to_thread 调度。"""
    upload = Path(upload_dir if upload_dir is not None else settings.upload_dir)
    retention = (
        retention_hours
        if retention_hours is not None
        else settings.ai_voice_audio_retention_hours
    )
    current = time.time() if now is None else now
    stats = VoiceCleanupStats()

    for subdir in SCAN_SUBDIRS:
        scan_dir = upload / subdir
        if not _is_within(upload, scan_dir) or not scan_dir.is_dir():
            # 目录缺失 / upload_dir 本身异常：安全空转，不报错。
            continue
        try:
            entries = list(scan_dir.iterdir())
        except OSError:
            logger.exception("voice_audio_cleanup_list_failed dir=%s", subdir)
            continue
        for entry in entries:
            if not entry.is_file():
                # 子目录等非常规条目跳过：清理只针对顶层音频文件。
                stats.skipped += 1
                continue
            stats.scanned += 1
            if not _expired_entry(entry, now=current, retention_hours=retention):
                stats.skipped += 1
                continue
            if not _is_within(upload, entry):
                # 路径穿越 / 越界符号链接（resolve 后越出配置的 upload_dir）：
                # 拒绝删除并跳过。
                logger.warning(
                    "voice_audio_cleanup_out_of_bounds_refused path=%s",
                    entry,
                )
                stats.skipped += 1
                continue
            try:
                _unlink(entry)
                stats.deleted += 1
            except FileNotFoundError:
                # 并发清理把文件删掉了：目标已消失，按 skipped 处理。
                stats.skipped += 1
            except OSError:
                # 单文件失败隔离：记错误继续处理其他文件，下轮重试。
                stats.failed += 1
                logger.exception(
                    "voice_audio_cleanup_delete_failed path=%s", entry
                )

    logger.info(
        "voice_audio_cleanup scanned=%d deleted=%d failed=%d skipped=%d "
        "retention_hours=%d",
        stats.scanned,
        stats.deleted,
        stats.failed,
        stats.skipped,
        retention,
    )
    return stats


async def cleanup_expired_voice_audio(
    *,
    upload_dir: str | None = None,
    retention_hours: int | None = None,
    now: float | None = None,
) -> VoiceCleanupStats:
    """删除两个 TTS 扫描目录内已过期的临时音频文件（统一入口）。

    默认读 ``settings.upload_dir`` 与 ``settings.ai_voice_audio_retention_hours``
    （与 ``voice_transcript`` retention 独立）；参数仅供测试注入。删除失败
    不中断本轮：记错误日志、累计 ``failed`` 并继续处理其余文件。重复执行
    幂等——首轮删除后第二轮 ``deleted=0``。

    文件扫描/删除是阻塞 I/O，经 :func:`asyncio.to_thread` 调度，避免在
    worker 事件循环上长时间滞留（目录积压大量文件时不拖慢任务轮询）。
    """
    return await asyncio.to_thread(
        _cleanup_expired_voice_audio_sync,
        upload_dir=upload_dir,
        retention_hours=retention_hours,
        now=now,
    )


__all__ = [
    "SCAN_SUBDIRS",
    "TTS_SUBDIR",
    "VOICE_TTS_SUBDIR",
    "VoiceCleanupStats",
    "cleanup_expired_voice_audio",
]
