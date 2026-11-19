from app.services.regions import (
    list_cities,
    list_districts,
    list_provinces,
    region_codes_from_text,
    region_display,
)


def test_region_tree_lookup():
    provinces = list_provinces()
    assert provinces.total == len(provinces.items)
    assert any(item.code == "11" and item.name == "北京市" for item in provinces.items)

    cities = list_cities("11")
    assert any(item.code == "1101" for item in cities.items)

    districts = list_districts("1101")
    assert any(item.code == "110101" and item.name == "东城区" for item in districts.items)


def test_region_route_codes_can_be_normalized_to_tree_codes() -> None:
    assert list_cities("110000").items == list_cities("11").items
    assert list_districts("110100").items == list_districts("1101").items


def test_region_display_normalizes_padded_codes() -> None:
    assert region_display("110000", "110100", "110105") == "北京市 朝阳区"
    assert region_display("330000", "330100", None) == "浙江省 杭州市"


def test_region_codes_from_text_resolves_level_by_level() -> None:
    """「省 市 区」空格分隔逐级解析，结果能原样回到 region_display。"""
    assert region_codes_from_text("江苏省 南京市 鼓楼区") == ("320000", "320100", "320106")
    assert region_codes_from_text("福建省 福州市 鼓楼区") == ("350000", "350100", "350102")
    assert region_codes_from_text("北京市 市辖区 朝阳区") == ("110000", "110100", "110105")
    assert region_display(*region_codes_from_text("浙江省 杭州市 西湖区")) == "浙江省 杭州市 西湖区"


def test_region_codes_from_text_stops_at_the_first_unmatched_level() -> None:
    """对不上的等级留空，不猜相近区划；连省都对不上就整串不认。"""
    assert region_codes_from_text("浙江省 杭州市") == ("330000", "330100", None)
    assert region_codes_from_text("浙江省 杭州市 天河区") == ("330000", "330100", None)
    assert region_codes_from_text("浙江省 南京市") == ("330000", None, None)
    assert region_codes_from_text("江苏南京") == (None, None, None)
    assert region_codes_from_text("") == (None, None, None)
    assert region_codes_from_text(None) == (None, None, None)
