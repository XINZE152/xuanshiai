"""工作台客源意向档位对齐 A-G 七档回归（2026-11-07）。

背景：迁移 ``20261027_05_lead_intention_grades`` 把
``customer_lead(.customer_lead_follow_up).intention_level`` 从 1低/2中/3高 扩为
A-G 1-7，并回填存量「中→4」「高→6」。后台与 AI 抽取都跟着改了，唯独红娘工作台
的 5 个契约仍留着 ``Literal[1, 2, 3]``。``_lead_from_row`` 直接拿库行构造
``WorkspaceLead``，碰到回填出来的 4/6 就抛 pydantic ``ValidationError``，
``GET /matchmaker/workbench/leads`` 整页 500（测试服 33 条线索里 10 条越界）。

覆盖：
- 读：1-7 每一档的库行都能构造 ``WorkspaceLead`` / ``WorkspaceLeadFollowUp``；
- 边界：0 和 8 仍被拒，越界脏值不静默放过；
- 写：工作台新建/编辑/跟进可提交 1-7；
- 守卫：档位取值在客源域三个契约文件里完全一致，且只有一处定义。

注意 ``user_profile.intention_level``（会员跟进侧）是另一套 1低/2中/3高 口径，
不在本文件的守卫范围内，见 ``app/schemas/matchmaker_member_admin.py``。

无真实 DB：读路径直接喂记录型 dict。
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Literal, get_origin

import pytest
from pydantic import ValidationError

from app.schemas.customer_lead_admin import (
    CustomerLead,
    CustomerLeadCreate,
    CustomerLeadFollowUpCreate,
    CustomerLeadUpdate,
)
from app.schemas.lead_ai_extract import LeadExtractedFields
from app.schemas.matchmaker_workspace import (
    WorkspaceLead,
    WorkspaceLeadCreate,
    WorkspaceLeadFollowUp,
    WorkspaceLeadFollowUpCreate,
    WorkspaceLeadUpdate,
)
from app.services.matchmaker_workspace import _lead_from_row

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEAD_DOMAIN_SCHEMA_FILES = (
    "app/schemas/customer_lead_admin.py",
    "app/schemas/matchmaker_workspace.py",
    "app/schemas/lead_ai_extract.py",
)
ALL_GRADES = frozenset({1, 2, 3, 4, 5, 6, 7})

NOW = dt.datetime(2026, 11, 7, 12, 0, 0)


def _lead_row(intention_level: int) -> dict:
    """``_LEAD_SELECT`` 的一行结果，intention_level 取任意待测档位。"""
    return {
        "id": 10,
        "name": "林晚",
        "phone": "13800000000",
        "wechat": None,
        "source": "朋友推荐",
        "intention_level": intention_level,
        "status": "CONTACTED",
        "next_follow_at": None,
        "created_at": NOW,
        "updated_at": NOW,
        "matchmaker_id": 11,
        "matchmaker_name": "王红娘",
        "remark": None,
        "last_follow_up_at": None,
        "converted_user_id": None,
    }


def _literal_values(annotation: object) -> frozenset:
    """取出字段注解里的 Literal 取值集合（Optional 包装也继续下钻）。"""
    if get_origin(annotation) is Literal:
        return frozenset(annotation.__args__)  # type: ignore[attr-defined]
    for arg in getattr(annotation, "__args__", ()) or ():
        found = _literal_values(arg)
        if found:
            return found
    return frozenset()


# --------------------------------------------------------------------- 读路径


@pytest.mark.parametrize("intention_level", sorted(ALL_GRADES))
def test_lead_row_accepts_every_grade(intention_level: int) -> None:
    """迁移回填出来的 4/6 必须能读出来：这就是线上 500 的那一条。"""
    assert _lead_from_row(_lead_row(intention_level)).intention_level == intention_level


@pytest.mark.parametrize("intention_level", [0, 8, -1])
def test_lead_row_still_rejects_out_of_range(intention_level: int) -> None:
    with pytest.raises(ValidationError):
        _lead_from_row(_lead_row(intention_level))


def test_follow_up_row_accepts_backfilled_grade() -> None:
    """跟进表同样被迁移回填过；后台可写 1-7，工作台读就不能卡在 1-3。"""
    follow_up = WorkspaceLeadFollowUp(
        id=1,
        lead_id=10,
        method="PHONE",
        content="已确认到店时间",
        intention_level=6,
        next_follow_at=None,
        created_at=NOW,
    )
    assert follow_up.intention_level == 6


# --------------------------------------------------------------------- 写路径


def test_workspace_lead_write_accepts_grades_above_three() -> None:
    created = WorkspaceLeadCreate(
        name="林晚", phone="13800000000", source="朋友推荐", intention_level=7
    )
    assert created.intention_level == 7
    assert WorkspaceLeadUpdate(intention_level=4).intention_level == 4
    assert WorkspaceLeadFollowUpCreate(
        method="VISIT", content="到店未签约", intention_level=5
    ).intention_level == 5


def test_workspace_lead_write_rejects_unknown_grade() -> None:
    with pytest.raises(ValidationError):
        WorkspaceLeadCreate(name="林晚", phone="13800000000", source="朋友推荐", intention_level=8)
    with pytest.raises(ValidationError):
        WorkspaceLeadUpdate(intention_level=0)


# ----------------------------------------------------------------------- 守卫


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (WorkspaceLead, "intention_level"),
        (WorkspaceLeadCreate, "intention_level"),
        (WorkspaceLeadUpdate, "intention_level"),
        (WorkspaceLeadFollowUp, "intention_level"),
        (WorkspaceLeadFollowUpCreate, "intention_level"),
        (CustomerLead, "intention_level"),
        (CustomerLeadCreate, "intention_level"),
        (CustomerLeadUpdate, "intention_level"),
        (CustomerLeadFollowUpCreate, "intention_level"),
        (LeadExtractedFields, "intention_level"),
    ],
)
def test_lead_contracts_share_the_seven_grade_domain(model: type, field: str) -> None:
    """客源域任何契约的 intention_level 取值集合必须完全一致，防止再次漏改消费方。"""
    assert _literal_values(model.model_fields[field].annotation) == ALL_GRADES


def test_intention_level_is_never_redeclared_inline() -> None:
    """档位范围只允许来自共享别名，不允许再就地写 Literal[...]。"""
    offenders: list[str] = []
    for relative in LEAD_DOMAIN_SCHEMA_FILES:
        path = PROJECT_ROOT / relative
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "intention_level" in line and "Literal[" in line:
                offenders.append(f"{relative}:{lineno}")
    assert not offenders, offenders


def test_grade_alias_is_defined_exactly_once() -> None:
    definitions = [
        str(path.relative_to(PROJECT_ROOT).as_posix())
        for path in (PROJECT_ROOT / "app").rglob("*.py")
        if re.search(r"^LeadIntentionGrade\s*=\s*", path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert definitions == ["app/schemas/customer_lead_admin.py"], definitions
