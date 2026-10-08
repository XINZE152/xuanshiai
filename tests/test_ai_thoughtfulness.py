import asyncio
import json
import inspect

import pytest
from pydantic import ValidationError

from app.api.routes import ai
from app.schemas.ai import AIProfileThoughtfulnessRequest, AIProfileThoughtfulnessResponse
from app.services import ai_assistant
from app.services.ai import THOUGHTFULNESS_KEY_LABELS, _thoughtfulness_row_to_response, analyze_thoughtfulness
from app.services.ai_provider import _mock_response


def test_thoughtfulness_routes_are_registered() -> None:
    methods_by_path: dict[str, set] = {}
    for route in ai.router.routes:
        methods_by_path.setdefault(route.path, set()).update(route.methods or set())
    assert methods_by_path["/ai/profile/thoughtfulness"] == {"GET", "POST"}


def test_thoughtfulness_request_contract() -> None:
    request = AIProfileThoughtfulnessRequest(trigger="save", edited_keys=["self_intro", "interest_tags"])
    assert request.trigger == "save"
    assert AIProfileThoughtfulnessRequest().trigger == "save"
    assert AIProfileThoughtfulnessRequest().edited_keys == []
    assert AIProfileThoughtfulnessRequest(analysis_run_id="thoughtfulness-test-1").analysis_run_id == "thoughtfulness-test-1"
    with pytest.raises(ValidationError):
        AIProfileThoughtfulnessRequest(trigger="auto")
    with pytest.raises(ValidationError):
        AIProfileThoughtfulnessRequest(edited_keys=[str(i) for i in range(21)])


def test_thoughtfulness_todo_keys_are_whitelisted() -> None:
    assert set(THOUGHTFULNESS_KEY_LABELS) == {
        "basic_info", "self_intro", "qa_answers", "interest_tags",
        "personality_tags", "mbti", "avatar", "photos",
    }


def test_thoughtfulness_mock_returns_structured_json() -> None:
    raw = _mock_response([{"role": "user", "content": "THOUGHTFULNESS_REVIEW trigger=save\n自我介绍：未填写"}], json_mode=True)
    data = json.loads(raw)
    assert 0 <= int(data["score"]) <= 100
    assert isinstance(data["summary"], str) and data["summary"]
    assert isinstance(data["todos"], list)
    for item in data["todos"]:
        assert item["key"] in THOUGHTFULNESS_KEY_LABELS
        assert item["priority"] in ("high", "medium", "low")


def test_thoughtfulness_row_normalization_filters_invalid_todos() -> None:
    row = {
        "score": 130,
        "summary": " ok ",
        "todos": [
            {"key": "self_intro", "label": "", "advice": "建议补充", "priority": "high"},
            {"key": "hacker", "label": "x", "advice": "ignore", "priority": "low"},
            {"key": "mbti", "label": "MBTI", "advice": "", "priority": "low"},
            "not-a-dict",
        ],
        "created_at": "2026-09-07T09:00:00",
        "updated_at": "2026-09-07T09:30:00",
    }
    response = _thoughtfulness_row_to_response(row)
    assert isinstance(response, AIProfileThoughtfulnessResponse)
    assert response.score == 100
    assert response.summary == "ok"
    assert [t.key for t in response.todos] == ["self_intro"]
    assert response.todos[0].label == "自我介绍"


def test_thoughtfulness_reads_the_real_profile_table() -> None:
    source = inspect.getsource(analyze_thoughtfulness)
    assert "LEFT JOIN user_profile p ON p.user_id = u.id" in source
    assert "LEFT JOIN profiles p ON p.user_id = u.id" not in source


def test_thoughtfulness_reanalysis_carries_run_id_and_rewrite_guard() -> None:
    source = inspect.getsource(analyze_thoughtfulness)
    assert "analysis_run_id" in source
    assert "THOUGHTFULNESS_REWRITE" in source
    assert "上一版结果" in source

class _Row(dict):
    """SQLAlchemy Row 的替身：缺失列返回 None，贴近 LEFT JOIN 未命中的行为。"""

    def __missing__(self, key: str) -> None:
        return None


class _FakeMappings:
    def __init__(self, row) -> None:
        self._row = row

    def first(self):
        return self._row

    def one(self):
        return self._row


class _FakeResult:
    def __init__(self, row=None, scalar_value=0) -> None:
        self._row = row
        self._scalar = scalar_value

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._row)

    def scalar(self):
        return self._scalar


class _FakeDb:
    """按 SQL 片段分派的最小 AsyncSession 替身，只覆盖用心度用到的四类查询。"""

    def __init__(self, profile_row, previous_row=None, final_row=None) -> None:
        self.profile_row = profile_row
        self.previous_row = previous_row
        self.final_row = final_row or {
            "score": 88, "summary": "落库摘要", "todos": [],
            "created_at": "2026-10-07T10:00:00", "updated_at": "2026-10-07T10:00:00",
        }
        self.statements: list[str] = []
        self.committed = False

    async def execute(self, statement, params=None) -> _FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        if "COUNT(*) FROM user_media" in sql:
            return _FakeResult(None, 3)
        if "SELECT summary, todos" in sql:
            return _FakeResult(self.previous_row)
        if "SELECT score, summary, todos" in sql:
            return _FakeResult(self.final_row)
        return _FakeResult(self.profile_row)

    async def commit(self) -> None:
        self.committed = True


def _patch_thoughtfulness_model(monkeypatch) -> dict:
    """替换额度消耗与模型调用，捕获真正喂给模型的用户消息。"""
    captured: dict = {}

    async def fake_quota(db, user_id, code, limit):
        return "quota-key"

    async def fake_complete(messages, json_mode=False, scene=""):
        captured["prompt"] = messages[-1]["content"]
        return json.dumps({"score": 80, "summary": "模型摘要", "todos": []})

    monkeypatch.setattr(ai_assistant, "_consume_ai_quota", fake_quota)
    monkeypatch.setattr(ai_assistant, "complete", fake_complete)
    return captured


def test_analyze_thoughtfulness_reads_saved_qa_answers_column() -> None:
    source = inspect.getsource(analyze_thoughtfulness)
    assert "p.qa_answers" in source


def test_analyze_thoughtfulness_prompt_contains_saved_qa_answers(monkeypatch) -> None:
    captured = _patch_thoughtfulness_model(monkeypatch)
    profile_row = _Row({
        "nickname": "小爱",
        "avatar": "https://cdn/avatar.webp",
        "self_intro": "周末喜欢爬山和做饭",
        "qa_answers": json.dumps([
            {"question_id": 2, "question": "喜欢什么运动", "answer": "周末常去奥森打羽毛球"},
            {"question_id": 3, "question": "你期待的爱情是什么样子的", "answer": ""},
        ], ensure_ascii=False),
    })
    db = _FakeDb(profile_row)

    response = asyncio.run(analyze_thoughtfulness(
        db, 7, AIProfileThoughtfulnessRequest(trigger="save", edited_keys=["qa_answers"]),
    ))

    prompt = captured["prompt"]
    assert response.score == 88  # 返回值是 INSERT 后重读的落库行，不是模型原始分
    assert "关于我问答「喜欢什么运动」：周末常去奥森打羽毛球" in prompt
    # 空答案不得当成已回答，且未答题数必须如实告诉模型。
    assert "关于我问答：已回答 1 题，未回答 2 题" in prompt
    assert "「你期待的爱情是什么样子的」：" not in prompt
    assert db.committed is True


def test_analyze_thoughtfulness_prompt_handles_missing_qa_column(monkeypatch) -> None:
    captured = _patch_thoughtfulness_model(monkeypatch)
    db = _FakeDb(_Row({"nickname": "小爱", "qa_answers": None}))

    asyncio.run(analyze_thoughtfulness(db, 7, AIProfileThoughtfulnessRequest(trigger="manual")))

    assert "关于我问答：已回答 0 题，未回答 3 题" in captured["prompt"]
