"""牵线超时自动失败 + match-records 列表字段/删除端点回归测试（2026-10-28）。

覆盖：
- sweep_match_timeouts：关闭态空跑、非法/非正 hours 跳过、无超时命中不动行、
  命中时置失败（status=2）+ 逐条写审计 + 提交；SELECT/UPDATE 口径正确；
- 配置域 tools_match_timeout 已注册（enabled/hours 默认值）；
- MatchRecordItem 补齐派生字段契约；
- 路由 DELETE /admin/matchmaker/match-records/{record_id} 已注册（敏感写）。

无真实 DB：monkeypatch get_runtime_value；FakeDb 记录 SQL 与写调用。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.schemas.matchmaker_crm_admin import MatchRecordItem
from app.services import admin_config as admin_config_module
from app.services import match_timeout as sweep


class _FakeMappings:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def all(self) -> list[dict]:
        return self._rows


class _FakeResult:
    def __init__(self, rowcount: int = 1, mappings_rows: list[dict] | None = None) -> None:
        self.rowcount = rowcount
        self._mappings = mappings_rows or []

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._mappings)


class _FakeDb:
    """按 SQL 关键字路由返回值，并记录所有执行过的语句。"""

    def __init__(self, *, select_ids: list[int] | None = None, update_rowcount: int = 1) -> None:
        self.select_ids = select_ids or []
        self.update_rowcount = update_rowcount
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.committed = False

    async def execute(self, stmt, params=None):  # noqa: ANN001
        sql = str(stmt)
        self.statements.append(sql)
        self.params.append(dict(params or {}))
        if sql.lstrip().upper().startswith("SELECT"):
            rows = [{"id": i} for i in self.select_ids]
            return _FakeResult(mappings_rows=rows)
        if sql.lstrip().upper().startswith("UPDATE"):
            return _FakeResult(rowcount=self.update_rowcount)
        return _FakeResult()

    async def commit(self) -> None:
        self.committed = True


async def _patch_config(monkeypatch: pytest.MonkeyPatch, values: dict) -> None:
    async def fake_get_runtime_value(_db, _ns, key, fallback):
        return values.get(key, fallback)

    monkeypatch.setattr(sweep, "get_runtime_value", fake_get_runtime_value)


@pytest.mark.asyncio
async def test_sweep_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    await _patch_config(monkeypatch, {"enabled": False})
    db = _FakeDb(select_ids=[1, 2])

    out = await sweep.sweep_match_timeouts(db)

    assert out == {"enabled": False, "hours": None, "affected": 0}
    assert db.statements == []  # 关闭态完全不查库
    assert db.committed is False


@pytest.mark.asyncio
async def test_sweep_non_positive_hours_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    await _patch_config(monkeypatch, {"enabled": True, "hours": 0})
    db = _FakeDb(select_ids=[1])

    out = await sweep.sweep_match_timeouts(db)

    assert out == {"enabled": True, "hours": 0, "affected": 0}
    assert db.statements == []  # 非法时长直接跳过，不触碰行


@pytest.mark.asyncio
async def test_sweep_invalid_hours_string_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    await _patch_config(monkeypatch, {"enabled": True, "hours": "abc"})
    db = _FakeDb(select_ids=[])  # 回落 DEFAULT_HOURS 后无命中

    out = await sweep.sweep_match_timeouts(db)

    assert out["hours"] == sweep.DEFAULT_HOURS
    assert out["affected"] == 0
    # 命中为空：只有 SELECT，不 UPDATE、不写审计、不提交
    assert all(s.lstrip().upper().startswith("SELECT") for s in db.statements)
    assert db.committed is False


@pytest.mark.asyncio
async def test_sweep_marks_timeout_and_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    await _patch_config(monkeypatch, {"enabled": True, "hours": 1})
    db = _FakeDb(select_ids=[11, 22], update_rowcount=2)

    out = await sweep.sweep_match_timeouts(db)

    assert out == {"enabled": True, "hours": 1, "affected": 2}
    select_sql = db.statements[0]
    assert "a.status = 0" in select_sql
    assert "DATE_SUB(UTC_TIMESTAMP(), INTERVAL :hours HOUR)" in select_sql
    update_sql = next(s for s in db.statements if s.lstrip().upper().startswith("UPDATE"))
    assert "status = 2" in update_sql and "AND status = 0" in update_sql
    # 每条命中各写一行审计，actor 为 NULL（系统任务）
    audits = [s for s in db.statements if "match_record.timeout_fail" in s]
    assert len(audits) == 2
    reason_params = [p for p in db.params if p.get("reason") == sweep.TIMEOUT_REASON]
    assert len(reason_params) == 2
    assert db.committed is True


def test_timeout_domain_registered() -> None:
    entry = admin_config_module.DEFAULT_CONFIGS["tools_match_timeout"]
    name, _desc, default_config, sensitive_keys = entry
    assert name == "牵线超时自动失败"
    assert default_config == {"enabled": False, "hours": 48}
    assert sensitive_keys == []


def test_match_record_item_has_derived_fields() -> None:
    now = datetime(2026, 10, 28)
    item = MatchRecordItem(
        id=1, from_user_id=2, to_user_id=3, status=0, created_at=now, responded_at=None,
        from_gender=1, to_gender=2, from_age=26, to_age=36,
        from_is_verified=True, to_is_verified=False,
        from_has_promise=True, to_has_promise=False, matchmaker_name="红娘A",
        from_avatar="/storage/a.jpg", to_avatar="/storage/b.jpg",
        from_member_code="G000002", to_member_code="G000003",
    )
    assert item.from_gender == 1 and item.to_age == 36
    assert item.from_is_verified is True and item.to_is_verified is False
    assert item.from_has_promise is True and item.to_has_promise is False
    assert item.matchmaker_name == "红娘A"
    assert item.from_member_code == "G000002" and item.to_avatar == "/storage/b.jpg"
    # 默认可选：旧库无派生列时不报错
    bare = MatchRecordItem(id=1, from_user_id=2, to_user_id=3, status=0, created_at=now, responded_at=None)
    assert bare.from_is_verified is False and bare.matchmaker_name is None
    assert bare.from_avatar is None and bare.to_member_code is None


def test_match_record_item_bool_fields_tolerate_none() -> None:
    # 回归：LEFT JOIN 无实名/无承诺书关联时布尔派生列取到 NULL，
    # 旧版 bool 字段收到 None 直接 ValidationError 拖垮整页 500。现必须归一为 False。
    now = datetime(2026, 10, 28)
    item = MatchRecordItem(
        id=1, from_user_id=2, to_user_id=3, status=0, created_at=now, responded_at=None,
        from_is_verified=None, to_is_verified=None,
        from_has_promise=None, to_has_promise=None,
    )
    assert item.from_is_verified is False and item.to_is_verified is False
    assert item.from_has_promise is False and item.to_has_promise is False
    # 真值不被误吞：0/1/int 仍能正确转布尔。
    truthy = MatchRecordItem(
        id=1, from_user_id=2, to_user_id=3, status=0, created_at=now, responded_at=None,
        from_is_verified=1, to_is_verified=0, from_has_promise=0, to_has_promise=1,
    )
    assert truthy.from_is_verified is True and truthy.to_is_verified is False
    assert truthy.from_has_promise is False and truthy.to_has_promise is True


def test_delete_route_registered_and_openapi_fields() -> None:
    from app.api.routes import matchmaker_crm_admin as crm_routes
    from app.main import app

    del_paths = {r.path for r in crm_routes.router.routes if "DELETE" in (r.methods or set())}
    assert "/admin/matchmaker/match-records/{record_id}" in del_paths

    spec = app.openapi()
    props = spec["components"]["schemas"]["MatchRecordItem"]["properties"]
    for field in (
        "from_gender", "to_gender", "from_age", "to_age",
        "from_is_verified", "to_is_verified", "from_has_promise", "to_has_promise",
        "matchmaker_name", "from_avatar", "to_avatar", "from_member_code", "to_member_code",
    ):
        assert field in props, f"MatchRecordItem 缺字段 {field}"
    # OpenAPI 路径带 api_prefix（/api/v1），按含前缀的真实路径断言 delete 已挂载。
    delete_path = next((p for p in spec["paths"] if p.endswith("/admin/matchmaker/match-records/{record_id}")), None)
    assert delete_path is not None
    assert "delete" in spec["paths"][delete_path]
