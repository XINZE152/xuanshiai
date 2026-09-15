"""Manual certification submissions; external verification is deliberately deferred."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from app.core.config import settings
from app.services.auth import accept_agreement
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


async def list_certification_reviews(db: AsyncSession, *, page: int, page_size: int, kind: str | None = None, status: int | None = None, search: str | None = None) -> CertificationReviewPage:
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
        where = f"ua.{material_field} IS NOT NULL"
        params: dict[str, object] = {}
        if status is not None:
            where += f" AND ua.{status_field} = :status"
            params["status"] = status
        if search:
            where += " AND (u.nickname LIKE CONCAT('%', :search, '%') OR u.phone LIKE CONCAT('%', :search, '%'))"
            params["search"] = search
        rows = (await db.execute(text(f"""SELECT ua.user_id, u.nickname, ua.{status_field} AS status, ua.{material_field} AS material,
            ua.{submitted_field} AS submitted_at, ua.{reviewed_field} AS reviewed_at, ua.{reason_field} AS fail_reason
            FROM user_auth ua JOIN users u ON u.id = ua.user_id
            WHERE {where} ORDER BY ua.{submitted_field} ASC, ua.user_id ASC"""), params)).mappings().all()
        items.extend(CertificationReviewItem(user_id=int(row["user_id"]), nickname=row["nickname"], kind=current_kind, status=int(row["status"]), material_submitted=bool(row["material"]), material=row["material"], submitted_at=row["submitted_at"], reviewed_at=row["reviewed_at"], fail_reason=row["fail_reason"]) for row in rows)
    items.sort(key=lambda item: (item.submitted_at is None, item.submitted_at, item.user_id))
    total = len(items)
    offset = (page - 1) * page_size
    return CertificationReviewPage(items=items[offset:offset + page_size], page=page, page_size=page_size, total=total, has_more=page * page_size < total)
def _item(kind: str, row: dict, material: str | None) -> dict:
    status = int(row.get("status") or 0)
    return {"kind": kind, "status": status, "material_submitted": bool(material),
            "material": material,
            "submitted_at": row.get("submitted_at"), "reviewed_at": row.get("reviewed_at"),
            "fail_reason": row.get("fail_reason"),
            "next_action": "等待平台审核" if status == 1 else ("无需操作" if status == 2 else ("重新提交材料" if status == 3 else "提交认证材料"))}


def _single_pledge_item(row: dict) -> dict:
    if not row:
        return _item("single_pledge", {}, None)
    internal_status = int(row.get("status") or 0)
    public_status = 1 if internal_status == 0 else (2 if internal_status == 1 else 3)
    return {
        **_item("single_pledge", {
            "status": public_status,
            "submitted_at": row.get("created_at"),
            "reviewed_at": row.get("reviewed_at"),
            "fail_reason": row.get("remark"),
        }, row.get("file_url")),
        "title": row.get("title"),
        "content": row.get("content"),
    }


async def get_certifications(db: AsyncSession, user_id: int) -> CertificationsResponse:
    result = await db.execute(text("""SELECT education, school, education_cert, education_verified, education_fail_reason,
        education_submitted_at, education_reviewed_at, house_cert, house_verified, house_fail_reason,
        house_submitted_at, house_reviewed_at, marriage_cert, marriage_verified, marriage_fail_reason,
        marriage_submitted_at, marriage_reviewed_at, updated_at FROM user_auth WHERE user_id=:id"""), {"id": user_id})
    row = result.mappings().first() or {}
    pledge_result = await db.execute(text("""SELECT title, content, file_url, status, remark, reviewed_at, created_at
        FROM user_commitment_sign WHERE user_id=:id ORDER BY id DESC LIMIT 1"""), {"id": user_id})
    pledge = pledge_result.mappings().first() or {}
    return CertificationsResponse(
        education={**_item("education", {"status": row.get("education_verified"), "submitted_at": row.get("education_submitted_at"), "reviewed_at": row.get("education_reviewed_at"), "fail_reason": row.get("education_fail_reason")}, row.get("education_cert")), "education": row.get("education"), "school": row.get("school")},
        house=_item("house", {"status": row.get("house_verified"), "submitted_at": row.get("house_submitted_at"), "reviewed_at": row.get("house_reviewed_at"), "fail_reason": row.get("house_fail_reason")}, row.get("house_cert")),
        marriage=_item("marriage", {"status": row.get("marriage_verified"), "submitted_at": row.get("marriage_submitted_at"), "reviewed_at": row.get("marriage_reviewed_at"), "fail_reason": row.get("marriage_fail_reason")}, row.get("marriage_cert")),
        single_pledge=_single_pledge_item(pledge),
    )


async def submit_education(db: AsyncSession, user_id: int, body: EducationCertificationRequest) -> CertificationsResponse:
    await db.execute(text("""INSERT INTO user_auth (user_id, education, education_verified, education_submitted_at)
        VALUES (:id,:education,1,UTC_TIMESTAMP()) ON DUPLICATE KEY UPDATE education=:education,
        education_verified=1, education_fail_reason=NULL, education_submitted_at=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP()"""), {"id": user_id, **body.model_dump()})
    await db.commit()
    return await get_certifications(db, user_id)


async def _store_certification_image(user_id: int, prefix: str, file: UploadFile) -> str:
    raw = await _read_limited(file, 5 * 1024 * 1024)
    image_data, _ = _image_outputs(raw)
    name = uuid.uuid4().hex
    image_path = _user_media_dir(user_id) / f"{prefix}-{name}.webp"
    await _write_bytes(image_path, image_data)
    return _media_url(user_id, image_path.name)


async def submit_education_material(
    db: AsyncSession,
    user_id: int,
    education: str,
    school: str,
    file: UploadFile,
) -> CertificationsResponse:
    try:
        validated = EducationCertificationRequest(education=education)
    except ValidationError as exc:
        raise HTTPException(422, detail="请选择有效的最高学历") from exc
    normalized_school = school.strip()
    if len(normalized_school) < 2 or len(normalized_school) > 128:
        raise HTTPException(422, detail="毕业学校需填写2至128个字符")
    material = await _store_certification_image(user_id, "education-cert", file)
    await db.execute(text("""INSERT INTO user_auth
        (user_id, education, school, education_cert, education_verified, education_submitted_at)
        VALUES (:id,:education,:school,:material,1,UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE education=VALUES(education), school=VALUES(school),
        education_cert=VALUES(education_cert), education_verified=1, education_fail_reason=NULL,
        education_reviewed_at=NULL, education_submitted_at=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP()"""), {
            "id": user_id,
            "education": validated.education,
            "school": normalized_school,
            "material": material,
        })
    await db.commit()
    return await get_certifications(db, user_id)


async def submit_single_pledge(
    db: AsyncSession,
    user_id: int,
    agreement_version: str,
    file: UploadFile,
) -> CertificationsResponse:
    current_version = settings.agreement_versions.get("safety_pledge")
    if not current_version or agreement_version != current_version:
        raise HTTPException(409, detail="单身承诺版本不是当前发布版本")
    identity = (await db.execute(text("""SELECT u.nickname, COALESCE(ua.realname_status, 0) AS realname_status
        FROM users u LEFT JOIN user_auth ua ON ua.user_id=u.id WHERE u.id=:id"""), {"id": user_id})).mappings().first()
    if not identity or int(identity["realname_status"] or 0) != 2:
        raise HTTPException(403, detail="请先完成实名认证")
    latest = (await db.execute(text("""SELECT status FROM user_commitment_sign
        WHERE user_id=:id ORDER BY id DESC LIMIT 1"""), {"id": user_id})).mappings().first()
    if latest and int(latest["status"] or 0) in (0, 1):
        raise HTTPException(409, detail="单身承诺正在审核或已通过，无需重复提交")
    material = await _store_certification_image(user_id, "single-pledge", file)
    nickname = str(identity["nickname"] or "用户")
    member_code = f"G{user_id:06d}"
    content = (
        f"本人使用网名{nickname}，编号：{member_code}，在宣誓爱登记婚恋资料。"
        "本人承诺所登记资料真实、准确，当前婚姻状态为单身；如状态发生变化，"
        "将及时更新资料，并自行承担信息不实造成的相应责任。"
    )
    count = await db.execute(text("SELECT COUNT(*) FROM user_commitment_sign WHERE user_id=:id"), {"id": user_id})
    sign_times = int(count.scalar() or 0) + 1
    await accept_agreement(db, user_id, "safety_pledge", agreement_version, None, "certification", None, None)
    await db.execute(text("""INSERT INTO user_commitment_sign
        (user_id, title, content, file_url, sign_times, status)
        VALUES (:id,'单身承诺',:content,:material,:sign_times,0)"""), {
            "id": user_id,
            "content": content,
            "material": material,
            "sign_times": sign_times,
        })
    await db.execute(text("UPDATE users SET is_single_pledge=0, updated_at=UTC_TIMESTAMP() WHERE id=:id"), {"id": user_id})
    await db.commit()
    return await get_certifications(db, user_id)


async def submit_house(db: AsyncSession, user_id: int, file: UploadFile) -> CertificationsResponse:
    material = await _store_certification_image(user_id, "house-cert", file)
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
