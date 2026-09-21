"""实时语音 v2 协议常量与 wire 事件构造。

供应商侧依据 SenseAudio 官方协议（end-to-end voice dialog WSS，
AsyncAPI 1.0.1，2026-09 核对）：上行 16kHz、下行 24kHz 单声道
``pcm_s16le``；JSON 文本帧控制、二进制帧承载音频。

客户端侧为墨相师 WS v2 新增事件（方案 §3），与 v1 事件并存。
"""

from __future__ import annotations

from typing import Any

# ----------------------------------------------------------------------
# 供应商（SenseAudio）协议常量
# ----------------------------------------------------------------------

UPLINK_SAMPLE_RATE = 16000
DOWNLINK_SAMPLE_RATE = 24000
AUDIO_FORMAT = "pcm_s16le"
AUDIO_CHANNELS = 1

SENSEAUDIO_DEFAULT_WS_URL = "wss://api.senseaudio.cn/ws/v1/realtime/voice-dialog"
SENSEAUDIO_MODEL = "senseaudio-realtime-1.0"

# 供应商客户端 → 服务端事件（JSON 文本帧；二进制帧直接承载 PCM，无包裹）。
VENDOR_TYPE_START = "start"
VENDOR_TYPE_UPDATE = "update"
VENDOR_TYPE_CANCEL = "cancel"
VENDOR_TYPE_END = "end"

# 供应商服务端 → 客户端事件。
VENDOR_TYPE_READY = "ready"
VENDOR_TYPE_SPEECH_STARTED = "speech.started"
VENDOR_TYPE_USER_TRANSCRIPT_DELTA = "user.transcript.delta"
VENDOR_TYPE_USER_TRANSCRIPT_DONE = "user.transcript.done"
VENDOR_TYPE_ASSISTANT_TEXT_DELTA = "assistant.text.delta"
VENDOR_TYPE_ASSISTANT_TEXT_DONE = "assistant.text.done"
VENDOR_TYPE_ASSISTANT_AUDIO_START = "assistant.audio.start"
VENDOR_TYPE_ASSISTANT_AUDIO_DONE = "assistant.audio.done"
VENDOR_TYPE_TURN_DONE = "turn.done"
VENDOR_TYPE_ERROR = "error"

# 供应商错误分类（官方文档）：fatal 类服务端随后关闭连接；非 fatal 类
# 会话继续。未知错误码按非 fatal 处理并记录。
_FATAL_VENDOR_ERROR_CODES = frozenset(
    {
        "bad_request",
        "model_not_found",
        "insufficient_funds",
        "internal_error",
        "fatal",
    }
)
_KNOWN_NONFATAL_VENDOR_ERROR_CODES = frozenset(
    {
        "empty_audio_buffer",
        "no_active_response",
        "invalid_voice",
    }
)


def is_fatal_vendor_error(code: str) -> bool:
    return str(code) in _FATAL_VENDOR_ERROR_CODES


def build_vendor_start_event(
    *,
    model: str,
    voice: str,
    instructions: str,
) -> dict[str, Any]:
    """构造供应商 ``start`` 事件（连接后第一条，仅发一次）。

    首版 ``greeting=""``、``tools=[]``：开场沿用页面既有文案，避免重复
    开场与额外工具流程。``instructions`` 承载人设、画像上下文与标注清楚
    的历史数据块。``voice`` 为空时省略字段（音色 ID 是枚举，空串不是
    合法值；省略由服务端选默认音色）。
    """
    payload: dict[str, Any] = {
        "type": VENDOR_TYPE_START,
        "model": model,
        "instructions": instructions,
        "greeting": "",
        "audio_setting": {
            "sample_rate": UPLINK_SAMPLE_RATE,
            "format": AUDIO_FORMAT,
            "channel": AUDIO_CHANNELS,
        },
        "tools": [],
    }
    if voice:
        payload["voice"] = voice
    return payload


def build_vendor_update_event(instructions: str) -> dict[str, Any]:
    """构造供应商 ``update`` 事件（业务上下文变化时增量更新）。

    官方约束（AsyncAPI 3.0.0）：``additionalProperties=false``，只能更新
    ``instructions`` / ``voice`` / ``tools``，不能改 ``model`` /
    ``audio_setting``。实测（2026-09-12 P0 探针）供应商对 instructions-only
    的 update 返回非致命 ``invalid_tts_speed`` 并整条忽略——会话层因此
    在业务上下文变化时改用轮转上游保证正确性，本事件保留作降级通道。
    """
    return {
        "type": VENDOR_TYPE_UPDATE,
        "instructions": instructions,
    }


VENDOR_CANCEL_EVENT: dict[str, Any] = {"type": VENDOR_TYPE_CANCEL}
VENDOR_END_EVENT: dict[str, Any] = {"type": VENDOR_TYPE_END}


# ----------------------------------------------------------------------
# 客户端（墨相师 WS v2）wire 事件构造
# ----------------------------------------------------------------------

# 回复生成状态（voice_reply_metadata.generation_status）。
GENERATION_STATUSES = ("streaming", "completed", "interrupted", "failed")
# 回复播放状态（voice_reply_metadata.playback_status）。
PLAYBACK_STATUSES = ("not_started", "playing", "completed", "interrupted", "unknown")

PROTOCOL_VERSION_V2 = 2

# 下行音频分片上限（base64 前）。供应商下行按内部节奏切片，服务端转推时
# 二次切分到该尺寸以内，约束单条 WS 消息体积。
CLIENT_AUDIO_CHUNK_MAX_BYTES = 8 * 1024


def build_client_v2_event(event_type: str, **fields: Any) -> dict[str, Any]:
    """构造一条发往前端的 v2 wire 事件（字段已做防御性收窄）。"""
    payload: dict[str, Any] = {"type": event_type}
    payload.update(fields)
    return payload


# 服务端 → 客户端 v2 事件名（供路由与 session 共用，避免散落字符串）。
CLIENT_EVENT_VOICE_READY = "voice_ready"
CLIENT_EVENT_INPUT_CLOSED = "input_closed"
CLIENT_EVENT_AUDIO_OUTPUT_START = "audio_output_start"
CLIENT_EVENT_AUDIO_CHUNK = "audio_chunk"
CLIENT_EVENT_AUDIO_OUTPUT_END = "audio_output_end"
CLIENT_EVENT_RESPONSE_DONE = "response_done"
CLIENT_EVENT_CANCELLED = "cancelled"
CLIENT_EVENT_RESPONSE_STATUS = "response_status"

__all__ = [
    "UPLINK_SAMPLE_RATE",
    "DOWNLINK_SAMPLE_RATE",
    "AUDIO_FORMAT",
    "AUDIO_CHANNELS",
    "SENSEAUDIO_DEFAULT_WS_URL",
    "SENSEAUDIO_MODEL",
    "VENDOR_TYPE_START",
    "VENDOR_TYPE_UPDATE",
    "VENDOR_TYPE_CANCEL",
    "VENDOR_TYPE_END",
    "VENDOR_TYPE_READY",
    "VENDOR_TYPE_SPEECH_STARTED",
    "VENDOR_TYPE_USER_TRANSCRIPT_DELTA",
    "VENDOR_TYPE_USER_TRANSCRIPT_DONE",
    "VENDOR_TYPE_ASSISTANT_TEXT_DELTA",
    "VENDOR_TYPE_ASSISTANT_TEXT_DONE",
    "VENDOR_TYPE_ASSISTANT_AUDIO_START",
    "VENDOR_TYPE_ASSISTANT_AUDIO_DONE",
    "VENDOR_TYPE_TURN_DONE",
    "VENDOR_TYPE_ERROR",
    "is_fatal_vendor_error",
    "build_vendor_start_event",
    "build_vendor_update_event",
    "VENDOR_CANCEL_EVENT",
    "VENDOR_END_EVENT",
    "GENERATION_STATUSES",
    "PLAYBACK_STATUSES",
    "PROTOCOL_VERSION_V2",
    "CLIENT_AUDIO_CHUNK_MAX_BYTES",
    "build_client_v2_event",
    "CLIENT_EVENT_VOICE_READY",
    "CLIENT_EVENT_INPUT_CLOSED",
    "CLIENT_EVENT_AUDIO_OUTPUT_START",
    "CLIENT_EVENT_AUDIO_CHUNK",
    "CLIENT_EVENT_AUDIO_OUTPUT_END",
    "CLIENT_EVENT_RESPONSE_DONE",
    "CLIENT_EVENT_CANCELLED",
    "CLIENT_EVENT_RESPONSE_STATUS",
]
