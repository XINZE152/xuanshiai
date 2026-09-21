"""新旧评分离线对照工具（第四批 C2/C3/C5 交付物）。

只调用现有 compute 纯函数，不连数据库、不写快照、不扣配额、不调用任何模型：

- 旧展示分：``app.services.discovery._candidate_score``（``legacy-rule-v1``，
  发现页 match_score）
- 新兼容分：``app.services.ai.compatibility.compute_compatibility`` /
  ``directional_score``（``compatibility-rule-v1``，双向 + 谐波均值 pair +
  0.50 覆盖率门）

人物语义单一来源：每个样本用业务语义声明一次（学历档/收入档/年龄/城市/标签/
目标），再分别投影为两个引擎的输入域：

- legacy 域（user_profile 存储域）：education_level 1..5（1=高中及以下…5=博士，
  编辑写入域）、birthday、年收入元（编辑器中位映射）
- compat 域（AI 投影域，fields_json 原样保存抽取刻度）：education_level 1..6
  （1=初中及以下…6=博士，经 ``_AI_SYNC_EDU_MAP`` 与存储域语义对齐）、
  income_band 0..6（月收入档）、age 整数

输出逐样本字段：sample_id / legacy_display_score / legacy_reason / dir_a2b /
dir_b2a / pair_score / cov_a2b / cov_b2a / coverage / status / version_legacy /
version_pair / missing_reasons / notes。

用法::

    .venv/Scripts/python.exe scripts/compare_scores_offline.py

声明：以下全部为合成样本，结论只用于口径对照，不得解读为真实用户的
覆盖率或推荐质量。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from app.services.ai.compatibility import (
    COMPATIBILITY_ALGORITHM_VERSION,
    COMPATIBILITY_RULES,
    COVERAGE_THRESHOLD,
    FeatureSet,
    compute_compatibility,
    directional_score,
)
from app.services.discovery import (
    LEGACY_MATCH_ALGORITHM_VERSION,
    _candidate_score,
)

# AI 学历 1..6（初中及以下..博士）-> 基础资料存储域 1..5（高中及以下..博士）。
# 与 app/services/profile.py:_AI_SYNC_EDU_MAP 同表（双源，改动需互相同步）。
_AI_EDU_TO_STORAGE = {1: 1, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}

# 编辑器收入档位 -> 年收入元（pagesSub/userExtra/user/edit.uvue incomeValue）。
_INCOME_BAND_YEARLY = {
    "less_10w": 50_000,
    "10-20w": 150_000,
    "30-50w": 400_000,
    "50-100w": 750_000,
    "100w+": 1_000_000,
}
# AI 月收入档（profile_extract 契约 0..6）<- 收入档位标签。
_INCOME_BAND_AI = {
    "none": 0,
    "less_10w": 1,
    "10-20w": 2,
    "30-50w": 3,
    "50-100w": 4,
    "100w+": 5,
}


def _person(
    person_id: str,
    *,
    age: int,
    education_ai: int | None,
    income_band: str | None,
    city: str | None,
    tags: list[str] | None,
    goal: str | None,
    mbti: str | None,
    pref_age: tuple[int, int] | None,
    pref_education_min_ai: int | None,
    pref_city: str | None,
    pref_income_min_ai: int | None,
    pref_goal: str | None,
    pref_tags: list[str] | None,
    pref_education_unlimited: bool = False,
) -> dict[str, Any]:
    """业务语义声明一个合成人物（两引擎输入域由此派生）。"""
    return {
        "person_id": person_id,
        "age": age,
        "education_ai": education_ai,
        "income_band": income_band,
        "city": city,
        "tags": tags or [],
        "goal": goal,
        "mbti": mbti,
        "pref_age": pref_age,
        "pref_education_min_ai": pref_education_min_ai,
        "pref_education_unlimited": pref_education_unlimited,
        "pref_city": pref_city,
        "pref_income_min_ai": pref_income_min_ai,
        "pref_goal": pref_goal,
        "pref_tags": pref_tags or [],
    }


def _birthday(age: int) -> datetime:
    # discovery 的行来自 SQL，birthday 是 datetime；离线工具对齐同型输入。
    return datetime(datetime.now(UTC).year - age, 6, 15)


def _legacy_viewer(p: dict[str, Any]) -> dict[str, Any]:
    """compat 语义 -> discovery._candidate_score 的 viewer 行（存储域）。"""
    pref_age = p["pref_age"]
    return {
        "birthday": _birthday(p["age"]),
        "age_min": pref_age[0] if pref_age else None,
        "age_max": pref_age[1] if pref_age else None,
        "residence_city_code": p["city"],
        "mbti": p["mbti"],
        "interest_tags": json.dumps(p["tags"], ensure_ascii=False),
        "personality_tags": "[]",
        "tags": "{}",
    }


def _legacy_candidate(p: dict[str, Any]) -> dict[str, Any]:
    last_active = datetime.now(UTC).replace(tzinfo=None)
    return {
        "birthday": _birthday(p["age"]),
        "residence_city_code": p["city"],
        "mbti": p["mbti"],
        "interest_tags": json.dumps(p["tags"], ensure_ascii=False),
        "personality_tags": "[]",
        "tags": "{}",
        "last_active_at": last_active,
        "realname_status": 2,
        "is_single_pledge": 1,
    }


def _compat_features(p: dict[str, Any]) -> FeatureSet:
    """compat 语义 -> AI 投影域 FeatureSet（fields_json 同形）。"""
    profile: dict[str, Any] = {"age": p["age"]}
    if p["education_ai"] is not None:
        profile["education_level"] = p["education_ai"]
    if p["income_band"] is not None:
        profile["income_band"] = _INCOME_BAND_AI[p["income_band"]]
    if p["city"] is not None:
        profile["city_code"] = p["city"]
    if p["tags"]:
        profile["interest_tags"] = p["tags"]
    if p["goal"] is not None:
        profile["relationship_goal"] = p["goal"]
    if p["mbti"] is not None:
        profile["height_cm"] = _MBTI_FAKE_HEIGHT.get(p["person_id"], 170)

    preference: dict[str, Any] = {}
    if p["pref_age"] is not None:
        preference["age"] = {"min": p["pref_age"][0], "max": p["pref_age"][1]}
    if p["pref_education_unlimited"]:
        # 显式不限：抽取契约允许 {"min": null}，引擎按"满足但计权重"处理。
        preference["education_level"] = {"min": None}
    elif p["pref_education_min_ai"] is not None:
        preference["education_level"] = {"min": p["pref_education_min_ai"]}
    if p["pref_city"] is not None:
        preference["city_code"] = [p["pref_city"]]
    if p["pref_income_min_ai"] is not None:
        preference["income_band"] = {"min": p["pref_income_min_ai"]}
    if p["pref_goal"] is not None:
        # 引擎已测契约（test_ai_compatibility 夹具）：goal 偏好为字符串。
        preference["relationship_goal"] = p["pref_goal"]
    if p["pref_tags"]:
        preference["interest_tags"] = p["pref_tags"]
    return FeatureSet(profile=profile, preference=preference)


# height_cm 占位：compat 引擎有身高维度，合成样本用固定值控制覆盖率算术。
_MBTI_FAKE_HEIGHT: dict[str, int] = {}


def _full_pair() -> tuple[dict[str, Any], dict[str, Any]]:
    a = _person(
        "A",
        age=28,
        education_ai=4,  # 本科（AI 刻度；存储域 3）
        income_band="10-20w",
        city="330100",
        tags=["旅行", "摄影"],
        goal="marriage",
        mbti="INFJ",
        pref_age=(26, 34),
        pref_education_min_ai=4,  # 本科及以上
        pref_city="330100",
        pref_income_min_ai=3,
        pref_goal="marriage",
        pref_tags=["旅行"],
    )
    b = _person(
        "B",
        age=30,
        education_ai=5,  # 硕士（AI 刻度；存储域 4）
        income_band="30-50w",
        city="330100",
        tags=["旅行", "音乐"],
        goal="marriage",
        mbti="INFP",
        pref_age=(26, 34),
        pref_education_min_ai=3,  # 大专及以上
        pref_city="330100",
        pref_income_min_ai=2,
        pref_goal="marriage",
        pref_tags=["摄影"],
    )
    return a, b


def build_rows() -> list[dict[str, Any]]:
    """跑全部场景，返回逐样本对照行（CLI 输出与测试断言共用）。"""
    a_full, b_full = _full_pair()
    scenarios: list[tuple[str, dict[str, Any], dict[str, Any], str]] = []

    # 1) 完整资料：双方全维度。
    scenarios.append(("complete_pair", a_full, b_full, "双方全维度齐备"))

    # 2) 不对称偏好：A 偏好严格，B 无任何偏好字段。
    a_strict, b_nopref = a_full, _person(
        "B-nopref",
        age=30,
        education_ai=5,
        income_band="30-50w",
        city="330100",
        tags=["旅行", "音乐"],
        goal="marriage",
        mbti="INFP",
        pref_age=None,
        pref_education_min_ai=None,
        pref_city=None,
        pref_income_min_ai=None,
        pref_goal=None,
        pref_tags=[],
    )
    scenarios.append(("asymmetric_preference", a_strict, b_nopref, "B 无偏好：B→A 方向偏好缺失"))

    # 3) 显式不限：B 对学历显式 {"min": null}。
    b_unlimited = _person(
        "B-unlimited",
        age=30,
        education_ai=5,
        income_band="30-50w",
        city="330100",
        tags=["旅行", "音乐"],
        goal="marriage",
        mbti="INFP",
        pref_age=(26, 34),
        pref_education_min_ai=None,
        pref_education_unlimited=True,
        pref_city="330100",
        pref_income_min_ai=2,
        pref_goal="marriage",
        pref_tags=["摄影"],
    )
    scenarios.append(("explicit_unrestricted", a_full, b_unlimited, "B 学历显式不限：计权重、按满足计分"))

    # 4) 缺字段：B 缺 height/interest/收入 → DIMENSION_UNKNOWN，coverage 下探。
    b_missing = _person(
        "B-missing",
        age=30,
        education_ai=5,
        income_band=None,
        city="330100",
        tags=[],
        goal="marriage",
        mbti="INFP",
        pref_age=(26, 34),
        pref_education_min_ai=3,
        pref_city=None,
        pref_income_min_ai=None,
        pref_goal=None,
        pref_tags=[],
    )
    scenarios.append(("missing_fields", a_full, b_missing, "B 缺收入/标签，A 缺身高信息"))

    # 5) 真实零分：A 要求本科及以上（AI min=4），候选初中学历（AI=1）。
    a_demanding, b_junior = _full_pair()
    b_junior["education_ai"] = 1
    scenarios.append(("real_zero", a_demanding, b_junior, "学历硬冲突：方向分 0（非缺失）"))

    # 6) 覆盖率阈值边界：仅保留 age20+education10+income10+goal10=50 的
    #    维度，coverage 恰为 0.50（< 0.50 才拦）。
    def _thin(person_id: str, dims: set[str]) -> dict[str, Any]:
        return _person(
            person_id,
            age=28 if person_id.startswith("thin-a") else 30,
            education_ai=4 if "education" in dims else None,
            income_band="10-20w" if "income" in dims else None,
            city=None,
            tags=[],
            goal="marriage" if "goal" in dims else None,
            mbti=None,
            pref_age=(26, 34) if "age" in dims else None,
            pref_education_min_ai=3 if "education" in dims else None,
            pref_city=None,
            pref_income_min_ai=2 if "income" in dims else None,
            pref_goal="marriage" if "goal" in dims else None,
            pref_tags=[],
        )

    thin_50 = {"age", "education", "income", "goal"}
    a_thin50 = _thin("thin-a-50", thin_50)
    b_thin50 = _thin("thin-b-50", thin_50)
    scenarios.append(("coverage_boundary_050", a_thin50, b_thin50, "可用权重恰 50/100"))

    # 7) 低于阈值：age20+city15+goal10=45 → blocked。
    thin_45 = {"age", "city", "goal"}
    a_thin45 = _thin("thin-a-45", thin_45)
    b_thin45 = _thin("thin-b-45", thin_45)
    scenarios.append(("coverage_below_threshold", a_thin45, b_thin45, "可用权重 45/100 → 拦"))

    # 8) 方向互换：同一对样本 A/B 互换角色。
    scenarios.append(("direction_swap", b_full, a_full, "与 complete_pair 互换方向"))

    rows: list[dict[str, Any]] = []
    for sample_id, viewer, candidate, note in scenarios:
        # 新兼容分：compat 投影域。单方向分直接取 directional_score——
        # blocked 结果会丢弃 directions，而对照需要逐方向证据。
        vf, cf = _compat_features(viewer), _compat_features(candidate)
        dir_a2b, cov_a2b, reasons_a2b = directional_score(vf, cf, COMPATIBILITY_RULES)
        dir_b2a, cov_b2a, reasons_b2a = directional_score(cf, vf, COMPATIBILITY_RULES)
        result = compute_compatibility(vf, cf, COMPATIBILITY_RULES)
        # 旧展示分：legacy 存储域。legacy 天然单向（候选侧证书/活跃计分，
        # 观察者侧偏好计分），双向都跑以实证不对称性。
        legacy_score, legacy_reason = _candidate_score(
            _legacy_viewer(viewer), _legacy_candidate(candidate)
        )
        legacy_swapped, _ = _candidate_score(
            _legacy_viewer(candidate), _legacy_candidate(viewer)
        )
        missing = sorted(
            {
                reason
                for reason in (*reasons_a2b, *reasons_b2a, *result.reason_codes)
                if reason in ("DIMENSION_UNKNOWN", "COVERAGE_INSUFFICIENT")
            }
        )
        rows.append(
            {
                "sample_id": sample_id,
                "legacy_display_score": legacy_score,
                "legacy_display_score_swapped": legacy_swapped,
                "legacy_reason": legacy_reason,
                "dir_a2b": dir_a2b,
                "dir_b2a": dir_b2a,
                "pair_score": result.pair_score,
                "cov_a2b": round(cov_a2b, 4),
                "cov_b2a": round(cov_b2a, 4),
                "coverage": result.coverage,
                "status": result.status,
                "version_legacy": LEGACY_MATCH_ALGORITHM_VERSION,
                "version_pair": COMPATIBILITY_ALGORITHM_VERSION,
                "missing_reasons": missing,
                "notes": note,
            }
        )
    return rows


def directional_coverage(source: FeatureSet, target: FeatureSet) -> float:
    """单方向可用权重占比（复用引擎规则；无可用维度时引擎返回 0.0）。"""
    _score, coverage, _reasons = directional_score(source, target, COMPATIBILITY_RULES)
    return coverage


def _semantic_correspondence_checks() -> list[dict[str, Any]]:
    """C3：raw 抽取刻度 → 投影 → 两引擎消费的语义对应核验（纯函数级）。"""
    from app.services.ai.compatibility import _score_education

    checks: list[dict[str, Any]] = []
    for ai_edu, storage_edu, label in (
        (2, 1, "高中及以下"),
        (3, 2, "大专"),
        (4, 3, "本科"),
        (5, 4, "硕士"),
        (6, 5, "博士"),
    ):
        checks.append(
            {
                "label": label,
                "ai_scale": ai_edu,
                "storage_scale": storage_edu,
                "map_holds": _AI_EDU_TO_STORAGE[ai_edu] == storage_edu,
                "compat_pref_min_ai4_accepts": _score_education(
                    {"min": 4}, ai_edu
                )
                == (100.0 if ai_edu >= 4 else 0.0),
            }
        )
    return checks


def main() -> int:
    rows = build_rows()
    print("== 新旧评分离线对照（合成样本；不写快照、不扣配额、不调模型）==")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("== raw 抽取刻度 → 投影 → 两引擎语义对应核验 ==")
    print(json.dumps(_semantic_correspondence_checks(), ensure_ascii=False, indent=2))
    blocked = [r["sample_id"] for r in rows if r["status"] != "ready"]
    print(
        f"== 汇总：{len(rows)} 个场景；COVERAGE_THRESHOLD={COVERAGE_THRESHOLD}；"
        f"blocked={blocked}。合成样本结论不得宣称为真实用户覆盖率或推荐质量 =="
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
