import json
import inspect

import pytest
from pydantic import ValidationError

from app.api.routes import ai
from app.schemas.ai import AIProfileThoughtfulnessRequest, AIProfileThoughtfulnessResponse
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
