"""恋 AI BOT 会话 API schemas。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AibotAssignmentResponse(BaseModel):
    agent_id: str | None = None
    assigned_at: str | None = None


class AibotAssignmentUpdate(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=32)


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
