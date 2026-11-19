"""剧本游玩 / 同好匹配：纯函数与契约口径。"""

from app.services.aibot_story_play import _age_from_birthday


def test_age_from_birthday_basic():
    assert _age_from_birthday("1995-06-15") is not None
    assert _age_from_birthday(None) is None
    assert _age_from_birthday("not-a-date") is None


def test_story_play_routes_registered():
    from app.api.routes import aibot as routes

    paths = {getattr(r, "path", "") for r in routes.router.routes}
    assert "/story-plays" in paths or any("story-plays" in p for p in paths)
    assert any("peer" in p for p in paths)
