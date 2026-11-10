"""认证 / 家乡筛选维度（2026-11-10，Q20）。

前端「精准甄选」此前只能把「仅认证用户」和「家乡偏好」置灰，因为
``DiscoveryFilters`` 没有对应字段。本文件锁定：

1. ``certified_only`` 只匹配 ``realname_status=2``；
2. 家乡省/市/区逐级精确匹配，空值不筛选；
3. ``filter-options`` 下发认证维度；
4. 所有用户输入走参数占位符，不拼 SQL。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.discovery import DiscoveryFilters
from app.services.discovery import _filter_sql, get_filter_options


def test_certified_only_uses_realname_status() -> None:
    params: dict[str, object] = {}
    clauses = _filter_sql(DiscoveryFilters(certified_only=True), params)

    assert clauses == ["COALESCE(ua.realname_status, 0) = 2"]
    assert params == {}


def test_certified_only_false_is_noop() -> None:
    params: dict[str, object] = {}
    assert _filter_sql(DiscoveryFilters(certified_only=False), params) == []
    assert params == {}


def test_hometown_filters_are_parameterized_and_independent() -> None:
    params: dict[str, object] = {}
    clauses = _filter_sql(
        DiscoveryFilters(
            hometown_province_code="320000",
            hometown_city_code="320100",
            hometown_district_code="320102",
        ),
        params,
    )

    assert clauses == [
        "p.hometown_province_code = :filter_hometown_province_code",
        "p.hometown_city_code = :filter_hometown_city_code",
        "p.hometown_district_code = :filter_hometown_district_code",
    ]
    assert params == {
        "filter_hometown_province_code": "320000",
        "filter_hometown_city_code": "320100",
        "filter_hometown_district_code": "320102",
    }


def test_hometown_partial_filter_only_adds_provided_level() -> None:
    params: dict[str, object] = {}
    clauses = _filter_sql(DiscoveryFilters(hometown_city_code="330100"), params)

    assert clauses == ["p.hometown_city_code = :filter_hometown_city_code"]
    assert params == {"filter_hometown_city_code": "330100"}


def test_empty_hometown_is_noop() -> None:
    params: dict[str, object] = {}
    assert _filter_sql(DiscoveryFilters(hometown_province_code=""), params) == []
    assert params == {}


def test_hometown_code_length_capped() -> None:
    with pytest.raises(ValidationError):
        DiscoveryFilters(hometown_province_code="1" * 33)


def test_certified_and_hometown_can_combine() -> None:
    params: dict[str, object] = {}
    clauses = _filter_sql(
        DiscoveryFilters(certified_only=True, hometown_province_code="440000"),
        params,
    )

    assert clauses == [
        "p.hometown_province_code = :filter_hometown_province_code",
        "COALESCE(ua.realname_status, 0) = 2",
    ]
    assert params == {"filter_hometown_province_code": "440000"}


@pytest.mark.asyncio
async def test_filter_options_expose_certification_dimension() -> None:
    options = await get_filter_options()
    assert options.certification_options == [
        {"value": "realname", "label": "实名认证"}
    ]
