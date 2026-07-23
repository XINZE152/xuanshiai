"""Read-only province, city and district lookups backed by the bundled JSON file."""

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.schemas.regions import RegionItem, RegionListResponse

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "p-c-a.json"


def _load_regions() -> list[dict[str, Any]]:
    try:
        with _DATA_PATH.open("r", encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"区域数据文件不可用: {_DATA_PATH}") from exc
    if not isinstance(data, list):
        raise RuntimeError("区域数据格式必须是数组")
    return data


_REGIONS = _load_regions()


def _response(items: list[dict[str, Any]]) -> RegionListResponse:
    normalized = [RegionItem(code=str(item["code"]), name=str(item["name"])) for item in items]
    return RegionListResponse(items=normalized, total=len(normalized))


def list_provinces() -> RegionListResponse:
    return _response(_REGIONS)


def list_cities(province_code: str) -> RegionListResponse:
    province = next((item for item in _REGIONS if str(item.get("code")) == province_code), None)
    if province is None:
        raise HTTPException(404, detail="省份编码不存在")
    return _response(province.get("children", []))


def list_districts(city_code: str) -> RegionListResponse:
    for province in _REGIONS:
        city = next((item for item in province.get("children", []) if str(item.get("code")) == city_code), None)
        if city is not None:
            return _response(city.get("children", []))
    raise HTTPException(404, detail="城市编码不存在")
