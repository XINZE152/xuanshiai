"""独立角色扮演的源码级契约（不依赖数据库或 Provider）。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_roleplay_characters_are_independent_and_proactive() -> None:
    from app.services.ai.roleplay_scene import (
        PUBLIC_SCENE_FIELDS,
        ROLEPLAY_SCENES,
        build_roleplay_system_prompt,
        public_scenes,
        validate_scene_key,
    )

    assert 3 <= len(ROLEPLAY_SCENES) <= 5
    assert validate_scene_key("coffee_chat")
    assert not validate_scene_key("custom_role")
    for scene in ROLEPLAY_SCENES:
        assert scene["role_name"] and "知遇" not in scene["role_name"]
        assert scene["avatar_token"].startswith("--")
        assert scene["avatar_glyph"]
        assert scene["opening_line"].strip()
    # persona 与安全规则不下发客户端
    assert "persona" not in PUBLIC_SCENE_FIELDS
    assert all("persona" not in item for item in public_scenes())

    prompt = build_roleplay_system_prompt("coffee_chat", "想聊得慢一点")
    assert "知遇" not in prompt
    assert "MOXIANG_ROLE_NAME" not in prompt
    for rule in ("AI 角色扮演", "不冒充真人", "最多主动问一个问题", "不编造", "不承诺"):
        assert rule in prompt


def test_roleplay_prompt_rejects_unknown_scene() -> None:
    import pytest

    from app.services.ai.roleplay_scene import build_roleplay_system_prompt

    with pytest.raises(ValueError):
        build_roleplay_system_prompt("not_in_whitelist")


def test_roleplay_route_uses_existing_tables_and_commits() -> None:
    route = _read("app/api/routes/ai_roleplay.py")
    for path in ("/roleplay/scenes", "/roleplay/turns", "/roleplay/submit", "/roleplay/interests/confirm"):
        assert path in route
    assert "current_user.id" in route
    assert "await db.commit()" in route
    assert "AiFeatureDisabledError" in route
    assert "source_type" in route and "ai_roleplay" in route
    assert "moderate_text" in route
    assert "ai_roleplay_session" not in route
    assert "ai_roleplay_turn" not in route
    assert "moxiang_ip" not in route
    assert "current_user.user_id" not in route


def test_no_new_schema_columns_or_migrations() -> None:
    schema = _read("app/db/ai_schema.py")
    assert "enum('freechat','roleplay')" not in schema
    assert "metrics_snapshot_json" not in schema
    assert "ensure_ai_profile_metric_columns" not in schema

    metrics = _read("app/api/routes/ai_profile_metrics.py")
    assert "metrics_snapshot_json" not in metrics
    assert "ai_profile_revision_field" in metrics

    manifest = _read("migrations/ai/manifest.json")
    assert "HASH_UP" not in manifest and "HASH_DOWN" not in manifest
    assert "20261109" not in manifest

    assert not list((ROOT / "migrations/ai").glob("20261109*"))


def test_metrics_snapshot_only_reads_frozen_fields() -> None:
    from app.services.ai.metric_snapshot import compute_metric_snapshot

    fields = [
        {"field_key": "age", "subject": "personal", "field_kind": "structured",
         "profile_dimension": "lifestyle", "content_hash": "a" * 64, "confidence": 1.0},
        {"field_key": "entry_lifestyle_x", "subject": "personal", "field_kind": "entry",
         "profile_dimension": "lifestyle", "content_hash": "b" * 64, "confidence": 1.0},
    ]
    snapshot = compute_metric_snapshot(fields)
    assert snapshot["confirmed_field_count"] == 1
    assert snapshot["entry_count"] == 1
    assert 0 <= snapshot["overall_percent"] <= 100
    assert len(snapshot["dimensions"]) == 6
    assert compute_metric_snapshot([])["dimensions"] == [] or all(
        item["percent"] == 0 for item in compute_metric_snapshot([])["dimensions"]
    )


def test_router_mounts_roleplay_only_once() -> None:
    router = _read("app/api/router.py")
    assert len(re.findall(r"include_router\(ai_roleplay\.router", router)) == 1
    assert len(re.findall(r"include_router\(ai_profile_metrics\.router", router)) == 1
    assert "from app.api.routes import ai_roleplay" not in router


def test_roleplay_api_contract_doc_exists() -> None:
    doc = _read("docs/api/AI角色扮演.md")
    for required in ("请求参数", "返回参数", "错误码", "业务规则", "兼容性", "幂等", "非法示例"):
        assert required in doc
    assert "不新建表" in doc
