"""Provider boundary for live-room control-plane operations."""

from dataclasses import dataclass
from typing import Protocol


class LiveProviderError(RuntimeError):
    """A sanitized provider failure safe to map to an API/business error."""

    def __init__(self, code: str, message: str = "直播供应商暂不可用") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LiveRoomResult:
    provider: str
    sdk_app_id: int
    room_id: int
    request_id: str | None = None


class LiveProvider(Protocol):
    name: str

    async def dismiss_room(self, *, sdk_app_id: int, room_id: int) -> LiveRoomResult: ...
