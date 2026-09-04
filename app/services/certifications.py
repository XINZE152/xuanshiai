"""Manual certification submissions; external verification is deliberately deferred."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, UploadFile

from app.services.profile import _image_outputs, _media_url, _read_limited, _user_media_dir, _write_bytes
import uuid

from app.schemas.certifications import (
    CertificationsResponse,
    EducationCertificationRequest,
    MarriageCertificationRequest,
    CertificationReviewItem,
    CertificationReviewPage,
)
from app.schemas.admin import CertificationReviewRequest, CertificationReviewResponse


async def list_certification_reviews(db: AsyncSession, *, page: int, page_size: int, kind: str | None = None, status: int = 1) -> CertificationReviewPage:
    fields = {
        "education": ("education_verified", "education_cert", "education_submitted_at", "education_reviewed_at", "education_fail_reason"),
        "house": ("house_verified", "house_cert", "house_submitted_at", "house_reviewed_at", "house_fail_reason"),
        "marriage": ("marriage_verified", "marriage_cert", "marriage_submitted_at", "marriage_reviewed_at", "marriage_fail_reason"),
    }
    kinds = [kind] if kind else list(fields)
    if any(value not in fields for value in kinds):
        raise HTTPException(422, detail="不支持的认证类型")
    items: list[CertificationReviewItem] = []
    for current_kind in kinds:
        status_field, material_field, submitted_field, reviewed_field, reason_field = fields[current_kind]
        rows = (await db.execute(text(f"""SELECT ua.user_id, u.nickname, ua.{status_field} AS status, ua.{material_field} AS material,
            ua.{submitted_field} AS submitted_at, ua.{reviewed_field} AS reviewed_at, ua.{reason_field} AS fail_reason
            FROM user_auth ua JOIN users u ON u.id = ua.user_id
            WHERE ua.{status_field} = :status ORDER BY ua.{submitted_field} ASC, ua.user_id ASC"""), {"status": status})).mappings().all()
        items.extend(CertificationReviewItem(user_id=int(row["user_id"]), nickname=row["nickname"], kind=current_kind, status=int(row["status"]), material_submitted=bool(row["material"]), submitted_at=row["submitted_at"], reviewed_at=row["reviewed_at"], fail_reason=row["fail_reason"]) for row in rows)
    items.sort(key=lambda item: (item.submitted_at is None, item.submitted_at, item.user_id))
    total = len(items)
    offset = (page - 1) * page_size
    return CertificationReviewPage(items=items[offset:offset + page_size], page=page, page_size=page_size, total=total, has_more=page * page_size < total)
def _item(kind: str, row: dict, material: str | None) -> dict:
    status = int(row.get("status") or 0)
    return {"kind": kind, "status": status, "material_submitted": bool(material),
            "submitted_at": row.get("submitted_at"), "reviewed_at": row.get("reviewed_at"),
            "fail_reason": row.get("fail_reason"),
            "next_action": "等待平台审核" if status == 1 else ("重新提交材料" if status == 3 else "提交认证材料")}


async def get_certifications(db: AsyncSession, user_id: int) -> CertificationsResponse:
    result = await db.execute(text("""SELECT education, education_cert, education_verified, education_fail_reason,
        education_submitted_at, education_reviewed_at, house_cert, house_verified, house_fail_reason,
        house_submitted_at, house_reviewed_at, marriage_cert, marriage_verified, marriage_fail_reason,
        marriage_submitted_at, marriage_reviewed_at, updated_at FROM user_auth WHERE user_id=:id"""), {"id": user_id})
    row = result.mappings().first() or {}
    return CertificationsResponse(
        education=_item("education", {"status": row.get("education_verified"), "submitted_at": row.get("education_submitted_at"), "reviewed_at": row.get("education_reviewed_at"), "fail_reason": row.get("education_fail_reason")}, row.get("education_cert")),
        house=_item("house", {"status": row.get("house_verified"), "submitted_at": row.get("house_submitted_at"), "reviewed_at": row.get("house_reviewed_at"), "fail_reason": row.get("house_fail_reason")}, row.get("house_cert")),
        marriage=_item("marriage", {"status": row.get("marriage_verified"), "submitted_at": row.get("marriage_submitted_at"), "reviewed_at": row.get("marriage_reviewed_at"), "fail_reason": row.get("marriage_fail_reason")}, row.get("marriage_cert")),
    )


async def submit_education(db: AsyncSession, user_id: int, body: EducationCertificationRequest) -> CertificationsResponse:
    await db.execute(text("""INSERT INTO user_auth (user_id, education, education_verified)
        VALUES (:id,:education,1) ON DUPLICATE KEY UPDATE education=:education,
        education_verified=1, education_fail_reason=NULL, education_submitted_at=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP()"""), {"id": user_id, **body.model_dump()})
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
        VALUES (:id,:material,1) ON DUPLICATE KEY UPDATE house_cert=:material, house_verified=1, house_fail_reason=NULL, house_submitted_at=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP()"""), {"id": user_id, "material": material})
    await db.commit()
    return await get_certifications(db, user_id)


async def submit_marriage(db: AsyncSession, user_id: int, body: MarriageCertificationRequest) -> CertificationsResponse:
    material = "user_confirmed_unmarried" if body.is_unmarried else "user_not_confirmed_unmarried"
    await db.execute(text("""INSERT INTO user_auth (user_id, marriage_cert, marriage_verified, marriage_submitted_at)
        VALUES (:id,:material,1,UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE marriage_cert=:material,
        marriage_verified=1, marriage_fail_reason=NULL, marriage_submitted_at=UTC_TIMESTAMP()"""), {"id": user_id, "material": material})
    await db.commit()
    return await get_certifications(db, user_id)


async def review_certification(db: AsyncSession, user_id: int, kind: str, request: CertificationReviewRequest) -> CertificationReviewResponse:
    fields = {
        "education": ("education_verified", "education", "education_fail_reason", "education_reviewed_at"),
        "house": ("house_verified", "house_cert", "house_fail_reason", "house_reviewed_at"),
        "marriage": ("marriage_verified", "marriage_cert", "marriage_fail_reason", "marriage_reviewed_at"),
    }
    if kind not in fields:
        raise HTTPException(422, detail="不支持的认证类型")
    status_field, material_field, reason_field, reviewed_field = fields[kind]
    result = await db.execute(text(f"SELECT {material_field}, {status_field} AS current_status FROM user_auth WHERE user_id=:user_id FOR UPDATE"), {"user_id": user_id})
    row = result.mappings().first()
    if not row or not row[material_field]:
        raise HTTPException(404, detail="认证材料不存在")
    if int(row["current_status"] or 0) != 1:
        raise HTTPException(409, detail="当前认证不在审核中")
    await db.execute(text(f"UPDATE user_auth SET {status_field}=:status, {reason_field}=:reason, {reviewed_field}=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP() WHERE user_id=:user_id"), {"status": request.status, "reason": request.reason, "user_id": user_id})
    await db.commit()
    return CertificationReviewResponse(user_id=user_id, kind=kind, status=request.status, reason=request.reason)
