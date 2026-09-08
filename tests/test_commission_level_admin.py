"""单元测试：总店红娘 → 分成配置 后台 API。

测试策略（参照 test_apportion_config_admin.py / test_admin_home_routes.py）：
- OpenAPI 路径注册校验；
- 未登录 401；
- Pydantic model_validator 互斥校验；
- 业务约束：mode=fixed 时必须填 fixed_amount。
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.matchmaker_admin import CommissionLevelUpdate


client = TestClient(app)


def test_commission_level_routes_are_registered() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    base = "/api/v1/admin/commission-levels"
    assert "get" in paths[base]
    assert "get" in paths[f"{base}/{{level_id}}"]
    assert "put" in paths[f"{base}/{{level_id}}"]


def test_commission_level_endpoints_require_authentication() -> None:
    assert client.get("/api/v1/admin/commission-levels").status_code == 401
    assert client.get("/api/v1/admin/commission-levels/1").status_code == 401
    response = client.put(
        "/api/v1/admin/commission-levels/1",
        json={"name": "初级分成"},
    )
    assert response.status_code == 401


def test_update_with_mode_fixed_requires_fixed_amount() -> None:
    with pytest.raises(ValidationError) as exc:
        CommissionLevelUpdate(mode="fixed")
    assert "fixed_amount" in str(exc.value)


def test_update_with_mode_rate_passes() -> None:
    body = CommissionLevelUpdate(mode="rate", rate_percent="10.0000")
    assert body.mode == "rate"
    assert str(body.rate_percent) == "10.0000"


def test_update_with_mode_fixed_and_amount_passes() -> None:
    body = CommissionLevelUpdate(mode="fixed", fixed_amount="50.00")
    assert body.mode == "fixed"
    assert str(body.fixed_amount) == "50.00"


def test_update_status_enum_is_1_or_2() -> None:
    with pytest.raises(ValidationError):
        CommissionLevelUpdate(status=3)  # type: ignore[arg-type]


def test_update_name_max_length() -> None:
    with pytest.raises(ValidationError):
        CommissionLevelUpdate(name="x" * 65)


def test_update_promotion_condition_max_length() -> None:
    with pytest.raises(ValidationError):
        CommissionLevelUpdate(promotion_condition="x" * 256)