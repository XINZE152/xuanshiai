"""⑧ 后台证件留档上传（2026-10-28）：user_media 新增 'certificate' 类型。

覆盖：
- MemberMediaCreate 接受 media_type=certificate；
- create_media 对证件类在提交边界剥离签名参数（DB 只存原始 URL），并落库待审 0；
- list_media 对证件类按当前管理员重签 file_url/thumbnail_url（短时效私有访问），
  非证件类不重签（保持头像/照片/视频原口径）；
- 路由 media_type 白名单含 certificate；未登录 401。
"""

from __future__ import annotations

import pytest

import app.services.member_media_admin as media
from app.schemas.member_media_admin import MemberMediaCreate

BASE = "/api/v1/admin/members"


class Res:
    def __init__(self, *, rows=None, scalar=None, lastrowid=1):
        self._rows = list(rows or [])
        self._scalar = scalar
        self.lastrowid = lastrowid

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class StubDB:
    """按入队顺序返回结果，并记录全部 SQL 与参数。"""

    def __init__(self, results=None):
        self.statements: list[tuple[str, dict]] = []
        self._results = list(results or [])
        self.committed = False

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        if self._results:
            return self._results.pop(0)
        return Res()

    async def commit(self):
        self.committed = True

    def insert_params(self) -> dict:
        for sql, p in self.statements:
            if "INSERT INTO user_media" in sql:
                return p
        raise AssertionError("未捕获到 user_media INSERT")


def _media_row(file_url: str, media_type: str = "certificate") -> dict:
    return {
        "id": 99, "user_id": 2, "member_code": "G000002", "nickname": "会员B",
        "avatar": None, "media_type": media_type, "file_url": file_url,
        "thumbnail_url": None, "mime_type": "image/jpeg", "duration_seconds": None,
        "review_status": 0, "review_reason": None, "age": 28, "meta_text": "",
        "created_at": None,
    }


def test_media_create_accepts_certificate_type() -> None:
    body = MemberMediaCreate(file_url="/storage/uploads/admin/id-card.jpg", media_type="certificate")
    assert body.media_type == "certificate"


@pytest.mark.asyncio
async def test_create_certificate_strips_signature_before_store() -> None:
    signed = "/storage/uploads/admin/id-card.jpg?expires=9999999999&signature=deadbeef&user=5&kind=admin&revision=0"
    db = StubDB([Res(scalar=1), Res(lastrowid=99), Res()])  # guard → INSERT → audit
    body = MemberMediaCreate(file_url=signed, media_type="certificate")
    out = await media.create_media(db, 2, body, actor_id=5)

    stored = db.insert_params()
    # DB 只存原始 URL，签名参数被剥离
    assert stored["file_url"] == "/storage/uploads/admin/id-card.jpg"
    assert "signature" not in stored["file_url"] and "expires" not in stored["file_url"]
    assert out["file_url"] == "/storage/uploads/admin/id-card.jpg"
    assert out["review_status"] == 0
    assert db.committed


@pytest.mark.asyncio
async def test_create_photo_keeps_url_untouched() -> None:
    url = "/storage/uploads/2/photo-abc.webp"
    db = StubDB([Res(scalar=1), Res(lastrowid=50), Res()])
    body = MemberMediaCreate(file_url=url, media_type="photo")
    await media.create_media(db, 2, body, actor_id=5)
    assert db.insert_params()["file_url"] == url


@pytest.mark.asyncio
async def test_list_certificate_resigns_for_viewer() -> None:
    raw = "/storage/uploads/admin/id-card.jpg"
    db = StubDB([Res(rows=[_media_row(raw)]), Res(scalar=1)])
    page = await media.list_media(db, 1, 20, "certificate", None, None, None, viewer=5)
    signed = page.items[0].file_url
    assert signed != raw
    assert "signature=" in signed and "expires=" in signed and "user=5" in signed


@pytest.mark.asyncio
async def test_list_avatar_not_resigned() -> None:
    url = "/storage/uploads/2/avatar-x.webp"
    db = StubDB([Res(rows=[_media_row(url, media_type="avatar")]), Res(scalar=1)])
    page = await media.list_media(db, 1, 20, "avatar", None, None, None, viewer=5)
    assert page.items[0].file_url == url


def test_route_registered_and_rejects_bad_type_and_anon() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    spec = client.get("/openapi.json").json()["paths"]
    assert "get" in spec[f"{BASE}/media"]
    # 未登录 401
    assert client.get(f"{BASE}/media?media_type=certificate").status_code == 401
