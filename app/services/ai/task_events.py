"""Task terminal-state wake-up notifications over Redis pub/sub.

发布方（worker / 取消路由）在终态事务 **commit 之后** 才 publish 一条唤醒
信号；消息本身不携带权威状态，订阅方收到后必须重读 ``ai_task`` 以数据库
为准。因此消息丢失/乱序无害：等待方自带周期轮询兜底，Redis 不可用时
发布静默失败、订阅方退回纯轮询。
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

TASK_EVENT_CHANNEL_PREFIX = "ai:task-events"

# 与 app/services/ai/tasks.py 的 _TERMINAL 保持一致；这里独立声明是为了让
# worker / 路由不为此反向依赖状态机内部符号。
TERMINAL_TASK_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "superseded"}
)


def task_event_channel(task_id: str) -> str:
    return f"{TASK_EVENT_CHANNEL_PREFIX}:{task_id}"


async def notify_task_event(task_id: str, status: str | None = None) -> bool:
    """Best-effort publish; never raises, returns False when Redis is unavailable."""

    try:
        from app.core.redis import redis_client

        payload = json.dumps(
            {"type": "task_event", "task_id": str(task_id), "status": status},
            ensure_ascii=False,
        )
        await redis_client.publish(task_event_channel(str(task_id)), payload)
        return True
    except Exception:
        logger.debug("task_event_publish_failed task_id=%s", task_id, exc_info=True)
        return False
