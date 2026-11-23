"""红娘记忆分桶：隔离开关开时读写按 agent；关时退回全局。"""

from app.services.aibot_profile_sync import (
    clear_bot_intake_for_agent,
    flat_bot_intake_for_agent,
    merge_bot_intake_for_agent,
)


def test_merge_isolated_buckets_do_not_leak(monkeypatch):
    monkeypatch.setattr(
        "app.services.aibot_profile_sync.agent_memory_isolated", lambda: True
    )
    root = merge_bot_intake_for_agent({}, {"gender": "女", "city": "杭州"}, "anian")
    root = merge_bot_intake_for_agent(root, {"gender": "男", "city": "南京"}, "yuzhou")
    anian = flat_bot_intake_for_agent(root, "anian")
    yuzhou = flat_bot_intake_for_agent(root, "yuzhou")
    assert anian.get("gender", {}).get("id") == "女" or anian.get("gender") == "女" or (
        isinstance(anian.get("gender"), dict) and anian["gender"]["id"] == "女"
    )
    assert yuzhou.get("gender", {}).get("id") == "男" or (
        isinstance(yuzhou.get("gender"), dict) and yuzhou["gender"]["id"] == "男"
    )
    assert "agents" in root
    assert "anian" in root["agents"] and "yuzhou" in root["agents"]


def test_clear_one_agent_keeps_other(monkeypatch):
    monkeypatch.setattr(
        "app.services.aibot_profile_sync.agent_memory_isolated", lambda: True
    )
    root = merge_bot_intake_for_agent({}, {"city": "杭州"}, "anian")
    root = merge_bot_intake_for_agent(root, {"city": "南京"}, "yuzhou")
    cleared = clear_bot_intake_for_agent(root, "anian")
    assert flat_bot_intake_for_agent(cleared, "anian") == {} or (
        flat_bot_intake_for_agent(cleared, "anian").get("city") is None
    )
    yuzhou = flat_bot_intake_for_agent(cleared, "yuzhou")
    assert isinstance(yuzhou.get("city"), dict)
    assert yuzhou["city"]["id"] == "南京"


def test_shared_mode_reads_flat(monkeypatch):
    monkeypatch.setattr(
        "app.services.aibot_profile_sync.agent_memory_isolated", lambda: False
    )
    flat = flat_bot_intake_for_agent(
        {"gender": {"id": "女", "label": "女"}, "agents": {"anian": {"city": {"id": "杭", "label": "杭"}}}},
        "anian",
    )
    assert flat.get("gender", {}).get("id") == "女"
    assert flat.get("city", {}).get("id") == "杭"
