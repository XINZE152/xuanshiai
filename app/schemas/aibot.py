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
