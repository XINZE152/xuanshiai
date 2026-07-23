"""Manual submission endpoints for education, house and marriage certifications."""

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.certifications import CertificationsResponse, EducationCertificationRequest, MarriageCertificationRequest
from app.services.certifications import get_certifications, submit_education, submit_house, submit_marriage

router = APIRouter(prefix="/users/me/certifications")


@router.get("", response_model=CertificationsResponse, summary="查询三项资质认证")
async def certifications(current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CertificationsResponse:
    return await get_certifications(db, current.id)


@router.put("/education", response_model=CertificationsResponse, summary="提交学历认证材料")
async def education(body: EducationCertificationRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CertificationsResponse:
    return await submit_education(db, current.id, body)


@router.put("/house", response_model=CertificationsResponse, summary="提交房产认证材料")
async def house(file: UploadFile = File(...), current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CertificationsResponse:
    return await submit_house(db, current.id, file)


@router.put("/marriage", response_model=CertificationsResponse, summary="提交婚姻认证材料")
async def marriage(body: MarriageCertificationRequest, current: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CertificationsResponse:
    return await submit_marriage(db, current.id, body)
