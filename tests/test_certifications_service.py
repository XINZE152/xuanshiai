from app.services.certifications import _item


def test_certification_item_includes_null_material_when_not_submitted() -> None:
    item = _item("education", {}, None)

    assert item["material"] is None
    assert item["material_submitted"] is False
