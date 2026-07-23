"""Manual certification submissions; external verification is deliberately deferred."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile

from app.services.profile import _image_outputs, _media_url, _read_limited, _user_media_dir, _write_bytes
import uuid

from app.schemas.certifications import (
    CertificationsResponse,
    EducationCertificationRequest,
    MarriageCertificationRequest,
)


def _item(kind: str, row: dict, material: str | None) -> dict:
    status = int(row.get("status") or 0)
    return {"kind": kind, "status": status, "material_submitted": bool(material),
            "submitted_at": row.get("submitted_at"), "reviewed_at": row.get("reviewed_at"),
            "fail_reason": row.get("fail_reason"),
            "next_action": "等待平台审核" if status == 1 else ("重新提交材料" if status == 3 else "提交认证材料")}


async def get_certifications(db: AsyncSession, user_id: int) -> CertificationsResponse:
    result = await db.execute(text("""SELECT education, education_cert, education_verified,
        house_cert, house_verified, marriage_cert, marriage_verified, marriage_fail_reason,
        marriage_submitted_at, marriage_reviewed_at, updated_at FROM user_auth WHERE user_id=:id"""), {"id": user_id})
    row = result.mappings().first() or {}
    return CertificationsResponse(
        education=_item("education", {"status": row.get("education_verified"), "submitted_at": row.get("updated_at")}, row.get("education_cert")),
        house=_item("house", {"status": row.get("house_verified"), "submitted_at": row.get("updated_at")}, row.get("house_cert")),
        marriage=_item("marriage", {"status": row.get("marriage_verified"), "submitted_at": row.get("marriage_submitted_at"), "reviewed_at": row.get("marriage_reviewed_at"), "fail_reason": row.get("marriage_fail_reason")}, row.get("marriage_cert")),
    )


async def submit_education(db: AsyncSession, user_id: int, body: EducationCertificationRequest) -> CertificationsResponse:
    await db.execute(text("""INSERT INTO user_auth (user_id, education, education_verified)
        VALUES (:id,:education,1) ON DUPLICATE KEY UPDATE education=:education,
        education_verified=1, updated_at=UTC_TIMESTAMP()"""), {"id": user_id, **body.model_dump()})
    await db.commit()
    return await get_certifications(db, user_id)


async def submit_house(db: AsyncSession, user_id: int, file: UploadFile) -> CertificationsResponse:
    raw = await _read_limited(file, 5 * 1024 * 1024)
    image_data, _ = _image_outputs(raw)
    name = uuid.uuid4().hex
    directory = _user_media_dir(user_id)
    image_path = directory / f"house-cert-{name}.webp"
    await _write_bytes(image_path, image_data)
    material = _media_url(user_id, image_path.name)
    await db.execute(text("""INSERT INTO user_auth (user_id, house_cert, house_verified)
        VALUES (:id,:material,1) ON DUPLICATE KEY UPDATE house_cert=:material, house_verified=1, updated_at=UTC_TIMESTAMP()"""), {"id": user_id, "material": material})
    await db.commit()
    return await get_certifications(db, user_id)


async def submit_marriage(db: AsyncSession, user_id: int, body: MarriageCertificationRequest) -> CertificationsResponse:
    material = "user_confirmed_unmarried" if body.is_unmarried else "user_not_confirmed_unmarried"
    await db.execute(text("""INSERT INTO user_auth (user_id, marriage_cert, marriage_verified, marriage_submitted_at)
        VALUES (:id,:material,1,UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE marriage_cert=:material,
        marriage_verified=1, marriage_fail_reason=NULL, marriage_submitted_at=UTC_TIMESTAMP()"""), {"id": user_id, "material": material})
    await db.commit()
    return await get_certifications(db, user_id)
