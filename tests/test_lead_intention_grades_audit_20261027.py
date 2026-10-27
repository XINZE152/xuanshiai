"""2026-10-27 客源审核状态「无效(rejected)」+ 客户意向 A-G 七档 契约回归。

覆盖三处真后端缺口的落地：
- #1 audit_status 枚举扩到 active/pending/rejected（schema + 路由 pattern）。
- #2 intention_level 由 1-3 粗粒度扩为 1-7（schema 边界 + 路由 le=7 + 导入别名）。
- #2 存量迁移回填规则（低→A=1、中→D=4、高→F=6）与 up/down 语义互逆。

无需 DB：直测 pydantic 契约、OpenAPI 参数边界、导入别名纯函数、迁移脚本正文。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.main import app
from app.schemas.customer_lead_admin import CustomerLeadCreate, CustomerLeadUpdate
from app.services import customer_lead_admin as lead_service

LEAD_LIST_PATH = "/api/v1/admin/customer-leads"


def _get_param(path: str, name: str) -> dict:
    params = app.openapi()["paths"][path]["get"]["parameters"]
    return next(item for item in params if item["name"] == name)


def _scalar_schema(param: dict) -> dict:
    """取可选参数（T | None）的实际类型分支：OpenAPI 里约束落在 anyOf 的非 null 支。"""
    schema = param["schema"]
    if "anyOf" in schema:
        return next(branch for branch in schema["anyOf"] if branch.get("type") != "null")
    return schema


# --------------------------------------------------------------------------- #
# #1 审核状态 rejected
# --------------------------------------------------------------------------- #


def test_audit_status_rejected_accepted_on_create_and_update() -> None:
    created = CustomerLeadCreate(name="张女士", phone="13800000000", source="抖音", audit_status="rejected")
    assert created.audit_status == "rejected"
    assert CustomerLeadUpdate(audit_status="rejected").audit_status == "rejected"


def test_audit_status_rejected_in_route_pattern() -> None:
    pattern = _scalar_schema(_get_param(LEAD_LIST_PATH, "audit_status"))["pattern"]
    assert "rejected" in pattern


# --------------------------------------------------------------------------- #
# #2 意向 A-G 七档
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("grade", [1, 2, 3, 4, 5, 6, 7])
def test_intention_accepts_all_seven_grades(grade: int) -> None:
    assert CustomerLeadCreate(name="张女士", phone="13800000000", source="抖音", intention_level=grade).intention_level == grade


def test_intention_rejects_out_of_range() -> None:
    for bad in (0, 8):
        with pytest.raises(ValidationError):
            CustomerLeadCreate(name="张女士", phone="13800000000", source="抖音", intention_level=bad)


def test_intention_route_bound_is_seven() -> None:
    schema = _scalar_schema(_get_param(LEAD_LIST_PATH, "intention_level"))
    assert schema["maximum"] == 7
    assert schema["minimum"] == 1


def test_intention_grades_catalog_has_seven_tiers() -> None:
    grades = lead_service.LEAD_INTENTION_GRADES
    assert sorted(grades) == [1, 2, 3, 4, 5, 6, 7]
    assert grades[1].startswith("A") and grades[7].startswith("G")


def test_intention_import_aliases_cover_digits_letters_fullnames_legacy() -> None:
    aliases = lead_service._INTENTION_ALIASES
    # 数字 1-7
    assert all(aliases[str(g)] == g for g in range(1, 8))
    # 字母 A-G（大小写）
    assert aliases["A"] == 1 and aliases["G"] == 7
    assert aliases["a"] == 1 and aliases["g"] == 7
    # 全称
    assert aliases[lead_service.LEAD_INTENTION_GRADES[5]] == 5
    # 旧粗粒度回退：低→1(A)、中→4(D)、高→6(F)
    assert aliases["低"] == 1 and aliases["中"] == 4 and aliases["高"] == 6


def test_parse_intention_falls_back_to_default_on_unknown() -> None:
    assert lead_service._parse_intention("不认识的档位") == 1


# --------------------------------------------------------------------------- #
# #2 迁移：回填规则与 up/down 互逆
# --------------------------------------------------------------------------- #


def _migration_sql(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "migrations" / "customer_lead_intention_grades" / name).read_text(encoding="utf-8")


def test_migration_backfill_rule_matches_frontend_merge() -> None:
    up = _migration_sql("20261027_05_lead_intention_grades_up.sql")
    # 高→6(F)、中→4(D) 的回填语句存在，低(1) 保持不动
    assert re.search(r"SET `intention_level` = 6 WHERE `intention_level` = 3", up)
    assert re.search(r"SET `intention_level` = 4 WHERE `intention_level` = 2", up)


def test_migration_down_reverses_up_lossy() -> None:
    down = _migration_sql("20261027_05_lead_intention_grades_down.sql")
    # 6/7→3、4/5→2、2/3→1 折算回粗粒度
    assert re.search(r"= 3 WHERE `intention_level` IN \(6, 7\)", down)
    assert re.search(r"= 2 WHERE `intention_level` IN \(4, 5\)", down)
    assert re.search(r"= 1 WHERE `intention_level` IN \(2, 3\)", down)


def test_migration_verify_asserts_range_and_comment() -> None:
    verify = _migration_sql("20261027_05_lead_intention_grades_verify.sql")
    assert "NOT BETWEEN 1 AND 7" in verify
    assert "intention_level" in verify
