"""D-14 回归：实名审核双写统一 + 人脸适配层。

历史缺陷：实名有两条并行审核链路，写入不同列——
* 旧后台实名审核（`app/services/auth.py::review_realname`）写 `user_auth.realname_status`；
* 会员认证后台（`app/services/member_auth_admin.py::review_auth`）只写 `user_auth.face_verified`。

而 C 端门禁 `get_realname_verified_user` / `get_face_verified_user` 读的是
`realname_status == 2`，于是「在会员认证后台通过实名」不会让用户过门禁。

验收口径（见 docs/全项目后端详细修改方案清单-2026-10-12.md D-14）：
1. 两个后台入口都必须写 `realname_status`；
2. 会员认证后台审核通过 → `realname_status=2`；只有已存人脸材料才同步 `face_verified=1`；
3. 旧后台实名审核通过 → 只写 `realname_status`，不越权授予人脸标记；
4. 审核未通过 → `realname_status=3`；
5. 门禁读取的列与审核写入的列一致（防再次分裂）；
6. 人脸适配层：disabled fail-closed，mock 仅测试环境可用。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.routes import admin, matchmaker_crm_admin, member_auth_admin
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

    def __init__(self, results=None, *, fail_notification=False):
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])
        self.commits = 0
        self.fail_notification = fail_notification

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self.fail_notification and "INSERT INTO user_notification" in str(statement):
            raise RuntimeError("通知写入失败")
        if self._results:
            return self._results.pop(0)
        return _Mapping()

    async def commit(self):
        self.commits += 1

    def sql(self) -> str:
        return "\n".join(text for text, _ in self.statements)

    def params_of(self, needle: str) -> list[dict]:
        return [params for text, params in self.statements if needle in text]


def _review_row(status=1, version=1, photo=None):
    return _Mapping(user_id=USER_ID, realname_status=status, retry_count=version,
                    real_name="测试用户", id_card="encrypted", id_card_hash=None,
                    face_verified=0, face_photo=photo)


# --------------------------------------------------------------------------- #
# 统一落列函数
# --------------------------------------------------------------------------- #


def test_shared_review_writes_gate_column() -> None:
    db = StubDB([_review_row()])
    asyncio.run(auth_service.apply_realname_review(db, USER_ID, auth_service.REALNAME_STATUS_PASSED, None, 99))
    sql = db.sql()
    assert "realname_status=:status" in sql, "必须写入门禁读取的 realname_status"
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["status"] == 2 and params["auth_step"] == 4
    assert "face_verified=:face" not in sql, "纯实名审核不应写人脸标记"


def test_shared_review_syncs_face_marker_when_requested() -> None:
    db = StubDB([_review_row(photo="/face-proof")])
    asyncio.run(
        auth_service.apply_realname_review(
            db, None, auth_service.REALNAME_STATUS_PASSED, None, 99, review_id=REVIEW_ID
        )
    )
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["face"] == 1, "会员认证后台通过审核须同步人脸标记"


def test_shared_review_rejection_sets_failed_status() -> None:
    db = StubDB([_review_row(photo="/face-proof")])
    asyncio.run(
        auth_service.apply_realname_review(
            db, None, auth_service.REALNAME_STATUS_FAILED, "document_unclear", 99, review_id=REVIEW_ID
        )
    )
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["status"] == 3 and params["face"] == 2
    assert params["reason"] == "证件资料不清晰，请核对后重新提交"


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
    db = StubDB(results=[_review_row(photo="/face-proof")])
    asyncio.run(member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=1), 99))
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["status"] == 2, "会员认证后台通过实名必须写 realname_status=2"
    assert params["face"] == 1
    assert params["user_id"] == USER_ID, "必须以 user_id 定位（审核记录主键是 user_auth.id）"


def test_member_auth_backend_realname_rejection_sets_both_columns() -> None:
    db = StubDB(results=[_review_row(photo="/face-proof")])
    asyncio.run(
        member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=2, remark="照片不符"), 99)
    )
    params = db.params_of("UPDATE user_auth SET")[0]
    assert params["status"] == 3 and params["face"] == 2


def test_member_auth_backend_house_approval_maps_to_public_verified_state() -> None:
    db = StubDB(results=[_Mapping({"id": REVIEW_ID, "house_cert": "/proof.jpg", "house_verified": 1})])
    asyncio.run(member_auth.review_auth(db, "house", REVIEW_ID, ReviewActionRequest(status=1), 99))
    update = db.params_of("UPDATE user_auth SET house_verified")[0]
    assert update["st"] == 2
    assert update["remark"] is None
    assert "house_reviewed_at = UTC_TIMESTAMP()" in db.sql()


@pytest.mark.parametrize(
    ("stored_status", "expected_result"),
    [(0, "pending"), (1, "pending"), (2, "pass"), (3, "fail")],
)
def test_house_admin_list_uses_public_four_state_mapping(stored_status: int, expected_result: str) -> None:
    item = member_auth._build_house({
        "id": REVIEW_ID,
        "user_id": USER_ID,
        "member_code": "G000007",
        "nickname": "Member",
        "avatar": None,
        "real_name": None,
        "id_card": None,
        "house_cert": "/proof.jpg",
        "house_verified": stored_status,
        "created_at": None,
    })
    assert item.result == expected_result


def test_member_auth_backend_house_review_rejects_non_pending_state() -> None:
    db = StubDB(results=[_Mapping({"id": REVIEW_ID, "house_cert": "/proof.jpg", "house_verified": 2})])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(member_auth.review_auth(db, "house", REVIEW_ID, ReviewActionRequest(status=1), 99))
    assert exc.value.status_code == 409
    assert "UPDATE user_auth SET house_verified" not in db.sql()


def test_old_backend_realname_approval_does_not_grant_face_marker() -> None:
    db = StubDB(results=[_review_row()])
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


@pytest.mark.asyncio
async def test_review_can_finalize_after_escalation_without_duplicate_notice():
    db = StubDB([_review_row(), _Mapping(), _review_row(status=4), _Mapping(), _Mapping(), _review_row(status=2)])
    await auth_service.review_realname(db, USER_ID, 4, None, 99)
    assert not db.params_of("INSERT INTO user_notification")
    await auth_service.review_realname(db, USER_ID, 2, None, 99)
    with pytest.raises(HTTPException) as exc:
        await member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=1), 99)
    assert exc.value.status_code == 409
    notices = db.params_of("INSERT INTO user_notification")
    assert len(notices) == 1 and notices[0]["user_id"] == USER_ID
    assert notices[0]["notification_type"] == "realname_review_result"
    assert notices[0]["related_user_id"] is None
    assert db.commits == 2
    assert all("FOR UPDATE" in sql for sql, _ in db.statements if sql.startswith("SELECT"))


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 1])
async def test_resubmitted_material_requires_current_version(version):
    db = StubDB([_review_row(version=2)])
    with pytest.raises(HTTPException) as exc:
        await auth_service.review_realname(db, USER_ID, 2, None, 99, submission_version=version)
    assert exc.value.status_code == 409
    assert not db.params_of("UPDATE user_auth") and db.commits == 0


@pytest.mark.asyncio
async def test_member_review_without_face_material_does_not_grant_face():
    db = StubDB([_review_row(status=4, version=2)])
    await member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=1, submission_version=2), 99)
    assert "face_verified=:face" not in db.sql()
    assert len(db.params_of("INSERT INTO user_notification")) == 1
    assert db.commits == 1


@pytest.mark.asyncio
async def test_rejection_rejects_blank_reason_and_does_not_forward_free_text():
    db = StubDB([_review_row()])
    with pytest.raises(HTTPException) as exc:
        await member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=2, remark="  "), 99)
    assert exc.value.status_code == 422
    assert db.statements == []
    private = "PRIVATE_ID PHOTO_URL VENDOR_ERROR"
    await member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=2, remark=private), 99)
    assert private not in str(db.statements)
    assert db.params_of("UPDATE user_auth")[0]["reason"] == auth_service.REALNAME_GENERIC_FAILURE


@pytest.mark.asyncio
@pytest.mark.parametrize("member_entry", [False, True])
async def test_legacy_rejection_without_reason_uses_safe_default(member_entry):
    db = StubDB([_review_row()])
    if member_entry:
        await member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=2), 99)
    else:
        response = await auth_service.review_realname(db, USER_ID, 3, None, 99)
        assert response.reason == auth_service.REALNAME_GENERIC_FAILURE
    assert db.params_of("UPDATE user_auth")[0]["reason"] == auth_service.REALNAME_GENERIC_FAILURE
    notices = db.params_of("INSERT INTO user_notification")
    assert len(notices) == 1 and notices[0]["user_id"] == USER_ID
    assert db.commits == 1


@pytest.mark.parametrize("path, field", [
    ("/admin/users/7/realname/review", "reason"),
    ("/admin/matchmaker/members/7/realname/review", "reason"),
    ("/admin/members/auth/realname/33", "remark"),
])
def test_admin_realname_validation_does_not_echo_raw_input(path, field):
    app = FastAPI()
    db = StubDB()
    for routes, dependency in (
        (admin, admin.get_current_admin),
        (matchmaker_crm_admin, matchmaker_crm_admin.get_current_matchmaker_admin),
        (member_auth_admin, member_auth_admin.get_current_matchmaker_admin),
    ):
        app.include_router(routes.router)
        app.dependency_overrides[dependency] = lambda: object()
        app.dependency_overrides[routes.get_db] = lambda: db
    private_input = "PRIVATE_INPUT_" * 30
    with TestClient(app) as client:
        body = {"status": 2, field: private_input}
        response = client.patch(path, json=body)
        assert response.status_code == 422
        assert response.json() == {"detail": "实名认证请求参数无效"}
        assert private_input not in response.text
        if field == "remark":
            response = client.patch("/admin/members/auth/education/33", json=body)
            assert response.status_code == 422
            assert response.json()["detail"][0]["loc"] == ["body", "remark"]
    assert not db.statements and db.commits == 0


@pytest.mark.asyncio
async def test_skipped_mandatory_notice_cannot_commit_review(monkeypatch):
    async def skip_notice(_db, **_kwargs):
        return False

    monkeypatch.setattr(auth_service, "emit_notification", skip_notice)
    db = StubDB([_review_row()])
    with pytest.raises(HTTPException) as exc:
        await auth_service.review_realname(db, USER_ID, 2, None, 99)
    assert exc.value.status_code == 503
    assert db.commits == 0


@pytest.mark.asyncio
async def test_manual_approval_checks_duplicate_identity_under_lock(monkeypatch):
    async def forbid_duplicates(_db):
        return False

    monkeypatch.setattr(auth_service, "same_id_multiple_accounts_allowed", forbid_duplicates)
    row = _review_row(status=4)
    row["id_card_hash"] = "synthetic-hash"
    db = StubDB([row, _Mapping(user_id=8)])
    with pytest.raises(HTTPException) as exc:
        await auth_service.review_realname(db, USER_ID, 2, None, 99)
    assert exc.value.status_code == 409
    assert "realname_status = 2 AND user_id <> :uid FOR UPDATE" in db.sql()
    assert not db.params_of("UPDATE user_auth") and db.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("member_entry", [False, True])
async def test_notification_write_failure_cannot_commit_review(member_entry):
    db = StubDB([_review_row()], fail_notification=True)
    with pytest.raises(RuntimeError, match="通知写入失败"):
        if member_entry:
            await member_auth.review_auth(db, "realname", REVIEW_ID, ReviewActionRequest(status=1), 99)
        else:
            await auth_service.review_realname(db, USER_ID, 2, None, 99)
    assert db.params_of("UPDATE user_auth") and db.commits == 0


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
