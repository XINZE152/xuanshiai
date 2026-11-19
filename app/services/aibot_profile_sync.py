"""BOT 模糊采集 ↔ 精准资料：双轨写入规则。

- ``bot_intake_json``：永远按 BOT 选项原样（id + label）合并保存，可供检索与 AI。
- 精准字段（gender/birthday/height…）：仅当与现网枚举/类型「一致可对齐」且
  当前精准值为空时才写入；绝不取中值、不覆盖主业务已填的精准值。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

# BOT 选项 id → 可安全对齐的精准值（仅列「一致」项）
_GENDER_MAP = {"男": 1, "女": 2}
_MARITAL_MAP = {"never": 1}  # 仅「未婚」与 is_married=1 一致；离异细分不对齐
_EDU_MAP = {
    "edu_col": 2,  # 大专
    "edu_bach": 3,  # 全日制本科
    "edu_grad": 4,  # 硕博 → 硕士档（无法区分博士，仍属学历体系内明确档）
}
# 一本/211/QS/其他：BOT 专有分层，不对齐精准 1–5，只进 bot_intake


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_bot_intake_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _as_dict(raw: Any) -> dict[str, Any]:
    return parse_bot_intake_dict(raw)


def normalize_bot_intake_entry(value: Any, *, source: str = "aibot") -> dict[str, Any] | None:
    """把单字段规范成 {id, label, source, updated_at}；非法则丢弃。"""
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        if not text or text == "skip":
            return None
        return {
            "id": text,
            "label": text,
            "source": source,
            "updated_at": _now_iso(),
        }
    if not isinstance(value, dict):
        return None
    entry_id = value.get("id")
    if entry_id is None:
        return None
    id_text = str(entry_id).strip()
    if not id_text or id_text == "skip":
        return None
    label = value.get("label")
    label_text = str(label).strip() if label is not None else id_text
    return {
        "id": id_text,
        "label": label_text or id_text,
        "source": str(value.get("source") or source),
        "updated_at": str(value.get("updated_at") or _now_iso()),
    }


def merge_bot_intake(existing: Any, incoming: Any, *, source: str = "aibot") -> dict[str, Any]:
    """合并 BOT 采集：incoming 覆盖同名字段，保留其余历史键。"""
    merged = _as_dict(existing)
    for key, raw in _as_dict(incoming).items():
        field = str(key).strip()
        if not field or field.startswith("_"):
            continue
        entry = normalize_bot_intake_entry(raw, source=source)
        if entry is None:
            continue
        merged[field] = entry
    merged["_updated_at"] = _now_iso()
    return merged


def precise_fills_from_bot_intake(
    bot_intake: dict[str, Any],
    *,
    current_gender: Any = None,
    current_is_married: Any = None,
    current_education_level: Any = None,
    current_occupation: Any = None,
) -> dict[str, Any]:
    """仅返回「可对齐且精准侧为空」的字段，供写入 users/user_profile。"""
    fills: dict[str, Any] = {}
    gender_entry = bot_intake.get("gender")
    if isinstance(gender_entry, dict) and current_gender is None:
        mapped = _GENDER_MAP.get(str(gender_entry.get("id") or ""))
        if mapped is not None:
            fills["gender"] = mapped

    marital_entry = bot_intake.get("marital")
    if isinstance(marital_entry, dict) and current_is_married is None:
        mapped = _MARITAL_MAP.get(str(marital_entry.get("id") or ""))
        if mapped is not None:
            fills["is_married"] = mapped

    edu_entry = bot_intake.get("edu")
    if isinstance(edu_entry, dict) and current_education_level is None:
        mapped = _EDU_MAP.get(str(edu_entry.get("id") or ""))
        if mapped is not None:
            fills["education_level"] = mapped

    job_entry = bot_intake.get("job")
    if isinstance(job_entry, dict) and (current_occupation is None or str(current_occupation).strip() == ""):
        label = str(job_entry.get("label") or job_entry.get("id") or "").strip()
        # occupation 是自由文本：可把 BOT 选项文案原样写入精准职业
        if label and label != "其他":
            fills["occupation"] = label[:128]

    return fills


def facts_for_ai(
    *,
    precise: dict[str, Any] | None,
    bot_intake: Any,
) -> dict[str, Any]:
    """AI / 报告上下文：精准值与 Bot 原文可并存。

    - 精准字段供打分与模型推理；
    - Bot 区间/手输原文（``*_band`` / ``*_bot``）始终保留，报告 PART01 优先如实展示。
    """
    precise = precise or {}
    bot = _as_dict(bot_intake)
    out: dict[str, Any] = {}

    def bot_label(key: str) -> str | None:
        entry = bot.get(key)
        if isinstance(entry, dict):
            label = entry.get("label") or entry.get("id")
            if label is None:
                return None
            text = str(label).strip()
            return text if text != "" else None
        return None

    if precise.get("gender") in (1, 2):
        out["gender"] = "男" if int(precise["gender"]) == 1 else "女"
        out["gender_source"] = "precise"
    elif bot_label("gender"):
        out["gender"] = bot_label("gender")
        out["gender_source"] = "bot"

    if precise.get("birthday") is not None:
        out["birthday"] = str(precise["birthday"])
        out["birthday_source"] = "precise"
    if bot_label("ageBand"):
        out["age_band"] = bot_label("ageBand")
        if "birthday_source" not in out:
            out["age_band_source"] = "bot"

    if precise.get("height") is not None:
        out["height_cm"] = int(precise["height"])
        out["height_source"] = "precise"
    if bot_label("height"):
        out["height_band"] = bot_label("height")
        if "height_source" not in out:
            out["height_source"] = "bot"

    if precise.get("weight") is not None:
        out["weight_kg"] = int(precise["weight"])
        out["weight_source"] = "precise"
    if bot_label("bmi"):
        out["bmi_band"] = bot_label("bmi")
        if "bmi_source" not in out:
            out["bmi_source"] = "bot"

    if precise.get("education_level") is not None:
        out["education_level"] = int(precise["education_level"])
        out["education_source"] = "precise"
    if bot_label("edu"):
        out["education_bot"] = bot_label("edu")
        if "education_source" not in out:
            out["education_source"] = "bot"

    if precise.get("occupation"):
        out["occupation"] = str(precise["occupation"])
        out["occupation_source"] = "precise"
    elif bot_label("job"):
        out["occupation"] = bot_label("job")
        out["occupation_source"] = "bot"

    if precise.get("is_married") is not None:
        out["is_married"] = int(precise["is_married"])
        out["marriage_source"] = "precise"
    if bot_label("marital"):
        out["marriage_bot"] = bot_label("marital")
        if "marriage_source" not in out:
            out["marriage_source"] = "bot"

    if precise.get("income") is not None:
        out["income"] = float(precise["income"])
        out["income_source"] = "precise"
    if bot_label("income"):
        out["income_bot"] = bot_label("income")
        if "income_source" not in out:
            out["income_source"] = "bot"

    # 城市：Bot 原文优先留给报告；精准地名作补充。禁止裸行政区划码。
    bot_city = bot_label("city")
    city_display = precise.get("residence_display")
    city_text = str(city_display).strip() if city_display is not None else ""
    if bot_city:
        out["city"] = bot_city
        out["city_source"] = "bot"
        if city_text != "" and not city_text.isdigit() and city_text != bot_city:
            out["city_precise"] = city_text
    elif city_text != "" and not city_text.isdigit():
        out["city"] = city_text
        out["city_source"] = "precise"

    return out
