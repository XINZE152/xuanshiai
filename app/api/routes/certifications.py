"""Manual submission endpoints for user qualification certifications."""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.certifications import CertificationsResponse, EducationCertificationRequest, MarriageCertificationRequest
from app.services.certifications import (
    get_certifications,
    submit_education,
    submit_education_material,
    submit_house,
    submit_marriage,
    submit_single_pledge,
)

router = APIRouter(prefix="/users/me/certifications")


@router.get("", response_model=CertificationsResponse, summary="查询用户资质认证")
async def certifications(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CertificationsResponse:
    return await get_certifications(db, current.id)


@router.put("/education", response_model=CertificationsResponse, summary="提交学历认证材料")
async def education(body: EducationCertificationRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CertificationsResponse:
    """Legacy JSON entry retained for old clients; new clients upload evidence below."""
    return await submit_education(db, current.id, body)


@router.put("/education/material", response_model=CertificationsResponse, summary="提交学历认证图片材料")
async def education_material(
    education: str = Form(..., min_length=1, max_length=16),
    school: str = Form(..., min_length=2, max_length=128),
    file: UploadFile = File(...),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CertificationsResponse:
    return await submit_education_material(db, current.id, education, school, file)


@router.put("/single-pledge", response_model=CertificationsResponse, summary="提交单身承诺手写签名")
async def single_pledge(
    agreement_version: str = Form(..., min_length=1, max_length=32),
    file: UploadFile = File(...),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CertificationsResponse:
    return await submit_single_pledge(db, current.id, agreement_version, file)


@router.put("/house", response_model=CertificationsResponse, summary="提交房产认证材料")
async def house(file: UploadFile = File(...), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CertificationsResponse:
    return await submit_house(db, current.id, file)


@router.put("/marriage", response_model=CertificationsResponse, summary="提交婚姻认证材料")
async def marriage(body: MarriageCertificationRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CertificationsResponse:
    return await submit_marriage(db, current.id, body)
