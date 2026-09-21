"""实时全双工语音对话（v2 协议）包。

分层：
- ``protocol``：供应商（SenseAudio）事件名/格式常量与客户端 v2 wire 事件构造。
- ``provider``：``SenseAudioUpstream`` 单条上游连接适配（连接、start/ready、
  音频收发、cancel/update、事件解析；二进制帧绑定接收它的连接与响应上下文）。
- ``session``：``RealtimeVoiceSession`` 会话控制器（世代/连接代次身份、
  有界队列、审核放行门、打断轮转、播放状态元数据）。

业务侧（voice_moxiang 路由）只依赖 ``RealtimeVoiceSession`` 与回调契约，
不直接接触供应商协议。
"""

from app.services.voice.realtime.protocol import (
    AUDIO_FORMAT,
    DOWNLINK_SAMPLE_RATE,
    GENERATION_STATUSES,
    PLAYBACK_STATUSES,
    UPLINK_SAMPLE_RATE,
    build_client_v2_event,
)
from app.services.voice.realtime.provider import (
    RealtimeProviderConfig,
    RealtimeProviderError,
    SenseAudioUpstream,
    VendorAudio,
    VendorAssistantAudioDone,
    VendorAssistantAudioStart,
    VendorAssistantTextDelta,
    VendorAssistantTextDone,
    VendorConnectionClosed,
    VendorError,
    VendorEvent,
    VendorReady,
    VendorSpeechStarted,
    VendorTurnDone,
    VendorUserTranscriptDelta,
    VendorUserTranscriptDone,
)
from app.services.voice.realtime.session import (
    RealtimeSessionCallbacks,
    RealtimeVoiceSession,
    TurnOutcome,
)

__all__ = [
    "AUDIO_FORMAT",
    "DOWNLINK_SAMPLE_RATE",
    "UPLINK_SAMPLE_RATE",
    "GENERATION_STATUSES",
    "PLAYBACK_STATUSES",
    "build_client_v2_event",
    "RealtimeProviderConfig",
    "RealtimeProviderError",
    "SenseAudioUpstream",
    "VendorEvent",
    "VendorReady",
    "VendorSpeechStarted",
    "VendorUserTranscriptDelta",
    "VendorUserTranscriptDone",
    "VendorAssistantTextDelta",
    "VendorAssistantTextDone",
    "VendorAssistantAudioStart",
    "VendorAudio",
    "VendorAssistantAudioDone",
    "VendorTurnDone",
    "VendorError",
    "VendorConnectionClosed",
    "RealtimeSessionCallbacks",
    "RealtimeVoiceSession",
    "TurnOutcome",
]
