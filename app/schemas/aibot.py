"""恋 AI BOT 会话 API schemas。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AibotAssignmentResponse(BaseModel):
    agent_id: str | None = None
    assigned_at: str | None = None


class AibotAssignmentUpdate(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=32)


class AibotMemoryResetRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=32)


class AibotMemoryResetResponse(BaseModel):
    agent_id: str
    ok: bool = True
    deleted: dict[str, int] = Field(default_factory=dict)
    message: str = ""


class AibotTurnItem(BaseModel):
    turn_id: str
    role: str
    content: str
    intent: str | None = None
    choices: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    cards: Any = None
    created_at: str | None = None


class AibotSessionResponse(BaseModel):
    session_id: str
    agent_id: str
    status: str = "active"
    needs_welcome: bool = False
    last_intent: str | None = None
    turns: list[AibotTurnItem] = Field(default_factory=list)


class AibotSessionCreate(BaseModel):
    agent_id: str | None = Field(default=None, max_length=32)


class AibotTurnRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    client_context: dict[str, Any] | None = None


class AibotTurnResponse(BaseModel):
    turn_id: str
    reply: str
    intent: str = "chat"
    choices: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    user_turn: AibotTurnItem
    assistant_turn: AibotTurnItem
    session_id: str


class AibotWelcomeResponse(BaseModel):
    session_id: str
    agent_id: str
    messages: list[AibotTurnItem] = Field(default_factory=list)
    guide_actions: list[str] = Field(
        default_factory=lambda: ["report", "recommend", "story", "profile"]
    )


class AibotReportScoreBar(BaseModel):
    name: str = Field(..., max_length=16)
    pct: int = Field(0, ge=0, le=100)


class AibotReportScore(BaseModel):
    """竞争力分数由客户端既有打分表算出，服务端只落库引用。"""

    total: int = 0
    raw: float | None = None
    skipped: list[str] = Field(default_factory=list)
    bars: list[AibotReportScoreBar] = Field(default_factory=list)


class AibotReportLoveIndex(BaseModel):
    value: float = 0
    intent: str | None = Field(default=None, max_length=24)
    intentLabel: str | None = Field(default=None, max_length=24)
    state: str | None = Field(default=None, max_length=16)


class AibotReportRequest(BaseModel):
    score: AibotReportScore | None = None
    love_index: AibotReportLoveIndex | None = None
    agent_id: str | None = Field(default=None, max_length=32)


class AibotReportResponse(BaseModel):
    """完整报告正文（结构对齐前端既有渲染，part08 为情绪与来源）。"""

    version: int
    brand: dict[str, Any] = Field(default_factory=dict)
    member: dict[str, Any] = Field(default_factory=dict)
    date: str = ""
    score: dict[str, Any] = Field(default_factory=dict)
    loveIndex: dict[str, Any] = Field(default_factory=dict)
    letter: str = ""
    part01: dict[str, Any] = Field(default_factory=dict)
    part02: dict[str, Any] = Field(default_factory=dict)
    part03: dict[str, Any] = Field(default_factory=dict)
    part04: dict[str, Any] = Field(default_factory=dict)
    part05: dict[str, Any] = Field(default_factory=dict)
    part06: dict[str, Any] = Field(default_factory=dict)
    part07: dict[str, Any] = Field(default_factory=dict)
    part08: dict[str, Any] | None = None
    matchFocus: list[str] = Field(default_factory=list)
    changelog: str | None = None
    generatedAt: int = 0


class AibotReportListItem(BaseModel):
    version: int
    scene: str = "ai"
    score_total: int = 0
    love_index: float = 0
    emotion_label: str = ""
    created_at: str = ""


class AibotReportListResponse(BaseModel):
    items: list[AibotReportListItem] = Field(default_factory=list)


class AibotTimelineMessageIn(BaseModel):
    """前端 pushMsg 上报的一条 UI 消息。"""

    agent_id: str = Field(..., min_length=1, max_length=32)
    role: str = Field("assistant", max_length=16)
    kind: str = Field("text", max_length=32)
    content: str = Field("", max_length=8000)
    payload: dict[str, Any] | list[Any] | None = None
    session_id: str | None = Field(default=None, max_length=64)
    client_msg_id: str | None = Field(default=None, max_length=128)


class AibotTimelineMessageOut(BaseModel):
    id: int
    message_id: str
    role: str
    kind: str = "text"
    content: str = ""
    payload: Any = None
    client_msg_id: str | None = None
    session_id: str | None = None
    agent_id: str = ""
    created_at: str | None = None
    created_at_ms: int = 0


class AibotTimelinePageResponse(BaseModel):
    items: list[AibotTimelineMessageOut] = Field(default_factory=list)
    has_more: bool = False
    oldest_id: int | None = None
    newest_id: int | None = None
    limit: int = 40


class AibotStoryPlayUpsert(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=64)
    status: str = Field("playing", max_length=16)
    chapter_idx: int = Field(0, ge=0, le=200)


class AibotStoryPlayResponse(BaseModel):
    ok: bool = True
    task_id: str = ""
    status: str = "playing"
    chapter_idx: int = 0
    reason: str | None = None


class AibotStoryPeerResponse(BaseModel):
    """演完后同本同好；无合适人选时 peer 为 null。"""

    peer: dict[str, Any] | None = None


class AibotInterestTagIn(BaseModel):
    tag: str = Field(..., min_length=1, max_length=16)
    category: str | None = Field(default=None, max_length=16)
    heat: float | None = Field(default=None, ge=0, le=1)
    level: str | None = Field(default=None, max_length=8)


class AibotInterestQuizRequest(BaseModel):
    """第 2 轮起兴趣连问出题：前端传入已有标签与已问题干。"""

    agent_id: str | None = Field(default=None, max_length=32)
    session_count: int = Field(1, ge=0, le=99)
    interests: list[AibotInterestTagIn] = Field(default_factory=list, max_length=40)
    asked_questions: list[str] = Field(default_factory=list, max_length=40)
    willingness: str | None = Field(default=None, max_length=120)


class AibotInterestQuizQuestion(BaseModel):
    q: str
    category: str = "生活"
    opts: list[str] = Field(default_factory=list)
    anchor_tags: list[str] = Field(default_factory=list)


class AibotInterestQuizResponse(BaseModel):
    session_theme: str = ""
    question_count: int = 0
    why_count: str = ""
    source: str = "ai"
    questions: list[AibotInterestQuizQuestion] = Field(default_factory=list)
    agent_id: str = ""
    agent_name: str = ""


class AibotProfileBasicParseRequest(BaseModel):
    """经典模式基础信息：从恋爱报告/BOT 采集解析可填字段（不落库）。"""

    bot_intake: dict[str, Any] | None = None
    precise: dict[str, Any] | None = None
    report_profile: list[dict[str, Any]] | None = None


class AibotProfileBasicParseResponse(BaseModel):
    fills: dict[str, Any] = Field(default_factory=dict)
    filled_keys: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    summary: str = ""
    source: str = "ai"
