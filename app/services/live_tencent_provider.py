"""Tencent TRTC server SDK adapter.

Only this module imports Tencent SDK classes. Domain services receive the
small internal provider contract instead of vendor request/response objects.
"""

import asyncio
from functools import lru_cache

from app.core.config import settings
from app.services.live_provider import LiveProviderError, LiveRoomResult


@lru_cache(maxsize=1)
def _client():
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.trtc.v20190722 import models, trtc_client
    except ImportError as exc:
        raise LiveProviderError("LIVE_SDK_MISSING", "腾讯直播 SDK 未安装") from exc
    if not settings.tencent_live_secret_id or not settings.tencent_live_secret_key:
        raise LiveProviderError("LIVE_PROVIDER_NOT_CONFIGURED")
    cred = credential.Credential(
        settings.tencent_live_secret_id,
        settings.tencent_live_secret_key.get_secret_value(),
    )
    http_profile = HttpProfile()
    http_profile.endpoint = "trtc.tencentcloudapi.com"
    http_profile.req_timeout = settings.tencent_live_api_timeout_seconds
    profile = ClientProfile()
    profile.httpProfile = http_profile
    return trtc_client.TrtcClient(cred, settings.tencent_live_region, profile), models


class TencentLiveProvider:
    name = "TENCENT"

    async def dismiss_room(self, *, sdk_app_id: int, room_id: int) -> LiveRoomResult:
        for attempt in range(settings.tencent_live_max_retries + 1):
            try:
                return await asyncio.to_thread(self._dismiss_room_sync, sdk_app_id, room_id)
            except LiveProviderError:
                raise
            except Exception as exc:
                if attempt >= settings.tencent_live_max_retries:
                    raise LiveProviderError("LIVE_PROVIDER_UNAVAILABLE") from exc
                await asyncio.sleep(0.2 * (attempt + 1))
        raise LiveProviderError("LIVE_PROVIDER_UNAVAILABLE")

    @staticmethod
    def _dismiss_room_sync(sdk_app_id: int, room_id: int) -> LiveRoomResult:
        client, models = _client()
        request = models.DismissRoomRequest()
        request.SdkAppId = sdk_app_id
        request.RoomId = room_id
        response = client.DismissRoom(request)
        return LiveRoomResult(
            provider="TENCENT",
            sdk_app_id=sdk_app_id,
            room_id=room_id,
            request_id=getattr(response, "RequestId", None),
        )
