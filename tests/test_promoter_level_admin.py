"""单元测试：推广红娘后台 → 分成配置 (4 固定级别) API。"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.promoter_level_admin import PromoterLevelUpdate

client = TestClient(app)

BASE = "/api/v1/admin/promoter-levels"


def test_promoter_level_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths[BASE]
    assert "get" in paths[f"{BASE}/{{level_id}}"]
    assert "put" in paths[f"{BASE}/{{level_id}}"]


def test_promoter_level_endpoints_require_authentication() -> None:
    assert client.get(BASE).status_code == 401
    assert client.get(f"{BASE}/1").status_code == 401
    assert client.put(f"{BASE}/1", json={"promote_threshold": 100}).status_code == 401


def test_promote_threshold_min_zero() -> None:
    with pytest.raises(ValidationError):
        PromoterLevelUpdate(promote_threshold=-1)


def test_register_reward_max_decimal() -> None:
    with pytest.raises(ValidationError):
        PromoterLevelUpdate(register_reward_male=10**7)


def test_consume_commission_rate_max_hundred() -> None:
    with pytest.raises(ValidationError):
        PromoterLevelUpdate(consume_commission_mode="auto_rate", consume_commission_rate=101)
