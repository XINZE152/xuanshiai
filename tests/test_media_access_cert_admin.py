"""认证材料类（cert）与后台资源类（admin）私有媒体签名止损回归测试。

覆盖：本人签 200（kind=user）、他用户拿不到签发+直连被拒 403、认证审核
管理员签 200（kind=admin）、匿名 403、admin 类直连 403、公开类不回归、
kind 维度纳入签名（user 签名不可冒充 admin）、未签名观测 WARNING
（含类别与相对路径，不含 query）。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.main import _ProtectedStorageFiles
from app.services import media_access
from app.services.media_access import (
    MEDIA_CATEGORY_ADMIN,
    MEDIA_CATEGORY_AUDIO,
    MEDIA_CATEGORY_CERT,
    VIEWER_KIND_ADMIN,
    VIEWER_KIND_USER,
    sign_media_url,
    verify_media_access,
)

_CERT = "77/education-cert-example.webp"
_CERT_PAYLOAD = b"private-cert-sentinel"
_ADMIN = "admin/example.webp"
_ADMIN_PAYLOAD = b"admin-resource-sentinel"
_PUBLIC_IMAGE = "77/photo-example.webp"


def _signed_url(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    *,
    viewer: int,
    kind: str,
) -> str:
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    return sign_media_url(path, category=MEDIA_CATEGORY_CERT, viewer=viewer, kind=kind)


def _http_scope(query_string: bytes = b"") -> dict[str, object]:
    return {
        "type": "http",
        "method": "GET",
        "path": "/storage/uploads",
        "query_string": query_string,
        "headers": [],
    }


def _hook(monkeypatch: pytest.MonkeyPatch, kind: str, result: bool) -> None:
    async def fixed(viewer_id: int, db=None) -> bool:
        return result

    monkeypatch.setitem(
        media_access._MEDIA_VIEWER_HOOKS,
        (MEDIA_CATEGORY_CERT, kind),
        fixed,
    )


def _write_fixture(upload_dir: Path, relative: str, payload: bytes) -> None:
    target = upload_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


@pytest.mark.asyncio
async def test_owner_signature_reads_cert_and_public_stays_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    _write_fixture(upload_dir, _CERT, _CERT_PAYLOAD)
    _write_fixture(upload_dir, _PUBLIC_IMAGE, b"public")
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    _hook(monkeypatch, VIEWER_KIND_USER, True)
    _hook(monkeypatch, VIEWER_KIND_ADMIN, True)
    signed = _signed_url(monkeypatch, f"/storage/uploads/{_CERT}", viewer=77, kind=VIEWER_KIND_USER)

    anonymous = await files.get_response(_CERT, _http_scope())
    allowed = await files.get_response(
        _CERT, _http_scope(urlsplit(signed).query.encode("ascii"))
    )
    public = await files.get_response(_PUBLIC_IMAGE, _http_scope())

    assert anonymous.status_code == 403
    assert allowed.status_code == 200
    assert public.status_code == 200


@pytest.mark.asyncio
async def test_review_admin_signature_reads_cert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    _write_fixture(upload_dir, _CERT, _CERT_PAYLOAD)
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    _hook(monkeypatch, VIEWER_KIND_USER, True)
    _hook(monkeypatch, VIEWER_KIND_ADMIN, True)
    signed = _signed_url(monkeypatch, f"/storage/uploads/{_CERT}", viewer=7, kind=VIEWER_KIND_ADMIN)

    response = await files.get_response(
        _CERT, _http_scope(urlsplit(signed).query.encode("ascii"))
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_kind_is_bound_into_signature_and_cannot_be_swapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """user 签名冒充 admin（改 kind 参数）必须 403：kind 纳入签名消息。"""
    signed = _signed_url(monkeypatch, f"/storage/uploads/{_CERT}", viewer=77, kind=VIEWER_KIND_USER)
    query = parse_qs(urlsplit(signed).query)

    swapped = dict(query)
    swapped["kind"] = [VIEWER_KIND_ADMIN]
    assert not await verify_media_access(_CERT, swapped)

    missing_kind = {k: v for k, v in query.items() if k != "kind"}
    assert not await verify_media_access(_CERT, missing_kind)


def test_cert_signing_requires_explicit_kind() -> None:
    """cert 双语义无缺省：不传 kind 直接 ValueError，防止静默选错钩子。"""
    with pytest.raises(ValueError):
        sign_media_url(
            f"/storage/uploads/{_CERT}", category=MEDIA_CATEGORY_CERT, viewer=77
        )


@pytest.mark.asyncio
async def test_inactive_viewer_signatures_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    _write_fixture(upload_dir, _CERT, _CERT_PAYLOAD)
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    signed_user = _signed_url(monkeypatch, f"/storage/uploads/{_CERT}", viewer=77, kind=VIEWER_KIND_USER)
    signed_admin = _signed_url(monkeypatch, f"/storage/uploads/{_CERT}", viewer=7, kind=VIEWER_KIND_ADMIN)

    for kind in (VIEWER_KIND_USER, VIEWER_KIND_ADMIN):
        _hook(monkeypatch, kind, False)
        for signed in (signed_user if kind == VIEWER_KIND_USER else signed_admin,):
            response = await files.get_response(
                _CERT, _http_scope(urlsplit(signed).query.encode("ascii"))
            )
            assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_resource_rejects_anonymous_and_accepts_admin_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "uploads"
    _write_fixture(upload_dir, _ADMIN, _ADMIN_PAYLOAD)
    files = _ProtectedStorageFiles(directory=str(upload_dir))

    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    signed = sign_media_url(
        f"/storage/uploads/{_ADMIN}", category=MEDIA_CATEGORY_ADMIN, viewer=7
    )

    async def active(viewer_id: int, db=None) -> bool:
        return True

    monkeypatch.setitem(
        media_access._MEDIA_VIEWER_HOOKS,
        (MEDIA_CATEGORY_ADMIN, VIEWER_KIND_ADMIN),
        active,
    )

    anonymous = await files.get_response(_ADMIN, _http_scope())
    allowed = await files.get_response(
        _ADMIN, _http_scope(urlsplit(signed).query.encode("ascii"))
    )
    assert anonymous.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_unsigned_private_access_is_observed_with_category_and_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """观测：未签名命中私有前缀输出 WARNING（类别+相对路径；不含 query）。"""
    upload_dir = tmp_path / "uploads"
    _write_fixture(upload_dir, _CERT, _CERT_PAYLOAD)
    files = _ProtectedStorageFiles(directory=str(upload_dir))
    _hook(monkeypatch, VIEWER_KIND_USER, True)
    _hook(monkeypatch, VIEWER_KIND_ADMIN, True)

    with caplog.at_level("WARNING", logger="app.services.media_access"):
        # 未签名直连（无 signature 参数）触发观测日志。
        await files.get_response(_CERT, _http_scope())

    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any(
        "unsigned_private_media_access" in message
        and "category=cert" in message
        and "path=77/education-cert-example.webp" in message
        for message in warnings
    )
    assert all("evil" not in message and "signature=fake" not in message for message in warnings)


def test_public_media_url_is_not_signed_or_blocked() -> None:
    url = f"/storage/uploads/{_PUBLIC_IMAGE}"

    assert sign_media_url(url, category=MEDIA_CATEGORY_CERT, viewer=77, kind=VIEWER_KIND_USER) == url


@pytest.mark.asyncio
async def test_audio_signature_cannot_be_replayed_on_cert_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """跨类别重放：audio 签名（kind=user）访问 cert 路径必须 403。"""
    monkeypatch.setattr("app.services.media_access.time.time", lambda: 1_000)
    signed = sign_media_url(
        "/storage/uploads/42/audio/example.mp3", category=MEDIA_CATEGORY_AUDIO, viewer=42
    )
    query = parse_qs(urlsplit(signed).query)

    assert not await verify_media_access(_CERT, query)
