"""经典模式「基础信息」：用大模型把恋爱报告 PART01 / BOT 采集解析成可提交字段。

只解析、不落库；前端填入表单后仍走现有 updateOwnProfile。
解析不了的字段必须输出 null，禁止取区间中值、禁止脑补。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.flags import AiFeature
from app.services.ai_provider import complete, parse_json
from app.services.aibot_report import latest_report

logger = logging.getLogger(__name__)

# 与经典编辑页枚举对齐（见 pagesSub/userExtra/user/edit.uvue）
_INCOME_LABELS = {"小于10w", "10-20w", "30-50w", "50-100w", "100w以上", "保密"}
_OCCUPATION_HINTS = [
    "私企员工",
    "央企/国企",
    "事业单位/公务员",
    "外企员工",
    "自由职业",
    "创业者/个体",
    "教师",
    "医生",
    "律师",
    "金融从业",
    "互联网/IT",
    "制造业",
    "服务业",
    "在校学生",
    "其他",
]

PROFILE_BASIC_PARSE_PROMPT = """
你是「经典模式资料填写助手」。任务：阅读用户恋爱报告里的【基础信息】与【BOT 采集原文】，
解析成经典模式「编辑资料 · 基础信息」可直接选用的字段。

【最高优先级】
1. 只根据输入里已有的信息解析；禁止脑补、禁止猜测、禁止编造。
2. 解析不了、对不齐枚举、只有区间没有精确值 → 该字段输出 null。
3. 禁止把身高/体重/收入区间取中值填成精确数（例如「165–170 cm」不能填 167）。
4. 禁止把 BMI/身材描述（偏瘦、微胖）填成体重 kg。
5. 禁止把年龄段（如「1996–2000 年出生」）编成具体生日。
6. 已有【当前表单空位】以外的字段也允许输出；前端会决定是否覆盖。你仍须遵守 1～5。

【目标字段与合法取值】（不对齐就 null）
- gender: 只能 1（男）或 2（女）
- birthday: 只能完整日期 "YYYY-MM-DD"；没有精确生日 → null
- height_cm: 整数厘米 140～220；必须是精确厘米（如 175 / "175 cm"）。区间 → null
- weight_kg: 整数公斤 40～120；必须是精确公斤。身材描述 → null
- education_level: 只能 1～5
  1=高中及以下  2=大专  3=本科  4=硕士  5=博士
  映射提示（仅当语义明确）：
  · 大专 → 2
  · 全日制本科 / 本科 / 一本 / 211 / 985 / QS前100 → 3
  · 硕士 → 4；明确写「博士」→ 5；「硕博」含糊 → null（不要猜）
  · 「其他」→ null
- occupation: 短文本 ≤32 字。优先贴合：{occupation_hints}；对不上可用原文短称；没有 → null
- income_label: 只能是以下之一，否则 null：
  小于10w | 10-20w | 30-50w | 50-100w | 100w以上 | 保密
  映射提示（仅当能唯一对应）：
  · 0–10 万 → 小于10w
  · 10–20 万 → 10-20w
  · 100 万以上 / 100–500 / 500 以上 → 100w以上
  · 10–15、15–20、20–30、30–100 等与上表不能一一对应 → null
- is_married: 只能 1=未婚、2=离异、3=丧偶
  · 「未婚」→ 1
  · 任何「离异…」细分 → 2
  · 「丧偶」→ 3
  · 没有 → null
- company: 公司名短文本 ≤64；没有 → null
- mbti: 必须是标准四字母如 INTJ；否则 null
- city_name: 可读城市名（如「杭州」「上海市」）；区划码或无法识别 → null
  （仅作展示参考；前端有编码表才写入现居地）

【输入】
【恋爱报告 PART01 基础信息行】
{{report_profile_lines}}

【BOT 采集原文 JSON】
{{bot_intake_json}}

【会员档案已有精确值（可作交叉校验，仍不要脑补缺失项）】
{{precise_json}}

【输出 JSON】只输出一个对象，不要 Markdown：
{
  "fills": {
    "gender": null,
    "birthday": null,
    "height_cm": null,
    "weight_kg": null,
    "education_level": null,
    "occupation": null,
    "income_label": null,
    "is_married": null,
    "company": null,
    "mbti": null,
    "city_name": null
  },
  "filled_keys": ["实际填了的键"],
  "skipped": [{"key":"height_cm","reason":"只有区间无精确值"}],
  "summary": "≤40字，告诉用户填了几项、哪些因对不齐跳过"
}
""".strip()


def build_profile_basic_parse_messages(
    *,
    report_profile_lines: str,
    bot_intake_json: str,
    precise_json: str,
) -> list[dict[str, str]]:
    prompt = (
        PROFILE_BASIC_PARSE_PROMPT.replace(
            "{occupation_hints}", " / ".join(_OCCUPATION_HINTS)
        )
        .replace("{{report_profile_lines}}", report_profile_lines or "（无）")
        .replace("{{bot_intake_json}}", bot_intake_json or "{}")
        .replace("{{precise_json}}", precise_json or "{}")
    )
    return [
        {
            "role": "system",
            "content": "你是资料字段解析器。只输出一个 JSON 对象。解析不了就填 null，禁止编造。",
        },
        {"role": "user", "content": "PROFILE_BASIC_PARSE\n" + prompt},
    ]


def _as_int(value: Any, *, lo: int, hi: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(float(str(value).strip().replace("cm", "").replace("kg", "").replace("厘米", "").replace("公斤", "")))
    except Exception:
        return None
    if n < lo or n > hi:
        return None
    return n


def _normalize_fills(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {
        "gender": None,
        "birthday": None,
        "height_cm": None,
        "weight_kg": None,
        "education_level": None,
        "occupation": None,
        "income_label": None,
        "is_married": None,
        "company": None,
        "mbti": None,
        "city_name": None,
    }

    g = src.get("gender")
    try:
        gi = int(g) if g is not None and str(g).strip() != "" else None
    except Exception:
        gi = None
    if gi in (1, 2):
        out["gender"] = gi

    b = str(src.get("birthday") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", b):
        out["birthday"] = b

    out["height_cm"] = _as_int(src.get("height_cm"), lo=140, hi=220)
    out["weight_kg"] = _as_int(src.get("weight_kg"), lo=40, hi=120)

    try:
        edu = int(src.get("education_level")) if src.get("education_level") is not None else None
    except Exception:
        edu = None
    if edu in (1, 2, 3, 4, 5):
        out["education_level"] = edu

    occ = str(src.get("occupation") or "").strip()
    if occ and occ not in ("其他", "无", "未知"):
        out["occupation"] = occ[:32]

    income = str(src.get("income_label") or "").strip()
    if income in _INCOME_LABELS:
        out["income_label"] = income

    try:
        married = int(src.get("is_married")) if src.get("is_married") is not None else None
    except Exception:
        married = None
    if married in (1, 2, 3):
        out["is_married"] = married

    company = str(src.get("company") or "").strip()
    if company:
        out["company"] = company[:64]

    mbti = str(src.get("mbti") or "").strip().upper()
    if re.fullmatch(r"[EI][NS][FT][JP]", mbti):
        out["mbti"] = mbti

    city = str(src.get("city_name") or "").strip()
    if city and not city.isdigit():
        out["city_name"] = city[:32]

    return out


async def parse_profile_basic_from_report(
    db: AsyncSession,
    user_id: int,
    *,
    bot_intake: dict[str, Any] | None = None,
    precise: dict[str, Any] | None = None,
    report_profile: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """调模型解析；失败时返回空 fills（不瞎填）。"""
    lines: list[str] = []
    profile_rows = report_profile
    if profile_rows is None:
        latest = await latest_report(db, user_id)
        part01 = (latest or {}).get("part01") if isinstance(latest, dict) else None
        rows = (part01 or {}).get("profile") if isinstance(part01, dict) else None
        profile_rows = rows if isinstance(rows, list) else []
    for row in profile_rows or []:
        if not isinstance(row, dict):
            continue
        k = str(row.get("k") or row.get("label") or "").strip()
        v = str(row.get("v") or row.get("value") or "").strip()
        if k and v:
            lines.append(f"- {k}: {v}")

    messages = build_profile_basic_parse_messages(
        report_profile_lines="\n".join(lines) if lines else "（无报告基础信息行）",
        bot_intake_json=json.dumps(bot_intake or {}, ensure_ascii=False),
        precise_json=json.dumps(precise or {}, ensure_ascii=False),
    )

    empty = {
        "fills": _normalize_fills({}),
        "filled_keys": [],
        "skipped": [{"key": "*", "reason": "模型不可用或无可用证据"}],
        "summary": "暂时解析不出可填写字段",
        "source": "empty",
    }

    try:
        raw = await complete(
            messages,
            json_mode=True,
            scene="aibot_profile_basic_parse",
            feature=AiFeature.ADVISOR,
        )
        parsed = parse_json(raw)
        fills = _normalize_fills(parsed.get("fills") if isinstance(parsed.get("fills"), dict) else parsed)
        filled_keys = [k for k, v in fills.items() if v is not None]
        skipped_raw = parsed.get("skipped") if isinstance(parsed.get("skipped"), list) else []
        skipped: list[dict[str, str]] = []
        for item in skipped_raw[:20]:
            if isinstance(item, dict):
                skipped.append(
                    {
                        "key": str(item.get("key") or "")[:32],
                        "reason": str(item.get("reason") or "")[:80],
                    }
                )
        summary = str(parsed.get("summary") or "").strip()[:80]
        if not summary:
            summary = f"已解析 {len(filled_keys)} 项可填字段" if filled_keys else "没有可对齐的字段"
        return {
            "fills": fills,
            "filled_keys": filled_keys,
            "skipped": skipped,
            "summary": summary,
            "source": "ai",
        }
    except Exception as exc:
        if isinstance(exc, HTTPException) and int(getattr(exc, "status_code", 0) or 0) not in (
            503,
            502,
            500,
        ):
            raise
        logger.warning(
            "aibot profile basic parse failed user=%s err=%s",
            user_id,
            type(exc).__name__,
            exc_info=True,
        )
        return empty
