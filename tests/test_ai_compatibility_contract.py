"""第一批契约回归：抽取契约 → 存储形态 → 投影适配 → 评分器全链一致。

本文件锁定 2026-09-17 修复清单
（``docs/AI画像-搜索-匹配度修复清单-2026-09-17.md`` §3.4）的契约边界，回答的
问题是「**字段都在、值也合法时，分数是否符合业务预期**」，而不是重复
``test_ai_compatibility.py`` 的规则单测。

设计纪律（清单 §3.4 要求）：

- **期望值独立书写**：断言里的数字按业务语义人工算出，不调用被测函数推导，
  避免实现与测试共享同一套错误逻辑。
- **走真实形态**：合法抽取结果先过 ``normalize_profile_extracted_value``（契约
  校验），再 ``json.dumps/loads`` 往返（投影 ``fields_json`` 的真实存储形态：
  集合由 tuple 变 list），最后交给评分器——这是生产链路上值真正经历的三步。
- **越界即未知**：不可表达的取值必须记 ``DIMENSION_UNKNOWN`` 并排除出加权分母，
  不得退化成 0 分（清单 C-01/C-02/C-04 的验收口径）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.schemas.ai_profile import normalize_profile_extracted_value
from app.services.ai.compatibility import (
    COMPATIBILITY_RULES,
    REASON_UNKNOWN,
    FeatureSet,
    compute_compatibility,
)

# ----------------------------------------------------------------------
# 链路工具：契约校验 → 投影存储形态
# ----------------------------------------------------------------------


def _through_storage(subject: str, fields: dict[str, Any]) -> dict[str, Any]:
    """把一份「模型抽取结果」走完契约校验与投影存储形态。

    ``normalize_profile_extracted_value`` 是抽取结果的唯一校验入口
    （``app/services/ai/base.py`` 构造字段时调用），集合会被归一为 tuple；
    投影 ``fields_json`` 是 JSON 文本，读出后集合回到 list。两步都不能省，
    否则测的是内存里的理想值而不是真实链路。
    """
    normalized = {
        key: normalize_profile_extracted_value(subject, key, value)
        for key, value in fields.items()
    }
    return json.loads(json.dumps(normalized, ensure_ascii=False))


# 合法抽取结果（与 ``app/services/ai/prompts/profile_extract.py`` 契约一致）：
# 个人侧是标量，理想型侧集合/区间。
_VIEWER_PERSONAL = {
    "age": 30,
    "city_code": "330100",
    "marriage_status": "single",
    "education_level": 4,
    "height_cm": 175,
    "income_band": 4,
    "interest_tags": ["旅行", "摄影"],
    "relationship_goal": "marriage",
}
_VIEWER_IDEAL = {
    "age": {"min": 26, "max": 34},
    "city_code": ["330100", "330200"],
    "marriage_status": ["single"],
    "education_level": {"min": 3},
    "height_cm": {"min": 160, "max": 180},
    "income_band": {"min": 3},
    "interest_tags": ["旅行", "音乐"],
    "relationship_goal": ["marriage"],
}
_TARGET_PERSONAL = {
    "age": 28,
    "city_code": "330100",
    "marriage_status": "single",
    "education_level": 4,
    "height_cm": 165,
    "income_band": 3,
    "interest_tags": ["旅行", "美食"],
    "relationship_goal": "marriage",
}
_TARGET_IDEAL = {
    "age": {"min": 28, "max": 36},
    "city_code": ["330100"],
    "marriage_status": ["single"],
    "education_level": {"min": 3},
    "height_cm": {"min": 170, "max": 185},
    "income_band": {"min": 2},
    "interest_tags": ["摄影", "旅行"],
    "relationship_goal": ["marriage"],
}


@pytest.fixture
def viewer() -> FeatureSet:
    return FeatureSet(
        profile=_through_storage("personal", _VIEWER_PERSONAL),
        preference=_through_storage("ideal_partner", _VIEWER_IDEAL),
    )


@pytest.fixture
def target() -> FeatureSet:
    return FeatureSet(
        profile=_through_storage("personal", _TARGET_PERSONAL),
        preference=_through_storage("ideal_partner", _TARGET_IDEAL),
    )


# ----------------------------------------------------------------------
# 1. 全链一致性：合法数据必须拿到业务预期分数
# ----------------------------------------------------------------------


def test_full_coverage_pair_score_matches_hand_computed_expectation(
    viewer: FeatureSet, target: FeatureSet
) -> None:
    """双手算期望：A→B 92.5、B→A 100、调和平均 96.1、coverage 1.0。

    A→B 逐维（权重 年龄20/城市15/婚姻10/学历10/身高10/收入10/兴趣15/期待10）：
    年龄 28∈[26,34]=100、城市 330100∈{330100,330200}=100、
    婚姻 single∈{single}=100、学历 4≥3=100、身高 165∈[160,180]=100、
    收入档 3≥3=100、兴趣 {旅行,美食}∩{旅行,音乐} 命中 1/2=50、
    期待 marriage∈{marriage}=100。
    Σ(w·s) = 20*100+15*100+10*100+10*100+10*100+10*100+15*50+10*100 = 9250
    → 9250/100 = 92.5。
    B→A：A 各维度均满足 B 的偏好（兴趣两侧标签集合相同）→ 100。
    调和平均 = 2*92.5*100/(92.5+100) = 18500/192.5 = 96.1038… → 96.1。
    """
    result = compute_compatibility(viewer, target, COMPATIBILITY_RULES)

    assert result.status == "ready"
    assert result.coverage == 1.0
    assert result.directions == (92.5, 100.0)
    assert result.pair_score == 96.1
    assert REASON_UNKNOWN not in result.reason_codes


def test_collection_membership_scores_both_contract_forms(
    viewer: FeatureSet, target: FeatureSet
) -> None:
    """集合类维度无论取 tuple 还是 list（契约的两种表示）都必须同样计分。

    ``normalize_profile_extracted_value`` 返回 tuple，投影 JSON 读出后是 list；
    评分器曾只认 list（``_score_city``）或完全不认集合（``_score_marriage``），
    导致同一份合法数据在两条路径上得分不同。
    """
    from_list = compute_compatibility(viewer, target, COMPATIBILITY_RULES)

    # 手工把理想型侧集合换成 tuple 形态（模拟未序列化的内存值）。
    tuple_viewer = FeatureSet(
        profile=viewer.profile,
        preference={
            **viewer.preference,
            "marriage_status": ("single",),
            "relationship_goal": ("marriage",),
            "city_code": ("330100", "330200"),
        },
    )
    tuple_target = FeatureSet(
        profile=target.profile,
        preference={
            **target.preference,
            "marriage_status": ("single",),
            "relationship_goal": ("marriage",),
            "city_code": ("330100",),
        },
    )
    from_tuple = compute_compatibility(tuple_viewer, tuple_target, COMPATIBILITY_RULES)

    assert from_tuple.pair_score == from_list.pair_score == 96.1
    assert from_tuple.directions == from_list.directions == (92.5, 100.0)
    assert from_tuple.coverage == from_list.coverage == 1.0


def test_personal_side_single_element_collection_is_equivalent(
    viewer: FeatureSet, target: FeatureSet
) -> None:
    """个人侧若存成单元素集合（历史/异常写入），语义等同单值，不得判 0。"""
    collection_target = FeatureSet(
        profile={
            **target.profile,
            "marriage_status": ["single"],
            "relationship_goal": ["marriage"],
        },
        preference=target.preference,
    )

    result = compute_compatibility(viewer, collection_target, COMPATIBILITY_RULES)

    assert result.directions == (92.5, 100.0)
    assert result.pair_score == 96.1


# ----------------------------------------------------------------------
# 2. 学历：方向 + 上下界（C-03）
# ----------------------------------------------------------------------


def test_education_respects_min_and_max_bounds() -> None:
    """学历刻度 1=初中及以下…6=博士：下限、上限、双边界都要生效。

    上界曾被完全忽略：``{"max": 4}``（本科及以下）对博士 6 也返回满分，
    对使用者意味着「我只要本科及以下」的偏好被当成满足。
    """
    from app.services.ai.compatibility import _score_education

    # 下限：本科(4)及以上 → 本科/硕士/博士满足，大专及以下不满足
    assert _score_education({"min": 4}, 6) == 100.0
    assert _score_education({"min": 4}, 4) == 100.0
    assert _score_education({"min": 4}, 3) == 0.0
    # 上限：本科(4)及以下 → 本科及以下满足，硕士不满足
    assert _score_education({"max": 4}, 4) == 100.0
    assert _score_education({"max": 4}, 3) == 100.0
    assert _score_education({"max": 4}, 6) == 0.0
    # 双边界：大专到本科
    assert _score_education({"min": 3, "max": 4}, 3) == 100.0
    assert _score_education({"min": 3, "max": 4}, 4) == 100.0
    assert _score_education({"min": 3, "max": 4}, 5) == 0.0
    # 不设限
    assert _score_education({"min": None, "max": None}, 1) == 100.0


# ----------------------------------------------------------------------
# 3. 不可表达取值 → DIMENSION_UNKNOWN，不污染加权平均（C-01/C-04）
# ----------------------------------------------------------------------


def test_amount_denominated_income_preference_is_unknown_not_zero(
    viewer: FeatureSet, target: FeatureSet
) -> None:
    """历史金额口径（``{"min": 10000}``）不得参与档位比较，也不得记 0 分。

    修复前：理想型 ``{"min": 10000}`` 与个人档位 3 直接数值比较 → 恒判不满足，
    把「口径不一致」伪装成「条件不满足」。契约收敛为 0-6 档位后，这种值属于
    不可表达输入，必须走 unknown（排除分母），让 coverage 如实下降。
    """
    legacy_preference = {**viewer.preference, "income_band": {"min": 10000}}

    result = compute_compatibility(
        FeatureSet(profile=viewer.profile, preference=legacy_preference),
        target,
        COMPATIBILITY_RULES,
    )

    # 收入维度（权重 10）退出分母 → 该方向覆盖率 90/100 = 0.9。
    assert result.coverage == 0.9
    assert REASON_UNKNOWN in result.reason_codes


def test_education_above_contract_scale_is_unknown(viewer: FeatureSet) -> None:
    """学历 7/8 已越出契约 1-6，属不可表达值，不得当有效学历比较。"""
    out_of_scale = FeatureSet(
        profile={**viewer.profile, "education_level": 7},
        preference=viewer.preference,
    )

    result = compute_compatibility(viewer, out_of_scale, COMPATIBILITY_RULES)

    # 关键口径：不再把 7 当成「比本科高」从而给对方满分。
    assert result.coverage < 1.0
    assert REASON_UNKNOWN in result.reason_codes


def test_multi_element_personal_fact_is_unknown(viewer: FeatureSet) -> None:
    """个人事实出现多元素集合时无法判定单值语义，按 unknown 处理而非猜一个。"""
    ambiguous = FeatureSet(
        profile={**viewer.profile, "marriage_status": ["single", "divorced"]},
        preference=viewer.preference,
    )

    result = compute_compatibility(viewer, ambiguous, COMPATIBILITY_RULES)

    assert result.coverage < 1.0
    assert REASON_UNKNOWN in result.reason_codes


# ----------------------------------------------------------------------
# 4. 契约边界：越界值必须在校验入口就被拒绝
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "field_key", "value"),
    (
        ("personal", "income_band", 20000),  # 金额混入档位
        ("personal", "income_band", 7),  # 档位越界
        ("personal", "education_level", 7),  # 学历越界（契约 1-6）
        ("ideal_partner", "income_band", {"min": 10000}),  # 金额口径区间
        ("ideal_partner", "education_level", {"min": 7}),
        ("ideal_partner", "marriage_status", "single"),  # 集合维度给了标量
    ),
)
def test_out_of_contract_values_are_rejected_at_the_contract(
    subject: str, field_key: str, value: Any
) -> None:
    """这些值都必须被契约拒绝——它们正是修复前测试夹具里的形态。"""
    with pytest.raises(ValueError):
        normalize_profile_extracted_value(subject, field_key, value)


def test_repo_fixtures_are_contract_valid() -> None:
    """夹具自检：本文件与主测试套件的夹具值必须全部通过契约校验。

    修复前的夹具用了 ``income_band: 20000/15000``、标量 ``marriage_status``、
    中文 ``relationship_goal``，因为不经过契约校验所以测试全绿——这条断言让
    错值再也进不了测试。
    """
    for subject, fields in (
        ("personal", _VIEWER_PERSONAL),
        ("ideal_partner", _VIEWER_IDEAL),
        ("personal", _TARGET_PERSONAL),
        ("ideal_partner", _TARGET_IDEAL),
    ):
        for key, value in fields.items():
            normalize_profile_extracted_value(subject, key, value)  # 不抛错即通过
