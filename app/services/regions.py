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
    # Accept padded route codes (e.g. "110000") by truncating to the tree code ("11").
    wanted = str(province_code)[:2]
    province = next((item for item in _REGIONS if str(item.get("code")) == wanted), None)
    if province is None:
        raise HTTPException(404, detail="省份编码不存在")
    return _response(province.get("children", []))


def list_districts(city_code: str) -> RegionListResponse:
    # Accept padded route codes (e.g. "110100") by truncating to the tree code ("1101").
    wanted = str(city_code)[:4]
    for province in _REGIONS:
        city = next((item for item in province.get("children", []) if str(item.get("code")) == wanted), None)
        if city is not None:
            return _response(city.get("children", []))
    raise HTTPException(404, detail="城市编码不存在")


def region_name(code: str | None) -> str | None:
    """Resolve one administrative code to its display name."""
    if not code:
        return None
    wanted = str(code)
    for province in _REGIONS:
        if str(province.get("code")) == wanted:
            return str(province.get("name"))
        for city in province.get("children", []):
            if str(city.get("code")) == wanted:
                return str(city.get("name"))
            for district in city.get("children", []):
                if str(district.get("code")) == wanted:
                    return str(district.get("name"))
    return None


def region_display(province_code: str | None, city_code: str | None, district_code: str | None) -> str | None:
    """Build a safe province/city/district label without exposing raw codes."""
    province = str(province_code)[:2] if province_code else None
    city = str(city_code)[:4] if city_code else None
    district = str(district_code)[:6] if district_code else None
    names = [region_name(code) for code in (province, city, district)]
    names = [name for name in names if name and name not in {"市辖区", "县"}]
    return " ".join(dict.fromkeys(names)) or None


def _match_region(nodes: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((node for node in nodes if str(node.get("name")) == name), None)


def _padded_region_code(code: str) -> str:
    """Tree codes are 2/4/6 digits; storage keeps them padded to six digits."""
    return code.ljust(6, "0")


def region_codes_from_text(value: str | None) -> tuple[str | None, str | None, str | None]:
    """把「江苏省 南京市 鼓楼区」这类逐级名称串解析为六位省市区编码。

    每一级只在上一级的子节点里按全名精确匹配，匹配不到就停在那一级：解析不出
    来的编码宁可留空，也不猜一个相近的区划。直辖市的下级名「市辖区」同样能匹配，
    与 region_display 的展示口径对得上。
    """
    if not value:
        return None, None, None
    parts = [part for part in str(value).split() if part]
    if not parts:
        return None, None, None
    province = _match_region(_REGIONS, parts[0])
    if province is None:
        return None, None, None
    province_code = _padded_region_code(str(province.get("code")))
    if len(parts) < 2:
        return province_code, None, None
    city = _match_region(province.get("children", []), parts[1])
    if city is None:
        return province_code, None, None
    city_code = _padded_region_code(str(city.get("code")))
    if len(parts) < 3:
        return province_code, city_code, None
    district = _match_region(city.get("children", []), parts[2])
    if district is None:
        return province_code, city_code, None
    return province_code, city_code, _padded_region_code(str(district.get("code")))
