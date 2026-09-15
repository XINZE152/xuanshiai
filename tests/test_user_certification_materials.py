"""Contracts for education evidence and handwritten single-pledge review."""

from fastapi.testclient import TestClient

from app.main import app
from app.services.certifications import _single_pledge_item
from app.services.member_auth_admin import _qualification_result


client = TestClient(app)


def test_material_upload_routes_are_registered_as_multipart() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    education = paths["/api/v1/users/me/certifications/education/material"]["put"]
    pledge = paths["/api/v1/users/me/certifications/single-pledge"]["put"]
    assert "multipart/form-data" in education["requestBody"]["content"]
    assert "multipart/form-data" in pledge["requestBody"]["content"]


def test_certification_response_exposes_single_pledge() -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    response = schemas["CertificationsResponse"]
    assert "single_pledge" in response["properties"]


def test_commitment_internal_status_maps_to_public_four_state() -> None:
    assert _single_pledge_item({})["status"] == 0
    base = {"file_url": "/signature.webp", "created_at": None, "reviewed_at": None,
            "remark": None, "title": "单身承诺", "content": "正文"}
    assert _single_pledge_item({**base, "status": 0})["status"] == 1
    assert _single_pledge_item({**base, "status": 1})["status"] == 2
    failed = _single_pledge_item({**base, "status": 2, "remark": "签名不清晰"})
    assert failed["status"] == 3
    assert failed["fail_reason"] == "签名不清晰"


def test_education_admin_maps_public_four_state() -> None:
    assert _qualification_result(1) == ("pending", "待审")
    assert _qualification_result(2) == ("pass", "通过")
    assert _qualification_result(3) == ("fail", "未通过")
