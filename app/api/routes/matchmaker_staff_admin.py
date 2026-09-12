"""Platform owner APIs for matchmaker staff management."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentMatchmakerAdmin, get_current_matchmaker_admin
from app.db.session import get_db
from app.schemas.matchmaker_staff_admin import (
    AdminMenuNode,
    CommissionLevelItem,
    CommonUploadResponse,
    MatchmakerDeleteResponse,
    MatchmakerDetailReport,
    MatchmakerLockUpdate,
    MatchmakerPermissions,
    MatchmakerPermissionsUpdate,
    MatchmakerPlatformTokenResponse,
    MatchmakerPosterResponse,
    MatchmakerStaffCreate,
    MatchmakerStaffDetail,
    MatchmakerStaffPage,
    MatchmakerUserCandidate,
    MatchmakerStaffUpdate,
    MatchmakerTutorial,
    MatchmakerVisibilityUpdate,
    MatchmakerWorkReport,
    StoreDictItem,
)
from app.services import matchmaker_staff_admin as service
from app.services.profile import _image_outputs, _read_limited
from uuid import uuid4
from pathlib import Path as FilePath
from fastapi import HTTPException
from app.core.config import settings

router = APIRouter(prefix="/admin")


def _guard(current: CurrentMatchmakerAdmin) -> None:
    current.require("matchmaker.manage")


@router.get("/matchmakers", response_model=MatchmakerStaffPage)
async def list_matchmakers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=100),
    store_id: int | None = Query(None, ge=1),
    commission_level_id: int | None = Query(None, ge=1),
    locked: bool | None = Query(None),
    in_store: bool | None = Query(None, description="true 仅分店红娘 / false 仅总店红娘 / 不传 全部"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerStaffPage:
    _guard(current)
    return await service.list_staff(
        db, current, page, page_size, keyword, store_id, commission_level_id, locked, in_store
    )


@router.get("/matchmakers/user-candidates", response_model=list[MatchmakerUserCandidate])
async def matchmaker_user_candidates(
    keyword: str = Query(..., min_length=2, max_length=100),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[MatchmakerUserCandidate]:
    _guard(current)
    return await service.search_user_candidates(db, keyword.strip())


@router.post("/matchmakers", response_model=MatchmakerStaffDetail, status_code=201)
async def create_matchmaker(
    body: MatchmakerStaffCreate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerStaffDetail:
    _guard(current)
    return await service.create_staff(db, current, body)


@router.get("/matchmakers/tutorial", response_model=MatchmakerTutorial)
async def get_tutorial(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerTutorial:
    _guard(current)
    return await service.tutorial(db)


@router.get("/matchmakers/{matchmaker_id}", response_model=MatchmakerStaffDetail)
async def get_matchmaker(
    matchmaker_id: int = Path(..., ge=1),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerStaffDetail:
    _guard(current)
    return await service.get_staff(db, matchmaker_id)


@router.put("/matchmakers/{matchmaker_id}", response_model=MatchmakerStaffDetail)
async def update_matchmaker(
    matchmaker_id: int,
    body: MatchmakerStaffUpdate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerStaffDetail:
    _guard(current)
    return await service.update_staff(db, matchmaker_id, body)


@router.delete("/matchmakers/{matchmaker_id}", response_model=MatchmakerDeleteResponse)
async def delete_matchmaker(
    matchmaker_id: int,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerDeleteResponse:
    _guard(current)
    return await service.delete_staff(db, matchmaker_id)


@router.patch("/matchmakers/{matchmaker_id}/lock", response_model=MatchmakerStaffDetail)
async def lock_matchmaker(
    matchmaker_id: int,
    body: MatchmakerLockUpdate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerStaffDetail:
    _guard(current)
    return await service.set_lock(db, matchmaker_id, body)


@router.patch("/matchmakers/{matchmaker_id}/visibility", response_model=MatchmakerStaffDetail)
async def visibility_matchmaker(
    matchmaker_id: int,
    body: MatchmakerVisibilityUpdate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerStaffDetail:
    _guard(current)
    return await service.set_visibility(db, matchmaker_id, body)


@router.get("/menus/tree", response_model=list[AdminMenuNode])
async def menus_tree(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminMenuNode]:
    _guard(current)
    return await service.menu_tree(db)


@router.get("/matchmakers/{matchmaker_id}/permissions", response_model=MatchmakerPermissions)
async def get_permissions(
    matchmaker_id: int,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerPermissions:
    _guard(current)
    return await service.permissions(db, matchmaker_id)


@router.put("/matchmakers/{matchmaker_id}/permissions", response_model=MatchmakerPermissions)
async def put_permissions(
    matchmaker_id: int,
    body: MatchmakerPermissionsUpdate,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerPermissions:
    _guard(current)
    return await service.save_permissions(db, matchmaker_id, body)


def _dates(from_date: date | None, to_date: date | None) -> tuple[date, date]:
    end = to_date or date.today()
    return from_date or end - timedelta(days=29), end


@router.get("/matchmakers/{matchmaker_id}/work-report", response_model=MatchmakerWorkReport)
async def get_work_report(
    matchmaker_id: int,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerWorkReport:
    _guard(current)
    start, end = _dates(from_date, to_date)
    return await service.work_report(db, matchmaker_id, start, end)


@router.get("/matchmakers/{matchmaker_id}/report", response_model=MatchmakerDetailReport)
async def get_report(
    matchmaker_id: int,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerDetailReport:
    _guard(current)
    start, end = _dates(from_date, to_date)
    return await service.report(db, matchmaker_id, start, end)


@router.post("/matchmakers/{matchmaker_id}/poster", response_model=MatchmakerPosterResponse)
async def create_poster(
    matchmaker_id: int,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerPosterResponse:
    _guard(current)
    await service.get_staff(db, matchmaker_id)
    return await service.poster(matchmaker_id)


@router.post(
    "/matchmakers/{matchmaker_id}/platform-token", response_model=MatchmakerPlatformTokenResponse
)
async def create_platform_token(
    matchmaker_id: int,
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> MatchmakerPlatformTokenResponse:
    _guard(current)
    return await service.platform_token(db, matchmaker_id)


@router.get("/dict/commission-levels", response_model=list[CommissionLevelItem])
async def commission_levels(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[CommissionLevelItem]:
    current.require("matchmaker.read")
    return await service.levels(db)


@router.get("/dict/stores", response_model=list[StoreDictItem])
async def dict_stores(
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
    db: AsyncSession = Depends(get_db),
) -> list[StoreDictItem]:
    current.require("matchmaker.read")
    return await service.stores(db)


@router.post("/common/upload", response_model=CommonUploadResponse, status_code=201)
async def common_upload(
    file: UploadFile = File(...),
    current: CurrentMatchmakerAdmin = Depends(get_current_matchmaker_admin),
) -> CommonUploadResponse:
    current.require("matchmaker.manage")
    data = await _read_limited(file, 5 * 1024 * 1024)
    try:
        output, thumbnail = _image_outputs(data)
    except HTTPException:
        raise
    directory = FilePath(settings.upload_dir) / "admin"
    directory.mkdir(parents=True, exist_ok=True)
    name = uuid4().hex
    target = directory / f"{name}.webp"
    target.write_bytes(output)
    (directory / f"{name}-thumb.webp").write_bytes(thumbnail)
    return CommonUploadResponse(
        url=f"/storage/uploads/admin/{name}.webp", content_type="image/webp", size=len(output)
    )
