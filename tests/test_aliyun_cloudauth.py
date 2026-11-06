"""阿里云 CloudAuth 签名客户端与实人认证 provider 契约。"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import decrypt_sensitive, encrypt_sensitive
from app.services.aliyun_cloudauth.client import (
    AliyunCloudAuthClient,
    _percent_encode,
)
from app.services.face.aliyun import AliyunCloudAuthFaceProvider
from app.services.identity_verify.aliyun import AliyunCloudAuthIdentityProvider
from app.services.identity_verify.base import IdentityVerifyRequest

_IDENTITY_REQUEST = IdentityVerifyRequest(
    user_id=7,
    real_name="张三",
    id_card_number="110101199001011234",
)


def _http_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _identity_provider(handler) -> tuple[AliyunCloudAuthIdentityProvider, httpx.AsyncClient]:
    http = _http_client(handler)
    client = AliyunCloudAuthClient(
        access_key_id="test-access-key",
        access_key_secret="test-access-secret",
        http_client=http,
    )
    return AliyunCloudAuthIdentityProvider(client), http


def test_percent_encode_matches_alibaba_rules() -> None:
    assert _percent_encode("a+b/c=d ~") == "a%2Bb%2Fc%3Dd%20~"


def test_client_signs_and_calls_id2metaverify() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        seen["body"] = body
        assert request.method == "POST"
        assert request.url.host == "cloudauth.aliyuncs.com"
        assert "Action=Id2MetaVerify" in body
        assert "ParamType=normal" in body
        assert "Signature=" in body
        return httpx.Response(
            200,
            json={"Code": "200", "RequestId": "r1", "ResultObject": {"BizCode": 1}},
        )

    provider, http = _identity_provider(handler)
    try:
        result = asyncio.run(provider.verify(_IDENTITY_REQUEST))
    finally:
        asyncio.run(http.aclose())

    assert result.verified is True
    assert result.provider == "aliyun_cloudauth"
    assert "UserName=%E5%BC%A0%E4%B8%89" in seen["body"]
    assert "IdentifyNum=110101199001011234" in seen["body"]


def test_identity_accepts_result_object_as_json_text() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Code": "200",
                "RequestId": "r1b",
                "ResultObject": '{"BizCode": 1}',
            },
        )

    provider, http = _identity_provider(handler)
    try:
        result = asyncio.run(provider.verify(_IDENTITY_REQUEST))
    finally:
        asyncio.run(http.aclose())

    assert result.verified is True


def test_identity_mismatch_is_safe_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Code": "200", "RequestId": "r2", "ResultObject": {"BizCode": 2}},
        )

    provider, http = _identity_provider(handler)
    try:
        result = asyncio.run(provider.verify(_IDENTITY_REQUEST))
    finally:
        asyncio.run(http.aclose())

    assert result.verified is False
    assert result.reason == "identity_mismatch"


def test_identity_business_error_falls_back_to_503() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Code": "400", "RequestId": "r3"})

    provider, http = _identity_provider(handler)
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(provider.verify(_IDENTITY_REQUEST))
    finally:
        asyncio.run(http.aclose())

    assert error.value.status_code == 503


def test_face_provider_compares_photo_and_returns_score(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    avatar = tmp_path / "7" / "avatar.webp"
    avatar.parent.mkdir(parents=True)
    avatar.write_bytes(b"fake-webp")

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        assert "Action=ContrastFaceVerify" in body
        assert "SceneId=1000000006" in body
        assert "FaceContrastPicture=" in body
        return httpx.Response(
            200,
            json={
                "Code": "200",
                "RequestId": "r4",
                "ResultObject": {"Passed": "T", "VerifyScore": "98.5", "SubCode": "200"},
            },
        )

    http = _http_client(handler)
    client = AliyunCloudAuthClient(
        access_key_id="test-access-key",
        access_key_secret="test-access-secret",
        http_client=http,
    )
    provider = AliyunCloudAuthFaceProvider(client, scene_id=1000000006)
    try:
        result = asyncio.run(
            provider.verify(
                user_id=7,
                image_ref="/storage/uploads/7/avatar.webp",
                real_name="张三",
                id_card_number="110101199001011234",
            )
        )
    finally:
        asyncio.run(http.aclose())

    assert result.verified is True
    assert result.score == 98.5
    assert result.provider == "aliyun_cloudauth"


def test_face_provider_requires_verified_identity() -> None:
    provider = AliyunCloudAuthFaceProvider(scene_id=1000000006)
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            provider.verify(
                user_id=7,
                image_ref="/storage/uploads/7/avatar.webp",
                real_name=None,
                id_card_number=None,
            )
        )
    assert error.value.status_code == 503


def test_sensitive_value_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secret_key", "x" * 32)
    assert decrypt_sensitive(encrypt_sensitive("110101199001011234")) == "110101199001011234"
