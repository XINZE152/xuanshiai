"""Public administrative region lookup endpoints."""

from fastapi import APIRouter, Query

from app.schemas.regions import RegionListResponse
from app.services.regions import list_cities, list_districts, list_provinces

router = APIRouter(prefix="/regions")


@router.get("/provinces", response_model=RegionListResponse, summary="查询省份")
async def provinces() -> RegionListResponse:
    return list_provinces()


@router.get("/cities", response_model=RegionListResponse, summary="按省份查询城市")
async def cities(province_code: str = Query(..., min_length=2, max_length=2, pattern=r"^\d{2}$")) -> RegionListResponse:
    return list_cities(province_code)


@router.get("/districts", response_model=RegionListResponse, summary="按城市查询区县")
async def districts(city_code: str = Query(..., min_length=4, max_length=4, pattern=r"^\d{4}$")) -> RegionListResponse:
    return list_districts(city_code)
