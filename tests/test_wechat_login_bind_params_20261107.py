"""微信登录老用户 500：绑定参数缺失回归（2026-11-07）。

背景：A12 login_num 重构把 ``login_wechat`` 里内联的 UPDATE 换成
``touch_login(extra_set=...)``，但执行的参数字典只留了 ``{"id": user_id}``。
``COALESCE(:nickname, nickname)`` 想保留原值的前提是字典里存在 ``nickname``
这个键且值为 ``None``；"键缺失" 会让 SQLAlchemy 在参数构造阶段抛
``InvalidRequestError: A value is required for bind parameter 'nickname'``，
于是所有 openid 已存在的微信用户登录都 500（测试服 id=34 复现）。

覆盖：
- ``touch_login`` 把 ``extra_params`` 合并进绑定参数；
- ``login_wechat`` 老用户分支 UPDATE 的占位符与参数一一对应，客户端不传
  nickname/avatar 时也必须绑定 ``None``；
- ``httpx`` logger 被压到 WARNING，避免 jscode2session 的 AppSecret 明文进日志。

无真实 DB / 无真实微信调用：全部走记录型桩。
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import pytest

from app.core.logging import configure_logging
from app.schemas.auth import WechatLoginRequest
from app.services import auth as auth_service


class _Result:
    def __init__(self, *, first=None) -> None:
        self._first = first

    def mappings(self) -> "_Result":
        return self

    def first(self):
        return self._first


class _Ctx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc) -> bool:
        return False


class _StubDB:
    """按顺序吐出预置结果，并记录压平后的 (sql, params)。"""

    def __init__(self, results: list[_Result] | None = None) -> None:
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])

    async def execute(self, statement, params=None):  # noqa: ANN001
        self.statements.append((" ".join(str(statement).split()), dict(params or {})))
        return self._results.pop(0) if self._results else _Result()

    def begin(self) -> _Ctx:
        return _Ctx()


def _bound_names(sql: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r":(\w+)", sql)}


def _update_users(db: _StubDB) -> tuple[str, dict]:
    matches = [item for item in db.statements if item[0].startswith("UPDATE users")]
    assert matches, db.statements
    return matches[-1]


def _stub_login_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _identity(_code: str) -> dict[str, str]:
        return {"openid": "openid-existing"}

    async def _noop(*_args, **_kwargs) -> None:
        return None

    async def _session(*_args, **_kwargs) -> dict:
        return {"access_token": "access", "refresh_token": "refresh", "expires_in": 60, "user_id": 34}

    monkeypatch.setattr(auth_service, "exchange_wechat_code", _identity)
    monkeypatch.setattr(auth_service, "ensure_default_user_role", _noop)
    monkeypatch.setattr(auth_service, "create_session", _session)


@pytest.mark.asyncio
async def test_touch_login_binds_extra_params() -> None:
    db = _StubDB([_Result(first=(1,))])
    await auth_service.touch_login(
        db,
        9,
        extra_set="nickname = COALESCE(:nickname, nickname), avatar = COALESCE(:avatar, avatar)",
        extra_params={"nickname": "小明", "avatar": None},
    )

    sql, params = _update_users(db)
    assert _bound_names(sql) == set(params)
    assert params == {"id": 9, "nickname": "小明", "avatar": None}


@pytest.mark.asyncio
async def test_wechat_login_existing_user_binds_profile_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_login_dependencies(monkeypatch)
    db = _StubDB([
        _Result(first={"id": 34, "phone": "13800000000", "status": 1}),
        _Result(first=(1,)),
    ])

    tokens = await auth_service.login_wechat(
        db,
        WechatLoginRequest(code="stub", nickname="新昵称", avatar="https://cdn.example/a.png"),
        "1.2.3.4",
        "pytest",
    )

    sql, params = _update_users(db)
    # 占位符与参数必须双向一致：少一个键就是这次线上 500 的根因。
    assert _bound_names(sql) == set(params)
    assert params == {"id": 34, "nickname": "新昵称", "avatar": "https://cdn.example/a.png"}
    assert "login_num = login_num + 1" in sql
    assert tokens["need_bind_phone"] is False


@pytest.mark.asyncio
async def test_wechat_login_existing_user_binds_none_when_profile_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前端只发 {code, platform} 时，COALESCE 依赖的键也必须显式为 None。"""
    _stub_login_dependencies(monkeypatch)
    db = _StubDB([
        _Result(first={"id": 34, "phone": None, "status": 1}),
        _Result(first=(1,)),
    ])

    tokens = await auth_service.login_wechat(db, WechatLoginRequest(code="stub"), None, None)

    sql, params = _update_users(db)
    assert _bound_names(sql) == set(params)
    assert params["nickname"] is None
    assert params["avatar"] is None
    assert tokens["need_bind_phone"] is True


def test_configure_logging_silences_httpx_url_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """httpx INFO 会打印含 secret 的完整 URL，必须压到 WARNING。"""
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    httpx_logger = logging.getLogger("httpx")
    saved_flag = getattr(root, "_xuanshi_configured", False)
    saved_root_level = root.level
    saved_httpx_level = httpx_logger.level
    before = list(root.handlers)
    try:
        if hasattr(root, "_xuanshi_configured"):
            del root._xuanshi_configured
        httpx_logger.setLevel(logging.NOTSET)
        configure_logging(SimpleNamespace(log_level="INFO"))

        assert httpx_logger.level == logging.WARNING
        assert not httpx_logger.isEnabledFor(logging.INFO)
        # 其余业务日志仍按根级别输出，不是把全局日志一起关掉。
        assert logging.getLogger("app.services.auth").isEnabledFor(logging.INFO)
    finally:
        for handler in [item for item in root.handlers if item not in before]:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(saved_root_level)
        httpx_logger.setLevel(saved_httpx_level)
        if saved_flag:
            root._xuanshi_configured = True
        elif hasattr(root, "_xuanshi_configured"):
            del root._xuanshi_configured
