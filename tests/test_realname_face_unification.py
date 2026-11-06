"""D-14 回归：实名审核双写统一 + 人脸适配层。

历史缺陷：实名有两条并行审核链路，写入不同列——
* 旧后台实名审核（`app/services/auth.py::review_realname`）写 `user_auth.realname_status`；
* 会员认证后台（`app/services/member_auth_admin.py::review_auth`）只写 `user_auth.face_verified`。

而 C 端门禁 `get_realname_verified_user` / `get_face_verified_user` 读的是
`realname_status == 2`，于是「在会员认证后台通过实名」不会让用户过门禁。

验收口径（见 docs/全项目后端详细修改方案清单-2026-10-12.md D-14）：
1. 两个后台入口都必须写 `realname_status`；
2. 会员认证后台审核通过 → `realname_status=2` 且 `face_verified=1`（同步人脸标记）；
3. 旧后台实名审核通过 → 只写 `realname_status`，不越权授予人脸标记；
4. 审核未通过 → `realname_status=3`；
5. 门禁读取的列与审核写入的列一致（防再次分裂）；
6. 人脸适配层：disabled fail-closed，mock 仅测试环境可用。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import auth as auth_service
from app.services import member_auth_admin as member_auth
from app.services.face import providers as face_providers
from app.schemas.member_auth_admin import ReviewActionRequest

USER_ID = 7
REVIEW_ID = 33


class _Mapping(dict):
    def mappings(self):
        return self

    def first(self):
        return self

    def scalar(self):
        return list(self.values())[0] if self else None


class StubDB:
    """记录全部 SQL 的假会话。"""

    def __init__(self, results=None):
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        return _Mapping()

    async def commit(self):
        return None

    def sql(self) -> str:
        return "\n".join(text for text, _ in self.statements)

    def params_of(self, needle: str) -> list[dict]:
        return [params for text, params in self.statements if needle in text]


# --------------------------------------------------------------------------- #
# 统一落列函数
# --------------------------------------------------------------------------- #


def test_shared_review_writes_gate_column() -> None:
    db = StubDB()
    asyncio.run(auth_service.apply_realname_review(db, USER_ID, auth_service.REALNAME_STATUS_PASSED, None, 99))
    sql = db.sql()
    assert "realname_status=:status" in sql, "必须写入门禁读取的 realname_status"
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["status"] == 2 and params["auth_step"] == 4
    assert "face_verified=:face" not in sql, "纯实名审核不应写人脸标记"


def test_shared_review_syncs_face_marker_when_requested() -> None:
    db = StubDB()
    asyncio.run(
        auth_service.apply_realname_review(
            db, USER_ID, auth_service.REALNAME_STATUS_PASSED, None, 99, sync_face=True
        )
    )
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["face"] == 1, "会员认证后台通过审核须同步人脸标记"


def test_shared_review_rejection_sets_failed_status() -> None:
    db = StubDB()
    asyncio.run(
        auth_service.apply_realname_review(
            db, USER_ID, auth_service.REALNAME_STATUS_FAILED, "证件不清晰", 99, sync_face=True
        )
    )
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["status"] == 3 and params["face"] == 2 and params["reason"] == "证件不清晰"


def test_shared_review_rejects_unsupported_status() -> None:
    db = StubDB()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_service.apply_realname_review(db, USER_ID, 1, None, 99))
    assert exc.value.status_code == 422
    assert db.statements == []


# --------------------------------------------------------------------------- #
# 两个后台入口
# --------------------------------------------------------------------------- #


def test_member_auth_backend_realname_approval_sets_both_columns() -> None:
    db = StubDB(results=[_Mapping({"id": REVIEW_ID, "user_id": USER_ID})])
    asyncio.run(member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=1), 99))
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["status"] == 2, "会员认证后台通过实名必须写 realname_status=2"
    assert params["face"] == 1
    assert params["user_id"] == USER_ID, "必须以 user_id 定位（审核记录主键是 user_auth.id）"


def test_member_auth_backend_realname_rejection_sets_both_columns() -> None:
    db = StubDB(results=[_Mapping({"id": REVIEW_ID, "user_id": USER_ID})])
    asyncio.run(
        member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=2, remark="照片不符"), 99)
    )
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["status"] == 3 and params["face"] == 2


def test_old_backend_realname_approval_does_not_grant_face_marker() -> None:
    db = StubDB(results=[_Mapping({"realname_status": 1})])
    asyncio.run(auth_service.review_realname(db, USER_ID, 2, None, 99))
    sql = db.sql()
    assert "realname_status=:status" in sql
    assert "face_verified=:face" not in sql, "旧后台实名审核不得越权写人脸标记"


def test_both_backends_share_one_implementation() -> None:
    """两个入口必须调用同一落列函数，防止再次出现双写分裂。"""
    member_src = inspect.getsource(member_auth.review_auth)
    assert "apply_realname_review" in member_src, "会员认证后台未复用统一落列函数"
    old_src = inspect.getsource(auth_service.review_realname)
    assert "apply_realname_review" in old_src, "旧后台未复用统一落列函数"
    # 旧后台不得再自行拼 UPDATE ... face_verified 语句
    assert "face_verified" not in old_src


# --------------------------------------------------------------------------- #
# 人脸适配层
# --------------------------------------------------------------------------- #


def test_face_disabled_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "face_provider", "disabled")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(face_providers.get_face_provider().verify(user_id=USER_ID, image_ref="x"))
    assert exc.value.status_code == 503


def test_face_mock_provider_respects_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "testing")
    monkeypatch.setattr(settings, "face_provider", "mock")
    monkeypatch.setattr(settings, "face_mock_pass", True)
    result = asyncio.run(face_providers.get_face_provider().verify(user_id=USER_ID, image_ref="x"))
    assert result.verified is True and result.provider == "mock"

    monkeypatch.setattr(settings, "face_mock_pass", False)
    result = asyncio.run(face_providers.get_face_provider().verify(user_id=USER_ID, image_ref="x"))
    assert result.verified is False


def test_face_mock_provider_rejected_outside_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "face_provider", "mock")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(face_providers.get_face_provider().verify(user_id=USER_ID, image_ref="x"))
    assert exc.value.status_code == 503


def test_face_unknown_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "face_provider", "no-such-provider")
    with pytest.raises(HTTPException) as exc:
        face_providers.get_face_provider()
    assert exc.value.status_code == 503


def test_face_aliyun_provider_is_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "face_provider", "aliyun")
    assert face_providers.get_face_provider().name == "aliyun"
