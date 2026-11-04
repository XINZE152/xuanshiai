"""第三方属地 API 的共享 HTTP 客户端：超时、并发、QPS、重试。

日志与异常信息不包含 IP/手机号原文；调用方只统计失败数量，不把 PII 写进消息。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.services.region.types import RegionUpstreamError

_RETRY_STATUS = {429, 500, 502, 503, 504}


class RegionHttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        concurrency: int,
        qps: int,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._qps = max(1, qps)
        self._throttle_lock = asyncio.Lock()
        self._last_started = 0.0
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=max(1, concurrency)),
            transport=transport,
        )

    async def __aenter__(self) -> RegionHttpClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        interval = 1.0 / self._qps
        async with self._throttle_lock:
            wait = interval - (time.monotonic() - self._last_started)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_started = time.monotonic()

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After", "").strip()
        if not raw:
            return None
        try:
            return max(0.0, min(float(raw), 10.0))
        except ValueError:
            return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """执行一次请求；429/5xx 重试后仍失败时返回最后一次响应。

        超时/连接错误在重试耗尽后抛 ``RegionUpstreamError``；响应体不写入异常。
        """
        async with self._semaphore:
            await self._throttle()
            for attempt in range(self._max_retries + 1):
                try:
                    response = await self._client.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json,
                        data=data,
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt >= self._max_retries:
                        raise RegionUpstreamError(
                            "第三方属地接口请求失败", code="timeout" if isinstance(exc, httpx.TimeoutException) else "transport"
                        ) from exc
                    await asyncio.sleep(min(2**attempt * 0.2, 2.0))
                    continue

                if response.status_code in _RETRY_STATUS and attempt < self._max_retries:
                    delay = self._retry_after_seconds(response)
                    await asyncio.sleep(delay if delay is not None else min(2**attempt * 0.2, 2.0))
                    continue
                return response

        raise RegionUpstreamError("第三方属地接口重试耗尽")
